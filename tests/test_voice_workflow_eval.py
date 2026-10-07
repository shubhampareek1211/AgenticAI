import io
import json
import wave

import httpx
import pytest

from scripts.voice_workflow_eval import evaluate_clip, load_manifest, wav_frames


def audio():
    target = io.BytesIO()
    with wave.open(target, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x20\x01" * 100)
    return target.getvalue()


def test_framed_audio_handles_split_headers_and_requires_success_marker():
    wav = audio()
    body = len(wav).to_bytes(4, "big") + wav + bytes(4)
    assert list(wav_frames([body[:2], body[2:18], body[18:]])) == [wav]
    with pytest.raises(ValueError, match="terminator missing"):
        list(wav_frames([body[:-4]]))
    with pytest.raises(ValueError, match="before completion"):
        list(wav_frames([body[:-4] + b"\xff" * 4]))


def test_real_recordings_require_explicit_consent(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"source": "human", "clips": []}))
    with pytest.raises(ValueError, match="consent"):
        load_manifest(manifest)


def test_workflow_uses_actual_transcript_and_cleans_only_its_new_session(tmp_path):
    (tmp_path / "clip.wav").write_bytes(audio())
    cleared = []

    def handler(request):
        path = request.url.path
        if path == "/transcribe":
            return httpx.Response(200, json={"text": "Actual words", "duration_seconds": 5})
        if path == "/sessions":
            return httpx.Response(201, json={"session_id": "evaluation-only"})
        if path == "/chat":
            assert json.loads(request.content)["message"] == "Actual words"
            return httpx.Response(200, json={"response": "A cricket answer.", "tool_calls": []})
        if path == "/sessions/evaluation-only":
            return httpx.Response(
                200,
                json={
                    "messages": [
                        {
                            "id": "answer",
                            "role": "assistant",
                            "payload": {"content": "A cricket answer."},
                        }
                    ],
                    "charts": ["chart-one"],
                },
            )
        if path == "/sessions/evaluation-only/charts/chart-one":
            return httpx.Response(200, json={"chart_id": "chart-one"})
        if path.endswith("/speech"):
            # A partial failure must not be reported as successful end-to-end playback.
            wav = audio()
            return httpx.Response(
                200,
                content=len(wav).to_bytes(4, "big") + wav + b"\xff" * 4,
                headers={"Content-Type": "application/x-cricket-speech-stream"},
            )
        if path == "/clear":
            cleared.append(request.url.params["session_id"])
            return httpx.Response(200, json={"status": "ok"})
        raise AssertionError(path)

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(handler)
    ) as client:
        result = evaluate_clip(
            client,
            {"file": "clip.wav", "language": "en", "reference": "Ideal words"},
            tmp_path,
            tmp_path,
            "test",
            chat=True,
        )
    assert result["passed"] is False
    assert result["failed_stage"] == "speech"
    assert result["persistence_verified"] is True
    assert result["charts_verified"] == 1
    assert result["evaluation_session_cleared"] is True
    assert cleared == ["evaluation-only"]


def test_silence_requires_no_speech_and_never_starts_chat(tmp_path):
    (tmp_path / "silence.wav").write_bytes(audio())

    def handler(request):
        assert request.url.path == "/transcribe"
        return httpx.Response(422, json={"code": "no_speech"})

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(handler)
    ) as client:
        result = evaluate_clip(
            client,
            {"file": "silence.wav", "language": "en", "kind": "silence"},
            tmp_path,
            tmp_path,
            "silence",
            chat=True,
        )
    assert result["passed"] is True
