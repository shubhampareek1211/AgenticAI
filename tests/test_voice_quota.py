"""Pilot voice limits must hold across processes and fail closed on storage errors."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError

from cricket.db import ConfigurationError
from cricket.models import VoiceDailyUsage
from cricket.voice_quota import (
    VoiceQuotaExceeded,
    VoiceQuotaSettings,
    VoiceQuotaUnavailable,
    reserve_voice_attempt,
)


def test_quota_configuration_is_positive_and_explicit(monkeypatch):
    monkeypatch.delenv("VOICE_USER_DAILY_LIMIT", raising=False)
    monkeypatch.delenv("VOICE_GLOBAL_DAILY_LIMIT", raising=False)
    assert VoiceQuotaSettings.from_env() == VoiceQuotaSettings(10, 100)
    monkeypatch.setenv("VOICE_USER_DAILY_LIMIT", "3")
    monkeypatch.setenv("VOICE_GLOBAL_DAILY_LIMIT", "25")
    assert VoiceQuotaSettings.from_env() == VoiceQuotaSettings(3, 25)
    monkeypatch.setenv("VOICE_USER_DAILY_LIMIT", "0")
    with pytest.raises(ConfigurationError):
        VoiceQuotaSettings.from_env()
    monkeypatch.setenv("VOICE_USER_DAILY_LIMIT", "invalid")
    with pytest.raises(ConfigurationError):
        VoiceQuotaSettings.from_env()


def test_invalid_identity_or_database_fails_closed():
    class BrokenEngine:
        def begin(self):
            raise SQLAlchemyError("database offline")

    with pytest.raises(VoiceQuotaUnavailable):
        reserve_voice_attempt(None, "firebase:project:alice")
    with pytest.raises(VoiceQuotaUnavailable):
        reserve_voice_attempt(BrokenEngine(), "")
    with pytest.raises(VoiceQuotaUnavailable):
        reserve_voice_attempt(BrokenEngine(), "firebase:project:alice")


@pytest.fixture
def empty_quota(pg_engine):
    with pg_engine.begin() as connection:
        connection.execute(delete(VoiceDailyUsage))
    yield pg_engine
    with pg_engine.begin() as connection:
        connection.execute(delete(VoiceDailyUsage))


def test_user_and_global_limits_commit_atomically(empty_quota):
    engine = empty_quota
    settings = VoiceQuotaSettings(user_daily_limit=2, global_daily_limit=3)
    alice = "firebase:project:alice"
    bob = "firebase:project:bob"
    first = reserve_voice_attempt(engine, alice, settings)
    second = reserve_voice_attempt(engine, alice, settings)
    assert (first.user_used, first.global_used) == (1, 1)
    assert (second.user_used, second.global_used) == (2, 2)
    assert first.reset_at.tzinfo == timezone.utc
    assert 0 < (first.reset_at - datetime.now(timezone.utc)).total_seconds() <= 86400

    with pytest.raises(VoiceQuotaExceeded) as user_error:
        reserve_voice_attempt(engine, alice, settings)
    assert user_error.value.scope == "user"
    assert user_error.value.status == 429
    assert 1 <= user_error.value.retry_after_seconds <= 86400

    assert reserve_voice_attempt(engine, bob, settings).global_used == 3
    with pytest.raises(VoiceQuotaExceeded) as global_error:
        reserve_voice_attempt(engine, bob, settings)
    assert global_error.value.scope == "global"

    with engine.connect() as connection:
        rows = connection.execute(
            select(VoiceDailyUsage.scope, VoiceDailyUsage.owner_id, VoiceDailyUsage.used).where(
                VoiceDailyUsage.period_start == datetime.now(timezone.utc).date()
            )
        ).all()
    assert set(rows) == {("global", "*", 3), ("user", alice, 2), ("user", bob, 1)}


def test_concurrent_replicas_cannot_exceed_user_limit(empty_quota):
    settings = VoiceQuotaSettings(user_daily_limit=4, global_daily_limit=20)

    def reserve(_):
        try:
            return reserve_voice_attempt(empty_quota, "firebase:project:alice", settings)
        except VoiceQuotaExceeded as exc:
            return exc

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(12)))
    assert sum(not isinstance(result, VoiceQuotaExceeded) for result in results) == 4
    assert sum(isinstance(result, VoiceQuotaExceeded) for result in results) == 8
    with empty_quota.connect() as connection:
        rows = connection.execute(
            select(VoiceDailyUsage.scope, VoiceDailyUsage.used).where(
                VoiceDailyUsage.period_start == datetime.now(timezone.utc).date()
            )
        ).all()
    assert set(rows) == {("global", 4), ("user", 4)}
