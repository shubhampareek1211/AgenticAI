"""Explicit PostgreSQL connections. Importing this module never connects or migrates."""

import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session, sessionmaker


class ConfigurationError(ValueError):
    pass


def database_url(value: str | None = None) -> str:
    value = value or os.environ.get("DATABASE_URL")
    if not value:
        raise ConfigurationError("Set DATABASE_URL to a PostgreSQL connection string.")
    try:
        url = make_url(value)
    except (ArgumentError, ValueError):
        raise ConfigurationError("DATABASE_URL is not a valid PostgreSQL URL.") from None
    if url.get_backend_name() != "postgresql":
        raise ConfigurationError("This application requires PostgreSQL.")
    if url.drivername in {"postgresql", "postgresql+psycopg2"}:
        url = url.set(drivername="postgresql+psycopg")
    if url.drivername != "postgresql+psycopg":
        raise ConfigurationError("Use the postgresql+psycopg driver in DATABASE_URL.")
    return url.render_as_string(hide_password=False)


def make_engine(value: str | None = None) -> Engine:
    return create_engine(database_url(value), pool_pre_ping=True, pool_size=5, max_overflow=0)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
