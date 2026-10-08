"""Optional, private text-to-speech worker for saved assistant answers."""

import asyncio
import os
import re
from dataclasses import dataclass

import httpx

MAX_TTS_CHARS = 1800
MAX_AUDIO_BYTES = 12 * 1024 * 1024
TTS_CHUNK_CHARS = 220
TTS_FIRST_CHUNK_CHARS = 100


def readable_text(markdown: str) -> str:
    """Match the browser's read-aloud cleanup before sending text to a worker."""
    value = re.sub(r"```[\s\S]*?```", " Code example omitted. ", markdown)
    value = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = re.sub(r"https?://\S+", "", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[*_~>#|]", " ", value)
    return " ".join(value.split())


def speech_chunks(text: str) -> list[str]:
    """Bound time to first audio while keeping nearby words together."""
    chunks = []
    while text:
        limit = TTS_FIRST_CHUNK_CHARS if not chunks else TTS_CHUNK_CHARS
        if len(text) <= limit:
            chunks.append(text)
            break
        window = text[: limit + 1]
        sentence = max(window.rfind(". "), window.rfind("? "), window.rfind("! "))
        space = window.rfind(" ")
        split_at = sentence + 1 if sentence > limit // 2 else space
        if split_at <= 0:
            split_at = limit
        chunks.append(text[:split_at].strip())
        text = text[split_at:].strip()
    return chunks


class TTSError(Exception):
    def __init__(self, code: str, detail: str, status: int):
        self.code, self.detail, self.status = code, detail, status
        super().__init__(detail)


@dataclass(frozen=True)
class TTSSettings:
    enabled: bool = False
    base_url: str = "http://127.0.0.1:8082"
    auth_mode: str = "none"
    audience: str | None = None
    timeout_seconds: float = 90
    max_concurrent: int = 1

    @classmethod
    def from_env(cls):
        try:
            settings = cls(
                enabled=os.getenv("TTS_ENABLED", "false").lower() in {"1", "true", "yes"},
                base_url=os.getenv("TTS_BASE_URL", cls.base_url).rstrip("/"),
                auth_mode=os.getenv("TTS_AUTH_MODE", "none"),
                audience=os.getenv("TTS_AUDIENCE") or None,
                timeout_seconds=float(os.getenv("TTS_TIMEOUT_SECONDS", "90")),
                max_concurrent=int(os.getenv("TTS_MAX_CONCURRENT", "1")),
            )
            if settings.enabled and (
                not settings.base_url.startswith(("http://", "https://"))
                or settings.auth_mode not in {"none", "google_id_token"}
                or (settings.auth_mode == "google_id_token" and not settings.audience)
                or settings.timeout_seconds <= 0
                or not 1 <= settings.max_concurrent <= 16
                or (
                    os.getenv("APP_ENV") in {"pilot", "production"}
                    and (
                        settings.auth_mode != "google_id_token"
                        or not settings.base_url.startswith("https://")
                    )
                )
            ):
                raise ValueError("Invalid TTS configuration")
            return settings
        except (ValueError, TypeError):
            # Optional audio must not take the text chat service down.
            return cls()

    def public_config(self):
        return {"enabled": self.enabled, "languages": ["en"] if self.enabled else []}


class KokoroClient:
    def __init__(self, settings: TTSSettings):
        self.settings = settings

    async def synthesize(self, text: str) -> bytes:
        headers = {}
        if self.settings.auth_mode == "google_id_token":
            try:
                from google.auth.transport.requests import Request as GoogleRequest
                from google.oauth2.id_token import fetch_id_token

                google_request = GoogleRequest()

                def bounded_google_request(
                    url, method="GET", body=None, headers=None, timeout=None, **kwargs
                ):
                    request_timeout = min(5, self.settings.timeout_seconds)
                    if timeout is not None:
                        request_timeout = min(timeout, request_timeout)
                    return google_request(
                        url,
                        method=method,
                        body=body,
                        headers=headers,
                        timeout=request_timeout,
                        **kwargs,
                    )

                token = await asyncio.wait_for(
                    asyncio.to_thread(
                        fetch_id_token, bounded_google_request, self.settings.audience
                    ),
                    timeout=min(5, self.settings.timeout_seconds),
                )
                headers["Authorization"] = f"Bearer {token}"
            except asyncio.TimeoutError as exc:
                raise TTSError("tts_timeout", "Speech generation timed out.", 504) from exc
            except Exception as exc:
                raise TTSError("tts_unavailable", "Speech generation is unavailable.", 503) from exc
        try:
            timeout = httpx.Timeout(
                connect=min(5, self.settings.timeout_seconds),
                read=self.settings.timeout_seconds,
                write=min(5, self.settings.timeout_seconds),
                pool=min(5, self.settings.timeout_seconds),
            )
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{self.settings.base_url}/synthesize",
                    json={"text": text, "voice": "af_heart"},
                    headers=headers,
                )
        except httpx.TimeoutException as exc:
            raise TTSError("tts_timeout", "Speech generation timed out.", 504) from exc
        except httpx.HTTPError as exc:
            raise TTSError("tts_unavailable", "Speech generation is unavailable.", 503) from exc
        if response.status_code == 429:
            raise TTSError("tts_busy", "Generated speech is busy.", 429)
        if response.status_code != 200:
            raise TTSError("tts_unavailable", "Speech generation is unavailable.", 503)
        audio = response.content
        if (
            not audio.startswith(b"RIFF")
            or audio[8:12] != b"WAVE"
            or not 44 <= len(audio) <= MAX_AUDIO_BYTES
        ):
            raise TTSError("tts_invalid_audio", "Speech generation returned invalid audio.", 502)
        return audio


class TTSService:
    def __init__(self, settings: TTSSettings, *, provider=None):
        self.settings = settings
        self.provider = provider or KokoroClient(settings)
        self.semaphore = asyncio.Semaphore(settings.max_concurrent)

    def prepare_text(self, markdown: str) -> str:
        if not self.settings.enabled:
            raise TTSError("tts_unavailable", "Generated speech is unavailable.", 503)
        text = readable_text(markdown)
        if not text:
            raise TTSError("tts_empty", "There is no readable answer to speak.", 422)
        if any("\u0900" <= char <= "\u097f" for char in text):
            raise TTSError("tts_language", "Hindi playback is not enabled for this pilot.", 422)
        if len(text) > MAX_TTS_CHARS:
            raise TTSError("tts_too_long", "This answer is too long for generated speech.", 413)
        return text

    async def synthesize(self, markdown: str) -> bytes:
        text = self.prepare_text(markdown)
        try:
            await asyncio.wait_for(self.semaphore.acquire(), timeout=0.001)
        except asyncio.TimeoutError as exc:
            raise TTSError("tts_busy", "Generated speech is busy.", 429) from exc
        try:
            return await asyncio.wait_for(
                self.provider.synthesize(text), timeout=self.settings.timeout_seconds
            )
        except asyncio.TimeoutError as exc:
            raise TTSError("tts_timeout", "Speech generation timed out.", 504) from exc
        finally:
            self.semaphore.release()

    async def stream(self, markdown: str):
        """Return framed WAV chunks from one admitted, bounded playback request."""
        chunks = speech_chunks(self.prepare_text(markdown))
        try:
            await asyncio.wait_for(self.semaphore.acquire(), timeout=0.001)
        except asyncio.TimeoutError as exc:
            raise TTSError("tts_busy", "Generated speech is busy.", 429) from exc

        deadline = asyncio.get_running_loop().time() + self.settings.timeout_seconds
        try:
            first = await asyncio.wait_for(
                self.provider.synthesize(chunks[0]), timeout=self.settings.timeout_seconds
            )
        except asyncio.TimeoutError as exc:
            self.semaphore.release()
            raise TTSError("tts_timeout", "Speech generation timed out.", 504) from exc
        except BaseException:
            self.semaphore.release()
            raise

        async def frames():
            try:
                yield len(first).to_bytes(4, "big") + first
                for chunk in chunks[1:]:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        yield b"\xff\xff\xff\xff"
                        return
                    try:
                        audio = await asyncio.wait_for(
                            self.provider.synthesize(chunk), timeout=remaining
                        )
                    except (TTSError, asyncio.TimeoutError):
                        yield b"\xff\xff\xff\xff"
                        return
                    yield len(audio).to_bytes(4, "big") + audio
                yield b"\x00\x00\x00\x00"
            finally:
                self.semaphore.release()

        return frames()
