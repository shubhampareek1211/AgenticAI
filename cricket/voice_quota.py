"""Cross-replica voice attempt limits backed by atomic PostgreSQL counters."""

import math
import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from cricket.db import ConfigurationError
from cricket.models import VoiceDailyUsage

GLOBAL_OWNER = "*"


@dataclass(frozen=True)
class VoiceQuotaSettings:
    user_daily_limit: int = 10
    global_daily_limit: int = 100

    def __post_init__(self):
        if self.user_daily_limit < 1 or self.global_daily_limit < 1:
            raise ConfigurationError("Voice daily quota limits must be positive integers.")

    @classmethod
    def from_env(cls):
        try:
            return cls(
                user_daily_limit=int(os.getenv("VOICE_USER_DAILY_LIMIT", "10")),
                global_daily_limit=int(os.getenv("VOICE_GLOBAL_DAILY_LIMIT", "100")),
            )
        except ValueError as exc:
            raise ConfigurationError("Voice daily quota limits must be positive integers.") from exc


class VoiceQuotaExceeded(Exception):
    code = "voice_quota_exceeded"
    status = 429
    detail = "Daily voice limit reached. Try again after the UTC reset."

    def __init__(self, scope: str, retry_after_seconds: int, reset_at: datetime):
        super().__init__(self.detail)
        self.scope = scope
        self.retry_after_seconds = retry_after_seconds
        self.reset_at = reset_at


class VoiceQuotaUnavailable(Exception):
    code = "voice_quota_unavailable"
    status = 503
    detail = "Voice admission is temporarily unavailable."

    def __init__(self):
        super().__init__(self.detail)


@dataclass(frozen=True)
class VoiceQuotaReservation:
    user_used: int
    global_used: int
    reset_at: datetime


def _increment(connection, period_start, scope, owner_id, limit):
    statement = (
        insert(VoiceDailyUsage)
        .values(period_start=period_start, scope=scope, owner_id=owner_id, used=1)
        .on_conflict_do_update(
            index_elements=[
                VoiceDailyUsage.period_start,
                VoiceDailyUsage.scope,
                VoiceDailyUsage.owner_id,
            ],
            set_={"used": VoiceDailyUsage.used + 1},
            where=VoiceDailyUsage.used < limit,
        )
        .returning(VoiceDailyUsage.used)
    )
    return connection.scalar(statement)


def reserve_voice_attempt(
    engine, owner_id: str, settings: VoiceQuotaSettings | None = None
) -> VoiceQuotaReservation:
    """Count one admitted upload attempt; both counters commit or neither does.

    Call after local admission but before reading the upload body. A rejected or
    malformed recording still consumes the reservation to bound abuse.
    """
    if not isinstance(owner_id, str) or not owner_id or len(owner_id) > 255 or engine is None:
        raise VoiceQuotaUnavailable()
    try:
        limits = settings or VoiceQuotaSettings.from_env()
        with engine.begin() as connection:
            # Avoid holding an upload slot indefinitely if the database is contended.
            connection.execute(text("SET LOCAL lock_timeout = '2s'"))
            connection.execute(text("SET LOCAL statement_timeout = '3s'"))
            now = connection.scalar(select(text("CURRENT_TIMESTAMP"))).astimezone(timezone.utc)
            reset_at = datetime.combine(now.date() + timedelta(days=1), time.min, timezone.utc)
            retry_after = max(1, math.ceil((reset_at - now).total_seconds()))

            # Lock ordering is always global, then user, to avoid deadlocks.
            global_used = _increment(
                connection, now.date(), "global", GLOBAL_OWNER, limits.global_daily_limit
            )
            if global_used is None:
                raise VoiceQuotaExceeded("global", retry_after, reset_at)
            user_used = _increment(
                connection, now.date(), "user", owner_id, limits.user_daily_limit
            )
            if user_used is None:
                # Raising inside engine.begin rolls back the global increment too.
                raise VoiceQuotaExceeded("user", retry_after, reset_at)
            return VoiceQuotaReservation(user_used, global_used, reset_at)
    except (SQLAlchemyError, ConfigurationError, AttributeError) as exc:
        raise VoiceQuotaUnavailable() from exc
