"""The public deployment skips login without adopting Firebase-owned history."""

import pytest

from cricket.auth import AuthSettings, FirebaseAuthorizer
from cricket.db import ConfigurationError


def test_public_cloud_run_has_separate_owner_and_no_login_config(monkeypatch):
    monkeypatch.setenv("APP_ENV", "public")
    monkeypatch.setenv("K_SERVICE", "agenticai-public")
    settings = AuthSettings.from_env()
    assert settings.public_config() == {"enabled": False, "firebaseConfig": None}
    authorizer = FirebaseAuthorizer(settings)
    assert authorizer.authorize(None) == "public:agenticai-public"
    assert authorizer.authorize("Bearer unrelated") == "public:agenticai-public"


@pytest.mark.parametrize("service", ["agenticai-git", "agenticai-data-pilot", ""])
def test_public_mode_cannot_disable_login_on_other_services(monkeypatch, service):
    monkeypatch.setenv("APP_ENV", "public")
    monkeypatch.setenv("K_SERVICE", service)
    with pytest.raises(ConfigurationError):
        AuthSettings.from_env()
