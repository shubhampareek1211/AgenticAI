# Real recordings: cloud transcription diagnostic — 2026-10-04

## Scope and method

The user supplied 12 recordings and 12 cumulative manifest exports. The exports
contain 12 unique clips, consistent metadata, explicit human-recording source,
and cloud-transcription consent. Original downloads were preserved. Audio,
SHA-256 hashes, source-manifest provenance, decoded durations, per-clip levels,
and machine-readable results are under ignored
`.local/voice/recordings/2026-10-04/`.

The batch has one speaker: English 2 short + 2 long; Hindi 4 short + 2 long;
mixed English/Hindi 2 short. All files decode and are below upload/duration
limits. Short clips span 2.76–6.12 seconds; long clips span 32.04–33.42 seconds.
Only two short clips, both Hindi, lie in the 5–15-second latency-gate range.
There are no no-speech controls or near-60-second recordings in this batch.

Each original WebM is submitted once to the existing app `/transcribe` route
on loopback port 8002, then converted by the application and forwarded through
the authenticated Cloud Run proxy on port 8084. English uses `en`, Hindi uses
`hi`, and mixed clips use `auto`, as recorded in the manifests. The existing
English-only cricket vocabulary prompt is retained. No corrected reference is
sent as the desired transcript. No automatic retry, chat/LLM call, TTS call,
resource change, or timeout extension is used in this batch.

Cloud Run describes `agenticai-voice-pilot-00007-nqx` at 100% of service traffic:
multilingual Whisper small; 2 vCPU / 2 GiB; two threads; concurrency one. Image:
`sha256:fa0df5b1365de0759af07e5d1fa650329619c8bebc7b0dbd66987377a2347ee2`.
The captured service description is `worker-config.yaml` in the evidence folder.

Timing covers HTTP submission through the local app and cloud proxy. It excludes
recording and browser rendering, and is not pure model inference time. The
initial worker state is unknown; no sample is labeled a proven cold start.
A client-sequential batch does not guarantee worker-sequential execution when
an earlier request outlives the application's timeout.

## Accuracy interpretation

Compare returned text with the manifest reference as a **prompt comparison**.
References have not been independently checked against the spoken audio, so
missing words may reflect a recording/reference difference. No audio-grounded
WER, confident accent diagnosis, or final human meaning score is claimed.
Equivalent digits, spelling variants and a change of script alone are not
meaning failures. Losing a player identity, requested action, format or number
is material and needs review. The machine report's `passed` field means only
that the API returned successfully; manual accuracy remains unset.

## Results

**8 of 12 requests returned transcripts; 4 timed out. This is API availability,
not 8 accuracy passes.** Every returned transcript has differences requiring
review against the supplied prompt.

| Group | Returned / submitted | Successful request latency | Timeouts |
| --- | --- | --- | --- |
| English | 4 / 4 | 17.98–32.96 s | 0 |
| Hindi | 3 / 6 | 15.06–28.19 s | 3 |
| Mixed | 1 / 2 | 27.96–27.96 s | 1 |

Timeout responses arrived after 45.14–45.19 seconds. Returned short-clip
transcripts took 15.06–28.19 seconds. The two eligible 5–15-second clips took
28.19 and 15.06 seconds; this does not establish a warm p95, but provides no
evidence of meeting the ≤5-second target. Longer English clips took 31.98
and 32.96 seconds; both longer Hindi clips timed out.

| Clip | Audio duration | Request time | HTTP | Prompt comparison / issue |
| --- | ---: | ---: | ---: | --- |
| english-01 | 4.50 s | 17.98 s | 200 | Reference wicket becomes “evacate”; the core cricket term is lost. |
| english-04 | 3.48 s | 18.14 s | 200 | Player names and one-day format retained; the explicit compare instruction is missing. Confirm whether it was spoken. |
| hindi-01 | 4.14 s | 45.14 s | 504 | No transcript returned: application timeout. |
| hindi-02 | 2.76 s | 22.86 s | 200 | Virat Kohli becomes “राद कोली”; sentence-count wording is also corrupted. |
| hindi-03 | 6.12 s | 28.19 s | 200 | Rohit Sharma and ODI terminology are substantially corrupted. |
| hindi-04 | 5.34 s | 15.06 s | 200 | Comparison remains recognizable, but names and ODI terminology are distorted; Joe Root becomes “जो रोड”. |
| mixed-01 | 3.54 s | 27.96 s | 200 | Output uses Devanagari. Script alone is acceptable, but cricket/short-sentence wording is garbled. |
| mixed-03 | 3.00 s | 45.14 s | 504 | No transcript returned: application timeout. |
| english-long-03 | 32.04 s | 31.98 s | 200 | 2019/2023 become 29/23; Shubman Gill becomes Shubh Mandil; Jadeja/Ashwin names differ; batting becomes betting. |
| english-long-04 | 33.12 s | 32.96 s | 200 | 6.25 retained, but runs per over becomes runs per hour; men’s T20I format is distorted. |
| hindi-long-01 | 32.64 s | 45.17 s | 504 | No transcript returned: application timeout. |
| hindi-long-02 | 33.42 s | 45.19 s | 504 | No transcript returned: application timeout. |

## Timeout and cancellation finding

Cloud Run request metadata shows a POST starting at 06:25:05.048 UTC finishing
with HTTP 200 after 65.607 seconds. Its timestamp corresponds to `hindi-01`,
which returned HTTP 504 from the application after 45.140 seconds. There is
no propagated per-clip trace ID, so this association is by sequence and time.
The following request started before that prior cloud request finished.

This demonstrates that application timeout is not a reliable end-to-end stop
in the tested app → local proxy → Cloud Run path. It does not prove the exact
inference duration or why cancellation did not propagate. Worker-side
cancellation through the intended production service-account path remains a
release gate. Do not hide this by extending timeouts or retrying audio.

## Decision and next work

The tested configuration is not ready for the intended conversational pilot:
latency is high, four requests yield no usable text, and player names, formats
and numbers differ materially from the reference prompts. This batch is enough
to reveal those problems, but not to calculate a release accuracy score or
attribute every discrepancy to the model without listening verification.

1. Review the playable comparisons and correct any reference that differs from
   the recording. Keep the original reference and record corrections separately.
2. Trace one bounded transcription request through cancellation, then investigate
   decoder settings and language handling using this fixed corpus. Compare any
   candidate on the same files and retain the current model as the baseline.
3. Require both meaningful accuracy improvement and lower end-to-end latency
   before replacing the worker. Do not silently move to 8 vCPU/GPU, expand
   timeouts, provision the pilot database, or lower the release targets.
4. After a candidate succeeds on this diagnostic batch, complete the 20 short
   questions per language group, additional speakers, no-speech controls and
   near-60-second/concurrency tests needed for release.

## Inspectable evidence

- [Playable reference/transcript comparisons](../../.local/voice/recordings/2026-10-04/results/review.html)
- [Raw per-request report](../../.local/voice/recordings/2026-10-04/results/report.json)
- [Audio inventory and original-manifest provenance](../../.local/voice/recordings/2026-10-04/inventory.json)
- [Cloud request timing metadata](../../.local/voice/recordings/2026-10-04/request-latencies.json)

These evidence links are local and ignored by Git. They are not published.
All 12 clips were attempted exactly once. The evaluator exited with status 1
because four checks failed; all results were saved. No real recordings were
sent to Gemini or Kokoro during this batch. This evaluates speech input only,
not generated-voice quality or a complete voice conversation.
