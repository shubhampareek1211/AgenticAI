"""Provider-domain errors remain useful without exposing transport exceptions."""

import requests

from cricket.harness import execute_tool
from tools import run_tool


def test_missing_espn_player_keeps_domain_error_in_harness(monkeypatch):
    monkeypatch.setattr("cricket.espn_live._fetch", lambda *_: {"athlete": {"id": "42"}})
    result = execute_tool("get_espn_player_profile", {"player_id": "253802"}, run_tool)
    assert not result["ok"]
    assert result["error"]["code"] == "player_not_found"


def test_espn_transport_error_is_sanitized_even_when_tool_called_directly(monkeypatch):
    def fail(*args, **kwargs):
        raise requests.ConnectionError("password=do-not-expose")

    monkeypatch.setattr(requests, "get", fail)
    result = run_tool("find_espn_player", {"query": "Virat Kohli"})
    assert result["error"]["code"] == "provider_unavailable"
    assert "do-not-expose" not in str(result)
