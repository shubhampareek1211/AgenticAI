"""Durable transcripts and tool exchanges, serialized by a PostgreSQL session lock."""

import hashlib
import json
import uuid
from contextlib import contextmanager

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session, aliased

from cricket.models import ChartSpec, Conversation, Dataset, Message, ToolCall, utcnow
from cricket.results import error_result


class SessionBusy(ValueError):
    pass


class SessionNotFound(ValueError):
    pass


class SessionForbidden(ValueError):
    pass


def lock_key(session_id: uuid.UUID) -> int:
    """Derive a stable signed PostgreSQL advisory-lock key from the conversation UUID."""
    return int.from_bytes(hashlib.sha256(session_id.bytes).digest()[:8], "big", signed=True)


@contextmanager
def locked_session(engine, session_id: uuid.UUID):
    """Hold one connection's lock across commits and external calls, releasing it on exit."""
    key = lock_key(session_id)
    with engine.connect() as connection:
        acquired = connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
        connection.commit()
        if not acquired:
            raise SessionBusy("A request is already running for this session. Retry shortly.")
        try:
            with Session(bind=connection, expire_on_commit=False) as session:
                yield session
        finally:
            release_lock(connection, key)


def release_lock(connection, key):
    """Never return a connection with a session-level lock to the pool."""
    try:
        connection.rollback()
        connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
        connection.commit()
    except Exception:
        connection.invalidate()
        raise


@contextmanager
def transcript_session(engine, session_id: uuid.UUID):
    """Read one snapshot and probe the real worker lock without blocking its progress."""
    key = lock_key(session_id)
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        acquired = connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
        # Release a successful probe before reading the transcript. Holding it through
        # serialization can make a simultaneous chat request fail with a spurious 409.
        if acquired:
            release_lock(connection, key)
        with Session(bind=connection) as session:
            yield session, not acquired


class ConversationRepository:
    """Persist each boundary under a held session lock; commit before external work."""

    def __init__(self, session: Session, session_id: uuid.UUID, owner_id=None):
        self.session = session
        self.session_id = session_id
        self.owner_id = owner_id
        self.conversation = session.get(Conversation, session_id)
        if self.conversation and self.conversation.owner_id != owner_id:
            raise SessionForbidden("This conversation belongs to another user.")
        maximum = session.scalar(
            select(func.max(Message.ordinal)).where(Message.conversation_id == session_id)
        )
        self.next_ordinal = 0 if maximum is None else maximum + 1

    def require(self):
        """Require an existing conversation; supplied unknown IDs are never recreated."""
        if not self.conversation:
            raise SessionNotFound("Conversation not found. Start a new conversation.")
        return self.conversation

    def create(self, system_prompt: str):
        """Create the session and its initial system message in one transaction."""
        self.conversation = Conversation(id=self.session_id, owner_id=self.owner_id)
        self.session.add(self.conversation)
        self.session.flush()
        self.append(0, {"role": "system", "content": system_prompt})
        self.session.commit()

    def append(self, turn_number: int, payload: dict) -> Message:
        """Append in ordinal order; the surrounding operation controls its commit."""
        self.require().updated_at = utcnow()
        message = Message(
            conversation_id=self.session_id,
            ordinal=self.next_ordinal,
            turn_number=turn_number,
            role=payload["role"],
            payload=payload,
        )
        self.next_ordinal += 1
        self.session.add(message)
        self.session.flush()
        return message

    def messages(self) -> list[Message]:
        """Read the full transcript, including exchanges outside the model's context window."""
        self.require()
        return list(
            self.session.scalars(
                select(Message)
                .where(Message.conversation_id == self.session_id)
                .order_by(Message.ordinal)
            )
        )

    def unfinished_turns(self):
        """Find turns without a final assistant message, including pending tool results."""
        turns = {}
        for message in self.messages():
            if message.turn_number:
                turns.setdefault(message.turn_number, []).append(message)
        return {
            number: messages
            for number, messages in turns.items()
            if messages[-1].role != "assistant" or messages[-1].payload.get("tool_calls")
        }

    def recover_interrupted_turns(self):
        """Close unfinished turns left by a worker restart without retrying external tools."""
        for number in self.unfinished_turns():
            pending = self.session.scalars(
                select(ToolCall)
                .join(Message, Message.id == ToolCall.request_message_id)
                .where(
                    ToolCall.conversation_id == self.session_id,
                    Message.turn_number == number,
                    ToolCall.status == "requested",
                )
            )
            for call in pending:
                self.finish_tool(
                    call,
                    error_result(
                        "request_interrupted",
                        "The previous request was interrupted. Retry the request.",
                    ),
                    status="interrupted",
                    commit=False,
                )
            self.append(
                number,
                {
                    "role": "assistant",
                    "content": (
                        "The previous request was interrupted. Its saved history is available; please retry."
                    ),
                },
            )
        self.session.commit()

    def start_turn(self, content: str) -> int:
        """Save the user message before making any model call."""
        number = (
            self.session.scalar(
                select(func.coalesce(func.max(Message.turn_number), 0)).where(
                    Message.conversation_id == self.session_id
                )
            )
            + 1
        )
        self.append(number, {"role": "user", "content": content})
        self.session.commit()
        return number

    def begin_exchange(self, number: int, payload: dict, arguments: list[dict]) -> list[ToolCall]:
        """Save a request and all result slots atomically, so history never has orphan calls."""
        request = self.append(number, payload)
        calls = []
        for item, args in zip(payload["tool_calls"], arguments, strict=True):
            result = error_result("tool_pending", "This tool request has not finished.")
            message = self.append(
                number,
                {
                    "role": "tool",
                    "tool_call_id": item["id"],
                    "content": json.dumps(result),
                },
            )
            call = ToolCall(
                conversation_id=self.session_id,
                request_message_id=request.id,
                result_message_id=message.id,
                model_call_id=item["id"],
                name=item["function"]["name"],
                args=args,
                result=result,
                status="requested",
            )
            self.session.add(call)
            calls.append(call)
        self.session.commit()
        return calls

    def finish_tool(self, call: ToolCall, result: dict, status=None, commit=True):
        """Update the trace and its result message together before the next model request."""
        call.result = result
        call.status = status or ("complete" if result["ok"] else "failed")
        message = self.session.get(Message, call.result_message_id)
        message.payload = {
            "role": "tool",
            "tool_call_id": call.model_call_id,
            "content": json.dumps(result, allow_nan=False),
        }
        self.require().updated_at = utcnow()
        if commit:
            self.session.commit()

    def finish_turn(self, number: int, content: str):
        """Save both successful final answers and actionable harness failures."""
        self.append(number, {"role": "assistant", "content": content})
        self.session.commit()

    def save_summary(self, summary: str, through_turn: int):
        """Checkpoint a bounded summary without modifying or deleting transcript messages."""
        self.require().summary = summary
        self.conversation.summary_through_turn = through_turn
        self.session.commit()

    def tool_calls(self, turn_number=None) -> list[tuple[ToolCall, Message]]:
        """Read traces in result order, including multi-call assistant requests."""
        result_message = aliased(Message)
        statement = (
            select(ToolCall, Message)
            .join(Message, Message.id == ToolCall.request_message_id)
            .join(result_message, result_message.id == ToolCall.result_message_id)
            .where(ToolCall.conversation_id == self.session_id)
            .order_by(result_message.ordinal)
        )
        if turn_number is not None:
            statement = statement.where(Message.turn_number == turn_number)
        return list(self.session.execute(statement))

    def traces(self, turn_number: int) -> list[dict]:
        """Return the starter-compatible name/args/JSON-string result response shape."""
        return [
            {"name": call.name, "args": call.args, "result": json.dumps(call.result)}
            for call, _ in self.tool_calls(turn_number)
        ]

    def transcript(self) -> dict:
        """Serialize a session's full transcript, tool history, and artifact references."""
        conversation = self.require()
        return {
            "session_id": str(self.session_id),
            "created_at": conversation.created_at,
            "updated_at": conversation.updated_at,
            "summary": conversation.summary,
            "summary_through_turn": conversation.summary_through_turn,
            "messages": [
                {
                    "id": str(m.id),
                    "ordinal": m.ordinal,
                    "turn_number": m.turn_number,
                    "role": m.role,
                    "payload": m.payload,
                    "created_at": m.created_at,
                }
                for m in self.messages()
            ],
            "tool_calls": [
                {
                    "id": c.model_call_id,
                    "name": c.name,
                    "args": c.args,
                    "result": json.dumps(c.result),
                    "status": c.status,
                    "turn_number": m.turn_number,
                    "result_message_id": str(c.result_message_id),
                }
                for c, m in self.tool_calls()
            ],
            "datasets": [
                str(value)
                for value in self.session.scalars(
                    select(Dataset.id).where(Dataset.conversation_id == self.session_id)
                )
            ],
            "charts": [
                str(value)
                for value in self.session.scalars(
                    select(ChartSpec.id).where(ChartSpec.conversation_id == self.session_id)
                )
            ],
        }

    def clear(self):
        """Delete the conversation; foreign-key cascades remove its transcript and artifacts."""
        self.require()
        self.session.execute(delete(Conversation).where(Conversation.id == self.session_id))
        self.session.commit()
