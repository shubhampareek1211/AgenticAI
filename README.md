# Cricket Analyst

A chat app for cricket questions. A Gemini model calls tools over ESPN and Cricsheet data, and every tool call (arguments and raw result) is shown in the UI next to the answer. It can also draw charts, take voice input, and read answers aloud.

- **Backend:** FastAPI, LiteLLM (Gemini on Vertex AI), SQLAlchemy and PostgreSQL
- **Frontend:** React, Vite and Apache ECharts
- **Voice:** Whisper (whisper.cpp) for dictation, Kokoro for read-aloud
- **Data:** men's ODI ball-by-ball files from [Cricsheet](https://cricsheet.org), player profiles from ESPN

## Hosted demo

https://agenticai-git-3d4n5heeaq-uc.a.run.app, built and deployed from `main` by Cloud Build and backed by Cloud SQL. Sign in with a verified `@columbia.edu` email link. Coverage is men's ODI matches only.

**Expect a delay on the first sign-in.** Cloud Run starts a new instance when the app has been idle, so the first request, and the sign-in email that depends on it, can take up to a minute to arrive. Check spam if it is longer. One instance is kept warm during grading, and later requests are fast. The first voice transcription can also be slower while the Whisper worker loads its model.

## How a question is answered

The model never computes statistics itself. It chooses tools, the tools return structured results, and the UI shows each call (arguments and raw result) beside the answer.

| Step | Tool group | What it does | Source |
| --- | --- | --- | --- |
| 1. Find the data | **Cricket tools** | Career totals, player and match lookups, batter vs bowler matchups, squad comparisons, wicket-response analysis | PostgreSQL (imported [Cricsheet](https://cricsheet.org) ball-by-ball files), ESPN player profiles, Cricsheet files fetched live for career totals |
| 2. Draw it | **Chart tool** (`create_cricket_chart`) | Builds a chart from a dataset a cricket tool saved in the same conversation. Values are calculated in Python/SQL, then stored as a renderer-independent chart spec | PostgreSQL; rendered in the browser with [Apache ECharts](https://echarts.apache.org) (open source) |
| 3. Speak and listen | **Voice tools** (`transcribe_audio`, `read_aloud`) | Turns a recorded question into text and reads an answer aloud. Each call appears as a tool card in the chat, like the cricket tools | [Whisper](https://huggingface.co/ggerganov/whisper.cpp) (dictation) and [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M) (read aloud), run as private Cloud Run workers |

Cricket tools: `get_career_stats`, `get_player_data`, `get_match_data`, `get_batter_bowler_data`, `get_squad_comparison`, `analyze_wicket_response`.

Voice tools: `transcribe_audio` (Whisper) and `read_aloud` (Kokoro). They run in the browser flow rather than as model-selected tools, but appear in the chat the same way.

Chart types: bar, line, scatter, donut, heatmap, bubble, and stacked area/bar, with table and CSV views.

Statistics cover the imported Cricsheet matches only (men's ODI matches only). They are not official career records; T20Is and Tests are not included.

## Sample queries

1. `What are Virat Kohli's ODI career runs, average and strike rate?` calls `get_career_stats` and shows totals computed from Cricsheet files.
2. `Show Virat Kohli's ODI dismissal kinds as a donut chart` calls `get_player_data`, then `create_cricket_chart`.
3. `Load Cricsheet match 1022353 and show its run components by over` calls `get_match_data`, then `create_cricket_chart` (stacked area).

Follow-ups such as `Now show the same as a bar chart` reuse the conversation's saved data.

## Voice models

Voice is optional. Both models run as separate workers, and the browser UI shows each call as a tool card.

| Feature | Model | Hugging Face | Cloud Run worker |
| --- | --- | --- | --- |
| Dictation (speech to text) | OpenAI Whisper `small.en` / `small`, served by whisper.cpp | [ggerganov/whisper.cpp](https://huggingface.co/ggerganov/whisper.cpp) | `agenticai-voice-pilot` (https://agenticai-voice-pilot-3d4n5heeaq-uc.a.run.app), IAM-protected |
| Read aloud (text to speech) | Kokoro 82M, English only | [hexgrad/Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) | `agenticai-kokoro-pilot` (https://agenticai-kokoro-pilot-3d4n5heeaq-uc.a.run.app), IAM-protected |

The Cloud Run workers accept only Google ID tokens from the app's service account, so they are not callable from a browser or `curl` without credentials. Hindi dictation needs the multilingual `small` model; Hindi read-aloud falls back to the browser voice.

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
bash scripts/data.sh download --formats odi
bash scripts/data.sh import --formats odi

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

## Run the voice workers locally

- **Dictation:** `bash scripts/voice.sh setup small.en` then `bash scripts/voice.sh serve small.en` starts whisper.cpp on port 8081. Set `VOICE_ENABLED=true`.
- **Read aloud:** the browser's speech synthesis works with no setup. For Kokoro, build and run `deploy/kokoro` and set `TTS_ENABLED=true`.

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
