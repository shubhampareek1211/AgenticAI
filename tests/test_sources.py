import pytest
import requests

from cricket.db import ConfigurationError, database_url
from cricket.espn import PlayerResolutionError, fetch_profile, profile_identity
from cricket.sources import SourceError, artifact_metadata, download


@pytest.mark.parametrize("value", ["sqlite:///test.db", "not-a-url", "postgresql+asyncpg://a/b"])
def test_database_configuration_rejects_invalid_urls(value):
    with pytest.raises(ConfigurationError):
        database_url(value)


def test_database_configuration_requires_explicit_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ConfigurationError, match="Set DATABASE_URL"):
        database_url()


def test_database_configuration_normalizes_psycopg_driver():
    assert database_url("postgresql://a@localhost/test") == "postgresql+psycopg://a@localhost/test"


@pytest.mark.parametrize("profile", [{}, [], {"athlete": None}, {"athlete": {"id": "bad"}}])
def test_profile_requires_stable_identity(profile):
    with pytest.raises(PlayerResolutionError):
        profile_identity(profile)


@pytest.mark.parametrize(
    "error",
    [
        requests.Timeout(),
        requests.HTTPError("403"),
        requests.HTTPError("429"),
        ValueError("malformed JSON"),
    ],
)
def test_profile_provider_failures_are_actionable(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr("cricket.espn.requests.get", fail)
    with pytest.raises(PlayerResolutionError, match="unavailable"):
        fetch_profile("253802")


def test_unexpected_provider_identity_is_rejected(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"athlete": {"id": "34102"}}

    monkeypatch.setattr("cricket.espn.requests.get", lambda *args, **kwargs: Response())
    with pytest.raises(PlayerResolutionError, match="unexpected"):
        fetch_profile("253802")


def test_download_is_cached_and_checksum_verified(monkeypatch, tmp_path):
    class Response:
        def __init__(self):
            self.headers = {"ETag": "fixture-revision"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            pass

        def iter_content(self, _):
            yield b"fixture-content"

    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return Response()

    monkeypatch.setattr("cricket.sources.requests.get", get)
    first = download(tmp_path, ["odi"])
    assert len(first) == len(calls) == 3
    assert all(
        row["downloaded_at"] and row["source_revision"] == "fixture-revision" for row in first
    )
    assert download(tmp_path, ["odi"]) == first and len(calls) == 3
    (tmp_path / "people.csv").write_text("changed")
    with pytest.raises(SourceError, match="Checksum mismatch"):
        artifact_metadata(tmp_path / "people.csv")


def test_failed_refresh_preserves_previous_download(monkeypatch, tmp_path):
    path = tmp_path / "people.csv"
    path.write_text("known-good-data")

    def fail(*args, **kwargs):
        raise requests.Timeout("upstream details should not be exposed")

    monkeypatch.setattr("cricket.sources.requests.get", fail)
    with pytest.raises(SourceError, match="Could not download"):
        download(tmp_path, ["odi"], refresh=True)
    assert path.read_text() == "known-good-data"
    assert not path.with_name("people.csv.part").exists()
