"""Generate a reproducible English/Hindi listening deck with local Kokoro CPU."""

import argparse
import json
import sys
import time
import wave
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy" / "kokoro"))
from runtime import configure_espeak_data

SAMPLES = [
    ("en_question", "a", "af_heart", "Virat Kohli scored 82 runs from 63 balls in an ODI."),
    (
        "en_analysis",
        "a",
        "af_heart",
        "Jasprit Bumrah took three wickets in four overs. His economy rate was six point two five runs per over.",
    ),
    (
        "hi_question",
        "h",
        "hf_alpha",
        "विराट कोहली ने 63 गेंदों पर 82 रन बनाए। भारत छह विकेट से जीता।",
    ),
    (
        "hi_mixed",
        "h",
        "hf_alpha",
        "विराट कोहली ने ODI में 82 रन बनाए और टीम ने मैच जीता।",
    ),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path(".local/kokoro-eval"))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    if args.repeats < 1 or args.threads < 1:
        parser.error("--repeats and --threads must be positive")
    args.out.mkdir(parents=True, exist_ok=True)

    torch.set_num_threads(args.threads)
    configure_espeak_data(args.out / "espeak-data")
    from kokoro import KPipeline

    started = time.perf_counter()
    en = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M", device="cpu")
    hi = KPipeline(lang_code="h", repo_id="hexgrad/Kokoro-82M", model=en.model)
    pipelines = {"a": en, "h": hi}
    load_seconds = time.perf_counter() - started
    results = []
    for name, language, voice, source in SAMPLES:
        for run in range(args.repeats):
            start = time.perf_counter()
            chunks = [
                np.asarray(audio, dtype=np.float32)
                for _, _, audio in pipelines[language](source, voice=voice)
                if audio is not None
            ]
            synthesis_seconds = time.perf_counter() - start
            if not chunks:
                raise RuntimeError(f"No audio generated for {name}")
            samples = np.concatenate(chunks)
            duration = len(samples) / 24000
            path = args.out / f"{name}-{run + 1}.wav"
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(24000)
                output.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
            results.append(
                {
                    "sample": name,
                    "language": language,
                    "voice": voice,
                    "run": run + 1,
                    "text": source,
                    "synthesis_seconds": round(synthesis_seconds, 3),
                    "audio_seconds": round(duration, 3),
                    "real_time_factor": round(synthesis_seconds / duration, 3),
                    "file": str(path),
                }
            )
            print(f"{name} #{run + 1}: {synthesis_seconds:.2f}s for {duration:.2f}s audio")
    report = {
        "model": "hexgrad/Kokoro-82M",
        "device": "cpu",
        "threads": args.threads,
        "model_load_seconds": round(load_seconds, 3),
        "runs": results,
    }
    (args.out / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
