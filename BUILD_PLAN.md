# Cricket Analyst Agent — Build Plan

This is the historical Cricsheet-plus-ESPN build plan. On 2026-10-07 the user
selected live ESPN-only cricket tools and stopped the Cricsheet import while
retaining committed Cloud SQL data. The active tool contracts and grader
queries are in [README.md](README.md); the current release gates are in
[deploy/PILOT_RELEASE.md](deploy/PILOT_RELEASE.md). The Cricsheet phases below
are retained for reference and are not instructions to resume importing.

## 1. Goal

Turn the existing Gemini tool-calling starter into a deployed cricket analysis assistant that:

- remembers conversations across a session;
- exposes at least three model-callable tools;
- retrieves external cricket data;
- displays every tool call in the existing `/chat` response shape;
- provides a cricket-specific interface; and
- includes an original cricket analysis tool.

The first release covers men's ODI and T20I cricket. Test cricket, live-score tracking, vector memory, permanent user-profile memory, and paid cricket APIs are outside the first release.

## 2. Product behavior

The assistant should support conversations such as:

1. “Tell me about Virat Kohli.”
2. “Show only his ODI records.”
3. “Plot his runs by year.”
4. “How does his scoring change after a teammate gets out?”

The follow-up prompts must work without repeating the player name. Every statistical response must display its source, covered date range, and sample size. Statistics derived from Cricsheet must be described as applying to available matches, not as official complete career totals.

## 3. Target architecture

```text
Browser chat and analysis UI
            |
            v
FastAPI /chat on Cloud Run
            |
            v
Agent harness
  - load session transcript
  - assemble bounded context
  - call Gemini through LiteLLM
  - validate and execute tools
  - save messages, tool calls, and artifacts
            |
      +-----+------------------+
      |                        |
      v                        v
PostgreSQL / Cloud SQL     Cricket providers
  - conversations            - ESPN profile endpoints
  - messages                 - Cricsheet historical data
  - tool calls
  - players and ID mappings
  - matches and deliveries
  - generated datasets/charts
```

The browser must communicate only with FastAPI. The harness owns model calls, tool execution, context construction, and persistence.

## 4. Data sources

### ESPN

Use only endpoints verified during planning:

- player search: `https://site.web.api.espn.com/apis/common/v3/search`;
- player profile: `https://site.web.api.espn.com/apis/common/v3/sports/cricket/athletes/{player_id}`;
- active-series discovery and match-summary endpoints only if required by a supported feature and verified with integration tests.

Do not depend on ESPN Statsguru HTML scraping. It returned HTTP 403 during evaluation. Treat ESPN endpoints as unofficial and isolate them behind a provider adapter so they can be replaced.

### Cricsheet

Use men's ODI and T20I JSON downloads for historical analysis. Import the Cricsheet Register to map player identifiers across sources. Never join player records solely by display name.

Record the dataset URL, download/import time, covered date range, checksum, source revision, and counts. Preserve Cricsheet missing-data markers and exclude incomplete records from calculations that require complete delivery windows.

### Deferred providers

- Do not use the PyPI `cricapi` package; its latest release is from 2018.
- CricketData.org may be evaluated later if complete career totals become necessary.
- Roanuz and the Apify scraper are not required for the first release.

## 5. Implementation phases

### Phase 0 — Establish the Git working copy and handoff records

- Use the GitHub repository `shubhampareek1211/AgenticAI`.
- Start from its deployment branch, `gemini-test-project-1`.
- Create a feature branch for development.
- Compare the checkout with this extracted starter before moving work.
- Move or copy these planning files into the repository root if implementation occurs in a separate checkout.
- Record branch, commit, dirty state, and the next task in `STATE.md`.

Exit condition: another agent can find the working checkout and identify the next action using only `BUILD_PLAN.md`, `STATE.md`, and `HANDOFF.md`.

### Phase 1 — Data model and repeatable import

- Add PostgreSQL access through SQLAlchemy and migrations through Alembic.
- Model conversations, messages, tool calls, players, external player IDs, matches, innings, deliveries, source imports, datasets, and chart specifications.
- Create an idempotent command to download and import Cricsheet ODI/T20I JSON plus the Register.
- Upsert by stable source identifiers; rerunning the import must not duplicate rows.
- Validate the import with Virat Kohli, Rohit Sharma, and one bowler.
- Reproduce selected innings totals from source files without model arithmetic.

Exit condition: an ESPN profile can be resolved to the corresponding Cricsheet player and queried across imported matches.

### Phase 2 — Persistent session harness

- Replace the process-local `sessions` dictionary with PostgreSQL repositories.
- Persist user messages, assistant messages, assistant tool requests, tool results, and final answers.
- Preserve the top-level `/chat` response fields: `response`, `session_id`, and `tool_calls`.
- Store complete tool exchanges so a request is never separated from its result.
- Keep the latest ten complete conversational turns in model context.
- When context exceeds a configurable 12,000-token budget, summarize older completed turns while preserving the full database transcript.
- Serialize overlapping requests for one session using a database-backed lock or return an actionable “request already running” result.
- Keep the five-round tool limit.
- Validate tool names and JSON arguments and retain completed tool traces if a later model call fails.

Exit condition: sessions survive page refreshes and application restarts, remain isolated, and preserve tool history through errors.

### Phase 3 — Three tools

Implementation status (2026-09-29): all three tools are implemented locally and
verified through the model harness; see `STATE.md` for results.

#### `get_player_data`

Inputs:

- `player_name` or a previously resolved `player_id`;
- optional `format`: `odi` or `t20i`;
- optional ISO start and end dates.

Behavior:

- resolve ambiguous player names safely;
- retrieve and cache the external ESPN profile;
- query matching Cricsheet records;
- calculate supported statistics in backend code;
- return identity, statistics, source provenance, coverage, sample size, and a session-owned `dataset_id`.

Cache successful ESPN profiles for 24 hours. A stale cached response used during an outage must include its retrieval time.

#### `create_cricket_chart`

Inputs:

- session-owned `dataset_id`;
- chart type;
- permitted metric and grouping.

Supported views:

- runs by year — bar chart;
- innings scores over time — line or scatter chart;
- runs versus balls faced — scatter chart;
- wicket-response comparison — paired bar chart.

The backend validates the requested fields and calculates chart values. The tool returns a declarative chart specification; it never accepts executable chart code or model-invented values.

#### `analyze_wicket_response`

Inputs:

- resolved player ID;
- `odi` or `t20i`;
- optional ISO date range.

Metric definition:

- identify teammate dismissals while the selected batter is at the crease;
- inspect 12 legal team deliveries before and 12 legal team deliveries after the dismissal, excluding the dismissal delivery;
- require the batter to remain at the crease for both complete windows;
- exclude windows with another wicket, an innings boundary, or incomplete delivery data;
- use only deliveries faced by the selected batter and require at least one faced delivery in each window;
- aggregate total eligible runs and balls rather than averaging event-level rates;
- report runs per 100 legal balls and boundary-ball percentage before and after;
- report eligible and excluded event counts with reasons;
- below 10 eligible events, show results with an “insufficient sample” label and no directional interpretation.

Explain that this is a descriptive custom metric, not an official statistic or evidence that the wicket caused a change. Document the selection bias introduced by requiring complete windows.

Exit condition: all three tools run through the model harness and appear in `tool_calls` with `name`, `args`, and `result`.

### Phase 4 — Cricket-specific frontend

Implementation status (2026-09-29): completed locally. The React/Vite client,
Apache ECharts renderer, versioned chart format and legacy read adapter, annual
metrics, and FastAPI static integration are verified by automated tests and live
desktop/mobile browser checks. See `STATE.md` for exact evidence. Phase 5 is next.

Build a React/TypeScript interface with Vite and render charts through open-source Apache ECharts. Keep FastAPI as the same-origin API and production static-file server; the Node build runs before application startup, not during it. The user chose a clean light interface with charts inside the conversation and an expanded chart view.

- Give the application a cricket identity, clear coverage explanation, example prompts, safe Markdown answers, a multiline composer, and responsive desktop/mobile layouts.
- Show player and wicket-analysis cards, ODI/T20I and date controls, inline chart cards, and collapsed tool details. Applying a control submits an explicit follow-up so the conversation shows exactly what changed.
- Keep session allocation before model work, session storage/link restoration, lock-aware progress polling, interruption recovery, and duplicate-submission prevention. Offer separate New conversation and confirmed Clear conversation actions.
- Display source, covered dates, sample size, excluded rows, and insufficient-sample warnings next to each chart. Provide readable year categories, tooltips, an expanded view, and accessible data values.
- Extend deterministic chart calculations with annual batting average (runs/dismissals), mean runs per complete innings, runs per 100 legal balls, and boundary count (fours plus sixes). A zero denominator yields no rate. Do not silently substitute total runs for an average request or call legal-ball rate an official strike rate.
- Save new renderer-independent versioned chart values. Normalize existing saved Plotly figures when read so old sessions remain viewable without recalculating historical results. The model receives only a compact chart reference.
- Preserve the `/chat` and transcript contracts and session ownership checks. Keep FastAPI, PostgreSQL, imports, and deployment work from earlier phases intact.

Exit condition: a user can resolve Kohli, follow up with “only ODIs,” request annual runs and batting average charts, run wicket-response analysis without repeating his name, refresh during an active request, and reopen both old and new saved charts. Browser and backend checks pass at desktop and mobile widths.

#### Local chart expansion after Phase 4

Implementation status (2026-10-01): completed locally. The four views below,
match lookup, source and ownership checks, React rendering, and the live chat
path are verified. The full Python suite passes 167 tests and the frontend suite
passes 16 tests. See `STATE.md` for exact evidence.

Add four source-backed cricket views to the same versioned chart contract:

- Manhattan: recorded delivery runs per over for one imported match, with scoreboard wicket markers.
- Worm: cumulative recorded delivery runs by completed over for each complete regular innings, with wicket markers.
- Rolling form: full N-innings batting average or runs per 100 legal balls, separated by ODI/T20I, with a baseline from the same imported-match filter. N defaults to five and can be 3–20.
- Dismissal breakdown: scoreboard dismissals by kind and format from complete player innings.

Resolve a match by stable Cricsheet ID or explicit filters; ambiguous filters return candidates. Keep match datasets conversation-owned, exclude incomplete innings and super overs, disclose excluded pre/post innings penalty runs, and preserve zero-denominator nulls. Shot-direction and pitch-location visuals still require data not present in Cricsheet.

#### Five-view chart variety expansion after Phase 4

Implementation status (2026-10-01): completed locally through five separate
agent lanes and root integration/review. The Python suite passes 197 tests,
the frontend suite passes 21 tests, and the built React app renders all five
views in live Gemini conversations. See `STATE.md` and
`.local/chart-variety-orchestration.md` for evidence and lane ownership.

- Donut: the existing complete-innings dismissal counts as rings by format.
- Batter–bowler phase heatmap: batter runs per 100 legal balls against the ten
  most-faced bowlers. Cells with fewer than six legal balls remain blank.
- Squad bubble: batting average against runs per 100 legal balls, with legal
  balls as size, for up to twenty qualifying batters on one exact team/format.
- Stacked area: running, actual boundary, other batter, and extra runs by over,
  separated by innings. Components reconcile to recorded delivery runs.
- Partnership bars: two batters' runs and delivery extras per reconstructed
  stand, grouped by innings. Ambiguous stand boundaries return insufficient
  data instead of an estimated chart.

All five use the version 2 renderer-independent chart contract, conversation
ownership checks, Cricsheet provenance, accessible tables, and CSV/SVG export.
New batter–bowler and squad source tools save their own session-owned datasets.
These are imported-match descriptive views, not official career totals. Shot
direction and pitch position remain unavailable without ball-tracking data.

### Voice input extension — local implementation complete

The 2026-10-01 plan in [VOICE_INTEGRATION_PLAN.md](VOICE_INTEGRATION_PLAN.md)
is implemented locally: whisper.cpp sits behind FastAPI, and the React composer
supports record/stop/transcribe/edit/send with cancellation. A pinned local
`base.en`/`small.en` synthetic model comparison chose hinted `small.en` for the
initial English preview. A later Hindi request added a language selector and
switched the running local worker to multilingual `small`; see
[VOICE_BENCHMARK.md](VOICE_BENCHMARK.md). Real-speaker quality,
device browsers, target-class Linux CPU sizing, and Phase 5 authentication and
release work are still required before production voice.

### Phase 5 — Cloud Run and Cloud SQL release

Verified deployment context:

| Setting | Value |
|---|---|
| Google Cloud project | `phonic-weaver-475017-n1` |
| GitHub repository | `shubhampareek1211/AgenticAI` |
| Deployment branch | `gemini-test-project-1` |
| Cloud Run service | `agenticai-git` |
| Cloud Run region | `us-central1` |
| Cloud Build trigger region | `europe-west1` |
| Entrypoint | `uvicorn app:app --host 0.0.0.0 --port $PORT` |
| Existing service access control | Google IAP enabled; preserve it during the separate pilot |

- For the Columbia email pilot, first benchmark a private Linux x86-64 multilingual `small` speech worker. Use explicit model/checksum build arguments, measure two versus four threads on 2 vCPU, and apply the quality, latency, and cost gates in `VOICE_CLOUD_DEPLOYMENT_EVALUATION.md` before app/database release work.
- Use a separate Cloud Run pilot app with Firebase Authentication email-link sign-in for anyone controlling a verified `@columbia.edu` mailbox. Serve its login shell publicly; verify Firebase ID tokens and exact email domain for every data/API route, and bind every conversation to the verified `uid`. Existing anonymous sessions are not reassigned. Keep the old IAP service unchanged during the pilot.
- Enable the Cloud SQL Admin API after confirming the project permits it.
- Before provisioning, record the current price estimate in `STATE.md`.
- After the worker passes, select and price a zonal dedicated PostgreSQL 16 pilot instance in `us-central1`, including the explicit Cloud SQL edition, machine tier, storage, backup retention, and network path. Pilot zone downtime is accepted; regional high availability is a later choice. Google's shared-core `db-f1-micro` and `db-g1-small` tiers are for test/development and are outside the Cloud SQL SLA.
- Connect through the Cloud SQL Python Connector with a bounded connection pool.
- Store database credentials in Secret Manager; never commit credentials.
- Preserve IAP on the existing service; bind pilot conversations to verified Firebase `uid` identity.
- Start with at most three Cloud Run instances and five database connections per instance.
- Run database migrations and Cricsheet imports as explicit release operations, never during web-server startup.
- Add automated Python/frontend tests and a reproducible Vite production build to Cloud Build before deployment. Package `frontend/dist` with the FastAPI service; do not build assets at web-server startup.
- Deploy a separate pilot candidate, run authenticated smoke tests, then invite a small Columbia cohort before opening to the wider domain.
- Record revision names and rollback commands in `STATE.md`.

Exit condition: the pilot preserves owner-bound sessions, calls all four cricket tools, displays charts, denies unauthenticated and cross-user access, and has a tested rollback path.

## 6. Public interfaces

### Existing chat contract

```json
POST /chat
{
  "message": "Plot Virat Kohli's ODI innings",
  "session_id": "optional-uuid"
}
```

```json
{
  "response": "...",
  "session_id": "uuid",
  "tool_calls": [
    {
      "name": "get_player_data",
      "args": {"player_name": "Virat Kohli", "format": "odi"},
      "result": "{...JSON-encoded tool result...}"
    }
  ]
}
```

Keep `result` as a JSON-encoded string for starter compatibility. Internally, every tool result should use a consistent envelope containing success/error state, data, provenance, coverage, and actionable error details.

### Additional endpoints

- `GET /sessions/{session_id}` — restore an authorized transcript and its artifacts.
- `GET /sessions/{session_id}/charts/{chart_id}` — return an authorized saved chart with versioned renderer-independent data, including a read adapter for legacy Plotly figures.
- `POST /clear` — delete the selected conversation and its session artifacts.
- `GET /healthz` — lightweight process and configuration health; do not run expensive provider checks.

Return 404 for resources that do not exist and 403 for resources owned by another authenticated user. Validate UUIDs, message length, date ordering, format values, and allowed chart combinations.

## 7. Error behavior

Tools must return actionable structured errors for:

- unknown or ambiguous players;
- unsupported formats;
- invalid date ranges or chart combinations;
- external timeouts, 403, 429, malformed JSON, and unavailable providers;
- missing source coverage or an empty statistical sample;
- a dataset reference from another session;
- malformed model-generated arguments; and
- analysis samples too small to interpret.

Do not expose secrets, stack traces, database connection details, or unrestricted upstream payloads to the browser or model.

## 8. Test plan

Use pytest with deterministic provider and model fixtures for routine tests. Run a small real-provider smoke test before release.

### Data tests

- repeated imports produce no duplicate records;
- ESPN and Cricsheet identifiers map correctly;
- unmatched and ambiguous players do not receive a guessed mapping;
- innings totals match selected source fixtures;
- wides, no-balls, not-outs, run-outs, missing deliveries, and innings boundaries are handled correctly.

### Tool and analysis tests

- each tool publishes a clear JSON schema and validates arguments;
- successful and failed calls appear in the response trace;
- wicket-response calculations match hand-calculated fixtures;
- overlapping wickets and incomplete windows are excluded with counted reasons;
- samples below ten receive the insufficient-sample label;
- charts contain only values derived from the referenced dataset.

### Harness and memory tests

- a follow-up resolves the previous player and format;
- sessions survive process restart and page refresh;
- two sessions never share messages or datasets;
- concurrent requests for one session do not corrupt ordering;
- malformed tool JSON, unknown tools, provider failures, model failures, and the round limit degrade gracefully;
- summarization preserves recent complete tool exchanges.

### UI and deployment tests

- user text is rendered safely;
- duplicate submission is prevented;
- charts match backend values and remain readable on mobile;
- error and empty states explain the next action;
- migrations complete against a clean database;
- Cloud Run reaches Cloud SQL and ESPN;
- authenticated users can resume their own sessions only;
- the previous Cloud Run revision can be restored.

## 9. Assignment demonstration

The final walkthrough should show:

1. Ask about Virat Kohli.
2. Say “Only ODIs” to demonstrate memory.
3. Request a chart from the retrieved dataset.
4. Run the original wicket-response analysis.
5. Trigger an understandable error or insufficient-data result.
6. Refresh the page and continue the same conversation.
7. Expand the UI tool traces to show the name, arguments, and result of each call.

The README must describe the original tool's methodology and limitations. Its distinctiveness is defensible, but uniqueness against other class submissions cannot be guaranteed.

## 10. Definition of done

The work is complete when:

- all seven assignment requirements are demonstrated in the deployed application;
- all four cricket tools are independently testable and visible in the UI;
- sessions persist through Cloud Run restarts;
- statistical claims include coverage and provenance;
- automated tests and the production smoke test pass;
- rollback is documented; and
- `STATE.md` and `HANDOFF.md` accurately allow another agent to continue maintenance.
