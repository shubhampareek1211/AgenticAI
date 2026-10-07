import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, inspect, select
from sqlalchemy.exc import IntegrityError

from cricket.models import Base, ChartSpec, Conversation, Dataset, Message, ToolCall


def test_migration_upgrade_downgrade_and_metadata_agreement(pg_engine):
    # All DDL is rolled back; the dedicated test DB remains at head after this check.
    with pg_engine.connect() as connection:
        transaction = connection.begin()
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        command.downgrade(config, "base")
        assert set(inspect(connection).get_table_names()) == {"alembic_version"}
        command.upgrade(config, "head")
        assert set(inspect(connection).get_table_names()) == set(Base.metadata.tables) | {
            "alembic_version"
        }
        command.check(config)
        transaction.rollback()


def test_storage_links_and_conversation_cascade(session):
    conversation = Conversation(owner_id="test-user")
    session.add(conversation)
    session.flush()
    request = Message(
        conversation_id=conversation.id,
        ordinal=0,
        turn_number=0,
        role="assistant",
        payload={"tool_calls": [{"id": "call1"}]},
    )
    result = Message(
        conversation_id=conversation.id,
        ordinal=1,
        turn_number=0,
        role="tool",
        payload={"tool_call_id": "call1", "content": "{}"},
    )
    session.add_all([request, result])
    session.flush()
    call = ToolCall(
        conversation_id=conversation.id,
        request_message_id=request.id,
        result_message_id=result.id,
        model_call_id="call1",
        name="get_player_data",
        args={},
        result={"ok": True},
        status="complete",
    )
    dataset = Dataset(
        conversation_id=conversation.id,
        kind="batting",
        data={"rows": []},
        provenance={"source": "fixture"},
        coverage={"sample_size": 0},
    )
    session.add_all([call, dataset])
    session.flush()
    session.add(
        ChartSpec(
            conversation_id=conversation.id, dataset_id=dataset.id, spec={"type": "bar", "data": []}
        )
    )
    session.flush()
    session.execute(delete(Conversation).where(Conversation.id == conversation.id))
    for model in (Conversation, Message, ToolCall, Dataset, ChartSpec):
        assert session.scalar(select(func.count()).select_from(model)) == 0


def test_chart_cannot_reference_another_conversations_dataset(session):
    first, second = Conversation(), Conversation()
    session.add_all([first, second])
    session.flush()
    dataset = Dataset(conversation_id=first.id, kind="batting", data={}, provenance={}, coverage={})
    session.add(dataset)
    session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(ChartSpec(conversation_id=second.id, dataset_id=dataset.id, spec={}))
        session.flush()


def test_tool_call_cannot_reference_another_conversations_message(session):
    first, second = Conversation(), Conversation()
    session.add_all([first, second])
    session.flush()
    message = Message(
        conversation_id=first.id, ordinal=0, turn_number=0, role="assistant", payload={}
    )
    session.add(message)
    session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(
            ToolCall(
                conversation_id=second.id,
                request_message_id=message.id,
                model_call_id="other-call",
                name="test",
                args={},
            )
        )
        session.flush()
