"""The local authenticated preview must keep private worker authentication intact."""

import json
import runpy
from pathlib import Path

import pytest

speech_environment = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts/pilot-auth-preview.py")
)["speech_environment"]


def config_file(tmp_path, **changes):
    credentials = tmp_path / "adc.json"
    credentials.write_text(json.dumps({"type": "impersonated_service_account"}))
    config = {
        "stt_url": "https://whisper.example.test",
        "tts_url": "https://kokoro.example.test",
        "credentials_file": str(credentials),
        **changes,
    }
    path = tmp_path / "speech.json"
    path.write_text(json.dumps(config))
    return path


def test_cloud_preview_binds_iam_audiences_to_the_private_services(tmp_path):
    env = speech_environment(config_file(tmp_path))
    assert env["VOICE_ENABLED"] == env["TTS_ENABLED"] == "true"
    assert env["VOICE_LANGUAGES"] == "en,hi,auto"
    for prefix in ("STT", "TTS"):
        assert env[f"{prefix}_AUTH_MODE"] == "google_id_token"
        assert env[f"{prefix}_BASE_URL"] == env[f"{prefix}_AUDIENCE"]


@pytest.mark.parametrize(
    "url",
    ["http://localhost:8084", "https://user:secret@example.test", "https://example.test/path"],
)
def test_cloud_preview_rejects_proxy_or_credential_bearing_urls(tmp_path, url):
    with pytest.raises(ValueError, match="HTTPS service origin"):
        speech_environment(config_file(tmp_path, stt_url=url))


def test_preview_rejects_a_service_account_private_key(tmp_path):
    path = config_file(tmp_path)
    (tmp_path / "adc.json").write_text(json.dumps({"type": "service_account"}))
    with pytest.raises(ValueError, match="not a key"):
        speech_environment(path)
