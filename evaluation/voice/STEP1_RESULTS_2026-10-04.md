# Step 1: application and cloud speech evaluation

This is a diagnostic evaluation, not release approval. The public Columbia
pilot remains undeployed. The local evaluation app on port 8002 connects to
the private Whisper and Kokoro workers through authenticated local proxies.
It uses the local development database. Production service-account invocation,
Firebase sign-in and cloud persistence require separate checks.

## Completed integration work

- Added a repeatable evaluation launcher and HTTP workflow harness. The harness
  submits the actual transcript to chat, checks saved answers and chart retrieval,
  validates framed WAV responses, and clears only its own test conversations.
- Fixed the launcher's FFmpeg path. Enabled pilot/production STT now requires
  HTTPS and Google IAM authentication. Worker overload is mapped to HTTP 429.
- Limited the first Kokoro text segment to 100 characters, retaining 220 for
  subsequent segments. Words are preserved across splits.
- Added a consent-based local recording page with English, Hindi and mixed
  prompts. Recordings remain local until deliberately submitted for evaluation.

## Earlier synthetic measurements

These measurements precede the real-speaker batch and must not be treated as
real-speaker accuracy evidence. Raw evidence is under ignored
`.local/voice/cloud-workflow/`.

| Measurement | Observed result |
| --- | --- |
| Short synthetic transcription | Approximately 12–25 seconds; below-five-second target not achieved |
| Hindi synthetic example | Player name was mistranscribed |
| Five-second digital silence | Rejected with `no_speech` in both attempts |
| First audio, same answer, alternating segment limits | Previous limit: 10.035 / 8.863 / 7.846 seconds; shorter first segment: 6.841 / 3.893 / 3.934 seconds |
| Cricket chart workflow with shorter segment | STT 16.565 s; chat/tools 5.755 s; first speech segment 10.445 s; total to first audio 32.905 s |

The chart workflow called `get_player_data` and `create_cricket_chart`, restored
the saved answer, and generated two audio frames. Live chart retrieval was
added to the harness afterwards and has a passing mocked test; it was not
revalidated live in that workflow. HTTP times exclude audible browser playback.
The smaller first segment helps that answer's startup time, but is not a
general latency guarantee. Initial requests are not proven cold starts.

## Real-speaker batch

See [the recording evaluation](REAL_RECORDINGS_2026-10-04.md) for the user's
12 recordings, complete per-clip outcomes, limitations and next experiment.
Original recordings and transcripts stay in ignored `.local/voice/recordings/`.

## Validation and remaining gates

Earlier step-1 checks: backend suite 235 passed / 1 skipped; recording-page
tests 3 passed. The skip is a worker dependency unavailable in the main app
environment; cloud worker behavior was exercised separately. After the final
harness updates, its four focused tests and Ruff lint/format checks passed.

The original release targets remain unchanged: at least 18/20 short questions
retain essential meaning in each language group; no false transcripts on
no-speech controls; warm Stop-to-edit p95 ≤5 seconds for 5–15-second clips;
cold short-clip latency ≤20 seconds. Browser timing, multiple speakers,
near-60-second clips, concurrency, bounded memory and cancellation still need
dedicated coverage. Do not increase CPU/GPU spend or relax these gates silently.

No deployment, cloud resource configuration, original recording, existing
conversation, or production traffic was changed by this evaluation.
