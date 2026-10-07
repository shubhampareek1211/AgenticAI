"""The public shell has no DB and relays user tokens only to its private backend."""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import create_app as create_backend
from cricket.auth import AuthenticationError, AuthSettings
from cricket.db import ConfigurationError
from cricket.gateway import GatewaySettings
from cricket.gateway import create_app as create_gateway

SETTINGS = GatewaySettings(
    backend_url="https://private.example.run.app",
    audience="https://private.example.run.app",
    firebase_project_id="pilot",
    firebase_web_api_key="public-key",
    firebase_auth_domain="pilot.firebaseapp.com",
    firebase_web_app_id="web-id",
)


def test_gateway_serves_login_and_relays_only_allowlisted_authenticated_calls(tmp_path):
    (tmp_path / "index.html").write_text("<html>pilot</html>")
    calls = []

    def backend(request: httpx.Request):
        calls.append(request)
        return httpx.Response(
            201,
            json={"session_id": "server-owned"},
            headers={"Cache-Control": "no-store", "X-Backend-Secret": "do-not-relay"},
        )

    app = create_gateway(
        SETTINGS,
        frontend_dist=tmp_path,
        token_provider=lambda audience: "iam-token",
        transport=httpx.MockTransport(backend),
    )
    with TestClient(app) as client:
        assert "pilot" in client.get("/").text
        assert "pilot" in client.get("/auth/finish").text
        assert client.get("/auth/config").json()["firebaseConfig"]["projectId"] == "pilot"
        assert client.post("/sessions", json={}).status_code == 401
        assert (
            client.post("/sessions", json={}, headers={"Authorization": "Bearer "}).status_code
            == 401
        )
        assert (
            client.get("/unknown", headers={"Authorization": "Bearer firebase-token"}).status_code
            == 404
        )
        response = client.post(
            "/sessions",
            data="{}",
            headers={
                "Authorization": "Bearer firebase-token",
                "X-Firebase-Authorization": "Bearer forged",
                "Content-Type": "application/json",
            },
        )
        assert response.status_code == 201
        assert response.json() == {"session_id": "server-owned"}
        assert response.headers["cache-control"] == "no-store"
        assert "x-backend-secret" not in response.headers
        assert len(calls) == 1
        assert calls[0].headers["authorization"] == "Bearer iam-token"
        assert calls[0].headers["x-firebase-authorization"] == "Bearer firebase-token"
        assert calls[0].extensions["timeout"]["read"] == 285
        assert calls[0].url.path == "/sessions"
        assert json.loads(calls[0].content) == {}


def test_backend_private_mode_verifies_forwarded_firebase_token():
    class RecordingAuthorizer:
        def __init__(self):
            self.calls = []

        def authorize(self, header):
            self.calls.append(header)
            if header != "Bearer firebase-token":
                raise AuthenticationError(401, "Invalid sign-in token.")
            return "firebase:pilot:alice"

    authorizer = RecordingAuthorizer()
    auth_settings = AuthSettings(
        mode="pilot",
        project_id="pilot",
        web_api_key="public-key",
        auth_domain="pilot.firebaseapp.com",
        web_app_id="web-id",
    )
    backend = create_backend(
        engine=object(),
        auth_settings=auth_settings,
        authorizer=authorizer,
        private_data_endpoint=True,
    )
    with TestClient(backend) as client:
        path = "/voice/config"
        assert client.get(path, headers={"Authorization": "Bearer iam-token"}).status_code == 401
        response = client.get(
            path,
            headers={
                "Authorization": "Bearer iam-token",
                "X-Firebase-Authorization": "Bearer firebase-token",
            },
        )
        assert response.status_code == 200
        assert authorizer.calls == [None, "Bearer firebase-token"]


def test_private_backend_rejects_unprotected_local_mode():
    with pytest.raises(ConfigurationError, match="requires APP_ENV=pilot or production"):
        create_backend(
            engine=object(), auth_settings=AuthSettings(mode="local"), private_data_endpoint=True
        )


def test_private_backend_requires_database_at_startup(monkeypatch):
    class UnusedAuthorizer:
        def authorize(self, header):
            return "firebase:pilot:alice"

    monkeypatch.delenv("DATABASE_URL", raising=False)
    auth_settings = AuthSettings(
        mode="pilot",
        project_id="pilot",
        web_api_key="public-key",
        auth_domain="pilot.firebaseapp.com",
        web_app_id="web-id",
    )
    backend = create_backend(
        auth_settings=auth_settings,
        authorizer=UnusedAuthorizer(),
        private_data_endpoint=True,
    )
    with pytest.raises(ConfigurationError, match="Set DATABASE_URL"), TestClient(backend):
        pass


def test_gateway_admits_one_voice_upload_before_reading_next_body(tmp_path):
    (tmp_path / "index.html").write_text("<html>pilot</html>")

    async def scenario():
        first_started = asyncio.Event()
        release_first = asyncio.Event()

        async def backend(request):
            first_started.set()
            await release_first.wait()
            return httpx.Response(200, json={"text": "hello"})

        app = create_gateway(
            SETTINGS,
            frontend_dist=tmp_path,
            token_provider=lambda audience: "iam-token",
            transport=httpx.MockTransport(backend),
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
            ) as client,
        ):
            headers = {
                "Authorization": "Bearer firebase-token",
                "Content-Type": "multipart/form-data; boundary=test",
            }
            first = asyncio.create_task(
                client.post("/transcribe", headers=headers, content=b"audio")
            )
            await asyncio.wait_for(first_started.wait(), timeout=2)
            second = await client.post("/transcribe", headers=headers, content=b"audio")
            assert second.status_code == 429
            assert second.headers["Retry-After"] == "1"
            release_first.set()
            assert (await first).status_code == 200
            assert (
                await client.post("/transcribe", headers=headers, content=b"audio")
            ).status_code == 200

    asyncio.run(scenario())


def test_gateway_config_fails_closed_for_database_and_insecure_backend(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://not-for-gateway")
    with pytest.raises(ValueError, match="must not receive DATABASE_URL"):
        GatewaySettings.from_env()
    monkeypatch.delenv("DATABASE_URL")
    monkeypatch.setenv("K_SERVICE", "pilot-gateway")
    with pytest.raises(ValueError, match="HTTPS"):
        GatewaySettings(
            backend_url="http://localhost:8000",
            audience="http://localhost:8000",
            firebase_project_id="pilot",
            firebase_web_api_key="key",
            firebase_auth_domain="pilot.firebaseapp.com",
            firebase_web_app_id="web-id",
        )
