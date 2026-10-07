#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$project_dir"
export APP_ENV=test
export TEST_DATABASE_URL="${TEST_DATABASE_URL:-postgresql+psycopg://cricket@127.0.0.1:55432/cricket_test}"
uv run --frozen --no-sync pytest --require-postgres "$@"
