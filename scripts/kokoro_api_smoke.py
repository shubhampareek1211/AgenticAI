"""Exercise a worker through an authenticated local proxy, or an offline image.

No credentials or input text are written to the report. The fixed sentences
are synthetic checks, not a human listening or production latency evaluation.
"""

import argparse
import array
import io
import json
import math
import subprocess
import sys
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

PHRASES = [
    "Virat Kohli scored eighty two runs from fifty three balls.",
    (
        "The sample contains twelve matches. His strike rate was one hundred and forty two. "
        "This comparison describes those matches and does not establish a career average."
    ),
]


def request(url, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return response.status, response.read(), time.monotonic() - started
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), time.monotonic() - started


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--launch-worker", action="store_true")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, default=Path("/tmp/kokoro-smoke"))
    args = parser.parse_args()
    if not 1 <= args.repeats <= 20:
        parser.error("--repeats must be between 1 and 20")
    args.output.mkdir(parents=True, exist_ok=True)
    url = args.url.rstrip("/")
    worker = None
    try:
        if args.launch_worker:
            worker = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "server:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8080",
                ],
                cwd="/app",
            )
            deadline = time.monotonic() + 180
            while True:
                if worker.poll() is not None:
                    raise RuntimeError("Worker exited before becoming healthy")
                try:
                    if request(url + "/health")[0] == 200:
                        break
                except urllib.error.URLError:
                    pass
                if time.monotonic() >= deadline:
                    raise TimeoutError("Worker startup exceeded 180 seconds")
                time.sleep(1)

        rows = []
        # Synthesize first so the first request can expose startup latency.
        for repeat in range(args.repeats):
            for index, phrase in enumerate(PHRASES):
                status, body, elapsed = request(url + "/synthesize", {"text": phrase})
                if status != 200:
                    raise RuntimeError(f"Synthesis failed with HTTP {status}")
                with wave.open(io.BytesIO(body)) as wav:
                    assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (
                        1,
                        2,
                        24000,
                    )
                    duration = wav.getnframes() / wav.getframerate()
                    samples = array.array("h", wav.readframes(wav.getnframes()))
                if sys.byteorder != "little":
                    samples.byteswap()
                peak = max(abs(value) for value in samples)
                assert peak > 0 and 0 < duration <= 180, "Empty, silent, or oversized WAV"
                filename = f"phrase-{index + 1}-run-{repeat + 1}.wav"
                (args.output / filename).write_bytes(body)
                row = {
                    "sample": filename,
                    "seconds": round(elapsed, 3),
                    "audio_seconds": round(duration, 3),
                    "bytes": len(body),
                    "peak": peak,
                }
                rows.append(row)
                print(json.dumps(row), flush=True)

        assert request(url + "/health")[0] == 200
        for invalid in [
            {"text": "hello", "voice": "unknown"},
            {"text": "x" * 1801},
            {"text": ""},
            {"text": "नमस्ते"},
        ]:
            assert request(url + "/synthesize", invalid)[0] == 422
        subsequent = sorted(row["seconds"] for row in rows[1:])
        report = {
            "url": url,
            "samples": rows,
            "validation": "passed",
            "first_request_seconds": rows[0]["seconds"],
            "subsequent_sample_p95_seconds": subsequent[math.ceil(0.95 * len(subsequent)) - 1],
            "note": "Synthetic samples; first request is not necessarily a cold start.",
        }
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report), flush=True)
    finally:
        if worker:
            worker.terminate()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait()


if __name__ == "__main__":
    main()
