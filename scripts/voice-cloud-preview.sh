#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$project_dir"
if ! command -v ffmpeg >/dev/null && [[ -x "$project_dir/.local/voice/bin/ffmpeg" ]]; then
  export PATH="$project_dir/.local/voice/bin:$PATH"
fi
command -v ffmpeg >/dev/null || { echo 'Install FFmpeg before starting the voice preview.' >&2; exit 1; }
[[ -x .venv/bin/uvicorn ]] || { echo 'Run uv sync first.' >&2; exit 1; }
export APP_ENV=local
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://cricket@127.0.0.1:55432/cricket_analyst}"
export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-phonic-weaver-475017-n1}"
export VERTEX_LOCATION="${VERTEX_LOCATION:-global}"
export VOICE_ENABLED=true VOICE_LANGUAGES=en,hi,auto
export STT_BASE_URL="${STT_BASE_URL:-http://127.0.0.1:8084}" STT_AUTH_MODE=none
export TTS_ENABLED=true TTS_BASE_URL="${TTS_BASE_URL:-http://127.0.0.1:8083}" TTS_AUTH_MODE=none
echo 'Local evaluation app: http://127.0.0.1:8002 (requires authenticated worker proxies on 8084 and 8083).'
exec .venv/bin/uvicorn app:app --host 127.0.0.1 --port 8002
