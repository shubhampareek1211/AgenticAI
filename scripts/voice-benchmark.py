"""Benchmark a running whisper.cpp server against consented WAV clips.

Manifest JSON format:
{"clips": [{"file": "clip.wav", "reference": "spoken words",
            "entities": ["Kohli", "ODI"], "kind": "speech",
            "language": "en", "group": "english"}]}
Use kind="silence" for silence/noise controls; omit reference/entities there.
Paths are relative to the manifest file. Raw audio is never copied into the report.
Use language="hi" for Hindi and "auto" for mixed clips. A private Cloud Run
worker can be reached with a short-lived token saved in an ignored local file.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import re
import statistics
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path


def normalized_words(text: str) -> list[str]:
    return re.findall(r"[\w]+", text.casefold())


def word_error_count(reference: list[str], actual: list[str]) -> int:
    previous = list(range(len(actual) + 1))
    for i, word in enumerate(reference, start=1):
        current = [i]
        for j, candidate in enumerate(actual, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (word != candidate),
                )
            )
        previous = current
    return previous[-1]


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = math.ceil(p * len(ordered)) - 1
    return round(ordered[max(index, 0)], 3)


def transcribe(
    base_url: str,
    wav: Path,
    timeout: float,
    prompt: str = "",
    *,
    language: str = "en",
    id_token: str = "",
) -> tuple[str, float]:
    boundary = uuid.uuid4().hex
    data = wav.read_bytes()
    body = (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="clip.wav"\r\n'
            "Content-Type: audio/wav\r\n\r\n"
        ).encode()
        + data
        + (
            f"\r\n--{boundary}\r\n"
            f'Content-Disposition: form-data; name="language"\r\n\r\n{language}\r\n'
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="response_format"\r\n\r\njson\r\n'
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="prompt"\r\n\r\n'
            f"{prompt}\r\n"
            f"--{boundary}--\r\n"
        ).encode()
    )
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    if id_token:
        headers["Authorization"] = f"Bearer {id_token}"
    request = urllib.request.Request(
        base_url.rstrip("/") + "/inference",
        data=body,
        headers=headers,
        method="POST",
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.load(response)
    elapsed = time.monotonic() - started
    if not isinstance(result, dict) or not isinstance(result.get("text"), str):
        raise TypeError("Unexpected worker response shape")
    return result["text"].strip(), round(elapsed, 3)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8081")
    parser.add_argument("--model", required=True, help="Model label written to report")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--prompt", default="", help="Fixed vocabulary hint to evaluate")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument(
        "--id-token-file", type=Path, help="File containing a worker-audience Google ID token"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.runs < 1 or args.timeout <= 0:
        parser.error("--runs and --timeout must be positive")
    manifest = json.loads(args.manifest.read_text())
    clips = manifest.get("clips")
    if not isinstance(clips, list) or not clips:
        parser.error("Manifest must contain a nonempty clips array")
    for clip in clips:
        if clip.get("language", "en") not in {"en", "hi", "auto"}:
            parser.error(f"Unsupported language for {clip.get('file', '<unknown>')}")
    id_token = args.id_token_file.read_text().strip() if args.id_token_file else ""
    if args.id_token_file and not id_token:
        parser.error("--id-token-file is empty")

    first = clips[0]
    warmup_path = (args.manifest.parent / first["file"]).resolve()
    if not warmup_path.is_file():
        parser.error(f"Missing warm-up clip: {warmup_path}")
    _, warmup_seconds = transcribe(
        args.base_url,
        warmup_path,
        args.timeout,
        args.prompt if first.get("language", "en") == "en" else "",
        language=first.get("language", "en"),
        id_token=id_token,
    )

    records = []
    latencies: list[float] = []
    errors = 0
    reference_words = 0
    entity_passes = 0
    entity_total = 0
    silence_false_positives = 0
    silence_total = 0
    for clip in clips:
        path = (args.manifest.parent / clip["file"]).resolve()
        if not path.is_file():
            parser.error(f"Missing clip: {path}")
        if clip.get("kind", "speech") not in {"speech", "silence"}:
            parser.error(f"Invalid clip kind for {path}")
        for run in range(args.runs):
            try:
                language = clip.get("language", "en")
                transcript, elapsed = transcribe(
                    args.base_url,
                    path,
                    args.timeout,
                    args.prompt if language == "en" else "",
                    language=language,
                    id_token=id_token,
                )
            except (OSError, urllib.error.URLError, TypeError, ValueError) as exc:
                records.append(
                    {
                        "file": clip["file"],
                        "run": run + 1,
                        "group": clip.get("group", clip.get("language", "en")),
                        "error": str(exc),
                    }
                )
                continue
            latencies.append(elapsed)
            record = {
                "file": clip["file"],
                "run": run + 1,
                "latency_seconds": elapsed,
                "transcript": transcript,
                "language": language,
                "group": clip.get("group", language),
                "kind": clip.get("kind", "speech"),
            }
            if clip.get("kind", "speech") == "silence":
                silence_total += 1
                silence_false_positives += bool(normalized_words(transcript))
            else:
                reference = normalized_words(clip["reference"])
                actual = normalized_words(transcript)
                errors += word_error_count(reference, actual)
                reference_words += len(reference)
                entities = clip.get("entities", [])
                entity_total += len(entities)
                passed = sum(
                    all(word in actual for word in normalized_words(entity)) for entity in entities
                )
                entity_passes += passed
                record["word_errors"] = word_error_count(reference, actual)
                record["reference_words"] = len(reference)
                record["entity_passes"] = passed
                record["entity_total"] = len(entities)
                record["entities_preserved"] = [
                    entity
                    for entity in entities
                    if all(word in actual for word in normalized_words(entity))
                ]
            records.append(record)

    by_group = {}
    for clip in clips:
        group = clip.get("group", clip.get("language", "en"))
        summary = by_group.setdefault(
            group,
            {"clips": 0, "speech_clips": 0, "silence_clips": 0},
        )
        summary["clips"] += 1
        summary["silence_clips" if clip.get("kind") == "silence" else "speech_clips"] += 1
    for group, summary in by_group.items():
        runs = [item for item in records if item["group"] == group]
        successful = [item for item in runs if "error" not in item]
        group_latencies = [item["latency_seconds"] for item in successful]
        group_reference_words = sum(item.get("reference_words", 0) for item in successful)
        summary.update(
            successful_runs=len(successful),
            failed_runs=len(runs) - len(successful),
            p95_latency_seconds=percentile(group_latencies, 0.95),
            word_error_rate=(
                round(sum(item.get("word_errors", 0) for item in successful) / group_reference_words, 4)
                if group_reference_words
                else None
            ),
            entities_preserved=sum(item.get("entity_passes", 0) for item in successful),
            entities_total=sum(item.get("entity_total", 0) for item in successful),
            silence_false_positive_runs=sum(
                bool(normalized_words(item["transcript"]))
                for item in successful
                if item["kind"] == "silence"
            ),
        )

    report = {
        "benchmark_started_utc": datetime.now(timezone.utc).isoformat(),
        "host_platform": platform.platform(),
        "processor": platform.processor(),
        "model": args.model,
        "prompt": args.prompt,
        "warmup_seconds": warmup_seconds,
        "base_url": args.base_url,
        "runs_per_clip": args.runs,
        "clips": len(clips),
        "successful_runs": len(latencies),
        "failed_runs": len(records) - len(latencies),
        "median_latency_seconds": (round(statistics.median(latencies), 3) if latencies else None),
        "p95_latency_seconds": percentile(latencies, 0.95),
        "word_error_rate": round(errors / reference_words, 4) if reference_words else None,
        "entities_preserved": entity_passes,
        "entities_total": entity_total,
        "silence_false_positive_runs": silence_false_positives,
        "silence_runs": silence_total,
        "by_group": by_group,
        "results": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "results"}, indent=2))
    if report["failed_runs"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
