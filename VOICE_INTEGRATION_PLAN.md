# Voice input integration plan

Planning date: 2026-10-01. Status: V1/V2 local implementation and synthetic V0
comparison complete; V3 real-speaker/device validation and V4 release outstanding.
The local preview later added explicit English, Hindi, and Auto-detect using
multilingual `small`; the English-only model comparison remains historical.
The text below preserves the original design contract; actual local evidence and
remaining gates are in [VOICE_BENCHMARK.md](VOICE_BENCHMARK.md) and
[STATE.md](STATE.md). As of 2026-10-04, private Whisper and Kokoro cloud workers
are deployed; the user-facing cloud pilot remains unreleased. See
[the overall review](PROJECT_REVIEW_2026-10-04.md) for the current status.

## 1. Scope and decisions

Add voice dictation to the existing Cricket Analyst composer. The proposed first
release is **record → stop → transcribe → edit → Send**. English was the original
language scope; the local preview now also offers Hindi and Auto-detect. Mixed
speech remains unvalidated.

Use **whisper.cpp as a separate, long-running HTTP service**, behind the
FastAPI application. Load its model once per worker. The earlier `base.en` and
`small.en` synthetic results are historical; the Columbia pilot must benchmark
multilingual `small` on target Linux CPU. Keep one provider interface so a
future Speaches/faster-whisper service can replace it without frontend changes;
implement only the whisper.cpp adapter in this release.

If Hindi or mixed speech is required, benchmark multilingual `base` and `small`
instead of English-only models. Evaluate mixed speech separately; automatic
language detection is not proof of good code-switching performance. Transcription
preserves spoken language; translation and transliteration are separate scope.

Live partial transcripts, automatic sending, wake words, background listening,
speaker identification, and recordings beyond 60 seconds are deferred.
Server-side English spoken answers were subsequently implemented with Kokoro;
browser `speechSynthesis` remains the fallback.
If live text is selected, revise the transport and acceptance tests before UI
implementation; HTTP file upload alone does not provide live partial results.

## 2. Existing integration points

The working checkout is `AgenticAI/`, branch `feat/cricket-analyst`, HEAD
`31798f36b5064cc2d7c5e5936a3f5dfe5e10b79a`. Existing uncommitted work is preserved.

| File | Planned change |
|---|---|
| `frontend/src/App.tsx` | Add microphone controls and merge accepted dictation into `draft`; reuse `submit()` for Send |
| `frontend/src/useVoiceInput.ts` (new) | Own recorder lifecycle, cancellation, request generation, and voice state |
| `frontend/src/VoiceInput.tsx` (new) | Accessible record/stop/cancel controls, timer, status, and error display |
| `frontend/src/api.ts` | Add capability and multipart transcription calls with AbortSignal |
| `frontend/vite.config.ts` | Proxy `/transcribe` and `/voice` to FastAPI |
| `frontend/src/styles.css` | Fit controls into the existing responsive composer |
| `app.py` | Register voice routes and route-aware validation errors; inject speech dependencies for tests |
| `cricket/speech.py` (new) | Validated speech settings, service orchestration, typed results/errors, whisper.cpp adapter |
| `cricket/audio.py` (new) | Bounded audio validation/conversion and temporary-file cleanup |
| `pyproject.toml`, `uv.lock` | Declare multipart support and async HTTP client directly |
| `scripts/voice.sh` (new) | Explicit versioned model setup and local service commands |
| `deploy/voice/Dockerfile` (new) | Reproducible Linux whisper.cpp worker with a pinned model |
| `compose.voice.yaml` (new) | Optional local speech service; reuse existing PostgreSQL configuration |
| `.env.example`, `README.md` | Configuration, setup, browser support, limits, and troubleshooting |

The current global validation handler describes every error as a malformed chat
message. Give voice routes appropriate errors while retaining existing chat
behavior. The existing `/healthz` must remain a cheap app/configuration check.

Voice is an input feature, not an agent tool. It does not call Gemini, allocate a
conversation, acquire conversation locks, or save messages. The normal Send path
allocates a session if necessary and persists the edited text. No database
migration is required. Chat/tool response formats and recovery behavior remain
the existing contracts.

## 3. Request flow and API

```text
Browser: getUserMedia + MediaRecorder
  → same-origin POST /transcribe (multipart audio)
  → FastAPI: enforce limits, decode and normalize
  → private whisper.cpp POST /inference (WAV)
  → FastAPI: validate response, return text
  → browser: append to editable draft
  → explicit Send → existing POST /chat
```

`GET /voice/config` returns only public capabilities:

```json
{"enabled":true,"max_duration_seconds":60,"max_upload_bytes":8388608,"languages":["en"]}
```

This endpoint describes configuration, not worker readiness; it never exposes
service URLs or credentials. If disabled or misconfigured, ordinary text chat
continues to work and the microphone is unavailable.

`POST /transcribe` accepts `multipart/form-data` with exactly one `file` and an
optional `language` from the configured allowlist (English default). No arbitrary
model name, URL, local path, or decoding prompt may be supplied by the browser.
There is no session ID because this endpoint accesses no conversation data.

Success example (illustrative values):

```json
{"text":"Show Virat Kohli's ODI runs by year.","language":"en","duration_seconds":7.2,"request_id":"uuid"}
```

`language` is nullable when unknown; for forced English it describes the selected
language, not a claimed detection result. Duration comes from decoded samples.
Return plain UTF-8 text, bounded to the existing 8,000-character chat limit. Reject
invalid/overlong provider output rather than silently truncating a question.

Errors retain a string `detail` for the existing API client, plus `code` and
`request_id`. Suggested mappings:

| Status | Code and behavior |
|---|---|
| 413 | `audio_too_large` or `audio_too_long`; ask for a shorter recording |
| 415 | `unsupported_audio`; provide a supported format/browser hint |
| 422 | `invalid_audio`, `invalid_language`, or `no_speech`; preserve draft |
| 429 | `voice_busy`; bounded admission failed, include Retry-After |
| 502 | `transcription_failed`; malformed or invalid upstream output |
| 503 | `voice_unavailable`; disabled, starting, or unreachable worker |
| 504 | `transcription_timeout`; no automatic replay |

The separate Columbia pilot uses verified Firebase email-link identity for access to these endpoints.
Local anonymous development remains loopback-only. No production voice release
precedes the existing Phase 5 authentication work.

## 4. Recording and user experience

Use explicit states: `idle → requesting_permission → recording → transcribing →
idle`, with recoverable errors and cancellation from each active state.

- Request the microphone only after the user presses Record. Detect secure
  context, media API support, and a supported recording MIME type. Try WebM/Opus,
  MP4/AAC, and Ogg/Opus through runtime capability checks; handle recorder startup
  failures even after a positive capability result.
- Show a visible recording indicator, elapsed time, Stop, and Cancel. At 60
  seconds stop and transcribe automatically; never send chat automatically.
- Preserve existing draft text. While recording/transcribing, keep it visible and
  read-only; disable all chat submission entry points, including Enter, examples,
  and chart follow-ups. Enforce this inside `submit()`, not just on buttons.
- Append successful text with a separating space, then focus the editable draft.
  If the combined text exceeds 8,000 characters, show the transcript separately
  for copying/editing rather than losing or truncating either part.
- Stop every media track after stop/cancel/error/unmount. A permission request
  can resolve after cancellation: immediately stop those late-arriving tracks.
- Cancel discards the current clip and aborts its upload/request. A monotonically
  increasing operation ID prevents a late result from changing the draft.
- New/Clear conversation, navigation, and component unmount invalidate active
  voice work. New/Clear cancel dictation before changing conversation state;
  existing chat-busy restrictions still apply. Refresh discards an unsent clip.
- On backgrounding the page during recording, cancel and clean up; do not leave
  hidden recording active. Preserve text entry when permission is denied, no
  microphone is available, or the service fails.
- Use keyboard-operable controls, descriptive accessible names, and an aria-live
  status. Avoid announcing the timer every second.

## 5. Audio, inference, and resource boundaries

Start with a 60-second decoded duration limit, 8 MiB audio-file limit, and 10 MiB
total multipart-body limit. Count actual incoming bytes before multipart parsing
can spool an unlimited body; Content-Length alone is insufficient. Bound part
counts, upload duration, concurrent conversions, and temporary storage.

FastAPI normalizes allowlisted audio containers to **16 kHz mono, 16-bit PCM WAV**
using FFmpeg. Check actual decodability rather than trusting MIME or filename.
Run with fixed argument arrays, generated temporary paths, no shell, no network
protocol inputs, and a conversion deadline. Decode at most the duration limit
plus a small detection margin, rejecting excess rather than truncating it.
Delete original and normalized files in `finally`, including cancellation/errors.

The worker receives only normalized WAV. Disable its general format conversion
path. Pin whisper.cpp source revision, model checksum, and build dependencies;
retain licenses. Do not download the model on each request. Keep audio context
independent between requests and test for cross-request contamination.

Begin with a 10-second conversion timeout and 45-second inference timeout within
a 60-second post-upload application deadline; benchmark before finalizing them.
Use asynchronous HTTP and subprocess handling, or explicitly bounded offloading,
so heavy work does not block the FastAPI event loop. No automatic transcription
retry; an ambiguous timeout may leave upstream computation running.

Start with one active inference per worker. Local admission and production worker
concurrency must both be bounded; process-local semaphores do not impose a global
limit across replicas. Count timed-out work as potentially active until the
worker has finished/recovered. Browser cancellation guarantees discarded results,
not immediate interruption of whisper.cpp inference.

Evaluate voice activity detection (VAD) and silence rejection with real samples.
Use the supported worker VAD configuration if it passes the corpus tests; do not
assume nonempty model output proves speech occurred. Do not rewrite uncertain
player names with Gemini. A small fixed cricket vocabulary prompt can be tested
against the unprompted baseline for both accuracy and false insertions.

Raw audio is temporary and not stored in PostgreSQL or object storage. Record
only request ID, model version, duration, timings, outcome, and resource metrics
in operational logs. Edited text enters ordinary conversation persistence only
when sent. Synthetic/consented benchmark recordings are separate test fixtures.

## 6. Local and cloud topology

Local: FastAPI on its existing port; whisper.cpp on loopback port 8081. Prefer a
native build on an Apple Silicon machine when available and verified; separately
test the Linux CPU container intended for cloud use. A fast Mac result does not
establish cloud CPU performance. The optional Compose file must preserve the
existing PostgreSQL volume and service.

Proposed production: a separate **IAM-protected Cloud Run speech service** in the
app's region. Only the application's service account receives invoker permission;
FastAPI supplies an audience-bound Google ID token. The separate pilot app
verifies Firebase ID tokens for browser API access; the existing service keeps
its IAP policy. This topology lets speech resources and scaling be tuned separately.
Do not proxy the worker's model-loading or other administrative routes publicly.

Start evaluation with CPU, request concurrency 1, minimum instances 0, and a small
maximum instance cap. Choose CPU/memory and final caps from measured peak memory,
throughput, cold starts, expected traffic, and a documented cost estimate. Bundle
the pinned model in the worker image initially and verify readiness after loading.
Include FFmpeg in the app image and account for concurrent temporary buffers.

If CPU misses the latency target, compare a larger CPU allocation and
Speaches/faster-whisper on GPU using the same corpus. Do not provision GPU or
always-warm instances based on assumed performance. Estimate both low-traffic
scale-to-zero and warm-instance costs before selecting the production setting.
GPU quotas, regional availability, and pricing need a fresh check at release.

Configuration: `VOICE_ENABLED=false` by default, `STT_PROVIDER=whisper_cpp`,
`STT_BASE_URL`, `STT_AUTH_MODE=none|google_id_token`, `STT_AUDIENCE`, and validated
duration, byte, timeout, and concurrency limits. Keep these separate from Gemini
harness settings. Pin the model in deployment configuration; clients cannot
select arbitrary models. Misconfigured voice must not stop text chat startup.

## 7. Implementation sequence and exit gates

| Phase | Work | Exit evidence |
|---|---|---|
| V0 — Model spike | Preserve the historical English-only comparison; benchmark multilingual `small` on target-class Linux CPU using three 30-clip groups for English, Hindi, and mixed speech; compare two and four threads | Reproducible benchmark report, chosen model and resource budget, recorded limitations |
| V1 — API | Implement limits, normalization, provider adapter, config and transcription endpoints, injection and error handling | Deterministic API tests plus real WAV/WebM/MP4 smoke through whisper.cpp; no chat side effects |
| V2 — UI | Recorder hook/controls, state handling, API client, Vite proxies, draft integration | Browser recording→edited draft→existing chat works; cancellation and stale-response tests pass |
| V3 — Integrated validation | Browser/device matrix, real speech, concurrency, cleanup, regression suite | Quality/latency results and acceptance checklist below; typed chat works during worker outage |
| V4 — Release | After the private worker passes its benchmark, complete Phase 5 Firebase email-link auth and app/database cost/config review; deploy a separate candidate pilot app with voice disabled then enable on candidate | Authenticated candidate smoke; recorded image/model versions; rollback verified before promotion |

Recommended implementation order is V0 → V1 → V2 → V3 → V4. A model can be
changed between V0 and V3 without changing the browser/API contract. Local
implementation and synthetic comparison are complete; cloud provisioning and
traffic changes are later release work.

## 8. Acceptance and verification

Benchmark corpus: separate English, Hindi, and Hindi-English mixed groups, each
with 20 representative questions of roughly 5–15 seconds, five longer questions
of 30–60 seconds, and five silence/noise controls. Include
different speakers/accents, player names (Kohli, Bumrah, Root, de Villiers), formats
(ODI/T20I), years, comparisons, and background noise. Store references and runtime
settings. Apply the short-question and no-speech targets to each group separately.

Provisional targets, **not measured promises**:

- At least 18 of 20 short questions preserve intended players, format, dates, and
  metric without correction. Report entity errors and word error rate separately.
- Zero text for the five no-speech controls. Report residual limitations; a small
  test set cannot establish universal silence robustness.
- Warm p95 Stop-to-editable-text at most 5 seconds for 5–15-second clips on the
  chosen deployment configuration, using repeated runs. Measure browser upload,
  conversion, queue, and inference separately. Test cold starts separately, with
  a provisional 20-second target for a short clip.
- At least two simultaneous users complete without cross-request transcript
  mixing or degradation beyond the published capacity; overload returns a clear
  bounded response. Check text chat responsiveness during transcription.

Backend tests cover malformed/empty audio, forged MIME, byte/duration limits,
chunked bodies, conversion timeout/failure, no speech, provider failures, output
validation, cleanup, disabled voice, and concurrent admission. Verify no session,
message, chart, or agent call is created by transcription. Use fixture providers
in CI; actual model accuracy is a separate reproducible smoke/benchmark.

Frontend tests cover permission denial, late permission resolution, unsupported
recording, start/stop/cancel, timer limit, existing-draft append/overflow, double
clicks, hidden page, navigation, stale responses, worker outage, and all Send
entry points during voice activity. Verify Stop/Cancel release every media track.

Run real browser checks on desktop Chrome and Safari, plus Android Chrome and
iOS Safari before claiming mobile support. Use consented recordings; automated
fake-microphone fixtures can verify the ordinary browser flow in CI. Missing
device coverage must be recorded as unverified.

Run focused Python/frontend tests during implementation; at integration, run the
existing full Python suite, frontend suite, TypeScript/Vite build, Ruff, and diff
checks. The implemented local path passed 210 Python tests, 28 frontend tests,
the frontend build, and real WebM and Docker-worker smokes; see `STATE.md`.

Release smoke: record a player question, edit and Send, follow up with “Only
ODIs,” request a chart, and refresh. Confirm audio never becomes a tool trace and
session restoration behaves as before. Unauthenticated direct worker access must
fail; the app service identity must succeed.

Rollback: disable `VOICE_ENABLED` and deploy/restore the prior app revision;
retain the prior worker image/model digest for restoration. Text chat remains
available. Record final settings, measured latency, known browser/language limits,
and exact release/rollback commands in STATE/HANDOFF.

## 9. Sources verified during planning

- [whisper.cpp server](https://github.com/ggml-org/whisper.cpp/blob/master/examples/server/README.md): multipart `/inference`, language, VAD, and conversion options. Pin and test the selected revision.
- [whisper.cpp](https://github.com/ggml-org/whisper.cpp): platform/backend and model setup documentation.
- [MediaRecorder capability detection](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder/isTypeSupported_static) and [microphone access](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia): runtime format checks, permission, and secure contexts.
- [Cloud Run service authentication](https://docs.cloud.google.com/run/docs/authenticating/service-to-service): service identity, invoker permission, and audience-bound ID tokens.
- [Cloud Run GPU services](https://docs.cloud.google.com/run/docs/configuring/services/gpu): a fallback to evaluate if CPU misses measured targets.
- [Speaches](https://github.com/speaches-ai/speaches): alternative API serving implementation if later benchmarks justify a provider change.

Exact next implementation action: run V0 using the selected language/interaction
scope and record the benchmark before choosing the release model.
