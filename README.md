# Cricket Analyst Agent

Cricket Analyst helps fans explore historical ODI and T20I cricket through
conversation. It calls tools to retrieve imported Cricsheet match data, resolve
players and ESPN profiles, analyze scoring around teammate wickets, and create
charts. A weather tool fetches current conditions from Open-Meteo. The
FastAPI/Gemini agent saves conversations and tool traces; the React interface
shows those calls beside answers and charts. Match and player totals describe
the imported sample, not complete official career records or live scores.

## Three sample queries for graders

Use the same conversation for a follow-up so the agent can reuse its saved
dataset. Expand the tool-call details in the interface to inspect the tool
arguments and results.

1. “Use `get_player_data` for Virat Kohli in ODI cricket. How many matches are
   covered, and what are the source dates?” Follow up: “Chart his ODI runs by
   year.” This exercises player lookup, source coverage, saved context, and chart
   generation.
2. “Find India vs Pakistan in the 2017 Champions Trophy and draw a worm chart.”
   This exercises historical match selection and delivery-derived charting.
3. “Analyze Virat Kohli's ODI scoring around teammate wickets. How many events
   qualify, what exclusions matter, and can you chart runs per 100 balls before
   and after?” This exercises the project's custom wicket-response analysis;
   small samples must be reported as insufficient evidence.

If a player or match is ambiguous, choose one of the IDs returned by the tool
and continue in that conversation. Chart prompts require imported match data.

## Grader access and deployment status

The pilot website is
[Cricket Analyst](https://agenticai-columbia-pilot-3d4n5heeaq-uc.a.run.app).
Sign-in accepts verified `@columbia.edu` mailboxes. Its public gateway serves
the login and sends authenticated requests to a private Cloud Run backend.
That backend is the only application component with Cloud SQL credentials; all
conversation, chart, usage, and cricket-data operations go through it. The
browser retains its session reference only in the URL fragment, and Firebase
uses in-memory sign-in persistence. The private Whisper and Kokoro workers
serve speech features through the backend. The source import and live sign-in
validation are still in progress; course staff need a permitted Columbia
mailbox to evaluate the deployed pilot.

The root [submission.json](submission.json) names the deployed HTTPS website
and sole author, `sp4553@columbia.edu`. Run
`python3 scripts/validate_submission.py` to check its format; that validator
does not prove the website works for a signed-in grader. The protected GitHub
`pilot-release` branch now deploys the website through keyless GitHub Actions;
its first build and deployment passed. Keep the Cloud Run website available until grades
are released. Read [BUILD_PLAN.md](BUILD_PLAN.md), [STATE.md](STATE.md), and
[HANDOFF.md](HANDOFF.md) for implementation and project state.

## Local setup

Run from the repository root. Requires Python 3.10+, `uv`, and Docker with Compose.
PostgreSQL 16 is the tested database version.

```bash
uv sync
docker compose up -d --wait
docker compose exec -T postgres createdb -U cricket cricket_test
bash scripts/data.sh migrate
```

Create `cricket_test` once; subsequent runs can reuse it. PostgreSQL is bound to
`127.0.0.1:55432`. Data persists in Docker volume
`cricket-analyst-local_cricket_pg16`. The trust authentication in `compose.yaml`
is for local development. Stop with `docker compose stop`; restart with
`docker compose up -d --wait`.

`scripts/data.sh` uses the local URL in `.env.example` unless you export another
`DATABASE_URL`. It runs `uv run --frozen --no-sync`; run `uv sync` after dependency
changes. Configuration is explicit: the application never loads `.env`, migrates,
or imports data automatically at startup. Direct Alembic commands:

```bash
export DATABASE_URL=postgresql+psycopg://cricket@127.0.0.1:55432/cricket_analyst
uv run alembic upgrade head
uv run alembic current
uv run alembic check
```

An alternative with installed PostgreSQL binaries is
`bash scripts/local-postgres.sh start`. Select a specific installation with
`POSTGRES_BIN_DIR=/path/to/postgresql/bin`. This creates both databases on the
same local port; use either Docker or native PostgreSQL. Native initialization
on this Mac failed on shared-memory allocation, so Docker PostgreSQL 16 is the
verified setup. The helper uses mmap settings with PostgreSQL 17+.

## Persistent chat

Apply the current migration head explicitly, then start the app:

```bash
export DATABASE_URL=postgresql+psycopg://cricket@127.0.0.1:55432/cricket_analyst
bash scripts/data.sh migrate
uv run uvicorn app:app --reload
```

Build the React/TypeScript frontend before opening the FastAPI page:

```bash
cd frontend
npm ci
npm run build
cd ..
```

For frontend development, run `npm run dev` from `frontend/` in a separate
terminal and open its Vite URL. Vite proxies the API to FastAPI. Run
`npm test` and `npm run build` from `frontend/` after UI changes. The production
build lives in ignored `frontend/dist/`; FastAPI serves it from the same origin
as the API. A missing build returns an actionable 503 instead of a blank page.
Node is needed to build assets before deployment, not when the web process starts.

Open `http://127.0.0.1:8000`. Gemini calls use the existing Vertex/Google credential
setup. `GEMINI_MODEL` and `VERTEX_LOCATION` override the starter defaults;
`MODEL_TIMEOUT_SECONDS` defaults to 60. The app uses the installed LiteLLM model
cost map rather than fetching one at import. No migrations or source imports run
during application startup.

`POST /chat` preserves `response`, `session_id`, and `tool_calls`; each trace retains
`name`, `args`, and a JSON-encoded `result`. Tool results use `ok`, `data`, `error`,
`provenance`, and `coverage`. Names and arguments are validated against the
advertised schemas before execution, and the loop runs at most five rounds
(`MAX_TOOL_ROUNDS`, allowed 1–5). Completed traces survive later model failures.
Validated domain failures retain their code, message, data, provenance, and
coverage. Error codes are lowercase identifiers of at most 64 characters;
messages are nonempty and at most 2,000 characters. Arbitrary exceptions and
malformed envelopes are sanitized. The weather tool returns `city_not_found`
for a missing city and `provider_unavailable` for a transport failure.

`get_player_data` accepts either `player_name` or a previously returned Cricsheet
`player_id`, optional `format` (`odi` or `t20i`), and inclusive ISO `start_date` and
`end_date`. A name matching multiple Register players returns candidate IDs for
selection. ESPN IDs come only from the Register mapping. Profiles are cached for
24 hours by default (`PROFILE_CACHE_HOURS`); an outage can reuse a stale profile
with its original retrieval time. If no profile is available, Cricsheet statistics
still return with an explicit profile status. The response includes per-format
batting/bowling totals, covered match and innings counts, source provenance, and
a session-owned `dataset_id`. Detailed innings rows live in that dataset for
chart generation. These are **available imported-match totals**, not official
career totals; super overs and incomplete innings are excluded from aggregates.

For a local check after importing data, ask in the browser: “Use
`get_player_data` for Virat Kohli in ODI; show the match sample and source
coverage.” The `/chat` tool trace contains the structured result, and
`GET /sessions/{session_id}` lists the created dataset ID. A deterministic
integration check is `bash scripts/test.sh -q tests/test_player_tool.py`.

`create_cricket_chart` accepts a `dataset_id` from this conversation plus
`chart_type`, `metric`, and `group_by`. The allowed combinations are
annual `runs`, `batting_average` (runs per dismissal),
`mean_runs_per_innings`, `runs_per_100_legal_balls`, and `boundaries`
(fours plus sixes) as a bar or line; plus `runs/innings_date/line`,
`runs/innings_date/scatter`, and `runs/balls_faced/scatter`. The paired bar views
`runs_per_100_balls/wicket_phase/bar` and
`boundary_ball_percentage/wicket_phase/bar` use the saved wicket-response
dataset. Rolling-form lines use `batting_average/rolling_innings/line` or
`runs_per_100_legal_balls/rolling_innings/line`, with `window_size` from 3 to 20
(default 5) and a separate baseline from the same filtered imported matches.
`dismissals/kind/bar` or `dismissals/kind/donut` groups a player's scoreboard
dismissals by kind and format.
Manhattan bars use `runs_per_over/over/bar`; worm lines use
`cumulative_runs/over/line` from a saved match dataset. That dataset also
supports `run_components/over/stacked_area` (running, actual boundary runs,
other batter runs, and extras) and `partnership_runs/stand/stacked_bar` (two
batters' runs and delivery extras). `get_batter_bowler_data` saves one batter's
ODI/T20I deliveries for a `runs_per_100_legal_balls/bowler_phase/heatmap`;
it shows up to ten most-faced bowlers and leaves cells below six legal balls
blank. `get_squad_comparison` saves one exact team's imported-match ODI/T20I
aggregates for `batting_average/strike_rate/bubble`, with bubble size from
legal balls. Qualifying players need five batting innings and sixty legal
balls; undefined averages remain in the accessible table and do not become
zeroes. Incompatible choices,
foreign datasets, and selections
without complete innings return explicit errors; no chart is saved for them.
The backend calculates every value from complete saved innings, carries annual
sample sizes and denominator details, and stores a renderer-independent chart.
Rates with zero denominators have null values, not fabricated zero rates. The
tool returns a compact reference to keep model context bounded. React fetches
the full chart through `GET /sessions/{session_id}/charts/{chart_id}` and renders
it with Apache ECharts. Existing saved Plotly charts are normalized on read so
older conversations remain viewable; the historical Plotly bundle and license
remain in `static/vendor/` for old browser pages during transition. The UI
shows source, covered dates, sample, and insufficient-data context near charts.
The legal-ball rate is labeled explicitly and is not presented as an official
batting strike rate.
To reopen a saved conversation in another local tab, use
`http://127.0.0.1:8000/#session=<session_id>`; the page stores the session ID
locally and removes it from the address after loading.

To try it, ask for a player first, then “Chart his ODI runs by year” in the
same conversation. Run `bash scripts/test.sh -q tests/test_chart_tool.py` for
the deterministic chart checks.

`get_match_data` selects one imported ODI/T20I match by Cricsheet `match_id` or
team/opponent, format, date, year, and event filters. Ambiguous filters return
candidate IDs instead of choosing a match. It saves a session-owned
delivery-derived dataset for Manhattan, worm, run-component area, and
partnership charts. Each chart uses complete regular innings only,
counts recorded scoreboard wickets, and excludes pre/post innings penalty runs
from over totals; the chart shows this method and its source. These are
historical Cricsheet matches, not live scores. Ask, for example, “Find India vs
Pakistan in the 2017 Champions Trophy and draw a worm chart,” or “Load match
1022353 and draw partnership contributions.” Run
`bash scripts/test.sh -q tests/test_match_tool.py` for match selection checks.

`analyze_wicket_response` requires a resolved Cricsheet `player_id` and `format`
(`odi` or `t20i`); inclusive `start_date` and `end_date` are optional. For each
teammate dismissal while the selected batter is at the crease, it checks the
12 legal team deliveries before and after the wicket delivery. The batter must
remain at the crease throughout, with at least one legal ball faced on each
side. Incomplete windows or innings, another wicket, missing delivery data,
leaving the crease, and a side with no faced ball are excluded and counted by
reason. The saved `wicket_response` dataset contains pooled batter runs, legal
balls faced, boundary balls, and their before/after rates. Illegal deliveries
never enter those rates. The result reports candidate and eligible event counts,
source provenance, date coverage, and selection bias. Fewer than 10 eligible
events carry an **insufficient sample** label and must not receive a directional
interpretation. Zero eligible events return null rates and an explicit
`insufficient_data` chart error. This is a descriptive custom metric, not an
official statistic or evidence that the dismissal caused a change.

For a local check, ask: “Analyze Virat Kohli's ODI scoring around teammate
wickets, then chart runs per 100 balls before and after.” Run
`bash scripts/test.sh -q tests/test_wicket_tool.py` for deterministic window,
exclusion, aggregation, harness, and chart checks.

The browser first allocates a conversation with `POST /sessions` (201) and saves
its UUID in session storage **before** sending `/chat`. Allocation saves only the
initial system message and performs no model/tool work. Direct `/chat` callers
may still omit `session_id` to create a conversation in the original way.

`GET /sessions/{session_id}` restores messages and tool traces and returns
`request_state`: `idle`, `running`, or `interrupted`. The state uses the actual
PostgreSQL worker lock and one consistent transcript snapshot. After a refresh
during model/tool work, the browser polls once per second, updates the saved
progress and eventual answer, and disables Send/Clear while the request runs.
For abandoned progress it calls `POST /sessions/{session_id}/recover`, which
closes the interrupted turn without rerunning tools. A racing active worker
returns 409 and the browser continues polling. Recovery is idempotent and uses
the same existence/ownership checks as chat.

Refreshes and process restarts preserve context. **Clear** calls
`POST /clear?session_id=...`, deleting the selected
conversation, transcript, and associated artifacts. Unknown IDs return 404;
malformed IDs/messages return 422. A per-session PostgreSQL advisory lock survives
transcript commits; overlapping chat/clear requests return 409 with `Retry-After`
and can be retried. Different sessions can progress independently.

Assistant requests and matching result slots are saved atomically. Each completed
tool updates its message and trace together. After a worker interruption, pending
results are marked interrupted and the turn is closed before a new request; tools
are never silently re-executed. Transcript restoration uses one consistent database
snapshot. The full transcript remains in PostgreSQL even after context summarization.

Model context keeps exact older turns while they fit, alongside the latest ten
complete turns and the active turn. `CONTEXT_TOKEN_BUDGET` defaults to 12,000.
Budgeting uses LiteLLM's local model-token counter for messages and tool schemas,
plus a 25% margin and 256 framing tokens. If local counting fails, it falls back
to a safe UTF-8 byte bound. When context exceeds the budget, summaries compact only as many
older complete turns as needed, retaining the latest ten intact. Summary
checkpoints are reused; full original messages remain in PostgreSQL. Summaries
retain names, IDs, filters, findings, and limitations. Invalid UTF-8/NUL/empty
summary output produces a saved failure without advancing its checkpoint. If the required
recent turns or a summary input cannot fit, an actionable response asks for a new
conversation or a larger configured budget. Exchanges are never partially trimmed.

`GET /health` checks process/configuration only, without querying providers or the
database. Storage errors return a safe 503 and explain configuration/migrations.
Development sessions are anonymous UUID capabilities. In `APP_ENV=pilot` or
`production`, the API verifies Firebase ID tokens and exact verified Columbia
mailbox ownership, then binds conversations to the provider UID. Anonymous
legacy conversations remain inaccessible to pilot users. See
[pilot release procedure](deploy/PILOT_RELEASE.md) for deployment gates.

## Download and import

```bash
bash scripts/data.sh download
bash scripts/data.sh import
bash scripts/data.sh status
```

Downloads include the [Cricsheet Register](https://cricsheet.org/register/) and
[men's ODI/T20I JSON archives](https://cricsheet.org/downloads/). Artifacts and
SHA-256/retrieval metadata stay in ignored `.local/sources/`. Cached files are
checksum-verified. Fetch updated sources with `download --refresh`. Offline
imports accept `import --source-dir /path/to/artifacts`, containing `people.csv`,
`names.csv`, `odis_male_json.zip`, and `t20s_male_json.zip`.

Each Register or format archive imports in its own atomic transaction. A failure
rolls back that artifact; earlier completed artifacts remain imported. A database
advisory lock rejects concurrent imports. Upserts use stable Cricsheet IDs and
innings/delivery sequence. Unchanged match checksums are skipped. Corrected
matches replace their innings, deliveries, and wickets atomically; older source
revisions cannot overwrite newer ones. Rerunning identical inputs creates no
duplicate data or import records.

Register updates reconcile only supplied people and `key_*` provider columns.
Mappings for omitted people/providers or from other sources are preserved; absent
Register-owned IDs within the supplied scope are removed. Previously seen files
are checked against current rows, so repeating an import can restore missing IDs
and canonical names/aliases. Retained mappings keep their cached profiles.

Imports record URLs, checksums, source revisions, retrieval/import timestamps,
covered dates, selection, and row counts. Original match info, missing markers,
innings metadata, and delivery fields are retained. People absent from a Register
snapshot retain their match-supplied ID with no invented external mapping.
`players` also holds Register officials; `match_players` identifies playing rosters.
If one stable ID appears on both teams, both memberships are preserved and all
that match's innings are flagged and excluded from aggregates. Real source match
`1229824` has this conflict for J Butler; no identity correction is guessed.

Small samples:

```bash
bash scripts/data.sh import --formats odi --match-ids 1022353
bash scripts/data.sh import --formats t20i --match-ids 1041615
```

`--limit N` imports a subset per format. Partial archive coverage is recorded.
Neither a subset nor all available Cricsheet matches represent complete official
career records.

## Query and verify

```bash
bash scripts/data.sh player --espn-id 253802 --format odi
bash scripts/data.sh player --espn-id 34102 --format t20i
bash scripts/data.sh player --espn-id 625383 --format odi
bash scripts/data.sh player --cricsheet-id ba607b88 --format odi
bash scripts/data.sh totals 1022353
bash scripts/data.sh validate --live-espn --rerun-import --report .local/phase1-verification.json
```

Live ESPN profiles resolve through Register `key_cricinfo` IDs, including multiple
ID aliases. Missing mappings and conflicting assignments receive errors. Names
never establish cross-source identity. Saved profiles can be supplied with
`player --profile-json /path/to/profile.json`. ESPN is an unofficial provider
isolated in `cricket/espn.py`; profiles provide identity rather than complete
career totals. The chat tool applies the 24-hour cache policy above; the
standalone CLI remains a direct profile fetch.

Queries include provenance, actual covered dates, match/innings sample sizes,
and an available-match scope label. Team totals include extras and innings
penalties; batter runs exclude extras. **Legal balls** exclude wides and no-balls
and are labeled as such. Non-boundary fours/sixes, non-striker run-outs,
retired-hurt not-outs, miscounted overs, and separate super overs are retained.
Bowler wickets exclude run-outs and other uncredited dismissals; conceded runs
exclude byes, leg-byes, and penalties.

Known delivery gaps, missing required fields, unexpected internal-over ball
counts, and ambiguous source identities set `data_complete=false`. Incomplete
innings and super overs are excluded from aggregates. `totals` returns observed
runs plus a null total for incomplete innings. These checks cannot prove that
an unmarked omission never occurred; later analysis must validate the complete
delivery windows it uses.

## Tests

```bash
bash scripts/test.sh -q
uv run ruff check cricket migrations tests
uv run ruff format --check cricket migrations tests
```

The test script requires a dedicated PostgreSQL database ending in `_test` and
defaults to local `cricket_test`. Tests run real migrations and isolate data with
rolled-back transactions. They cover repeat/corrected imports, stable and
ambiguous mappings, source totals, extras/dismissal edge cases, missing data,
artifact rollback, migration downgrade/upgrade, ownership constraints, provider
failures, and CLI configuration errors. Regression tests also cover scoped Register
updates/restoration, non-bowler dismissals, wicket-only batting innings, and both
`T20`/`IT20` source formats. `uv run pytest` without
`TEST_DATABASE_URL` skips PostgreSQL tests; use the script to require them.
Live validation separately checks the three named players, selected innings
totals, and full-import row-count stability.

Phase 2 tests use deterministic model/provider fixtures with real PostgreSQL. They
cover complete exchanges, tool/schema failures, later model failures, five-round
limits, summary checkpoints, app/pool restarts, interrupted workers, concurrent
requests/readers, session/artifact isolation, clearing, and safe API errors. Run
`uv run ruff check app.py tools.py cricket migrations tests` and
`uv run ruff format --check app.py tools.py cricket migrations tests` for the full
implementation. The suite covers first-session allocation, active-request
restoration/recovery, invalid summaries, budget-driven
compaction, and safe domain failures. Browser-script regressions execute the
shipped inline JavaScript using Node (`node --test tests/browser_chat.cjs`);
the Python wrapper skips that test explicitly if Node is unavailable. Real
Chrome gated-request refresh/recovery,
actual worker-death recovery, and a live local Gemini/weather smoke also passed;
details are in `STATE.md` and `PHASE2_REVIEW.md`.

Source fixtures are attributed in [tests/fixtures/README.md](tests/fixtures/README.md);
checksums and revisions are in `manifest.json`. Register data is from Cricsheet
under the [Open Data Commons Attribution License](https://opendatacommons.org/licenses/by/1.0/).

## Local chat

Configure Google application-default credentials and your project, then run
`uv run app.py` and visit `http://localhost:8000`. The model is
`vertex_ai/gemini-3.5-flash-lite` in `global`. `/chat` returns `response`,
`session_id`, and tool calls with `name`, `args`, and a JSON-encoded `result`.
Persistent sessions, six cricket tools, the weather tool, and the React
interface are implemented. Build `frontend/dist` before starting FastAPI; the
frontend build is a separate release step. The Columbia pilot website is
deployed at the URL above; its signed-in cloud workflow is under validation.

## Local voice dictation

Voice uses a private [whisper.cpp](https://github.com/ggml-org/whisper.cpp)
worker. The browser records a short clip, FastAPI converts it to 16 kHz mono
PCM WAV and calls the worker, and the transcript enters the editable chat draft.
Sending the question still requires pressing Send. Voice is disabled by default;
ordinary text chat does not require the worker.

Assistant answers also have a **Read aloud** button. Turn on **Read answers
aloud** beside the composer to play each newly completed answer automatically;
this starts off for every page load so restored conversation history does not
speak unexpectedly. **Stop audio**, a new question, a new conversation, and
starting microphone recording interrupt playback. This local preview uses the
browser's `speechSynthesis` voice and needs no additional server or API key.
The voice and pronunciation depend on the browser and installed system voices;
browser speech may be blocked until the page receives a user gesture. It reads
answer text, omits fenced code blocks, and may not narrate tables/charts well.
Browser synthesis is not a guaranteed offline or open-source TTS engine. An
optional, private [Kokoro-82M worker](deploy/kokoro/README.md) can instead
generate English answer audio on CPU. FastAPI sends it only an authenticated
user's saved assistant answer; the browser voice remains the fallback when
generation is unavailable. Hindi currently uses the browser voice. The worker
is a separate Python 3.11 service because the main app runs Python 3.13.

Install CMake, a C++ compiler, Git, curl, and FFmpeg. On macOS, the local worker
can run natively; on Linux, Docker Compose builds the CPU worker. From this
directory:

```bash
bash scripts/voice.sh setup small
bash scripts/voice.sh serve small
```

`small` is multilingual; `small.en` is English-only and cannot recognize Hindi.
To show the language selector in the app, start FastAPI with
`VOICE_ENABLED=true VOICE_LANGUAGES=en,hi,auto` and the multilingual worker.
Choose **English**, **Hindi**, or **Auto-detect** beside Record. English and
Hindi force that spoken language; Auto-detect asks Whisper to identify the main
language of each clip. Hindi is transcribed in Devanagari rather than translated.
Short clips and Hindi-English code-switching can be detected or transcribed
incorrectly; edit the draft before Send. A small real-speaker batch had Hindi
timeouts and recognition errors; see
[the real-recordings report](evaluation/voice/REAL_RECORDINGS_2026-10-04.md).
No verified Hindi accuracy score is claimed. Browser read-aloud requests a Hindi
voice when its answer contains Devanagari, but actual pronunciation depends on
installed browser/OS voices.

In another terminal, `bash scripts/voice.sh synthesize` creates a synthetic
cricket sample on macOS. Run
`bash scripts/voice.sh smoke .local/voice/cricket-sample.wav` to verify
`/inference` (or supply your own
16 kHz mono 16-bit PCM WAV). Set `VOICE_LANGUAGE=hi` when using `voice.sh smoke`
with a Hindi WAV. Downloads and builds stay under ignored `.local/voice`.
The script verifies the upstream release commit and each model's SHA-256 before
use. It binds only to `127.0.0.1:8081`. The first synthetic cricket comparison
favored `small.en` with a fixed server-side vocabulary hint for English; see
[VOICE_BENCHMARK.md](VOICE_BENCHMARK.md) for the measurements and limits.
The multilingual `small` model receives that hint for English only; Hindi and
Auto-detect do not receive the English names prompt.
The raw worker emitted `[BLANK_AUDIO]` for silence, which the FastAPI endpoint
filters. Real-speaker accuracy and target Linux performance remain unverified.
The [cloud deployment evaluation](VOICE_CLOUD_DEPLOYMENT_EVALUATION.md) covers
verified `@columbia.edu` email-link access, the private Whisper worker, release
gates, and worker cost scenarios. The private workers and separate pilot app
are deployed; signed-in cloud use is still being checked.

The Docker alternative preserves the existing PostgreSQL service and volume:

```bash
docker compose -f compose.yaml -f compose.voice.yaml up -d --build postgres voice
```

For the `small.en` image, set both `VOICE_MODEL=small.en` and
`VOICE_MODEL_SHA256=c6138d6d58ecc8322097e0f987c32f1be8bb0a18532a3f88f734d1bbf9c41e5d`
before building. For Hindi, set `VOICE_MODEL=small` and
`VOICE_MODEL_SHA256=1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b`
instead. The Docker worker also maps only to local port 8081. It is a
CPU image; an Apple Silicon native result does not predict its Linux latency.

Set `VOICE_ENABLED=true` and `STT_BASE_URL=http://127.0.0.1:8081` for FastAPI,
then start the existing app and frontend. The app does not auto-load `.env`;
export your local settings in the shell. The example file lists all voice limits
and timeouts. Browser recording requires a secure context (localhost qualifies),
microphone permission, and a supported MediaRecorder format. If dictation fails,
the draft remains editable and text chat remains available. Clips up to 60
seconds are supported. The signed-in local pilot preview can call the private
cloud Whisper worker for English, Hindi, and Auto-detect.

For the cloud pilot, prepare separate consented English, Hindi, and mixed-language
groups, each with 20 short questions, five long questions, and five silence/noise
controls (at least 90 clips total). Each clip must be a
16 kHz mono PCM WAV. A manifest entry looks like:

```json
{"clips":[{"file":"short-01.wav","kind":"speech","language":"en","group":"english","reference":"Compare Virat Kohli and Joe Root ODI runs in 2019","entities":["Virat Kohli","Joe Root","ODI","2019"]},{"file":"silence-01.wav","kind":"silence","language":"hi","group":"hindi"}]}
```

The following commands reproduce the earlier English-only local comparison;
the [cloud evaluation](VOICE_CLOUD_DEPLOYMENT_EVALUATION.md) specifies the
multilingual Linux benchmark and release gates. Allow one warm-up request first:

```bash
python3 scripts/voice-benchmark.py .local/voice/corpus/manifest.json --model base.en --runs 3 --output .local/voice/base-report.json
python3 scripts/voice-benchmark.py .local/voice/corpus/manifest.json --model small.en --runs 3 --output .local/voice/small-report.json
```

Restart the worker with the matching model between runs. The reports contain
per-request transcription and latency, aggregate word error rate, entity
preservation, and silence false positives. Review those transcripts manually for
cricket meaning and speaker variation. Measure browser upload, conversion, queue,
and inference time separately before choosing deployment resources. Synthetic
macOS `say` audio is useful for a smoke test but cannot establish real-speaker
accuracy. The deployment image still needs a release-specific base-image digest,
resource budget, IAM setup, and authenticated candidate smoke before production.
