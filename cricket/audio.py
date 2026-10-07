"""Bounded browser-audio normalization for speech recognition."""

import asyncio
import io
import sys
import wave
from array import array
from pathlib import Path
from tempfile import TemporaryDirectory

SUPPORTED_AUDIO = {
    "audio/webm": "matroska",
    "audio/mp4": "mov",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
}


class AudioError(Exception):
    def __init__(self, code: str, detail: str, status: int):
        super().__init__(detail)
        self.code, self.detail, self.status = code, detail, status


def wav_duration(data: bytes) -> float:
    try:
        with wave.open(io.BytesIO(data), "rb") as audio:
            if audio.getnchannels() != 1 or audio.getframerate() != 16000:
                raise ValueError("Unexpected WAV layout")
            if audio.getsampwidth() != 2 or audio.getcomptype() != "NONE":
                raise ValueError("Unexpected WAV encoding")
            frames = audio.getnframes()
            return frames / audio.getframerate()
    except (EOFError, ValueError, wave.Error) as exc:
        raise AudioError("invalid_audio", "Audio could not be decoded.", 422) from exc


def is_effectively_silent(data: bytes) -> bool:
    """Reject digital silence and very low-level recording noise before inference."""
    with wave.open(io.BytesIO(data), "rb") as audio:
        samples = array("h")
        samples.frombytes(audio.readframes(audio.getnframes()))
    if not samples:
        return True
    if sys.byteorder != "little":
        samples.byteswap()
    return sum(sample * sample for sample in samples) / len(samples) <= 32**2


async def normalize_audio(
    data: bytes,
    mime_type: str,
    *,
    max_duration_seconds: int,
    conversion_timeout_seconds: float,
) -> tuple[bytes, float]:
    """Convert one uploaded clip to 16 kHz mono PCM WAV with FFmpeg."""
    container = SUPPORTED_AUDIO.get(mime_type.split(";", 1)[0].strip().lower())
    if container is None:
        raise AudioError("unsupported_audio", "This recording format is unsupported.", 415)
    if not data:
        raise AudioError("invalid_audio", "The recording is empty.", 422)

    with TemporaryDirectory(prefix="cricket-voice-") as directory:
        source = Path(directory) / "source.audio"
        output = Path(directory) / "normalized.wav"
        await asyncio.to_thread(source.write_bytes, data)
        try:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-f",
                container,
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-vn",
                "-t",
                str(max_duration_seconds + 1),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                "-f",
                "wav",
                "-y",
                str(output),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise AudioError("voice_unavailable", "Audio conversion is unavailable.", 503) from exc
        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(), timeout=conversion_timeout_seconds
            )
        except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
            process.kill()
            await process.communicate()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise AudioError("transcription_timeout", "Audio conversion timed out.", 504) from exc
        if process.returncode != 0:
            # FFmpeg diagnostic output is deliberately omitted from the API response.
            _ = stderr
            raise AudioError("invalid_audio", "Audio could not be decoded.", 422)
        normalized = await asyncio.to_thread(output.read_bytes)

    duration = wav_duration(normalized)
    if duration <= 0:
        raise AudioError("invalid_audio", "The recording is empty.", 422)
    if duration > max_duration_seconds:
        raise AudioError("audio_too_long", "The recording is too long.", 413)
    if is_effectively_silent(normalized):
        raise AudioError("no_speech", "No speech was detected.", 422)
    return normalized, duration
