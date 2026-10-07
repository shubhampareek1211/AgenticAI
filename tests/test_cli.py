import json

from cricket.cli import main


def test_cli_configuration_error_is_actionable_and_has_no_traceback(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert main(["status"]) == 1
    output = capsys.readouterr()
    assert "Set DATABASE_URL" in json.loads(output.err)["error"]
    assert "Traceback" not in output.err


def test_cli_invalid_profile_error_does_not_echo_sensitive_configuration(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql+unsupported://user:secret@host/db")
    assert main(["player", "--espn-id", "253802"]) == 1
    output = capsys.readouterr()
    assert "secret" not in output.err and "Traceback" not in output.err


def test_download_does_not_require_a_database(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr("cricket.cli.download", lambda *args: [{"checksum": "fixture"}])
    assert main(["download", "--source-dir", str(tmp_path), "--formats", "odi"]) == 0
    assert json.loads(capsys.readouterr().out)["artifacts"][0]["checksum"] == "fixture"
