"""Speech service configuration and whisper.cpp HTTP adapter."""

import asyncio
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx

from cricket.audio import AudioError, normalize_audio

CRICKET_VOCABULARY_PROMPT = (
    "Virat Kohli, Joe Root, Jasprit Bumrah, Rohit Sharma, Babar Azam, AB de Villiers, ODI, T20I."
)


class SpeechError(AudioError):
    pass


@dataclass(frozen=True)
class SpeechSettings:
    enabled: bool = False
    provider: str = "whisper_cpp"
    base_url: str = "http://127.0.0.1:8081"
    auth_mode: str = "none"
    audience: str | None = None
    max_duration_seconds: int = 60
    max_upload_bytes: int = 8 * 1024 * 1024
    max_body_bytes: int = 10 * 1024 * 1024
    conversion_timeout_seconds: float = 10
    inference_timeout_seconds: float = 45
    credential_timeout_seconds: float = 5
    max_concurrent: int = 1
    languages: tuple[str, ...] = ("en",)

    @classmethod
    def from_env(cls):
        enabled = os.getenv("VOICE_ENABLED", "false").lower() in {"1", "true", "yes"}
        try:
            settings = cls(
                enabled=enabled,
                provider=os.getenv("STT_PROVIDER", "whisper_cpp"),
                base_url=os.getenv("STT_BASE_URL", "http://127.0.0.1:8081").rstrip("/"),
                auth_mode=os.getenv("STT_AUTH_MODE", "none"),
                audience=os.getenv("STT_AUDIENCE") or None,
                max_duration_seconds=int(os.getenv("VOICE_MAX_DURATION_SECONDS", "60")),
                max_upload_bytes=int(os.getenv("VOICE_MAX_UPLOAD_BYTES", str(8 * 1024 * 1024))),
                max_body_bytes=int(os.getenv("VOICE_MAX_BODY_BYTES", str(10 * 1024 * 1024))),
                conversion_timeout_seconds=float(os.getenv("STT_CONVERSION_TIMEOUT_SECONDS", "10")),
                inference_timeout_seconds=float(os.getenv("STT_INFERENCE_TIMEOUT_SECONDS", "45")),
                credential_timeout_seconds=float(os.getenv("STT_CREDENTIAL_TIMEOUT_SECONDS", "5")),
                max_concurrent=int(os.getenv("VOICE_MAX_CONCURRENT", "1")),
                languages=tuple(
                    language.strip().lower()
                    for language in os.getenv("VOICE_LANGUAGES", "en").split(",")
                ),
            )
            settings.validate()
            return settings
        except (TypeError, ValueError):
            # Bad optional voice settings must not take text chat down.
            return cls()

    def validate(self):
        if self.provider != "whisper_cpp" or self.auth_mode not in {"none", "google_id_token"}:
            raise ValueError("Unsupported speech provider or authentication mode")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("Invalid speech service URL")
        if self.auth_mode == "google_id_token" and not self.audience:
            raise ValueError("STT_AUDIENCE is required")
        if (
            self.enabled
            and os.getenv("APP_ENV") in {"pilot", "production"}
            and (self.auth_mode != "google_id_token" or not self.base_url.startswith("https://"))
        ):
            raise ValueError("Cloud speech requires private HTTPS with IAM authentication")
        if self.max_duration_seconds < 1 or self.max_duration_seconds > 300:
            raise ValueError("Invalid voice duration limit")
        if self.max_upload_bytes < 1 or self.max_body_bytes < self.max_upload_bytes:
            raise ValueError("Invalid voice byte limits")
        if self.max_body_bytes > 32 * 1024 * 1024:
            raise ValueError("Invalid voice body limit")
        if (
            min(
                self.conversion_timeout_seconds,
                self.inference_timeout_seconds,
                self.credential_timeout_seconds,
            )
            <= 0
        ):
            raise ValueError("Invalid speech timeout")
        if self.max_concurrent < 1 or self.max_concurrent > 32:
            raise ValueError("Invalid voice concurrency")
        if (
            not self.languages
            or len(self.languages) != len(set(self.languages))
            or any(language not in {"en", "hi", "auto"} for language in self.languages)
        ):
            raise ValueError("Invalid voice languages")

    def public_config(self):
        return {
            "enabled": self.enabled,
            "max_duration_seconds": self.max_duration_seconds,
            "max_upload_bytes": self.max_upload_bytes,
            "languages": list(self.languages),
        }


class WhisperCppProvider:
    def __init__(self, settings: SpeechSettings):
        self.settings = settings

    async def transcribe(self, wav: bytes, language: str) -> str:
        headers = {}
        if self.settings.auth_mode == "google_id_token":
            try:
                from google.auth.transport.requests import Request as GoogleRequest
                from google.oauth2.id_token import fetch_id_token

                google_request = GoogleRequest()

                def bounded_google_request(
                    url, method="GET", body=None, headers=None, timeout=None, **kwargs
                ):
                    request_timeout = self.settings.credential_timeout_seconds
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
                    timeout=self.settings.credential_timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                raise SpeechError("transcription_timeout", "Transcription timed out.", 504) from exc
            except Exception as exc:
                raise SpeechError(
                    "voice_unavailable", "Speech service is unavailable.", 503
                ) from exc
            headers["Authorization"] = f"Bearer {token}"
        try:
            timeout = httpx.Timeout(
                connect=min(5, self.settings.inference_timeout_seconds),
                read=self.settings.inference_timeout_seconds,
                write=min(10, self.settings.inference_timeout_seconds),
                pool=min(5, self.settings.inference_timeout_seconds),
            )
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{self.settings.base_url}/inference",
                    files={"file": ("recording.wav", wav, "audio/wav")},
                    data={
                        "language": language,
                        "response_format": "json",
                        **({"prompt": CRICKET_VOCABULARY_PROMPT} if language == "en" else {}),
                    },
                    headers=headers,
                )
        except httpx.TimeoutException as exc:
            raise SpeechError("transcription_timeout", "Transcription timed out.", 504) from exc
        except httpx.HTTPError as exc:
            raise SpeechError("voice_unavailable", "Speech service is unavailable.", 503) from exc
        if response.status_code == 429:
            raise SpeechError("voice_busy", "Speech service is busy. Try again shortly.", 429)
        if response.status_code >= 500:
            raise SpeechError("voice_unavailable", "Speech service is unavailable.", 503)
        if response.status_code != 200:
            raise SpeechError("transcription_failed", "Transcription failed.", 502)
        try:
            payload = response.json()
            text = payload["text"]
        except (ValueError, KeyError, TypeError) as exc:
            raise SpeechError(
                "transcription_failed", "Invalid transcription response.", 502
            ) from exc
        if not isinstance(text, str):
            raise SpeechError("transcription_failed", "Invalid transcription response.", 502)
        return text


class SpeechService:
    def __init__(self, settings: SpeechSettings, *, converter=None, provider=None):
        self.settings = settings
        self.converter = converter or normalize_audio
        self.provider = provider or WhisperCppProvider(settings)
        self.semaphore = asyncio.Semaphore(settings.max_concurrent)

    @asynccontextmanager
    async def admit(self):
        """Reserve local voice capacity before accepting an uploaded body."""
        if not self.settings.enabled:
            raise SpeechError("voice_unavailable", "Voice input is unavailable.", 503)
        try:
            await asyncio.wait_for(self.semaphore.acquire(), timeout=0.001)
        except asyncio.TimeoutError as exc:
            raise SpeechError("voice_busy", "Voice input is busy. Try again shortly.", 429) from exc
        try:
            yield
        finally:
            self.semaphore.release()

    async def transcribe(self, data: bytes, mime_type: str, language: str):
        async with self.admit():
            return await self.transcribe_admitted(data, mime_type, language)

    async def transcribe_admitted(self, data: bytes, mime_type: str, language: str):
        """Run conversion and inference while the caller holds an admission slot."""
        if not self.settings.enabled:
            raise SpeechError("voice_unavailable", "Voice input is unavailable.", 503)
        wav, duration = await self.converter(
            data,
            mime_type,
            max_duration_seconds=self.settings.max_duration_seconds,
            conversion_timeout_seconds=self.settings.conversion_timeout_seconds,
        )
        text = await self.provider.transcribe(wav, language)
        if not text.strip() or text.strip().casefold() in {"[blank_audio]", "[blank_audio]."}:
            raise SpeechError("no_speech", "No speech was detected.", 422)
        try:
            text.encode("utf-8")
        except UnicodeError as exc:
            raise SpeechError(
                "transcription_failed", "Invalid transcription response.", 502
            ) from exc
        if len(text) > 8000 or any(
            ord(character) < 32 and character not in "\n\r\t" for character in text
        ):
            raise SpeechError("transcription_failed", "Invalid transcription response.", 502)
        return text.strip(), duration
