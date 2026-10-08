"""Focused checks for the pilot data bootstrap's isolation safeguards."""

import importlib.util
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import make_url

SCRIPT = Path(__file__).with_name("bootstrap-pilot-db.py")
SPEC = importlib.util.spec_from_file_location("bootstrap_pilot_db", SCRIPT)
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


def test_cloud_secret_url_is_bound_to_private_sql_socket():
    value = bootstrap.sql_url("sample_secret", cloud_run=True)
    parsed = make_url(value)
    assert parsed.username == "cricket_app"
    assert parsed.database == "cricket_pilot"
    assert parsed.host is None
    assert parsed.query["host"] == (
        "/cloudsql/phonic-weaver-475017-n1:us-central1:agenticai-pilot-pg16"
    )
    assert bootstrap.verify_secret_url(value) == "sample_secret"
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.verify_secret_url(bootstrap.sql_url("sample_secret", cloud_run=False))


def test_source_manifest_rejects_non_cricsheet_provenance(tmp_path, monkeypatch):
    for name in bootstrap.SOURCE_NAMES:
        (tmp_path / name).write_bytes(b"source")
    monkeypatch.setattr(
        bootstrap,
        "artifact_metadata",
        lambda path: {"source_url": bootstrap.SOURCE_URLS[path.name], "checksum": "abc"},
    )
    assert len(bootstrap.source_manifest(tmp_path)) == 4
    monkeypatch.setattr(
        bootstrap,
        "artifact_metadata",
        lambda path: {"source_url": "file:///local/conversations", "checksum": "abc"},
    )
    with pytest.raises(bootstrap.BootstrapError, match="Unexpected source URL"):
        bootstrap.source_manifest(tmp_path)


def test_import_uses_public_archives_and_preserves_private_rows(tmp_path, monkeypatch):
    (tmp_path / "a").write_text("stub")
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    (tmp_path / ".venv/bin").mkdir(parents=True)
    (tmp_path / ".venv/bin/python").write_text("stub")
    seen = []
    monkeypatch.setattr(bootstrap, "command", lambda args, **kwargs: seen.append(args))
    monkeypatch.setattr(bootstrap, "import_command", lambda args, env: seen.append(args))
    before = {name: 0 for name in bootstrap.PRIVATE_TABLES + bootstrap.CRICKET_TABLES}
    after = dict(before, players=1, matches=1, deliveries=1)
    values = iter((before, after))
    monkeypatch.setattr(bootstrap, "table_counts", lambda password: next(values))
    assert bootstrap.migrate_and_import("secret", tmp_path)["matches"] == 1
    assert seen[0][2:] == ["alembic", "upgrade", "head"]
    assert seen[1][2:] == [
        "cricket.cli",
        "import",
        "--source-dir",
        str(tmp_path),
        "--formats",
        "odi",
        "t20i",
    ]

    values = iter((before, dict(after, conversations=1)))
    monkeypatch.setattr(bootstrap, "table_counts", lambda password: next(values))
    assert bootstrap.migrate_and_import("secret", tmp_path)["conversations"] == 1

    existing = dict(before, conversations=2)
    values = iter((existing, dict(after, conversations=1)))
    monkeypatch.setattr(bootstrap, "table_counts", lambda password: next(values))
    with pytest.raises(
        bootstrap.BootstrapError, match="Private application table counts decreased"
    ):
        bootstrap.migrate_and_import("secret", tmp_path)


def test_apply_reuses_existing_secret_without_rotating_app_password(monkeypatch):
    monkeypatch.setattr(
        bootstrap, "secret_url", lambda token: bootstrap.sql_url("old", cloud_run=True)
    )
    monkeypatch.setattr(bootstrap, "api", lambda *args, **kwargs: {"name": "operation"})
    monkeypatch.setattr(bootstrap, "wait_operation", lambda *args: None)
    monkeypatch.setattr(
        bootstrap.socket, "create_connection", lambda *args, **kwargs: nullcontext()
    )
    observed = []
    monkeypatch.setattr(
        bootstrap,
        "create_role_and_database",
        lambda *args, **kwargs: observed.append((args, kwargs)),
    )
    monkeypatch.setattr(
        bootstrap, "verify_role_and_database", lambda password: observed.append(password)
    )
    monkeypatch.setattr(bootstrap, "migrate_and_import", lambda password, source: {})
    bootstrap.apply(SimpleNamespace(existing_proxy=True, source_dir=Path("/tmp")), "token")
    assert observed[0][0][1] == "old"
    assert observed[0][1]["rotate_role_password"] is False
    assert observed[1] == "old"
