"""Small, CPU-only Kokoro worker. Keep this service private behind the app."""

import io
import os
import threading
import wave

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

MAX_TEXT_CHARS = 1800
MAX_AUDIO_SECONDS = 180
SAMPLE_RATE = 24000
VOICE = "af_heart"


class SynthesisRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    voice: str = VOICE


def encode_wav(parts: list[np.ndarray]) -> bytes:
    total_samples = sum(part.size for part in parts)
    if total_samples == 0 or total_samples > MAX_AUDIO_SECONDS * SAMPLE_RATE:
        raise ValueError("Generated audio exceeded the pilot duration limit")
    samples = np.concatenate(parts)
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm.tobytes())
    return output.getvalue()


def create_app(pipeline=None) -> FastAPI:
    app = FastAPI()
    gate = threading.BoundedSemaphore(1)

    @app.on_event("startup")
    def load_model():
        if pipeline is None:
            from runtime import configure_espeak_data

            configure_espeak_data()
            import torch

            torch.set_num_threads(int(os.getenv("KOKORO_THREADS", "2")))
            from kokoro import KPipeline

            app.state.pipeline = KPipeline(
                lang_code="a", repo_id="hexgrad/Kokoro-82M", device="cpu"
            )
            # Download or load the selected voice before health becomes ready.
            app.state.pipeline.load_voice(VOICE)
        else:
            app.state.pipeline = pipeline

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/synthesize")
    def synthesize(request: SynthesisRequest):
        if request.voice != VOICE:
            raise HTTPException(422, "Unsupported pilot voice.")
        if any("\u0900" <= character <= "\u097f" for character in request.text):
            raise HTTPException(422, "Hindi playback is not enabled for this pilot.")
        if not gate.acquire(blocking=False):
            raise HTTPException(429, "Speech worker is busy.", headers={"Retry-After": "1"})
        try:
            parts = []
            total_samples = 0
            for _, _, audio in app.state.pipeline(request.text, voice=VOICE):
                if audio is not None:
                    part = np.asarray(audio, dtype=np.float32)
                    total_samples += part.size
                    if total_samples > MAX_AUDIO_SECONDS * SAMPLE_RATE:
                        raise ValueError("Generated audio exceeded the pilot duration limit")
                    parts.append(part)
            if not parts:
                raise ValueError("No audio was generated")
            result = encode_wav(parts)
            return Response(result, media_type="audio/wav", headers={"Cache-Control": "no-store"})
        except ValueError as exc:
            raise HTTPException(502, "Speech generation failed.") from exc
        finally:
            gate.release()

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT", "8082")))
