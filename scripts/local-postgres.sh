#!/usr/bin/env bash
# Local development only. Requires PostgreSQL binaries on PATH.
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
cluster_dir="$project_dir/.local/postgres"
socket_dir="$project_dir/.local/pg-socket"
port=55432
pgtool() {
  if [ -n "${POSTGRES_BIN_DIR:-}" ]; then
    "$POSTGRES_BIN_DIR/$1" "${@:2}"
  else
    "$@"
  fi
}
case "${1:-start}" in
  start)
    mkdir -p "$socket_dir"
    if [ ! -f "$cluster_dir/PG_VERSION" ]; then
      initdb_help="$(pgtool initdb --help)"
      if [[ "$initdb_help" == *"--set NAME=VALUE"* ]]; then
        # mmap avoids small macOS SysV shared-memory limits; PostgreSQL 17+ supports --set.
        pgtool initdb -D "$cluster_dir" -U cricket --auth-local=trust --auth-host=trust \
          --encoding=UTF8 --no-locale -c shared_memory_type=mmap \
          -c dynamic_shared_memory_type=mmap
      else
        pgtool initdb -D "$cluster_dir" -U cricket --auth-local=trust --auth-host=trust \
          --encoding=UTF8 --no-locale
      fi
    fi
    if ! pgtool pg_ctl -D "$cluster_dir" status >/dev/null 2>&1; then
      pgtool pg_ctl -D "$cluster_dir" -l "$project_dir/.local/postgres.log" \
        -o "-h 127.0.0.1 -p $port -k $socket_dir" -w start
    fi
    for database in cricket_analyst cricket_test; do
      exists="$(pgtool psql -h "$socket_dir" -p "$port" -U cricket -d postgres -Atc \
        "SELECT 1 FROM pg_database WHERE datname = '$database'")"
      if [ "$exists" != "1" ]; then
        pgtool createdb -h "$socket_dir" -p "$port" -U cricket "$database"
      fi
    done
    echo "Local PostgreSQL ready on 127.0.0.1:$port (cricket_analyst and cricket_test)."
    ;;
  stop)
    pgtool pg_ctl -D "$cluster_dir" -m fast -w stop
    ;;
  status)
    pgtool pg_ctl -D "$cluster_dir" status
    ;;
  *) echo "Usage: bash scripts/local-postgres.sh {start|stop|status}" >&2; exit 2 ;;
esac
