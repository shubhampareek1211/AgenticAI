#!/bin/sh
set -eu

threads=${WHISPER_THREADS:-2}
case "$threads" in
  ''|*[!0-9]*) echo 'WHISPER_THREADS must be an integer from 1 to 8' >&2; exit 2 ;;
esac
if [ "$threads" -lt 1 ] || [ "$threads" -gt 8 ]; then
  echo 'WHISPER_THREADS must be an integer from 1 to 8' >&2
  exit 2
fi

exec /usr/local/bin/whisper-server \
  --host 0.0.0.0 \
  --port "${PORT:-8080}" \
  --model /models/model.bin \
  --threads "$threads"
