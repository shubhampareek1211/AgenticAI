"""The voice input API is independent from conversation storage and Gemini."""

import asyncio
import io
import wave

from fastapi.testclient import TestClient

from app import create_app
from cricket.audio import AudioError, is_effectively_silent, normalize_audio, wav_duration
from cricket.speech import SpeechError, SpeechService, SpeechSettings, WhisperCppProvider


def wav(seconds=1):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\0\0" * int(seconds * 16000))
    return stream.getvalue()


class FakeProvider:
    def __init__(self, text="Show Virat Kohli's ODI runs by year."):
        self.text = text
        self.calls = []

    async def transcribe(self, audio, language):
        self.calls.append((audio, language))
        return self.text


class SpyHarness:
    def __init__(self):
        self.calls = 0

    def run(self, *args):
        self.calls += 1
        raise AssertionError("Transcription must not call the chat harness")


class SpyEngine:
    def __init__(self):
        self.calls = 0

    def connect(self, *args):
        self.calls += 1
        raise AssertionError("Transcription must not access the database")


def client_for(
    *, enabled=True, provider=None, converter=None, max_upload_bytes=1024, languages=("en",)
):
    settings = SpeechSettings(
        enabled=enabled,
        max_upload_bytes=max_upload_bytes,
        max_body_bytes=max_upload_bytes + 1024,
        languages=languages,
    )
    provider = provider or FakeProvider()

    async def valid_converter(data, mime_type, **kwargs):
        assert mime_type == "audio/webm"
        assert data == b"recording"
        return wav(), 1.0

    service = SpeechService(settings, converter=converter or valid_converter, provider=provider)
    harness, engine = SpyHarness(), SpyEngine()
    return (
        TestClient(create_app(engine, harness, speech_service=service)),
        provider,
        harness,
        engine,
    )


def test_config_and_success_have_no_chat_side_effects():
    client, provider, harness, engine = client_for()
    with client:
        assert client.get("/voice/config").json() == {
            "enabled": True,
            "max_duration_seconds": 60,
            "max_upload_bytes": 1024,
            "languages": ["en"],
        }
        response = client.post(
            "/transcribe",
            files={"file": ("clip.webm", b"recording", "audio/webm")},
            data={"language": "en"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["text"] == "Show Virat Kohli's ODI runs by year."
        assert payload["language"] == "en"
        assert payload["duration_seconds"] == 1.0
        assert payload["request_id"]
        assert provider.calls == [(wav(), "en")]
        assert harness.calls == engine.calls == 0


def test_disabled_voice_and_chat_validation_are_separate():
    client, _, _, _ = client_for(enabled=False)
    with client:
        assert client.get("/voice/config").json()["enabled"] is False
        response = client.post(
            "/transcribe", files={"file": ("clip.webm", b"recording", "audio/webm")}
        )
        assert response.status_code == 503
        assert response.json()["code"] == "voice_unavailable"
        chat = client.post("/chat", json={"message": ""})
        assert chat.status_code == 422
        assert "nonempty message" in chat.json()["detail"]
        assert "code" not in chat.json()


def test_upload_and_language_rejections_preserve_contract():
    client, provider, _, _ = client_for(max_upload_bytes=12)
    with client:
        invalid = client.post(
            "/transcribe",
            files={"file": ("clip.webm", b"recording", "audio/webm")},
            data={"language": "fr"},
        )
        assert invalid.status_code == 422 and invalid.json()["code"] == "invalid_language"
        oversized = client.post(
            "/transcribe",
            files={"file": ("clip.webm", b"x" * 2000, "audio/webm")},
        )
        assert oversized.status_code == 413
        assert oversized.json()["code"] == "audio_too_large"
        missing = client.post("/transcribe", data={"language": "en"})
        assert missing.status_code == 422
        assert missing.json()["code"] == "invalid_audio"
        assert not provider.calls


def test_configured_hindi_and_auto_language_reach_worker_without_chat_side_effects():
    client, provider, harness, engine = client_for(
        provider=FakeProvider("विराट कोहली के रन दिखाओ"),
        languages=("en", "hi", "auto"),
    )
    with client:
        assert client.get("/voice/config").json()["languages"] == ["en", "hi", "auto"]
        for language in ("hi", "auto"):
            response = client.post(
                "/transcribe",
                files={"file": ("clip.webm", b"recording", "audio/webm")},
                data={"language": language},
            )
            assert response.status_code == 200
            assert response.json()["language"] == language
            assert response.json()["text"] == "विराट कोहली के रन दिखाओ"
        assert [language for _, language in provider.calls] == ["hi", "auto"]
        assert harness.calls == engine.calls == 0


def test_language_configuration_is_explicit(monkeypatch):
    monkeypatch.setenv("VOICE_ENABLED", "true")
    monkeypatch.setenv("VOICE_LANGUAGES", "en,hi,auto")
    assert SpeechSettings.from_env().languages == ("en", "hi", "auto")
    monkeypatch.setenv("VOICE_LANGUAGES", "en,fr")
    assert SpeechSettings.from_env().enabled is False


def test_pilot_speech_requires_https_and_iam_but_local_proxy_is_allowed(monkeypatch):
    monkeypatch.setenv("VOICE_ENABLED", "true")
    monkeypatch.setenv("STT_BASE_URL", "http://127.0.0.1:8084")
    monkeypatch.setenv("STT_AUTH_MODE", "none")
    for mode in ("pilot", "production"):
        monkeypatch.setenv("APP_ENV", mode)
        assert not SpeechSettings.from_env().enabled
    monkeypatch.setenv("STT_BASE_URL", "https://worker.example")
    assert not SpeechSettings.from_env().enabled
    monkeypatch.setenv("STT_AUTH_MODE", "google_id_token")
    monkeypatch.setenv("STT_AUDIENCE", "https://worker.example")
    assert SpeechSettings.from_env().enabled
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("STT_BASE_URL", "http://127.0.0.1:8084")
    monkeypatch.setenv("STT_AUTH_MODE", "none")
    assert SpeechSettings.from_env().enabled


def test_worker_overload_stays_retryable_at_the_app_boundary(monkeypatch):
    import httpx

    from cricket import speech

    original = httpx.AsyncClient
    monkeypatch.setattr(
        speech.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(lambda _: httpx.Response(429))),
    )
    provider = WhisperCppProvider(SpeechSettings(enabled=True))
    client, _, _, _ = client_for(provider=provider)
    with client:
        response = client.post(
            "/transcribe", files={"file": ("clip.webm", b"recording", "audio/webm")}
        )
    assert response.status_code == 429
    assert response.json()["code"] == "voice_busy"
    assert response.headers["retry-after"] == "1"


def test_no_speech_and_bad_converter_output():
    provider = FakeProvider("  ")
    client, _, _, _ = client_for(provider=provider)
    with client:
        response = client.post(
            "/transcribe", files={"file": ("clip.webm", b"recording", "audio/webm")}
        )
        assert response.status_code == 422 and response.json()["code"] == "no_speech"

    async def invalid_converter(*args, **kwargs):
        raise AudioError("invalid_audio", "Audio could not be decoded.", 422)

    client, provider, _, _ = client_for(converter=invalid_converter)
    with client:
        response = client.post("/transcribe", files={"file": ("clip.webm", b"bad", "audio/webm")})
        assert response.status_code == 422 and response.json()["code"] == "invalid_audio"
        assert not provider.calls


def test_wav_duration_checks_normalized_layout():
    assert wav_duration(wav(2)) == 2.0
    assert is_effectively_silent(wav(2))
    try:
        wav_duration(b"not wave")
    except AudioError as exc:
        assert exc.code == "invalid_audio"
    else:
        raise AssertionError("Invalid WAV was accepted")


def test_whisper_blank_audio_marker_is_not_shown_as_a_transcript():
    client, _, _, _ = client_for(provider=FakeProvider(" [BLANK_AUDIO]\n"))
    with client:
        response = client.post(
            "/transcribe", files={"file": ("clip.webm", b"recording", "audio/webm")}
        )
        assert response.status_code == 422
        assert response.json()["code"] == "no_speech"


def test_rejects_unsupported_mime_without_starting_ffmpeg():
    try:
        asyncio.run(
            normalize_audio(
                b"not audio",
                "text/plain",
                max_duration_seconds=60,
                conversion_timeout_seconds=10,
            )
        )
    except AudioError as exc:
        assert exc.code == "unsupported_audio"
        assert exc.status == 415
    else:
        raise AssertionError("Unsupported format was accepted")


def test_streamed_body_limit_is_counted_without_content_length():
    client, provider, _, _ = client_for(max_upload_bytes=12)

    def chunks():
        yield b"x" * 600
        yield b"y" * 600

    with client:
        response = client.post(
            "/transcribe",
            content=chunks(),
            headers={"Content-Type": "multipart/form-data; boundary=some-boundary"},
        )
        assert response.status_code == 413
        assert response.json()["code"] == "audio_too_large"
        assert not provider.calls


def test_provider_output_length_rejected():
    client, _, _, _ = client_for(provider=FakeProvider("A" * 8001))
    with client:
        response = client.post(
            "/transcribe", files={"file": ("clip.webm", b"recording", "audio/webm")}
        )
        assert response.status_code == 502
        assert response.json()["code"] == "transcription_failed"


def test_admission_rejects_concurrent_inference():
    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()

        class SlowProvider:
            async def transcribe(self, audio, language):
                entered.set()
                await release.wait()
                return "Runs"

        async def converter(*args, **kwargs):
            return wav(), 1.0

        service = SpeechService(
            SpeechSettings(enabled=True), converter=converter, provider=SlowProvider()
        )
        first = asyncio.create_task(service.transcribe(b"clip", "audio/webm", "en"))
        await entered.wait()
        try:
            await service.transcribe(b"clip", "audio/webm", "en")
        except SpeechError as exc:
            assert exc.code == "voice_busy" and exc.status == 429
        else:
            raise AssertionError("Concurrent inference was admitted")
        release.set()
        assert await first == ("Runs", 1.0)

    asyncio.run(scenario())


def test_whisper_adapter_sends_wav_and_parses_json(monkeypatch):
    import httpx

    from cricket import speech

    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"text": "Bumrah wickets"})

    client_type = httpx.AsyncClient
    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(
        speech.httpx, "AsyncClient", lambda **kwargs: client_type(transport=transport)
    )
    provider = WhisperCppProvider(SpeechSettings(enabled=True))
    assert asyncio.run(provider.transcribe(wav(), "en")) == "Bumrah wickets"
    assert len(requests) == 1
    assert requests[0].url.path == "/inference"
    assert b'response_format"\r\n\r\njson' in requests[0].content
    assert b'language"\r\n\r\nen' in requests[0].content
    assert b'prompt"\r\n\r\nVirat Kohli, Joe Root' in requests[0].content
    assert asyncio.run(provider.transcribe(wav(), "hi")) == "Bumrah wickets"
    assert b'language"\r\n\r\nhi' in requests[1].content
    assert b'prompt"\r\n' not in requests[1].content


def test_early_admission_holds_slot_and_releases_it_after_cancel():
    async def scenario():
        service = SpeechService(SpeechSettings(enabled=True))
        entered = asyncio.Event()

        async def occupy():
            async with service.admit():
                entered.set()
                await asyncio.Event().wait()

        holder = asyncio.create_task(occupy())
        await entered.wait()
        try:
            async with service.admit():
                raise AssertionError("Busy service admitted another upload")
        except SpeechError as exc:
            assert exc.code == "voice_busy" and exc.status == 429
        holder.cancel()
        try:
            await holder
        except asyncio.CancelledError:
            pass
        async with service.admit():
            pass

    asyncio.run(scenario())


def test_google_token_request_has_bounded_network_and_wall_clock_timeout(monkeypatch):
    import time

    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    seen = []

    def fake_request(self, url, **kwargs):
        seen.append(kwargs["timeout"])

    def fake_fetch(request, audience):
        assert audience == "https://worker.example"
        request("http://metadata", timeout=120)
        time.sleep(0.05)
        return "token"

    monkeypatch.setattr(google_requests.Request, "__call__", fake_request)
    monkeypatch.setattr(id_token, "fetch_id_token", fake_fetch)
    settings = SpeechSettings(
        enabled=True,
        auth_mode="google_id_token",
        audience="https://worker.example",
        credential_timeout_seconds=0.01,
    )
    provider = WhisperCppProvider(settings)
    try:
        asyncio.run(provider.transcribe(wav(), "en"))
    except SpeechError as exc:
        assert exc.code == "transcription_timeout" and exc.status == 504
    else:
        raise AssertionError("Slow ID-token fetch was accepted")
    assert seen == [0.01]


def test_httpx_phase_timeouts_are_bounded(monkeypatch):
    import httpx

    from cricket import speech

    timeouts = []
    original = httpx.AsyncClient

    def client_with_transport(**kwargs):
        timeouts.append(kwargs["timeout"])
        return original(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"text": "Runs"}))
        )

    monkeypatch.setattr(speech.httpx, "AsyncClient", client_with_transport)
    settings = SpeechSettings(enabled=True, inference_timeout_seconds=45)
    assert asyncio.run(WhisperCppProvider(settings).transcribe(wav(), "en")) == "Runs"
    assert timeouts[0].connect == 5
    assert timeouts[0].write == 10
    assert timeouts[0].read == 45
