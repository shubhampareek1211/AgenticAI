#!/usr/bin/env python3
"""Bootstrap the pilot Cloud SQL database from public Cricsheet archives only.

Run `plan` first, then `apply --confirm-project phonic-weaver-475017-n1`.
No passwords, OAuth tokens, database dumps, or user conversations are written
to disk. The only import inputs are checksum-verified Cricsheet artifacts.
"""

import argparse
import base64
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cricket.sources import SOURCE_URLS, artifact_metadata

PROJECT = "phonic-weaver-475017-n1"
REGION = "us-central1"
INSTANCE = "agenticai-pilot-pg16"
DATABASE = "cricket_pilot"
APP_ROLE = "cricket_app"
SECRET = "agenticai-pilot-database-url"
PROXY_PORT = 55433
SOURCE_NAMES = ("people.csv", "names.csv", "odis_male_json.zip", "t20s_male_json.zip")
PRIVATE_TABLES = (
    "conversations",
    "messages",
    "tool_calls",
    "datasets",
    "chart_specs",
    "voice_daily_usage",
)
CRICKET_TABLES = (
    "source_imports",
    "players",
    "external_player_ids",
    "matches",
    "match_players",
    "innings",
    "deliveries",
    "wickets",
)


class BootstrapError(RuntimeError):
    """Safe-to-display failure; never include tokens or passwords."""


def command(args: list[str], *, env: dict | None = None) -> str:
    result = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    if result.returncode:
        raise BootstrapError(f"Command failed: {args[0]} {args[1] if len(args) > 1 else ''}")
    return result.stdout.strip()


def import_command(args: list[str], env: dict) -> None:
    """Display only the importer's numeric progress; suppress error details."""
    process = subprocess.Popen(
        args,
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stderr is not None
    for line in process.stderr:
        match = re.fullmatch(r"(odi|t20i): (\d+)/(\d+)\n?", line)
        if match and (int(match[2]) == int(match[3]) or int(match[2]) % 250 == 0):
            print(line.strip(), flush=True)
    if process.wait():
        raise BootstrapError("Cricsheet import failed; no private data was copied.")


def active_identity() -> None:
    account = command(
        ["gcloud", "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"]
    )
    if account != "sp4553@columbia.edu":
        raise BootstrapError("Active gcloud account must be sp4553@columbia.edu.")


def token() -> str:
    value = command(["gcloud", "auth", "print-access-token"])
    if not value:
        raise BootstrapError("gcloud returned no access token; refresh the login.")
    return value


def api(method: str, url: str, access_token: str, body: dict | None = None) -> dict | None:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and method == "GET":
            return None
        raise BootstrapError(f"Google API {method} failed with HTTP {exc.code}.") from None
    except (urllib.error.URLError, TimeoutError):
        raise BootstrapError("Google API request failed; check login and network.") from None


def sql_url(password: str, *, cloud_run: bool) -> str:
    if cloud_run:
        return URL.create(
            "postgresql+psycopg",
            username=APP_ROLE,
            password=password,
            database=DATABASE,
            query={"host": f"/cloudsql/{PROJECT}:{REGION}:{INSTANCE}"},
        ).render_as_string(hide_password=False)
    return URL.create(
        "postgresql+psycopg",
        username=APP_ROLE,
        password=password,
        host="127.0.0.1",
        port=PROXY_PORT,
        database=DATABASE,
    ).render_as_string(hide_password=False)


def verify_secret_url(value: str) -> str:
    try:
        parsed = make_url(value)
    except (ArgumentError, ValueError):
        raise BootstrapError("Existing database secret has an invalid URL.") from None
    if (
        parsed.drivername != "postgresql+psycopg"
        or parsed.username != APP_ROLE
        or parsed.database != DATABASE
        or parsed.host is not None
        or parsed.query.get("host") != f"/cloudsql/{PROJECT}:{REGION}:{INSTANCE}"
        or not parsed.password
    ):
        raise BootstrapError("Existing database secret targets a different database or role.")
    return parsed.password


def source_manifest(directory: Path) -> dict[str, str]:
    result = {}
    for name in SOURCE_NAMES:
        path = directory / name
        if not path.is_file():
            raise BootstrapError(f"Missing public Cricsheet artifact: {name}.")
        try:
            metadata = artifact_metadata(path)
        except (OSError, ValueError):
            raise BootstrapError(f"Checksum verification failed for {name}.") from None
        if metadata.get("source_url") != SOURCE_URLS[name]:
            raise BootstrapError(f"Unexpected source URL for {name}.")
        result[name] = metadata["checksum"]
    return result


def instance_info(access_token: str) -> dict:
    url = f"https://sqladmin.googleapis.com/v1/projects/{PROJECT}/instances/{INSTANCE}"
    value = api("GET", url, access_token)
    if value is None:
        raise BootstrapError("Expected Cloud SQL instance does not exist.")
    settings = value.get("settings", {})
    if (
        value.get("databaseVersion") != "POSTGRES_16"
        or value.get("region") != REGION
        or settings.get("tier") != "db-g1-small"
        or settings.get("ipConfiguration", {}).get("authorizedNetworks")
    ):
        raise BootstrapError("Cloud SQL instance configuration differs from the priced pilot.")
    return value


def wait_operation(operation: dict, access_token: str, deadline_seconds: int = 300) -> None:
    name = operation.get("name")
    if not name:
        raise BootstrapError("Cloud SQL returned an operation without a name.")
    url = f"https://sqladmin.googleapis.com/v1/projects/{PROJECT}/operations/{name}"
    end = time.monotonic() + deadline_seconds
    while time.monotonic() < end:
        current = api("GET", url, access_token)
        if current and current.get("status") == "DONE":
            if current.get("error"):
                raise BootstrapError("Cloud SQL operation failed; inspect it in the console.")
            return
        time.sleep(3)
    raise BootstrapError("Cloud SQL operation did not complete within five minutes.")


def secret_url(access_token: str) -> str | None:
    path = f"projects/{PROJECT}/secrets/{SECRET}"
    if api("GET", f"https://secretmanager.googleapis.com/v1/{path}", access_token) is None:
        return None
    payload = api(
        "GET",
        f"https://secretmanager.googleapis.com/v1/{path}/versions/latest:access",
        access_token,
    )
    if payload is None:
        # A previous run may have created the secret but failed before adding its first version.
        return None
    try:
        return base64.b64decode(payload["payload"]["data"], validate=True).decode()
    except (KeyError, TypeError, ValueError, UnicodeDecodeError):
        raise BootstrapError("Existing database secret could not be decoded.") from None


def publish_secret(access_token: str, value: str) -> None:
    parent = f"https://secretmanager.googleapis.com/v1/projects/{PROJECT}/secrets"
    if api("GET", f"{parent}/{SECRET}", access_token) is None:
        api(
            "POST",
            f"{parent}?secretId={SECRET}",
            access_token,
            {"replication": {"userManaged": {"replicas": [{"location": REGION}]}}},
        )
    api(
        "POST",
        f"{parent}/{SECRET}:addVersion",
        access_token,
        {"payload": {"data": base64.b64encode(value.encode()).decode()}},
    )


def free_port() -> None:
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", PROXY_PORT))
        except OSError:
            raise BootstrapError(f"Local proxy port {PROXY_PORT} is already in use.") from None


def start_proxy(path: Path) -> subprocess.Popen:
    if not path.is_file() or not os.access(path, os.X_OK):
        raise BootstrapError("Pinned Cloud SQL Auth Proxy binary is missing or not executable.")
    free_port()
    process = subprocess.Popen(
        [
            str(path),
            "--gcloud-auth",
            "--address=127.0.0.1",
            f"--port={PROXY_PORT}",
            f"{PROJECT}:{REGION}:{INSTANCE}",
        ],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    end = time.monotonic() + 30
    while time.monotonic() < end:
        if process.poll() is not None:
            raise BootstrapError("Cloud SQL Auth Proxy exited before accepting connections.")
        try:
            with socket.create_connection(("127.0.0.1", PROXY_PORT), timeout=0.3):
                return process
        except OSError:
            time.sleep(0.2)
    process.terminate()
    raise BootstrapError("Cloud SQL Auth Proxy did not start within 30 seconds.")


def stop_proxy(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def connect(user: str, password: str, dbname: str):
    return psycopg.connect(
        host="127.0.0.1",
        port=PROXY_PORT,
        user=user,
        password=password,
        dbname=dbname,
        connect_timeout=15,
        autocommit=True,
    )


def create_role_and_database(
    admin_password: str, app_password: str, *, rotate_role_password: bool
) -> None:
    with connect("postgres", admin_password, "postgres") as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT rolcanlogin, rolcreatedb, rolcreaterole, rolsuper, "
            "pg_has_role(%s, 'cloudsqlsuperuser', 'member') "
            "FROM pg_roles WHERE rolname = %s",
            (APP_ROLE, APP_ROLE),
        )
        role = cur.fetchone()
        if role and role != (True, False, False, False, False):
            raise BootstrapError("Existing app role has unexpected elevated privileges.")
        cur.execute(
            "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = %s", (DATABASE,)
        )
        database = cur.fetchone()
        if database and database != (APP_ROLE,):
            raise BootstrapError("Existing pilot database has an unexpected owner.")
        if role and rotate_role_password:
            cur.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    sql.Identifier(APP_ROLE), sql.Literal(app_password)
                )
            )
        elif not role:
            cur.execute(
                sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD {}").format(
                    sql.Identifier(APP_ROLE), sql.Literal(app_password)
                )
            )
        if not database:
            # Cloud SQL's postgres administrator is not a PostgreSQL superuser.
            # PostgreSQL requires membership in a role to create a database
            # owned by that role; revoke the temporary membership afterward.
            cur.execute(sql.SQL("GRANT {} TO postgres").format(sql.Identifier(APP_ROLE)))
            try:
                cur.execute(
                    sql.SQL("CREATE DATABASE {} OWNER {}").format(
                        sql.Identifier(DATABASE), sql.Identifier(APP_ROLE)
                    )
                )
            finally:
                cur.execute(sql.SQL("REVOKE {} FROM postgres").format(sql.Identifier(APP_ROLE)))


def verify_role_and_database(password: str) -> None:
    with connect(APP_ROLE, password, DATABASE) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT rolcanlogin, rolcreatedb, rolcreaterole, rolsuper, "
            "pg_has_role(%s, 'cloudsqlsuperuser', 'member') "
            "FROM pg_roles WHERE rolname = %s",
            (APP_ROLE, APP_ROLE),
        )
        if cur.fetchone() != (True, False, False, False, False):
            raise BootstrapError("App role has unexpected elevated privileges.")
        cur.execute(
            "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = %s", (DATABASE,)
        )
        if cur.fetchone() != (APP_ROLE,):
            raise BootstrapError("Pilot database has an unexpected owner.")


def table_counts(password: str) -> dict[str, int]:
    with connect(APP_ROLE, password, DATABASE) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.alembic_version')")
        if cur.fetchone()[0] is None:
            return {}
        result = {}
        for name in PRIVATE_TABLES + CRICKET_TABLES:
            cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(name)))
            result[name] = cur.fetchone()[0]
        return result


def migrate_and_import(password: str, source_dir: Path) -> dict[str, int]:
    env = dict(os.environ)
    env["DATABASE_URL"] = sql_url(password, cloud_run=False)
    python = ROOT / ".venv/bin/python"
    if not python.is_file():
        raise BootstrapError("Project .venv/bin/python is missing.")
    before = table_counts(password)
    command([str(python), "-m", "alembic", "upgrade", "head"], env=env)
    before = table_counts(password) if not before else before
    import_command(
        [
            str(python),
            "-m",
            "cricket.cli",
            "import",
            "--source-dir",
            str(source_dir),
            "--formats",
            "odi",
            "t20i",
        ],
        env,
    )
    after = table_counts(password)
    # The deployed application may create conversations while the public
    # reference-data import runs. Growth is legitimate; a decrease is not.
    if any(after[name] < before[name] for name in PRIVATE_TABLES):
        raise BootstrapError("Private application table counts decreased during cricket import.")
    if any(after[name] == 0 for name in ("players", "matches", "deliveries")):
        raise BootstrapError("Cricket import did not populate required reference tables.")
    return after


def apply(args, access_token: str) -> dict:
    existing = secret_url(access_token)
    first_run = existing is None
    password = verify_secret_url(existing) if existing else secrets.token_urlsafe(48)
    proxy = None
    try:
        admin_password = secrets.token_urlsafe(48)
        url = f"https://sqladmin.googleapis.com/v1/projects/{PROJECT}/instances/{INSTANCE}/users?name=postgres"
        operation = api("PUT", url, access_token, {"password": admin_password})
        wait_operation(operation, access_token)
        if args.existing_proxy:
            with socket.create_connection(("127.0.0.1", PROXY_PORT), timeout=3):
                pass
        else:
            proxy = start_proxy(args.proxy)
        create_role_and_database(admin_password, password, rotate_role_password=first_run)
        verify_role_and_database(password)
        if first_run:
            publish_secret(access_token, sql_url(password, cloud_run=True))
        counts = migrate_and_import(password, args.source_dir)
        return {"database": DATABASE, "secret": SECRET, "counts": counts}
    except psycopg.Error as exc:
        state = exc.sqlstate if exc.sqlstate and exc.sqlstate.isalnum() else "unknown"
        raise BootstrapError(
            f"PostgreSQL operation failed (SQLSTATE {state}); inspect Cloud SQL and retry safely."
        ) from None
    finally:
        if proxy is not None:
            stop_proxy(proxy)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("action", choices=("plan", "apply"))
    result.add_argument("--source-dir", type=Path, default=ROOT / ".local/sources")
    result.add_argument(
        "--proxy", type=Path, default=ROOT / ".local/cloud-sql/cloud-sql-proxy.darwin.arm64"
    )
    result.add_argument("--existing-proxy", action="store_true")
    result.add_argument("--confirm-project", default="")
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        sources = source_manifest(args.source_dir)
        active_identity()
        access_token = token()
        instance = instance_info(access_token)
        if instance.get("state") != "RUNNABLE":
            raise BootstrapError("Cloud SQL instance is not RUNNABLE yet.")
        if args.action == "plan":
            print(
                json.dumps(
                    {
                        "project": PROJECT,
                        "instance": INSTANCE,
                        "database": DATABASE,
                        "secret": SECRET,
                        "source_checksums": sources,
                        "import": "public Cricsheet archives only",
                    },
                    indent=2,
                )
            )
            return 0
        if args.confirm_project != PROJECT:
            raise BootstrapError(f"Pass --confirm-project {PROJECT} to apply.")
        print(json.dumps(apply(args, access_token), indent=2))
        return 0
    except BootstrapError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
