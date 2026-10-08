# Cricket Analyst Agent — Handoff Guide

Current release status (2026-10-07): the user clarified that **all** database
operations must go through the hosted endpoint. The public Columbia pilot
gateway and private data backend are deployed; only the backend connects to
the restricted Cloud SQL database. The cricket source import and signed-in
cloud checks are in progress. Read [deploy/PILOT_RELEASE.md](deploy/PILOT_RELEASE.md)
and [deploy/PILOT_CD.md](deploy/PILOT_CD.md) first. Entries below this paragraph
record earlier phases and should be read as historical notes.
The protected `pilot-release` branch has a passing keyless GitHub Actions
deployment; only the source import and signed-in cloud verification remain
before the release gate can be assessed. The user declined a budget alert.

Latest authentication/voice work (2026-10-04): see
`deploy/FIREBASE_SETUP_2026-10-04.md`. The user approved the exact IAM grants;
they are now applied. Combined signed-in preview: http://localhost:8004,
PID 68352, launched with `--config .local/pilot-auth/web-config.json
--speech-config .local/pilot-auth/voice-config.json`. Both speech workers are
called directly using app-identity IAM tokens; no private key. Local migration
0004 is applied. Browser chat, Kokoro playback, and microphone start/cancel
were observed. Next: resolve the new endpoint requirement before database and
separate cloud app prerequisites.
Preserve the private ignored impersonated ADC and never include it in a build.

Latest user decision (2026-10-04): speech limitations accepted for the pilot;
no larger-CPU tests. Proceed with Firebase email-link authentication, then
priced database/application prerequisites. Preserve both 2-vCPU workers and
the original benchmark evidence. See the newest decision in `STATE.md`.

Latest real-speaker evaluation (2026-10-04): read
`evaluation/voice/REAL_RECORDINGS_2026-10-04.md` and
`evaluation/voice/STEP1_RESULTS_2026-10-04.md` first. The 12 recordings and
checkpointed results are under ignored `.local/voice/recordings/2026-10-04/`.
Do not treat HTTP 200 as an accuracy pass or repeat cloud uploads automatically.

Earlier overall review (2026-10-04): read `PROJECT_REVIEW_2026-10-04.md`
and the newest section in `STATE.md` first. The local prototype and private
cloud speech APIs work; the complete Columbia cloud application is not
released. The next task is measured speech acceptance and end-to-end cloud
integration, followed by the Firebase/database/application release gates.

## 2026-10-03 update: Kokoro cloud API

The private Kokoro endpoint is deployed and tested; start with the newest
section in `STATE.md` and `deploy/kokoro/CLOUD_API.md`. Revision
`agenticai-kokoro-pilot-00001-vt5` uses 2 vCPU/2 GiB, min 0/max 3, IAM
authentication, and the checksum-verified model. It can be called through
the authenticated localhost proxy on port 8083 or directly with a Google
ID token. The existing cloud app and local preview defaults are unchanged.
Warm long-sentence and startup timings miss the current targets, so this is
a developer/pilot API and is not a completed public application release.
See the record for exact image digest, commands, benchmark evidence, pricing,
and remaining gates. Changes remain uncommitted; preserve prior work.

Current pilot status (2026-10-02): implementation code and private Cloud Run
Whisper candidate exist; the user-facing Columbia pilot is not released.
Read `deploy/PILOT_RELEASE.md` and `deploy/VOICE_CANDIDATE_2026-10-02.md` first.
The 223-test PostgreSQL suite and 36 frontend tests pass. An 8-vCPU/eight-thread
candidate got under five seconds on two short synthetic clips at the worker,
but quality errors remain and the real-speaker/end-to-end gates are untested.
Do not provision the pilot database or expose the app until a measured revision
passes the stated gates.
The older handoff entries below are historical.

## Start here

This file is for a new Codex or VS Code implementation session. Read these files in order:

1. `deploy/PILOT_RELEASE.md` and `deploy/VOICE_CANDIDATE_2026-10-02.md` —
   current cloud voice pilot procedure and measurements.
2. `BUILD_PLAN.md` — the broader cricket analyst implementation plan.
3. `STATE.md` — what is actually true now and what should happen next.
4. This file — operating instructions for continuing safely.

Then inspect the real checkout before editing. Repository state is the source of truth when it conflicts with an old status entry.

## Copyable prompt for a new agent

```text
Read BUILD_PLAN.md, STATE.md, and HANDOFF.md completely. Verify the current checkout, branch, commit, dirty files, and repository instructions before editing. Resume the exact next unfinished phase recorded in STATE.md. Preserve existing user work, follow the interfaces and acceptance criteria in BUILD_PLAN.md, run the required checks for the phase, and update STATE.md before ending. Do not repeat completed setup unless evidence shows it is broken. Do not provision paid cloud resources or deploy unless the current requested phase explicitly includes it.
```

## Current handoff target

Phases 0–4, both local chart expansions, the local multilingual voice path, and browser read-aloud are complete. Seven tool schemas cover weather, player lookup, match lookup, batter–bowler lookup, squad comparison, wicket analysis, and chart creation. The React/TypeScript/Vite frontend renders new and legacy saved charts through Apache ECharts. The full suite passes 210 Python tests and 28 frontend tests. See `STATE.md` for the latest verification. Use this working checkout:

Local manual testing continues at `http://127.0.0.1:8000`: Uvicorn PID `31972` serves built `frontend/dist` without auto-reload, using existing Docker PostgreSQL on `55432`. The separate local multilingual whisper.cpp `small` worker is PID `31907` on `127.0.0.1:8081`; logs are under `.local/voice/`. The app exposes English, Hindi, and Auto-detect with `VOICE_LANGUAGES=en,hi,auto`. Rebuild the frontend and restart Uvicorn after code changes.

`/Users/shubhampareek/Downloads/gemini-hello-world/gemini-web-tool-calling/AgenticAI`

The development branch is `feat/cricket-analyst`, based on deployment commit `31798f36b5064cc2d7c5e5936a3f5dfe5e10b79a`. It has no upstream. Verify current repository state rather than assuming these recorded values remain current.

Latest request (2026-10-01): implement voice input with separate backend,
frontend, and worker agent lanes, and start a local server. The record/stop/
transcribe/edit/send flow, bounded FastAPI API, FFmpeg conversion, whisper.cpp
worker, Docker build, and local synthetic base/small model comparison are done.
The user subsequently clarified they also wanted spoken assistant answers.
Per-answer and opt-in automatic read-aloud now use browser speech synthesis;
the browser/OS voice is not a controlled open-source TTS worker. Read-aloud
has a Stop control and does not play restored history.
`VOICE_INTEGRATION_PLAN.md` records the intended contract;
`VOICE_BENCHMARK.md` records the earlier English-only synthetic comparison.
Latest language follow-up: `small.en` was replaced locally with multilingual
`small` so the recording control can offer English, Hindi, and Auto-detect.
The Hindi smoke returned Devanagari but misspelled Virat Kohli. Treat automatic
language selection as dominant-language detection, not reliable code-switching.
The older `small.en` benchmark remains an English-only historical comparison.
The next action is the separate English, Hindi, and mixed-language real-speaker
corpus and target-class Linux CPU worker benchmark in
`VOICE_CLOUD_DEPLOYMENT_EVALUATION.md`. Phase 5 then requires a priced pilot
database, reproducible build, Firebase email-link owner binding, a separate
candidate app, and authenticated validation. All Phase 1 and 2 review findings
remain resolved.

The extracted starter in the parent directory is not a Git checkout. Its planning files are synchronized snapshots; its application files are historical originals. Use and maintain the working checkout for subsequent implementation. Do not repeat cloning or initialize Git in the extracted starter.

Phase 0 left `.gitignore`, `BUILD_PLAN.md`, `HANDOFF.md`, and `STATE.md` intentionally untracked. Preserve them. The six tracked application/configuration files matched the starter byte-for-byte, and no application code, cloud resources, or production traffic changed.

Changes remain uncommitted; inspect `git status` and preserve all work. Local Docker PostgreSQL 16.15 is running, with migration head `0003_conversation_summary` and the full Register/ODI/T20I archives imported. Do not recreate the database or repeat setup. Latest verification passes 210 Python tests, 28 frontend tests, frontend build, Ruff, English/Hindi WebM transcription, and a local Docker worker smoke. Earlier Gemini/Chrome chart evidence is preserved in `STATE.md`.

Phase 4 agent scratchpads are `.local/phase4-orchestration.md`, `.local/phase4-chart-backend.md`, `.local/phase4-api-integration.md`, and `.local/phase4-react-frontend.md`. The new chart format is renderer-independent `schema_version: 2`; `cricket/chart_view.py` normalizes legacy saved Plotly figures only on read. Annual metrics include batting average (runs/dismissals), mean runs per complete innings, runs per 100 legal balls, and boundaries. `frontend/` has the npm lockfile, tests, and build; FastAPI returns 503 at `/` if `frontend/dist` has not been built. The historical `index.html` browser tests still run as regression coverage, while new UI tests live in `frontend/`.

The chart expansion scratchpads are `.local/chart-expansion-orchestration.md`, `.local/chart-expansion-backend.md`, `.local/chart-expansion-frontend.md`, and `.local/chart-expansion-review.md`. `get_match_data` resolves exact Cricsheet match IDs or unambiguous filters and saves complete regular innings for Manhattan and worm charts. Player datasets support rolling batting average or runs per 100 legal balls with a fixed imported-match baseline, and dismissals by kind. Match chart over totals omit pre/post innings penalties; wicket markers exclude retired hurt/not out. `cricket/conversations.py` releases its transcript lock probe before serializing the transcript so a read does not spuriously reject a concurrent chat.

The latest chart variety scratchpads are `.local/chart-variety-orchestration.md` and one `.local/chart-variety-<view>.md` per view. Five disjoint agents implemented the donut, heatmap, bubble, stacked area, and partnership chart calculations; root integrated chart dispatch, tool schemas, React/ECharts views, tests, and docs. `get_batter_bowler_data` and `get_squad_comparison` save conversation-owned datasets. Match lookup now adds delivery-derived over components and reconstructable stands. Keep blank low-sample heatmap cells, undefined bubble averages, separate innings stacks, and insufficient-data handling for ambiguous stands. The live India ODI bubble call required an innings-joined wicket query to avoid PostgreSQL's 65,535 bind-parameter limit. The final browser plot reveals names on hover and in the data table, keeping clustered points legible.

`cricket/conversations.py` owns persistence and the session-level advisory lock, `cricket/context.py` keeps complete recent turns and checkpointed summaries, and `cricket/harness.py` validates and executes calls and preserves errors/traces. Maintain atomic request/result slots and update result messages with their traces together. Recovery must not re-execute interrupted tools. Keep transcript reads consistent across concurrent commits. Pilot Firebase owner binding remains Phase 5; current sessions are anonymous UUID capabilities.

`cricket/wicket_tool.py` now computes teammate-dismissal before/after windows from complete innings. It excludes another wicket anywhere between the 12th legal ball before and 12th legal ball after, including illegal deliveries; the selected batter must stay at the crease and face at least one legal ball on each side. Rates pool batter runs, boundary balls, and legal balls across eligible events. Zero eligible events retain a dataset with null rates and return an `insufficient_data` chart error. Under ten eligible events, charts show insufficient sample and the model should not infer direction or cause. Preserve exclusion counts, source provenance, and selection-bias language in the Phase 4 UI.

The browser allocates its UUID with `POST /sessions` before submitting model work. Transcript `request_state` is derived from the real worker lock and saved turn boundaries; restoration polls running work and uses the lock-protected `/sessions/{id}/recover` for abandoned progress. Preserve idempotent recovery and 409 races. Context must keep exact older turns while they fit, compact only when needed, retain the latest ten complete turns, and validate summary text before counting/checkpointing. `cricket/results.py` defines the validated domain-error envelope; retain expected failure data/provenance/coverage while sanitizing arbitrary exceptions. API/harness regressions and Node browser-script tests cover these rules.

`cricket/context.py` now estimates tokens with LiteLLM's local counter for the configured model, including tool schemas, then adds a 25% margin and 256 framing tokens. It falls back to the prior safe byte bound if counting fails. The saved five-turn KL Rahul conversation fits at 3,885 of 12,000 units without summarization; the old byte estimate was about 12,727. Do not revert to one byte per token or drop recent tool exchanges to mask this issue. The annual legal-ball rate added in Phase 4 addresses the earlier unsupported chart request when phrased as runs per 100 legal balls.

Keep the Register reconciliation scoped to supplied people/provider columns, and check current rows before reporting a previously seen artifact unchanged. Preserve non-bowler dismissal exclusions, wicket-only batting innings, and both `T20`/`IT20` spellings. Regression coverage is in `tests/test_register.py`, `tests/test_queries.py`, and `tests/test_import.py`. Concise docstrings/comments in ingestion, models, and queries explain these rules.

The source assigns J Butler's same ID to both teams in match `1229824`. Preserve its ambiguity marker and both memberships; its innings must remain excluded from aggregates. Do not guess a replacement identity. `data_complete` and source/missing metadata are required inputs to later calculations.

## Guardrails

- Preserve the `/chat` response shape: `response`, `session_id`, and `tool_calls`.
- Every tool call must contain `name`, `args`, and `result`.
- Do cricket arithmetic in deterministic backend code, never in the language model.
- Do not claim Cricsheet-derived results are complete official career records.
- Join players through stable identifiers and the Cricsheet Register, not names alone.
- Keep secrets out of Git and Markdown files.
- Do not scrape Statsguru as the main source; the tested innings route returned 403.
- Do not use the outdated PyPI `cricapi` package.
- Preserve IAP on the existing Cloud Run service; the separate Columbia pilot uses Firebase email-link authentication on every data/API route.
- Do not run migrations or large dataset imports during web-server startup.
- Before pushing to `gemini-test-project-1`, remember that its Cloud Build trigger deploys to production.
- Record paid-resource estimates before enabling Cloud SQL or provisioning an instance.

## State maintenance

At the end of every session, update `STATE.md` with:

- current branch and commit;
- dirty or untracked files;
- completed phase and concrete changes;
- exact commands and checks run, with pass/fail results;
- database migration revision and imported data version, when applicable;
- deployed Cloud Run revision and traffic state, when applicable;
- known problems or assumptions; and
- one exact next action.

Do not write “tests pass” unless they were run in that session or the preserved evidence is still applicable to the unchanged code.

## Verified local commands

Run from the working checkout. The local PostgreSQL container/data already exist:

```bash
uv sync
docker compose up -d --wait
bash scripts/data.sh migrate
bash scripts/data.sh status
bash scripts/test.sh -q
cd frontend && npm ci && npm test && npm run build && cd ..
export DATABASE_URL=postgresql+psycopg://cricket@127.0.0.1:55432/cricket_analyst
uv run uvicorn app:app --reload
```

Use `bash scripts/data.sh validate --live-espn --rerun-import --report .local/phase1-verification.json` to reproduce the full Phase 1 report when needed. `scripts/data.sh` supplies local database defaults; tests require a database ending in `_test`. See README for offline sources, explicit configuration, and Docker stop/restart commands. Migrations/imports must stay outside web-server startup. Cloud commands remain Phase 5 work.

## Environment variables to standardize

Use these names unless the repository already has a documented equivalent:

- `APP_ENV` — `local`, `test`, or `production`.
- `DATABASE_URL` — local/test PostgreSQL connection string.
- `INSTANCE_CONNECTION_NAME` — Cloud SQL connection name in production.
- `DB_NAME` — PostgreSQL database name.
- `DB_USER` — PostgreSQL user.
- `DB_PASSWORD` — local value or Secret Manager-injected production value.
- `GOOGLE_CLOUD_PROJECT` — Google Cloud project ID.
- `VERTEX_LOCATION` — Gemini/Vertex location, initially `global` if supported by the selected model.
- `GEMINI_MODEL` — LiteLLM model name.
- `ESPN_TIMEOUT_SECONDS` — external request timeout.
- `PROFILE_CACHE_HOURS` — initially `24`.
- `MAX_TOOL_ROUNDS` — initially `5`.
- `CONTEXT_TOKEN_BUDGET` — initially `12000`.
- `MODEL_TIMEOUT_SECONDS` — initially `60`.

Commit only an `.env.example` containing placeholder values.

## Phase completion checklist

Before declaring a phase complete:

- compare the implementation with that phase's exit condition in `BUILD_PLAN.md`;
- run relevant focused tests;
- run broader regression tests when shared harness or storage behavior changed;
- inspect error paths, not only the happy path;
- update documentation for new commands or configuration;
- update `STATE.md`; and
- leave the repository in a state another session can run or diagnose.

## Release handoff checklist

Before production traffic is changed, confirm and record:

- automated tests passed for the exact candidate commit;
- migrations ran successfully against the target database;
- the Cricsheet import version and coverage are known;
- the candidate Cloud Run revision received no production traffic during smoke tests;
- Firebase email-link access works for a verified `@columbia.edu` mailbox, and unauthenticated/non-Columbia and cross-user API access fails;
- session persistence works across separate requests;
- all four cricket tools work from Cloud Run;
- chart values match backend datasets;
- logs contain no credentials or raw sensitive headers;
- the prior revision name and rollback command are recorded; and
- `STATE.md` identifies the promoted revision and traffic percentage.

## Definition of a clean handoff

A clean handoff lets another agent answer these questions without guessing:

- What behavior are we building?
- Which phase is complete?
- What code, database, and cloud state currently exist?
- What checks actually passed?
- What limitation or risk remains?
- What is the single next action?

If any answer is missing, update `STATE.md` before ending the session.
