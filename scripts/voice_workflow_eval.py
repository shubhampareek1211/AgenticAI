"""Measure the real application's transcription, chat, and framed speech APIs.

Run against the separate loopback preview. Reports and audio contain evaluation
content and belong under ignored .local/. No retries or transcript corrections
are performed. API timings exclude microphone capture and audible browser output.
"""

from __future__ import annotations

import argparse
import array
import io
import json
import math
import sys
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

import httpx


def wav_frames(chunks):
    """Require a complete stream, including its explicit success terminator."""
    pending = bytearray()
    expected = None
    for chunk in chunks:
        pending.extend(chunk)
        while True:
            if expected is None:
                if len(pending) < 4:
                    break
                expected = int.from_bytes(pending[:4], "big")
                del pending[:4]
                if expected == 0:
                    return
                if expected == 0xFFFFFFFF:
                    raise ValueError("Speech generation stopped before completion")
                if not 44 <= expected <= 12 * 1024 * 1024:
                    raise ValueError("Invalid audio frame size")
            if len(pending) < expected:
                break
            frame = bytes(pending[:expected])
            del pending[:expected]
            expected = None
            with wave.open(io.BytesIO(frame)) as audio:
                if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (
                    1,
                    2,
                    24000,
                ):
                    raise ValueError("Unexpected audio format")
                pcm = array.array("h", audio.readframes(audio.getnframes()))
                if sys.byteorder != "little":
                    pcm.byteswap()
                if not pcm or not any(pcm):
                    raise ValueError("Empty or silent generated audio")
            yield frame
    raise ValueError("Truncated speech stream: success terminator missing")


def load_manifest(path):
    manifest = json.loads(path.read_text())
    if manifest.get("source") not in {"synthetic", "human"}:
        raise ValueError("Manifest source must explicitly be synthetic or human")
    if manifest["source"] == "human" and manifest.get("cloud_transcription_consent") is not True:
        raise ValueError("Human recordings require cloud_transcription_consent=true")
    if not isinstance(manifest.get("clips"), list) or not manifest["clips"]:
        raise ValueError("Manifest must contain clips")
    for clip in manifest["clips"]:
        if clip.get("language") not in {"en", "hi", "auto"}:
            raise ValueError("Each clip needs language en, hi, or auto")
        if clip.get("kind", "speech") not in {"speech", "silence"}:
            raise ValueError("Unsupported clip kind")
        if clip.get("kind", "speech") == "speech" and not clip.get("reference"):
            raise ValueError("Speech clips need their actual spoken reference text")
        recording = (path.parent / clip["file"]).resolve()
        if not recording.is_file():
            raise ValueError(f"Recording missing: {clip['file']}")
        if not recording.is_relative_to(path.parent.resolve()):
            raise ValueError("Keep recordings inside the manifest directory")
        if recording.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("Recording exceeds the app's 8 MiB limit")
    return manifest


def evaluate_clip(client, clip, directory, output, label, *, chat=False):
    record = {
        "sample": label,
        "file": clip["file"],
        "prompt_id": clip.get("prompt_id"),
        "length": clip.get("length"),
        "reference": clip.get("reference"),
        "language": clip["language"],
        "group": clip.get("group", clip["language"]),
        "kind": clip.get("kind", "speech"),
        "manual_meaning_pass": None,
    }
    started = time.perf_counter()
    session_id = None
    stage = "transcription"
    try:
        path = directory / clip["file"]
        mime = clip.get("mime_type") or {
            ".wav": "audio/wav",
            ".webm": "audio/webm",
            ".m4a": "audio/mp4",
            ".ogg": "audio/ogg",
        }.get(path.suffix.lower(), "application/octet-stream")
        with path.open("rb") as recording:
            response = client.post(
                "/transcribe",
                files={"file": (path.name, recording, mime)},
                data={"language": clip["language"]},
            )
        record["transcription_seconds"] = round(time.perf_counter() - started, 3)
        record["transcription_status"] = response.status_code
        if record["kind"] == "silence":
            record["passed"] = (
                response.status_code == 422 and response.json().get("code") == "no_speech"
            )
            return record
        response.raise_for_status()
        transcription = response.json()
        record.update(
            transcript=transcription["text"],
            reference=clip["reference"],
            duration_seconds=transcription["duration_seconds"],
        )
        record["short_clip_duration_eligible"] = 5 <= transcription["duration_seconds"] <= 15
        record["passed"] = True
        if not chat:
            return record
        stage = "chat"
        allocated = client.post("/sessions")
        allocated.raise_for_status()
        session_id = allocated.json()["session_id"]
        chat_started = time.perf_counter()
        response = client.post(
            "/chat", json={"session_id": session_id, "message": record["transcript"]}
        )
        record["chat_seconds"] = round(time.perf_counter() - chat_started, 3)
        response.raise_for_status()
        answer = response.json()
        record["answer"] = answer["response"]
        record["tool_calls"] = [call["name"] for call in answer["tool_calls"]]
        transcript = client.get(f"/sessions/{session_id}")
        transcript.raise_for_status()
        restored = transcript.json()
        messages = restored["messages"]
        assistant = next(
            message
            for message in reversed(messages)
            if message["role"] == "assistant" and message["payload"].get("content")
        )
        record["persistence_verified"] = assistant["payload"]["content"] == answer["response"]
        if not record["persistence_verified"]:
            raise ValueError("Saved assistant answer does not match the chat response")
        record["charts_verified"] = 0
        for chart_id in restored.get("charts", []):
            chart = client.get(f"/sessions/{session_id}/charts/{chart_id}")
            chart.raise_for_status()
            if chart.json().get("chart_id") != chart_id:
                raise ValueError("Restored chart ID does not match")
            record["charts_verified"] += 1
        stage = "speech"
        speech_started = time.perf_counter()
        frame_records = []
        with client.stream(
            "POST", f"/sessions/{session_id}/messages/{assistant['id']}/speech"
        ) as stream:
            record["speech_status"] = stream.status_code
            if stream.status_code in {413, 422}:
                stream.read()
                record["speech_unavailable"] = stream.json().get("code")
                record["passed"] = False
                return record
            stream.raise_for_status()
            if not stream.headers.get("Content-Type", "").startswith(
                "application/x-cricket-speech-stream"
            ):
                raise ValueError("Unexpected speech stream content type")
            for index, audio in enumerate(wav_frames(stream.iter_bytes())):
                elapsed = round(time.perf_counter() - speech_started, 3)
                if not frame_records:
                    record["speech_first_audio_seconds"] = elapsed
                    record["workflow_first_audio_seconds"] = round(time.perf_counter() - started, 3)
                filename = f"{label}-audio-{index + 1}.wav"
                (output / filename).write_bytes(audio)
                frame_records.append({"file": filename, "at_seconds": elapsed, "bytes": len(audio)})
        if not frame_records:
            raise ValueError("No audio frames")
        record["audio_frames"] = frame_records
        record["speech_complete_seconds"] = round(time.perf_counter() - speech_started, 3)
    except (httpx.HTTPError, ValueError, KeyError, StopIteration, wave.Error) as exc:
        record.update(passed=False, failed_stage=stage, error_type=type(exc).__name__)
        if isinstance(exc, httpx.HTTPStatusError):
            record["error_status"] = exc.response.status_code
            try:
                detail = exc.response.json()
                if isinstance(detail, dict):
                    record["error_code"] = detail.get("code")
            except ValueError:
                pass
        else:
            record["error"] = str(exc)
    finally:
        record["total_seconds"] = round(time.perf_counter() - started, 3)
        if session_id:
            # Only delete the new evaluation conversation, never user sessions.
            try:
                cleared = client.post("/clear", params={"session_id": session_id})
                cleared.raise_for_status()
                record["evaluation_session_cleared"] = True
            except httpx.HTTPError:
                record["evaluation_session_cleared"] = False
                record["cleanup_session_id"] = session_id
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument(
        "--chat-limit",
        type=int,
        default=0,
        help="Maximum recordings to submit to real Gemini and then TTS; default 0",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.runs <= 20 or not 0 <= args.chat_limit <= 20:
        parser.error("Use 1–20 runs and 0–20 chat calls")
    if httpx.URL(args.base_url).host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("Use the local evaluation app; cloud workers are reached through its proxies")
    manifest = load_manifest(args.manifest)
    args.output.mkdir(parents=True, exist_ok=True)
    report = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "source": manifest["source"],
        "app_url": args.base_url,
        "results": [],
        "release_acceptance": "not_established",
        "passed_field_scope": "API integration only; accuracy requires manual scoring",
        "timing_scope": "HTTP API; excludes microphone capture, user edit time, browser playback",
        "first_request_state": "unknown: not a proven cold start",
    }
    chats = 0
    with httpx.Client(base_url=args.base_url, timeout=180) as client:
        for run in range(args.runs):
            for index, clip in enumerate(manifest["clips"]):
                chat = clip.get("kind", "speech") == "speech" and chats < args.chat_limit
                if chat:
                    chats += 1
                row = evaluate_clip(
                    client,
                    clip,
                    args.manifest.parent,
                    args.output,
                    f"clip-{index + 1}-run-{run + 1}",
                    chat=chat,
                )
                report["results"].append(row)
                report["failed_checks"] = sum(not result["passed"] for result in report["results"])
                times = sorted(
                    result["transcription_seconds"]
                    for result in report["results"]
                    if result.get("transcription_status") == 200
                )
                report["sample_transcription_p95_seconds"] = (
                    times[math.ceil(0.95 * len(times)) - 1] if times else None
                )
                (args.output / "report.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2) + "\n"
                )
                print(json.dumps(row, ensure_ascii=False), flush=True)
    if report["failed_checks"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
