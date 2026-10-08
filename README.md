# Cricket Analyst Agent

Cricket Analyst uses Gemini and live ESPN cricket tool calls to answer questions
about players, currently listed matches, scorecards, and batting contributions.
The FastAPI agent saves conversations and tool traces; the React interface
shows those calls beside answers. Cricket answers use ESPN responses fetched at
request time. ESPN's currently exposed match list is not a historical archive,
and player search and profiles do not establish career statistics. The
earlier Cricsheet import and chart code remains in the repository but is not
advertised to the model in ESPN-only mode.

## Three sample queries for graders

Use the same conversation for follow-ups. Expand the tool-call details in the
interface to inspect the live ESPN request arguments and results.

1. “Search ESPN for Virat Kohli, then fetch the player profile for the matching
   player ID. Show the name and available profile details, and explain whether
   this includes career totals.” This exercises two live tool calls and their
   coverage limits.
2. “List cricket matches currently exposed by ESPN. Pick one with a scorecard
   and show its batting and bowling figures, including its league and event
   IDs.” This exercises live match discovery and `get_espn_scorecard`.
3. “For the match you just selected, calculate each innings' top-three batter
   share of listed batter runs. Show the numerator, denominator, and leading
   batters.” This exercises `analyze_espn_batting_contributions`, the custom
   scorecard-derived metric.

ESPN may return no listed matches or scorecard for a particular moment. If so,
the agent should explain the provider coverage and retry with an available
match. Historical worm charts and wicket-response analysis require Cricsheet
deliveries and are not part of the ESPN-only tool set.

## Grader access and deployment status

The pilot website is
[Cricket Analyst](https://agenticai-columbia-pilot-3d4n5heeaq-uc.a.run.app).
Sign-in accepts verified `@columbia.edu` mailboxes. Its public gateway serves
the login and sends authenticated requests to a private Cloud Run backend.
That backend is the only application component with Cloud SQL credentials; all
conversation, chart, and usage persistence goes through it. Live ESPN cricket
calls are made by the private backend. The browser retains its session
reference only in the URL fragment, and Firebase
uses in-memory sign-in persistence. The private Whisper and Kokoro workers
serve speech features through the backend. The Cricsheet import was stopped at
the user's request, with imported rows retained; ESPN-only tool validation and
live signed-in end-to-end checks are still needed. Course staff need a
permitted Columbia mailbox to evaluate the deployed pilot.

The root [submission.json](submission.json) names the deployed HTTPS website
and sole author, `sp4553@columbia.edu`. Run
`python3 scripts/validate_submission.py` to check its format; that validator
does not prove the website works for a signed-in grader. The protected GitHub
`pilot-release` branch is the repository default and deploys code changes
through keyless GitHub Actions; its first build and deployment passed. Keep the
Cloud Run website available until grades are released. Read [BUILD_PLAN.md](BUILD_PLAN.md), [STATE.md](STATE.md), and
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
malformed envelopes are sanitized. The live ESPN tools return a
`provider_unavailable` error on upstream failures.

The advertised cricket tools are `find_espn_player` (candidate identities),
`get_espn_player_profile` (details for a numeric ESPN `player_id`),
`list_espn_matches` (up to 30 matches in ESPN's current header),
`get_espn_scorecard` (batting and bowling rows for numeric `league_id` and
`event_id`), and `analyze_espn_batting_contributions` (top-three batter share
of listed batter runs for each innings). The last two tools use the same ESPN
summary endpoint; match IDs can come from `list_espn_matches`. Every result
reports provenance and coverage. The contribution denominator excludes extras
and is not a percentage of all team runs. Live matches can have incomplete
scorecards. These calls do not read the imported cricket tables and do not
provide career totals or ball-by-ball analysis.

The old Cricsheet-derived `get_player_data`, `get_match_data`,
`analyze_wicket_response`, and `create_cricket_chart` implementations and
their tests are retained in the repository for possible later work, but are
not advertised as tools in ESPN-only mode. Existing saved charts can still be
read through their conversation endpoint; new ESPN charts are not implemented.
In the pilot, conversation and chart persistence remains in Cloud SQL behind
the private backend. To reopen a conversation, use the session reference in
the URL fragment; the pilot browser does not persist it in local storage.

The browser first allocates a conversation with `POST /sessions` (201) before
sending `/chat`. The pilot keeps its UUID in the URL fragment; it does not save
it to browser storage. Allocation saves only the initial system message and
performs no model/tool work. Direct `/chat` callers may still omit `session_id`
to create a conversation in the original way.

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

## Historical Cricsheet pipeline (inactive)

The commands in this section are retained for local development and historical
reproducibility. They are not part of the pilot's ESPN-only request path. The
Cloud SQL import was stopped on request, and its partial rows were kept without
deleting conversation data. Do not resume importing for this release.

### Download and import

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

### Query and verify historical imports

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
Persistent sessions, five live ESPN cricket tools, and the React interface are
implemented. Build `frontend/dist` before starting FastAPI; the
frontend build is a separate release step. The Columbia pilot website is
deployed at the URL above; its ESPN-only and signed-in cloud workflow is under
validation.

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
