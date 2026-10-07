"""Generated answer playback stays bounded and independent of Gemini."""

import asyncio
import io
import wave

import pytest
from fastapi.testclient import TestClient

from app import create_app
from cricket.tts import (
    MAX_TTS_CHARS,
    TTSError,
    TTSService,
    TTSSettings,
    readable_text,
    speech_chunks,
)


def wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\0\0" * 100)
    return output.getvalue()


class FakeKokoro:
    def __init__(self):
        self.calls = []

    async def synthesize(self, text):
        self.calls.append(text)
        return wav()


def test_readable_answer_and_english_pilot_limits():
    provider = FakeKokoro()
    service = TTSService(TTSSettings(enabled=True), provider=provider)
    text = asyncio.run(
        service.synthesize(
            "**Virat Kohli** scored `82` runs. [Source](https://x.test)\n```py\nprint(1)\n```"
        )
    )
    assert text == wav()
    assert provider.calls == ["Virat Kohli scored 82 runs. Source Code example omitted."]

    for answer, code in [
        ("विराट कोहली के 82 रन", "tts_language"),
        ("x" * (MAX_TTS_CHARS + 1), "tts_too_long"),
        ("```python\npass\n```", None),
    ]:
        if code is None:
            assert readable_text(answer) == "Code example omitted."
            continue
        with pytest.raises(TTSError) as exc:
            asyncio.run(service.synthesize(answer))
        assert exc.value.code == code
    assert len(provider.calls) == 1


def test_long_answer_streams_first_audio_before_synthesizing_the_rest():
    provider = FakeKokoro()
    service = TTSService(TTSSettings(enabled=True), provider=provider)
    answer = "Kohli scored 82 runs in an ODI. " * 20
    expected = speech_chunks(answer.strip())
    assert len(expected) > 1

    async def run():
        frames = await service.stream(answer)
        first = await anext(frames)
        assert first[4:8] == b"RIFF"
        assert len(provider.calls) == 1
        rest = [frame async for frame in frames]
        assert rest[-1] == b"\0\0\0\0"

    asyncio.run(run())
    assert provider.calls == expected


def test_first_audio_piece_is_shorter_without_losing_answer_words():
    answer = "Virat Kohli scored eighty two runs in the selected match. " * 15
    chunks = speech_chunks(answer.strip())
    assert len(chunks[0]) <= 100
    assert all(len(chunk) <= 220 for chunk in chunks)
    assert any(len(chunk) > 100 for chunk in chunks[1:])
    assert " ".join(chunks) == answer.strip()
    assert speech_chunks("A short answer.") == ["A short answer."]


def test_disabled_tts_does_not_require_a_model_or_database():
    class NoDatabase:
        def connect(self):
            raise AssertionError("Disabled TTS must not query conversations")

    app = create_app(engine=NoDatabase(), harness=object())
    with TestClient(app) as client:
        assert client.get("/tts/config").json() == {"enabled": False, "languages": []}
        response = client.post(
            "/sessions/11111111-1111-4111-8111-111111111111/messages/22222222-2222-4222-8222-222222222222/speech"
        )
        assert response.status_code == 503


def test_cloud_tts_cannot_be_enabled_without_private_https_iam(monkeypatch):
    monkeypatch.setenv("APP_ENV", "pilot")
    monkeypatch.setenv("TTS_ENABLED", "true")
    monkeypatch.setenv("TTS_BASE_URL", "http://worker.example")
    monkeypatch.setenv("TTS_AUTH_MODE", "none")
    assert not TTSSettings.from_env().enabled
    monkeypatch.setenv("TTS_BASE_URL", "https://worker.example")
    monkeypatch.setenv("TTS_AUTH_MODE", "google_id_token")
    monkeypatch.setenv("TTS_AUDIENCE", "https://worker.example")
    assert TTSSettings.from_env().enabled


def test_local_worker_rejects_unsupported_voice_and_emits_pcm_wav():
    import importlib.util
    from pathlib import Path

    np = pytest.importorskip("numpy", reason="The isolated Kokoro worker installs NumPy")

    path = Path(__file__).resolve().parents[1] / "deploy/kokoro/server.py"
    spec = importlib.util.spec_from_file_location("kokoro_pilot_server", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    class Pipeline:
        def __call__(self, text, voice):
            assert text == "Kohli scored 82 runs."
            assert voice == "af_heart"
            yield text, "phonemes", np.zeros(2400, dtype=np.float32)

    with TestClient(module.create_app(Pipeline())) as client:
        assert client.get("/health").json() == {"status": "ok"}
        invalid = client.post("/synthesize", json={"text": "hello", "voice": "hf_alpha"})
        assert invalid.status_code == 422
        response = client.post("/synthesize", json={"text": "Kohli scored 82 runs."})
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/wav"
        with wave.open(io.BytesIO(response.content)) as audio:
            assert audio.getframerate() == 24000
            assert audio.getnframes() == 2400
