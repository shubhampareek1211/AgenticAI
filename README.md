# Cricket Analyst

A chat app for cricket questions. A Gemini model calls tools over ESPN and Cricsheet data, and every tool call (arguments and raw result) is shown in the UI next to the answer. It can also draw charts, take voice input, and read answers aloud.

- **Backend:** FastAPI, LiteLLM (Gemini on Vertex AI), SQLAlchemy and PostgreSQL
- **Frontend:** React, Vite and ECharts
- **Data:** men's ODI and T20I ball-by-ball files from [Cricsheet](https://cricsheet.org), player profiles from ESPN

## Tools the model can call

| Tool | Source | Purpose |
| --- | --- | --- |
| `get_career_stats` | Cricsheet files, fetched live, no database | ODI/T20I career batting and bowling totals |
| `get_player_data` | PostgreSQL + ESPN profile | Player statistics with filters; feeds charts |
| `get_match_data` | PostgreSQL | One match by ID or filters |
| `get_batter_bowler_data` | PostgreSQL | Batter vs bowler phase matchups |
| `get_squad_comparison` | PostgreSQL | Squad batting comparison |
| `analyze_wicket_response` | PostgreSQL | Run rate before and after a teammate's dismissal |
| `create_cricket_chart` | PostgreSQL | Bar, line, scatter, donut, heatmap, bubble, stacked area charts |
| `get_weather` | Open-Meteo | Current weather for a city |

Statistics cover the imported Cricsheet matches only. They are not official career records, and Tests are excluded.

## Requirements

- Python 3.10 or newer and [uv](https://docs.astral.sh/uv/)
- Node.js 20 or newer
- PostgreSQL 16 (the included `compose.yaml` runs one with Docker)
- A Google Cloud project with Vertex AI enabled and Application Default Credentials

## Run locally

```bash
# 1. Configuration. The app does not load .env itself, so export it in your shell.
cp .env.example .env            # set GOOGLE_CLOUD_PROJECT
set -a; source .env; set +a

# 2. Google credentials for Gemini
gcloud auth application-default login

# 3. Database
docker compose up -d
uv sync
bash scripts/data.sh migrate

# 4. Cricket data (about 30 MB download; the import takes several minutes)
bash scripts/data.sh download --formats odi t20i
bash scripts/data.sh import --formats odi t20i

# 5. Frontend
(cd frontend && npm ci && npm run build)

# 6. Start the app
uv run uvicorn app:app --reload
```

Open <http://127.0.0.1:8000>. Without step 4 the app still starts and `get_career_stats` still works, but the database-backed tools and charts return no data.

Frontend development with hot reload: run `npm run dev` in `frontend/` (port 5173) alongside the backend.

## Configuration

All settings are environment variables; see [.env.example](.env.example) for the full list.

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | local Postgres on port 55432 | PostgreSQL connection string (`postgresql+psycopg://`) |
| `GOOGLE_CLOUD_PROJECT` | none | Project used for Vertex AI |
| `GEMINI_MODEL` | `vertex_ai/gemini-3.5-flash-lite` | Any LiteLLM-supported model name |
| `VERTEX_LOCATION` | `global` | Vertex AI location |
| `MAX_TOOL_ROUNDS` | `5` | Model/tool rounds per message |
| `VOICE_ENABLED` / `TTS_ENABLED` | `false` | Optional dictation and read-aloud workers |

`APP_ENV=local` trusts the local machine and needs no sign-in. Setting `APP_ENV=pilot` or `production` turns on Firebase email authentication, which requires the `FIREBASE_*` variables.

## Optional voice

- **Dictation:** `bash scripts/voice.sh setup small.en` then `bash scripts/voice.sh serve small.en` runs a local whisper.cpp worker on port 8081. Set `VOICE_ENABLED=true`.
- **Read aloud:** the browser's speech synthesis works with no setup. For generated speech, build and run `deploy/kokoro` and set `TTS_ENABLED=true`.

## Docker

```bash
docker build -t cricket-analyst .
docker run --rm -p 8080:8080 -e DATABASE_URL=... -e GOOGLE_CLOUD_PROJECT=... cricket-analyst
```

Apply migrations first with `uv run alembic upgrade head` against the same database.

## Project layout

```
app.py            FastAPI app and routes
tools.py          Tool schemas shown to the model and the dispatcher
cricket/          Harness, tools, queries, importer, auth, voice services
migrations/       Alembic schema migrations
frontend/         React UI
scripts/          data.sh (migrate/download/import), local-postgres.sh, voice.sh
deploy/           Optional voice worker images
```
