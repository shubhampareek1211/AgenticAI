"""Provider-domain errors remain useful without exposing transport exceptions."""

import json

import requests

from cricket.harness import execute_tool
from tools import run_tool


def test_missing_city_keeps_its_domain_error_in_harness(monkeypatch):
    class EmptyPlaces:
        def json(self):
            return {"results": []}

    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: EmptyPlaces())
    result = execute_tool("get_weather", {"location": "MissingCity"}, run_tool)
    assert not result["ok"]
    assert result["error"]["code"] == "city_not_found"
    assert "MissingCity" in result["error"]["message"]
    assert "Try another city" in result["error"]["message"]


def test_weather_transport_error_is_sanitized_even_when_tool_called_directly(monkeypatch):
    def fail(*args, **kwargs):
        raise requests.ConnectionError("password=do-not-expose")

    monkeypatch.setattr(requests, "get", fail)
    result = run_tool("get_weather", {"location": "Delhi"})
    assert "do-not-expose" not in result
    assert json.loads(result)["error"]["code"] == "provider_unavailable"
