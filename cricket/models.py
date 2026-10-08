"""Phase 1 schema. Cricsheet identifiers, not names, identify people and matches."""

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Return an aware UTC timestamp for defaults evaluated when a row is inserted."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Share predictable constraint names across models and Alembic migrations."""

    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class Conversation(Base):
    """Chat session and its owner, timestamps, and bounded-context summary."""

    __tablename__ = "conversations"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[str | None] = mapped_column(String(255), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    summary: Mapped[str | None] = mapped_column(Text)
    summary_through_turn: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class VoiceDailyUsage(Base):
    """Atomic, UTC-day voice admission counters shared by all app replicas."""

    __tablename__ = "voice_daily_usage"
    __table_args__ = (
        CheckConstraint("scope IN ('global','user')", name="scope"),
        CheckConstraint("used >= 1", name="used_positive"),
    )
    period_start: Mapped[date] = mapped_column(Date, primary_key=True)
    scope: Mapped[str] = mapped_column(String(8), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    used: Mapped[int] = mapped_column(Integer)


class Message(Base):
    """Ordered transcript entry preserving the model payload and conversational turn."""

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "ordinal"),
        UniqueConstraint("id", "conversation_id"),
        CheckConstraint("ordinal >= 0 AND turn_number >= 0", name="positive_order"),
        CheckConstraint("role IN ('system','user','assistant','tool','summary')", name="role"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    turn_number: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ToolCall(Base):
    """Execution trace linking a model's tool request to its result messages."""

    __tablename__ = "tool_calls"
    # Composite foreign keys keep request/result messages in this conversation.
    __table_args__ = (
        UniqueConstraint("conversation_id", "model_call_id"),
        ForeignKeyConstraint(
            ["request_message_id", "conversation_id"],
            ["messages.id", "messages.conversation_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["result_message_id", "conversation_id"],
            ["messages.id", "messages.conversation_id"],
            ondelete="CASCADE",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    request_message_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    result_message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    model_call_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(100))
    args: Mapped[dict] = mapped_column(JSONB)
    result: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32), default="requested")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceImport(Base):
    """Artifact provenance and coverage, reused for the same checksum and selection."""

    __tablename__ = "source_imports"
    __table_args__ = (UniqueConstraint("provider", "checksum", "selection_key"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(32))
    dataset_url: Mapped[str] = mapped_column(Text)
    checksum: Mapped[str] = mapped_column(String(64))
    selection_key: Mapped[str] = mapped_column(String(64))
    source_revision: Mapped[str] = mapped_column(Text)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    date_start: Mapped[date | None] = mapped_column(Date)
    date_end: Mapped[date | None] = mapped_column(Date)
    counts: Mapped[dict] = mapped_column(JSONB)
    details: Mapped[dict] = mapped_column(JSONB)


class Player(Base):
    """Stable Cricsheet person identity with canonical Register names and aliases."""

    __tablename__ = "players"
    # Actual register identifiers can be short strings, not UUIDs.
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    unique_name: Mapped[str] = mapped_column(String(255))
    aliases: Mapped[list] = mapped_column(JSONB, default=list)
    register_import_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("source_imports.id"))


class ExternalPlayerID(Base):
    """Provider-to-Cricsheet identity mapping with an optional cached ESPN profile."""

    __tablename__ = "external_player_ids"
    __table_args__ = (UniqueConstraint("player_id", "provider", "external_id"),)
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    external_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"), index=True)
    source_import_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_imports.id"))
    profile: Mapped[dict | None] = mapped_column(JSONB)
    profile_retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Match(Base):
    """Historical match metadata with its source revision, checksum, and provenance."""

    __tablename__ = "matches"
    __table_args__ = (
        CheckConstraint("format IN ('odi','t20i')", name="format"),
        CheckConstraint("balls_per_over > 0", name="balls_per_over"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    format: Mapped[str] = mapped_column(String(8), index=True)
    date_start: Mapped[date] = mapped_column(Date, index=True)
    date_end: Mapped[date] = mapped_column(Date)
    teams: Mapped[list] = mapped_column(JSONB)
    venue: Mapped[str | None] = mapped_column(Text)
    balls_per_over: Mapped[int] = mapped_column(Integer)
    source_import_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_imports.id"))
    source_checksum: Mapped[str] = mapped_column(String(64))
    source_revision: Mapped[int] = mapped_column(Integer)
    data_version: Mapped[str] = mapped_column(String(16))
    missing: Mapped[list] = mapped_column(JSONB)
    info: Mapped[dict] = mapped_column(JSONB)


class MatchPlayer(Base):
    """Roster membership retaining both teams when source identities conflict."""

    __tablename__ = "match_players"
    match_id: Mapped[str] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), primary_key=True
    )
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"), primary_key=True)
    team: Mapped[str] = mapped_column(String(255), primary_key=True)
    source_name: Mapped[str] = mapped_column(String(255))


class Innings(Base):
    """Innings metadata, penalty runs, and completeness markers used by statistics."""

    __tablename__ = "innings"
    __table_args__ = (
        UniqueConstraint("match_id", "number"),
        CheckConstraint("number > 0", name="number"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    team: Mapped[str] = mapped_column(String(255))
    super_over: Mapped[bool] = mapped_column(Boolean)
    data_complete: Mapped[bool] = mapped_column(Boolean)
    missing: Mapped[list] = mapped_column(JSONB)
    penalty_pre: Mapped[int] = mapped_column(Integer, default=0)
    penalty_post: Mapped[int] = mapped_column(Integer, default=0)
    details: Mapped[dict] = mapped_column(JSONB)


class Delivery(Base):
    """Ordered source delivery with normalized runs and legal-ball metadata."""

    __tablename__ = "deliveries"
    __table_args__ = (
        UniqueConstraint("innings_id", "sequence"),
        CheckConstraint("sequence > 0 AND over_number >= 0 AND delivery_number > 0", name="order"),
        CheckConstraint("batter_runs >= 0 AND extras_runs >= 0 AND total_runs >= 0", name="runs"),
        CheckConstraint("total_runs = batter_runs + extras_runs", name="run_sum"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    innings_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("innings.id", ondelete="CASCADE"), index=True
    )
    # Sequence counts every delivery; legal_index is set only on legal deliveries.
    sequence: Mapped[int] = mapped_column(Integer)
    over_number: Mapped[int] = mapped_column(Integer)
    delivery_number: Mapped[int] = mapped_column(Integer)
    legal_index: Mapped[int | None] = mapped_column(Integer)
    batter_id: Mapped[str | None] = mapped_column(ForeignKey("players.id"), index=True)
    non_striker_id: Mapped[str | None] = mapped_column(ForeignKey("players.id"))
    bowler_id: Mapped[str | None] = mapped_column(ForeignKey("players.id"), index=True)
    # Unknown source runs remain NULL rather than being recorded as zero.
    batter_runs: Mapped[int | None] = mapped_column(Integer)
    extras_runs: Mapped[int | None] = mapped_column(Integer)
    total_runs: Mapped[int | None] = mapped_column(Integer)
    extras: Mapped[dict] = mapped_column(JSONB)
    is_legal: Mapped[bool | None] = mapped_column(Boolean)
    is_boundary: Mapped[bool | None] = mapped_column(Boolean)
    data_complete: Mapped[bool] = mapped_column(Boolean)
    source_data: Mapped[dict] = mapped_column(JSONB)


class Wicket(Base):
    """One dismissal entry; a single delivery can contain multiple dismissals."""

    __tablename__ = "wickets"
    __table_args__ = (UniqueConstraint("delivery_id", "ordinal"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    delivery_id: Mapped[int] = mapped_column(
        ForeignKey("deliveries.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    player_out_id: Mapped[str] = mapped_column(ForeignKey("players.id"), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    fielders: Mapped[list] = mapped_column(JSONB)


class Dataset(Base):
    """Conversation-owned computed data with coverage and source provenance."""

    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("id", "conversation_id"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    player_id: Mapped[str | None] = mapped_column(ForeignKey("players.id"))
    kind: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSONB)
    provenance: Mapped[dict] = mapped_column(JSONB)
    coverage: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChartSpec(Base):
    """Declarative chart linked to a dataset within the same conversation."""

    __tablename__ = "chart_specs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["dataset_id", "conversation_id"],
            ["datasets.id", "datasets.conversation_id"],
            ondelete="CASCADE",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    spec: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
