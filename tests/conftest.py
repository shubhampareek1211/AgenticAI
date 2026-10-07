import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

FIXTURES = Path(__file__).parent / "fixtures"


def pytest_addoption(parser):
    parser.addoption(
        "--require-postgres",
        action="store_true",
        help="Fail instead of skipping when TEST_DATABASE_URL is absent.",
    )


@pytest.fixture(scope="session")
def pg_engine(request):
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        if request.config.getoption("--require-postgres"):
            pytest.fail("Set TEST_DATABASE_URL to a dedicated PostgreSQL test database.")
        pytest.skip("TEST_DATABASE_URL not configured; PostgreSQL integration tests skipped.")
    url = make_url(value)
    if url.get_backend_name() != "postgresql" or not (url.database or "").endswith("_test"):
        pytest.fail(
            "TEST_DATABASE_URL must use PostgreSQL and a dedicated database ending in _test."
        )
    engine = create_engine(url, pool_pre_ping=True)
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    yield engine
    engine.dispose()


@pytest.fixture
def session(pg_engine):
    with pg_engine.connect() as connection:
        transaction = connection.begin()
        with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
            yield session
        transaction.rollback()
