# Phase 2 code review — 2026-09-28

All five findings are **fixed and verified**. Each was reproduced with failing regression tests, fixed, and verified before work began on the next finding. The final suite passes **117 tests** with no skips. Initial review findings and their evidence are preserved below as a historical record; their line references describe the pre-fix code.

## Fix verification — 2026-09-28 20:00 UTC

| Order | Fix | Verification before proceeding |
|---|---|---|
| 1 | `POST /sessions` allocates a durable UUID without model work; the browser stores it before `/chat` | 16 API/browser tests passed; existing unknown UUIDs still return 404 |
| 2 | Consistent transcript snapshots expose real worker-lock state; browser polls active work; locked recovery closes abandoned turns without re-execution | 19 API/browser tests passed, including delayed models/tools, recovery races, repeated recovery, isolation, and concurrent-reader consistency |
| 3 | Summary text is validated at provider and context boundaries before UTF-8 counting or checkpoint writes | 44 harness/API tests passed; NUL/surrogate regressions return saved chat failures and leave checkpoints unchanged |
| 4 | Exact history is retained while it fits; compaction stops as soon as the budget is met and keeps the latest ten turns intact | 49 harness/API/browser tests passed, including exact-budget inputs and summary checkpoint reuse |
| 5 | Validated domain-error envelopes preserve data/metadata; weather emits safe typed failures; arbitrary exceptions and malformed envelopes remain sanitized | 59 focused tests passed, including missing cities, candidates, coverage/provenance, and transport-exception sanitization |

Final checks: `bash scripts/test.sh -q` → **117 passed, 2 existing third-party deprecation warnings, 4.41 seconds, no skips**. Ruff lint and formatting pass (34 files); offline lockfile consistency passes (103 packages); inline JavaScript syntax and `git diff --check` pass.

Real Chrome on the isolated `cricket_test` app verified refresh during the **first model request** and during an **active tool call**. Both restored the same UUID, showed running progress with disabled buttons, and automatically displayed the completed answer/trace without a second refresh. Chrome also recovered an abandoned tool slot and preserved a previous successful result. The actual `os._exit(23)` worker-death probe passed again without tool re-execution. Temporary test conversations were removed, the isolated server was stopped, and its Chrome tab was closed.

The development app was gracefully restarted after confirming no active request locks. It is running at `http://127.0.0.1:8000`, PID `63281`, using the existing data and credentials. A live default-Gemini weather request via the new allocation path passed; its temporary conversation was cleaned up. Evidence is in `.local/phase2-fixes/ui-verification.json`, `live-verification.json`, and `crash-verification.json`. No migrations, cloud changes, commits, pushes, or deployments were required by these fixes.

Scope: Phase 2 requirements in `BUILD_PLAN.md:105`, `app.py`, the browser script, `tools.py`, `cricket/conversations.py`, `cricket/context.py`, `cricket/harness.py`, `cricket/settings.py`, the conversation models/migration, dependencies, and the Phase 2 tests. Production authentication and the Phase 3 cricket tools remain outside this review.

## Historical findings — resolved

### 1. [P1] Refreshing during the first request loses access to the saved conversation

References: `index.html:124`, `index.html:213`, `index.html:222`, `app.py:122`.

The server creates and commits the new conversation/user message before calling the model. The browser receives and stores its UUID only after the entire `/chat` request completes. If the page refreshes or closes while the first model/tool request is running, session storage has no UUID. The reloaded page cannot restore that conversation, and the next message creates a different session. The original answer can finish and remain saved in PostgreSQL while being unreachable through the UI.

Reproduction: a blocked real FastAPI request already had its user message in the test database before any response was delivered. Executing the actual inline JavaScript with a pending first request and reloading with the same storage restored zero messages; the next POST sent `session_id: null`. Completed-request restoration remains a passing control.

Suggested fix: allocate a conversation and save its UUID in browser storage before submitting model work. Preserve the existing rule that arbitrary supplied unknown UUIDs are not silently recreated. Add a first-request refresh test with a deliberately delayed model.

### 2. [P2] Refreshing an existing session during a request leaves the restored UI stale

Reference: `index.html:174`.

Restoration performs one transcript GET and immediately enables Send/Clear. When a model/tool is still running, this snapshot contains a pending user/tool entry without its final answer. The page never refreshes that snapshot when the backend completes, so the saved answer remains absent until another manual refresh. The UI also presents itself as ready while another request may still hold the session lock.

Reproduction: the actual browser script restored an unfinished existing conversation, then the controlled backend transcript gained a final answer. There was still only one GET, the answer was absent from the UI, and Send was enabled. Restoring the same transcript after completion did display the answer.

Suggested fix: expose/detect unfinished request state and reconcile the restored transcript until completion, with an explicit recovery path for interrupted workers. Test refresh during both model work and a tool call, including eventual completion.

### 3. [P2] Invalid summary text escapes normal model-failure handling

References: `cricket/harness.py:229`, `cricket/context.py:47`, `cricket/context.py:56`.

Regular assistant content uses `utf8_text`, but summary content is only checked for a nonempty string. A lone surrogate raises `UnicodeEncodeError` during context estimation; an embedded NUL reaches PostgreSQL's text adapter and raises `DataError`. Neither becomes the intended saved, actionable harness failure. The API returns 500 for the surrogate and a misleading database-configuration 503 for the NUL, leaving the new user turn unfinished.

Reproduction: seeded eleven completed turns in the dedicated test DB and returned each invalid summary through the actual API. Results were HTTP 500/503; the last saved message remained `user`, no final failure answer was saved, and the summary checkpoint stayed zero. A valid summary was a passing control.

Suggested fix: apply PostgreSQL-compatible UTF-8/NUL validation before counting or saving summaries and convert invalid provider output to `ModelUnavailable`. Add API regressions asserting the normal chat response, unchanged checkpoint, and a persisted final failure.

### 4. [P2] Summarization is triggered by turn count even when history fits the budget

Reference: `cricket/context.py:44`.

Every completed turn outside the latest ten is summarized unconditionally. The Phase 2 plan says to summarize older completed turns when context exceeds the configured budget. Eleven short turns plus the next user message used only 2,354 units under the implementation's own conservative estimate, against a 12,000 budget, yet the first provider request was a summary. A failed summary blocked the conversation despite the full original history fitting comfortably. This also introduces unnecessary provider calls and early loss of exact older details.

Suggested fix: first assess the unsummarized context against the budget. Keep complete original turns while they fit, and summarize older completed turns when needed, retaining the latest ten intact. Add an under-budget test asserting that no summary request is made, alongside the existing over-budget tests.

### 5. [P2] Tool failure normalization discards useful, structured results

References: `cricket/harness.py:139`, `cricket/harness.py:150`.

For every valid result with `ok: false`, the harness replaces the entire envelope with generic `tool_failed`, clearing `data`, provenance, coverage, and the supplied error code/message. Legacy weather errors are also flattened: a city-not-found result becomes a provider-failure message telling the model to retry later. The saved trace therefore loses the tool's explanation and can lead to the wrong next action. A safe ambiguity result containing candidate IDs would likewise lose those candidates before reaching the model or transcript.

Reproduction: a JSON-compatible failure envelope with candidate data, `ambiguous_player`, provenance, and coverage was replaced with generic empty fields. A current weather-shaped city-not-found response became `The provider could not complete the request. Retry later.` No Phase 3 implementation is assumed by this probe.

Suggested fix: define and validate the safe tool-error envelope, then preserve expected domain failures and their associated data/provenance/coverage. Continue sanitizing arbitrary provider exceptions. Add a missing-city regression and a structured failure preservation test.

## Initial review verification and evidence

- `bash scripts/test.sh -q`: **94 passed**, no PostgreSQL skips, 4.81 seconds. Two existing third-party TestClient deprecation warnings.
- Ruff lint and formatting: passed; 31 files formatted.
- Inline browser JavaScript syntax: passed with Node.
- Python 3.10 syntax parsing: passed for 19 application/migration files. This is syntax validation, not a separate Python 3.10 runtime test.
- `uv lock --check --offline --cache-dir .local/uv-cache`: passed; 103 packages resolved.
- Actual worker-death probe: a child exited with `os._exit(23)` while holding the database lock. A new process acquired the lock, retained the completed tool result, marked the pending call interrupted, and resumed with paired history without re-executing either tool.
- Existing tests verify session isolation, atomic exchanges, migration upgrade/downgrade/metadata agreement, five-round limits, schema rejection, error traces, repeatable-read transcript consistency, concurrent requests/clear, checkpoint reuse, and completed-session restart persistence.

Historical reproduction probes and machine-readable results are in ignored `.local/phase2-review/`. Those bug-reproduction scripts target the unfixed behavior; use the current regression suite in `tests/` to verify the fixes:

```text
.venv/bin/python .local/phase2-review/probes.py
.venv/bin/python .local/phase2-review/api_probes.py
.venv/bin/python .local/phase2-review/crash_probe.py
node .local/phase2-review/frontend.cjs
```

The original Python probes used only `cricket_test`; their data was rolled back or their exact temporary UUIDs were deleted. Original frontend probes executed the actual inline script with a controlled DOM/storage/fetch environment. The initial review did not modify application code. The later fixes and new real Chrome pending-request checks are recorded above.
