import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import create_app
from cricket.conversations import ConversationRepository, locked_session, transcript_session
from cricket.harness import AgentHarness
from cricket.models import ChartSpec, Conversation, Dataset, Message, ToolCall
from cricket.settings import HarnessSettings
from tests.test_harness import (
    FakeModel,
    assert_paired,
    seed_complete_turn,
    summarization_budget,
    tool_reply,
)


@pytest.fixture
def api_database(pg_engine):
    # API requests commit across connections; remove only conversations created by this test.
    with Session(pg_engine) as session:
        before = set(session.scalars(select(Conversation.id)))
    yield pg_engine
    with Session(pg_engine) as session:
        after = set(session.scalars(select(Conversation.id)))
        if after - before:
            session.execute(delete(Conversation).where(Conversation.id.in_(after - before)))
            session.commit()


def test_contract_transcript_and_app_restart_preserve_tool_memory(api_database):
    model = FakeModel(tool_reply(), {"content": "Delhi is warm. Virat Kohli is ba607b88; ODI."})
    harness = AgentHarness(completion=model, runner=lambda *a: '{"temp_f":80}')
    with TestClient(create_app(api_database, harness)) as client:
        response = client.post("/chat", json={"message": "Virat Kohli, ODI. Weather in Delhi?"})
        assert response.status_code == 200
        data = response.json()
        assert set(data) == {"response", "session_id", "tool_calls"}
        session_id = data["session_id"]
        saved = client.get(f"/sessions/{session_id}").json()
        assert [m["role"] for m in saved["messages"]] == [
            "system",
            "user",
            "assistant",
            "tool",
            "assistant",
        ]
        assert json.loads(saved["tool_calls"][0]["result"]) == json.loads(
            data["tool_calls"][0]["result"]
        )
        assert saved["tool_calls"][0]["status"] == "complete"
    # A new app and pool have no access to process-local state from the first request.
    fresh_engine = create_engine(api_database.url)
    next_model = FakeModel({"content": "Only Kohli's ODIs, as requested."})
    try:
        with TestClient(create_app(fresh_engine, AgentHarness(completion=next_model))) as client:
            assert client.get(f"/sessions/{session_id}").json() == saved
            response = client.post("/chat", json={"message": "Only ODIs", "session_id": session_id})
            assert response.status_code == 200 and response.json()["session_id"] == session_id
            context = next_model.requests[0]["messages"]
            assert "ba607b88" in json.dumps(context)
            assert context[-1]["content"] == "Only ODIs"
            assert_paired(context)
            assert len(client.get(f"/sessions/{session_id}").json()["messages"]) == 7
    finally:
        fresh_engine.dispose()


def test_sessions_never_share_context_or_artifacts(api_database):
    model = FakeModel({"content": "Secret first-session fact"}, {"content": "Second answer"})
    with TestClient(create_app(api_database, AgentHarness(completion=model))) as client:
        first = client.post("/chat", json={"message": "First-session secret"}).json()
        second = client.post("/chat", json={"message": "Second question"}).json()
        assert first["session_id"] != second["session_id"]
        assert "First-session secret" not in json.dumps(model.requests[1]["messages"])
        with Session(api_database) as session:
            dataset = Dataset(
                conversation_id=uuid.UUID(first["session_id"]),
                kind="test",
                data={},
                provenance={},
                coverage={},
            )
            session.add(dataset)
            session.commit()
            dataset_id = str(dataset.id)
        assert client.get(f"/sessions/{first['session_id']}").json()["datasets"] == [dataset_id]
        assert client.get(f"/sessions/{second['session_id']}").json()["datasets"] == []


def test_saved_chart_figure_requires_its_own_conversation(api_database):
    first, second = uuid.uuid4(), uuid.uuid4()
    with Session(api_database) as session:
        session.add_all([Conversation(id=first), Conversation(id=second)])
        session.flush()
        dataset = Dataset(
            conversation_id=first, kind="player_data", data={}, provenance={}, coverage={}
        )
        session.add(dataset)
        session.flush()
        chart = ChartSpec(
            conversation_id=first,
            dataset_id=dataset.id,
            spec={
                "figure": {"data": [{"type": "bar", "x": ["2026"], "y": [17]}], "layout": {}},
                "coverage": {"plotted": {"batting_innings": 1}},
                "provenance": [],
            },
        )
        session.add(chart)
        session.commit()
        chart_id = chart.id
        dataset_id = dataset.id
    with TestClient(create_app(api_database, AgentHarness(completion=FakeModel()))) as client:
        own = client.get(f"/sessions/{first}/charts/{chart_id}")
        assert own.status_code == 200
        payload = own.json()
        assert payload["chart_id"] == str(chart_id)
        assert payload["dataset_id"] == str(dataset_id)
        assert payload["schema_version"] == 2
        assert payload["chart"]["series"][0]["points"][0]["y"] == 17
        assert payload["coverage"] == {"plotted": {"batting_innings": 1}}
        assert client.get(f"/sessions/{first}/charts/{uuid.uuid4()}").status_code == 404
        assert client.get(f"/sessions/{second}/charts/{chart_id}").status_code == 404
        assert client.get(f"/sessions/{uuid.uuid4()}/charts/{chart_id}").status_code == 404
        assert client.get("/assets/plotly-basic-4.1.1.min.js").status_code == 200


def test_saved_versioned_chart_returns_normalized_values_and_checks_owner(api_database):
    owned_id, foreign_id = uuid.uuid4(), uuid.uuid4()
    with Session(api_database) as session:
        session.add_all(
            [Conversation(id=owned_id), Conversation(id=foreign_id, owner_id="other-owner")]
        )
        session.flush()
        dataset = Dataset(
            conversation_id=owned_id, kind="player_data", data={}, provenance={}, coverage={}
        )
        session.add(dataset)
        session.flush()
        chart = ChartSpec(
            conversation_id=owned_id,
            dataset_id=dataset.id,
            spec={
                "schema_version": 2,
                "chart": {
                    "metric": "batting_average",
                    "group_by": "year",
                    "chart_type": "bar",
                    "title": "Annual batting average",
                    "x_label": "Year",
                    "y_label": "Runs per dismissal",
                    "series": [
                        {"name": "ODI", "points": [{"x": "2024", "y": 52.5, "sample_size": 12}]}
                    ],
                },
                "coverage": {"plotted": {"batting_innings": 12}},
                "provenance": [{"source": "test"}],
            },
        )
        session.add(chart)
        session.commit()
        chart_id, dataset_id = chart.id, dataset.id
    with TestClient(create_app(api_database, AgentHarness(completion=FakeModel()))) as client:
        response = client.get(f"/sessions/{owned_id}/charts/{chart_id}")
        assert response.status_code == 200
        payload = response.json()
        assert set(payload) == {
            "chart_id",
            "dataset_id",
            "schema_version",
            "chart",
            "coverage",
            "provenance",
        }
        assert payload["chart_id"] == str(chart_id) and payload["dataset_id"] == str(dataset_id)
        assert payload["chart"]["series"][0]["points"] == [
            {"x": "2024", "y": 52.5, "sample_size": 12}
        ]
        assert client.get(f"/sessions/{foreign_id}/charts/{chart_id}").status_code == 403


def test_allocated_session_is_restorable_before_first_model_response(api_database):
    entered, release = Event(), Event()

    def delayed_model(**kwargs):
        entered.set()
        assert release.wait(5)
        return {"content": "Saved first answer"}

    with TestClient(create_app(api_database, AgentHarness(completion=delayed_model))) as client:
        allocated = client.post("/sessions")
        assert allocated.status_code == 201
        session_id = allocated.json()["session_id"]
        assert not entered.is_set()  # Allocation never starts provider work.
        assert len(client.get(f"/sessions/{session_id}").json()["messages"]) == 1
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(
                client.post, "/chat", json={"message": "First", "session_id": session_id}
            )
            try:
                assert entered.wait(5) and not pending.done()
                restored = client.get(f"/sessions/{session_id}").json()
                assert restored["messages"][-1]["payload"]["content"] == "First"
            finally:
                release.set()
            response = pending.result(timeout=5)
            assert response.status_code == 200 and response.json()["session_id"] == session_id
        assert client.get(f"/sessions/{session_id}").json()["messages"][-1]["payload"][
            "content"
        ] == ("Saved first answer")


def test_transcript_uses_one_snapshot_during_a_concurrent_tool_update(api_database):
    model = FakeModel(tool_reply(), {"content": "Saved answer"})
    with TestClient(
        create_app(api_database, AgentHarness(completion=model, runner=lambda *a: '{"temp_f":80}'))
    ) as client:
        session_id = client.post("/chat", json={"message": "Weather"}).json()["session_id"]
        changed = []

        def update_after_message_read(connection, cursor, statement, parameters, context, many):
            if changed or "ORDER BY messages.ordinal" not in statement:
                return
            changed.append(True)
            with Session(api_database) as writer:
                call = writer.scalar(
                    select(ToolCall).where(ToolCall.conversation_id == uuid.UUID(session_id))
                )
                replacement = {
                    "ok": True,
                    "data": {"temp_f": 90},
                    "error": None,
                    "provenance": [],
                    "coverage": {},
                }
                call.result = replacement
                message = writer.get(Message, call.result_message_id)
                message.payload = {
                    "role": "tool",
                    "tool_call_id": call.model_call_id,
                    "content": json.dumps(replacement),
                }
                writer.commit()

        event.listen(api_database, "after_cursor_execute", update_after_message_read)
        try:
            saved = client.get(f"/sessions/{session_id}").json()
        finally:
            event.remove(api_database, "after_cursor_execute", update_after_message_read)
        assert changed
        tool_message = next(m["payload"] for m in saved["messages"] if m["role"] == "tool")
        assert json.loads(tool_message["content"])["data"]["temp_f"] == 80
        assert json.loads(saved["tool_calls"][0]["result"])["data"]["temp_f"] == 80
        updated = client.get(f"/sessions/{session_id}").json()
        assert json.loads(updated["tool_calls"][0]["result"])["data"]["temp_f"] == 90


def test_overlapping_requests_and_clear_are_rejected_without_reordering(api_database):
    entered, release = Event(), Event()

    def model(**kwargs):
        if kwargs["messages"][-1]["content"] == "block":
            entered.set()
            assert release.wait(5)
        return {"content": "Done"}

    with TestClient(create_app(api_database, AgentHarness(completion=model))) as client:
        session_id = client.post("/chat", json={"message": "Start"}).json()["session_id"]
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(
                client.post, "/chat", json={"message": "block", "session_id": session_id}
            )
            try:
                assert entered.wait(5)
                overlap = client.post(
                    "/chat", json={"message": "Overlapping", "session_id": session_id}
                )
                assert overlap.status_code == 409 and overlap.headers["Retry-After"] == "1"
                assert "already running" in overlap.json()["detail"]
                assert client.post("/clear", params={"session_id": session_id}).status_code == 409
                assert (
                    client.post("/chat", json={"message": "Independent session"}).status_code == 200
                )
            finally:
                release.set()
            assert pending.result(timeout=5).status_code == 200
        saved = client.get(f"/sessions/{session_id}").json()
        assert [m["payload"]["content"] for m in saved["messages"] if m["role"] == "user"] == [
            "Start",
            "block",
        ]
        assert [m["ordinal"] for m in saved["messages"]] == list(range(5))


def test_transcript_read_does_not_block_next_chat(api_database):
    with TestClient(
        create_app(api_database, AgentHarness(completion=lambda **kwargs: {"content": "Done"}))
    ) as client:
        session_id = client.post("/sessions").json()["session_id"]
        with transcript_session(api_database, uuid.UUID(session_id)) as (session, running):
            assert not running
            assert ConversationRepository(session, uuid.UUID(session_id)).transcript()["messages"]
            response = client.post(
                "/chat", json={"message": "After read", "session_id": session_id}
            )
            assert response.status_code == 200


@pytest.mark.parametrize("phase", ["model", "tool"])
def test_restored_request_state_tracks_active_model_and_tool_work(api_database, phase):
    entered, release = Event(), Event()

    def block(value):
        entered.set()
        assert release.wait(5)
        return value

    harness = (
        AgentHarness(completion=lambda **kwargs: block({"content": "Saved answer"}))
        if phase == "model"
        else AgentHarness(
            completion=FakeModel(tool_reply(), {"content": "Saved answer"}),
            runner=lambda *args: block('{"temp_f":80}'),
        )
    )
    with TestClient(create_app(api_database, harness)) as client:
        session_id = client.post("/sessions").json()["session_id"]
        assert client.get(f"/sessions/{session_id}").json()["request_state"] == "idle"
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(
                client.post, "/chat", json={"message": "First", "session_id": session_id}
            )
            try:
                assert entered.wait(5)
                restored = client.get(f"/sessions/{session_id}").json()
                assert restored["request_state"] == "running"
                if phase == "tool":
                    assert restored["tool_calls"][0]["status"] == "requested"
                assert client.post(f"/sessions/{session_id}/recover").status_code == 409
            finally:
                release.set()
            assert pending.result(timeout=5).status_code == 200
        saved = client.get(f"/sessions/{session_id}").json()
        assert saved["request_state"] == "idle"
        assert saved["messages"][-1]["payload"]["content"] == "Saved answer"


def test_restore_recovers_abandoned_turn_without_executing_tools(api_database):
    session_id = uuid.uuid4()
    with locked_session(api_database, session_id) as session:
        repo = ConversationRepository(session, session_id)
        repo.create("System")
        turn = repo.start_turn("Interrupted question")
        repo.begin_exchange(turn, tool_reply(), [{"location": "Delhi"}])
    executions, model = [], FakeModel()
    with TestClient(
        create_app(
            api_database, AgentHarness(completion=model, runner=lambda *a: executions.append(a))
        )
    ) as client:
        assert client.get(f"/sessions/{session_id}").json()["request_state"] == "interrupted"
        assert client.post(f"/sessions/{session_id}/recover").status_code == 200
        saved = client.get(f"/sessions/{session_id}").json()
        assert saved["request_state"] == "idle"
        assert saved["tool_calls"][0]["status"] == "interrupted"
        assert "interrupted" in saved["messages"][-1]["payload"]["content"]
        assert not executions and not model.requests
        assert client.post(f"/sessions/{session_id}/recover").status_code == 200
        assert client.get(f"/sessions/{session_id}").json() == saved


def test_clear_cascades_only_the_selected_conversation(api_database):
    model = FakeModel(tool_reply(), {"content": "First"}, {"content": "Second"})
    with TestClient(
        create_app(api_database, AgentHarness(completion=model, runner=lambda *a: "{}"))
    ) as client:
        first = client.post("/chat", json={"message": "First"}).json()["session_id"]
        second = client.post("/chat", json={"message": "Second"}).json()["session_id"]
        with Session(api_database) as session:
            dataset = Dataset(
                conversation_id=uuid.UUID(first), kind="test", data={}, provenance={}, coverage={}
            )
            session.add(dataset)
            session.flush()
            session.add(ChartSpec(conversation_id=uuid.UUID(first), dataset_id=dataset.id, spec={}))
            session.commit()
        assert client.post("/clear", params={"session_id": first}).json() == {"status": "ok"}
        assert client.get(f"/sessions/{first}").status_code == 404
        assert client.get(f"/sessions/{second}").status_code == 200
        assert (
            client.post("/chat", json={"message": "Resume", "session_id": first}).status_code == 404
        )
        with Session(api_database) as session:
            for model in (Message, ToolCall, Dataset, ChartSpec):
                assert not list(
                    session.scalars(select(model).where(model.conversation_id == uuid.UUID(first)))
                )


def test_interrupted_worker_recovers_pending_calls_without_reexecuting_tools(api_database):
    session_id = uuid.uuid4()
    with locked_session(api_database, session_id) as session:
        repo = ConversationRepository(session, session_id)
        repo.create("System")
        turn = repo.start_turn("Old request")
        reply = tool_reply(call_id="done")
        reply["tool_calls"].append(tool_reply(call_id="pending")["tool_calls"][0])
        calls = repo.begin_exchange(turn, reply, [{"location": "Delhi"}, {"location": "Delhi"}])
        repo.finish_tool(
            calls[0],
            {"ok": True, "data": {"temp_f": 80}, "error": None, "provenance": [], "coverage": {}},
        )
        # Closing here simulates a worker stopping before the second tool/final answer.
    executions = []
    model = FakeModel({"content": "Recovered"})
    with TestClient(
        create_app(
            api_database, AgentHarness(completion=model, runner=lambda *a: executions.append(a))
        )
    ) as client:
        response = client.post("/chat", json={"message": "Continue", "session_id": str(session_id)})
        assert response.status_code == 200 and not executions
        saved = client.get(f"/sessions/{session_id}").json()
        assert [call["status"] for call in saved["tool_calls"]] == ["complete", "interrupted"]
        assert json.loads(saved["tool_calls"][0]["result"])["data"] == {"temp_f": 80}
        assert (
            json.loads(saved["tool_calls"][1]["result"])["error"]["code"] == "request_interrupted"
        )
        assert [m["turn_number"] for m in saved["messages"] if m["role"] == "user"] == [1, 2]
        assert_paired(model.requests[0]["messages"])


@pytest.mark.parametrize(
    "payload",
    [
        {"message": ""},
        {"message": "  "},
        {"message": "x" * 8001},
        {"message": "Hi", "session_id": "not-a-uuid"},
        {"message": "\x00"},
    ],
)
def test_invalid_chat_input_never_reaches_the_model(api_database, payload):
    model = FakeModel()
    with TestClient(create_app(api_database, AgentHarness(completion=model))) as client:
        assert client.post("/chat", json=payload).status_code == 422
        assert not model.requests


@pytest.mark.parametrize("invalid", ["bad\x00summary", "bad\ud800summary"])
def test_invalid_model_summary_returns_saved_failure_without_storage_error(api_database, invalid):
    session_id = uuid.uuid4()
    with locked_session(api_database, session_id) as session:
        repo = ConversationRepository(session, session_id)
        repo.create("System")
        for number in range(1, 12):
            seed_complete_turn(repo, number)
        budget = summarization_budget(repo, "Continue")
    model = FakeModel({"content": invalid})
    harness = AgentHarness(settings=HarnessSettings(context_token_budget=budget), completion=model)
    with TestClient(create_app(api_database, harness), raise_server_exceptions=False) as client:
        response = client.post("/chat", json={"message": "Continue", "session_id": str(session_id)})
        assert response.status_code == 200
        assert "invalid summary" in response.json()["response"]
        saved = client.get(f"/sessions/{session_id}").json()
        assert saved["summary"] is None and saved["summary_through_turn"] == 0
        assert saved["request_state"] == "idle"
        assert saved["messages"][-1]["payload"]["content"] == response.json()["response"]
        assert len([m for m in saved["messages"] if m["role"] == "user"]) == 12
        assert len(model.requests) == 1


def test_missing_foreign_owned_and_invalid_session_ids(api_database):
    owned_id = uuid.uuid4()
    with Session(api_database) as session:
        session.add(Conversation(id=owned_id, owner_id="other-owner"))
        session.commit()
    with TestClient(create_app(api_database, AgentHarness(completion=FakeModel()))) as client:
        for endpoint in (f"/sessions/{owned_id}",):
            assert client.get(endpoint).status_code == 403
        assert (
            client.post("/chat", json={"message": "Hi", "session_id": str(owned_id)}).status_code
            == 403
        )
        assert client.post("/clear", params={"session_id": str(owned_id)}).status_code == 403
        assert client.post(f"/sessions/{owned_id}/recover").status_code == 403
        assert client.get(f"/sessions/{uuid.uuid4()}").status_code == 404
        assert client.post(f"/sessions/{uuid.uuid4()}/recover").status_code == 404
        assert client.get("/sessions/invalid").status_code == 422
        assert client.post("/clear").status_code == 422


def test_storage_failure_is_actionable_and_does_not_expose_database_secrets(
    api_database, monkeypatch
):
    def failure(*args, **kwargs):
        raise OperationalError("secret-sql", {}, RuntimeError("password=do-not-expose"))

    monkeypatch.setattr(api_database, "connect", failure)
    with TestClient(create_app(api_database, AgentHarness(completion=FakeModel()))) as client:
        assert client.get("/healthz").status_code == 200  # Health does not connect to PostgreSQL.
        response = client.post("/chat", json={"message": "Hi"})
        assert response.status_code == 503 and "storage" in response.json()["detail"]
        assert "do-not-expose" not in response.text and "secret-sql" not in response.text


def test_compiled_frontend_shell_and_assets_are_served_without_startup_build(tmp_path):
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text(
        '<!doctype html><div id="root"></div><script src="/assets/app-abc.js"></script>',
        encoding="utf-8",
    )
    (assets / "app-abc.js").write_text("window.phase4 = true", encoding="utf-8")
    (dist / "private.txt").write_text("private", encoding="utf-8")
    (assets / "outside.txt").symlink_to(dist / "private.txt")
    with TestClient(
        create_app(harness=AgentHarness(completion=FakeModel()), frontend_dist=dist)
    ) as client:
        shell = client.get("/")
        assert shell.status_code == 200 and 'id="root"' in shell.text
        assert shell.headers["cache-control"] == "no-cache"
        script = client.get("/assets/app-abc.js")
        assert script.status_code == 200 and script.text == "window.phase4 = true"
        assert "immutable" in script.headers["cache-control"]
        assert client.get("/assets/missing.js").status_code == 404
        assert client.get("/assets/%2e%2e/private.txt").status_code == 404
        assert client.get("/assets/outside.txt").status_code == 404


def test_missing_database_configuration_does_not_migrate_or_call_providers(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with TestClient(
        create_app(harness=AgentHarness(completion=FakeModel()), frontend_dist=tmp_path)
    ) as client:
        shell = client.get("/")
        assert shell.status_code == 503 and "npm run build" in shell.json()["detail"]
        assert client.get("/healthz").status_code == 503
        response = client.post("/chat", json={"message": "Hi"})
        assert response.status_code == 503 and "DATABASE_URL" in response.json()["detail"]
