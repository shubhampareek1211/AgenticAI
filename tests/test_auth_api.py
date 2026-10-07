"""Pilot authentication and conversation ownership at the HTTP boundary."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import create_app
from cricket.auth import AuthenticationError, AuthSettings, FirebaseAuthorizer
from cricket.conversations import ConversationRepository, locked_session
from cricket.db import ConfigurationError
from cricket.harness import AgentHarness
from cricket.models import Conversation
from cricket.speech import SpeechService, SpeechSettings
from cricket.tts import TTSService, TTSSettings
from cricket.voice_quota import VoiceQuotaExceeded, VoiceQuotaUnavailable
from tests.test_harness import FakeModel

PROJECT = "pilot-project"
SETTINGS = AuthSettings(
    mode="pilot",
    project_id=PROJECT,
    web_api_key="public-key",
    auth_domain="pilot-project.firebaseapp.com",
    web_app_id="app-id",
)


def claims(uid="alice", email="alice@columbia.edu", verified=True, **overrides):
    return {
        "aud": PROJECT,
        "iss": f"https://securetoken.google.com/{PROJECT}",
        "uid": uid,
        "email": email,
        "email_verified": verified,
        **overrides,
    }


def authorizer_for(tokens):
    def verify(token):
        value = tokens.get(token)
        if value is None:
            raise AuthenticationError(401, "Invalid or expired sign-in token.")
        return value

    return FirebaseAuthorizer(SETTINGS, verify_claims=verify)


def headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_token_policy_requires_correct_project_verified_exact_domain_and_uid():
    cases = [
        (claims(email="ALICE@COLUMBIA.EDU"), f"firebase:{PROJECT}:alice"),
        (claims(email="alice@sub.columbia.edu"), 403),
        (claims(email="alice@columbia.edu.evil"), 403),
        (claims(email="alice@columbia.edu", verified=False), 403),
        (claims(email="alice@columbia.edu", uid=""), 401),
        (claims(aud="other-project"), 401),
        (claims(iss="https://securetoken.google.com/other-project"), 401),
    ]
    for i, (payload, expected) in enumerate(cases):
        auth = authorizer_for({str(i): payload})
        if isinstance(expected, int):
            with pytest.raises(AuthenticationError) as exc:
                auth.authorize(f"Bearer {i}")
            assert exc.value.status_code == expected
        else:
            assert auth.authorize(f"Bearer {i}") == expected
    with pytest.raises(AuthenticationError) as exc:
        authorizer_for({}).authorize(None)
    assert exc.value.status_code == 401


def test_firebase_admin_verification_checks_revocation(monkeypatch):
    import firebase_admin
    from firebase_admin import auth

    sdk_app = object()
    observed = []
    monkeypatch.setattr(firebase_admin, "get_app", lambda name: sdk_app)

    def verify(token, *, app, check_revoked):
        observed.append((token, app, check_revoked))
        return claims()

    monkeypatch.setattr(auth, "verify_id_token", verify)
    authorizer = FirebaseAuthorizer(SETTINGS)
    assert authorizer.authorize("Bearer signed-token") == f"firebase:{PROJECT}:alice"
    assert observed == [("signed-token", sdk_app, True)]


def test_pilot_requires_full_firebase_configuration_and_explicit_cloud_mode(monkeypatch):
    with pytest.raises(ConfigurationError):
        AuthSettings(mode="pilot", project_id=PROJECT)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("K_SERVICE", "pilot-app")
    with pytest.raises(ConfigurationError):
        AuthSettings.from_env()
    monkeypatch.setenv("APP_ENV", "local")
    with pytest.raises(ConfigurationError):
        AuthSettings.from_env()


def test_api_gate_precedes_body_parsing_and_public_config(tmp_path):
    class NoDatabase:
        def connect(self):
            raise AssertionError("Unauthenticated requests must not reach the database")

    (tmp_path / "index.html").write_text("<html>Pilot login</html>")
    app = create_app(
        NoDatabase(),
        AgentHarness(completion=FakeModel()),
        frontend_dist=tmp_path,
        auth_settings=SETTINGS,
        authorizer=authorizer_for({"alice": claims()}),
    )
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/auth/finish?oobCode=example").text == "<html>Pilot login</html>"
        assert client.get("/auth/config").json() == {
            "enabled": True,
            "firebaseConfig": {
                "apiKey": "public-key",
                "authDomain": "pilot-project.firebaseapp.com",
                "projectId": PROJECT,
                "appId": "app-id",
            },
        }
        paths = [
            ("post", "/sessions"),
            ("post", "/chat"),
            ("get", f"/sessions/{uuid.uuid4()}"),
            ("get", f"/sessions/{uuid.uuid4()}/charts/{uuid.uuid4()}"),
            ("post", f"/sessions/{uuid.uuid4()}/recover"),
            ("post", "/clear"),
            ("post", "/transcribe"),
            ("get", "/voice/config"),
            ("get", "/tts/config"),
            ("post", f"/sessions/{uuid.uuid4()}/messages/{uuid.uuid4()}/speech"),
        ]
        for method, path in paths:
            response = getattr(client, method)(path)
            assert response.status_code == 401, (method, path, response.text)
        assert (
            client.post("/chat", json={"message": ""}, headers=headers("alice")).status_code == 422
        )


def test_pilot_voice_quota_rejects_before_inference_with_retry_after(monkeypatch):
    import app as server

    class NoInference:
        async def transcribe(self, *_args):
            raise AssertionError("Rejected uploads must not reach Whisper")

    service = SpeechService(SpeechSettings(enabled=True), provider=NoInference())
    app = create_app(
        object(),
        AgentHarness(completion=FakeModel()),
        speech_service=service,
        auth_settings=SETTINGS,
        authorizer=authorizer_for({"alice": claims()}),
    )
    reset = datetime.now(timezone.utc) + timedelta(minutes=5)

    def exhausted(_engine, owner_id):
        assert owner_id == f"firebase:{PROJECT}:alice"
        raise VoiceQuotaExceeded("user", 300, reset)

    monkeypatch.setattr(server, "reserve_voice_attempt", exhausted)
    with TestClient(app) as client:
        response = client.post(
            "/transcribe",
            headers=headers("alice"),
            files={"file": ("clip.webm", b"recording", "audio/webm")},
        )
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "300"
        assert response.json()["code"] == "voice_quota_exceeded"

    def unavailable(*_args):
        raise VoiceQuotaUnavailable()

    monkeypatch.setattr(server, "reserve_voice_attempt", unavailable)
    with TestClient(app) as client:
        response = client.post(
            "/transcribe",
            headers=headers("alice"),
            files={"file": ("clip.webm", b"recording", "audio/webm")},
        )
        assert response.status_code == 503
        assert response.json()["code"] == "voice_quota_unavailable"


@pytest.fixture
def pilot_database(pg_engine):
    with Session(pg_engine) as session:
        before = set(session.scalars(select(Conversation.id)))
    yield pg_engine
    with Session(pg_engine) as session:
        after = set(session.scalars(select(Conversation.id)))
        if after - before:
            session.execute(delete(Conversation).where(Conversation.id.in_(after - before)))
            session.commit()


def test_owner_is_stable_uid_and_cross_user_and_legacy_sessions_are_denied(pilot_database):
    legacy_id = uuid.uuid4()
    with Session(pilot_database) as session:
        session.add(Conversation(id=legacy_id, owner_id=None))
        session.commit()
    app = create_app(
        pilot_database,
        AgentHarness(completion=FakeModel({"content": "Answer"})),
        auth_settings=SETTINGS,
        authorizer=authorizer_for(
            {
                "alice": claims(),
                "alice-new-email": claims(email="new.name@columbia.edu"),
                "bob": claims(uid="bob", email="bob@columbia.edu"),
            }
        ),
    )
    with TestClient(app) as client:
        allocated = client.post("/sessions", headers=headers("alice"))
        assert allocated.status_code == 201
        session_id = allocated.json()["session_id"]
        with Session(pilot_database) as session:
            assert session.get(Conversation, uuid.UUID(session_id)).owner_id == (
                f"firebase:{PROJECT}:alice"
            )
        assert (
            client.get(f"/sessions/{session_id}", headers=headers("alice-new-email")).status_code
            == 200
        )
        assert (
            client.post(
                "/chat",
                json={"session_id": session_id, "message": "Hello"},
                headers=headers("alice"),
            ).status_code
            == 200
        )
        for method, path, kwargs in [
            ("get", f"/sessions/{session_id}", {}),
            ("get", f"/sessions/{session_id}/charts/{uuid.uuid4()}", {}),
            ("post", f"/sessions/{session_id}/recover", {}),
            ("post", "/clear", {"params": {"session_id": session_id}}),
            ("post", "/chat", {"json": {"session_id": session_id, "message": "Hi"}}),
        ]:
            result = getattr(client, method)(path, headers=headers("bob"), **kwargs)
            assert result.status_code == 403, (method, path, result.text)
        assert client.get(f"/sessions/{legacy_id}", headers=headers("alice")).status_code == 403
        assert client.get(f"/sessions/{uuid.uuid4()}", headers=headers("alice")).status_code == 404


def test_generated_speech_requires_a_saved_answer_owned_by_the_caller(pilot_database, monkeypatch):
    import app as server

    class FakeSpeech:
        def __init__(self):
            self.texts = []

        async def synthesize(self, text):
            self.texts.append(text)
            return b"RIFF" + b"\0" * 4 + b"WAVE" + b"\0" * 36

    provider = FakeSpeech()
    quota_attempts = []
    monkeypatch.setattr(
        server,
        "reserve_voice_attempt",
        lambda _engine, owner_id: quota_attempts.append(owner_id),
    )
    app = create_app(
        pilot_database,
        AgentHarness(completion=FakeModel()),
        auth_settings=SETTINGS,
        authorizer=authorizer_for(
            {"alice": claims(), "bob": claims(uid="bob", email="bob@columbia.edu")}
        ),
        tts_service=TTSService(TTSSettings(enabled=True), provider=provider),
    )
    with TestClient(app) as client:
        session_id = uuid.UUID(
            client.post("/sessions", headers=headers("alice")).json()["session_id"]
        )
        with locked_session(pilot_database, session_id) as session:
            answer = ConversationRepository(
                session, session_id, f"firebase:{PROJECT}:alice"
            ).append(1, {"role": "assistant", "content": "**Kohli** scored 82 runs."})
            answer_id = answer.id
            session.commit()
        url = f"/sessions/{session_id}/messages/{answer_id}/speech"
        assert client.post(url).status_code == 401
        assert client.post(url, headers=headers("bob")).status_code == 403
        assert (
            client.post(
                f"/sessions/{session_id}/messages/{uuid.uuid4()}/speech", headers=headers("alice")
            ).status_code
            == 404
        )
        response = client.post(url, headers=headers("alice"))
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/x-cricket-speech-stream"
        assert int.from_bytes(response.content[:4], "big") == 48
        assert response.content[4:8] == b"RIFF"
        assert provider.texts == ["Kohli scored 82 runs."]
        assert quota_attempts == [f"firebase:{PROJECT}:alice"]
        with locked_session(pilot_database, session_id) as session:
            answer = session.get(server.Message, answer_id)
            answer.payload = {"role": "assistant", "content": "विराट कोहली ने 82 रन बनाए।"}
            session.commit()
        assert client.post(url, headers=headers("alice")).status_code == 422
        assert quota_attempts == [f"firebase:{PROJECT}:alice"]
        assert provider.texts == ["Kohli scored 82 runs."]
