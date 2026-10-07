#!/usr/bin/env bash
# Convenient local defaults; use an explicit DATABASE_URL for another database.
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$project_dir"
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://cricket@127.0.0.1:55432/cricket_analyst}"
if [ "${1:-}" = "migrate" ]; then
  uv run --frozen --no-sync alembic upgrade head
else
  uv run --frozen --no-sync python -m cricket.cli "$@"
fi
