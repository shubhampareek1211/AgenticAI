# Local voice model spike — 2026-10-01

The running preview now uses multilingual `small` for English, Hindi, and
Auto-detect. The English-only results below are historical and do not measure
Hindi quality. One synthetic Hindi clip spoken by macOS Lekha, "विराट कोहली ने
2019 में कितने रन बनाए?", returned "विरात कोज्ली ने 2019 में कितने रन बनाये"
in Hindi and Auto-detect modes. This shows a meaningful player-name error.
The existing English cricket WebM sample still transcribed correctly with the
multilingual model. No real-speaker Hindi, code-switching, or Linux benchmark
has been run.

The first local comparison ran pinned whisper.cpp v1.9.4 with checksum-verified
`base.en` and `small.en` on an Apple M4. The corpus has six cricket questions
spoken by each of two macOS synthetic voices, plus one digital silence clip.
Each clip was run once per configuration. This is a development smoke, not a
real-speaker accuracy study or a Linux Cloud Run capacity benchmark.

| Configuration | Word error rate | Exact entities preserved | Silence false positives |
|---|---:|---:|---:|
| `base.en`, no vocabulary hint | 24.58% | 28/40 | 1/1 |
| `small.en`, no vocabulary hint | 14.41% | 30/40 | 1/1 |
| `base.en`, fixed cricket hint | 8.47% | 36/40 | 1/1 |
| `small.en`, fixed cricket hint | 2.54% | 38/40 | 1/1 |

The fixed hint lists six player names and ODI/T20I. It improved the synthetic
transcripts without inserting those names into unrelated tested questions.
For example, the original `base.en` output for “Compare Virat Kohli and Joe
Root ODI runs in 2019” changed both names; hinted `small.en` returned the
intended sentence. Exact entity matching counts `ODIs` as different from
`ODI`, so the 38/40 figure is intentionally strict. A real-speaker corpus is
still required to check whether the hint biases transcriptions.

The raw worker emitted `[BLANK_AUDIO]` for digital silence. FastAPI now checks
normalized PCM amplitude before inference and maps that worker marker to
`no_speech`. Additional noise controls and varied real speech remain a release
gate; the single silence file is insufficient to establish robustness.

The initial English-only preview used hinted `small.en`. Recorded request latencies are in the
ignored reports under `.local/voice/`; the two models were benchmarked on the
same Mac and some runs overlapped, so their reported percentiles should not be
used to size production. A separate Linux CPU Docker image using `base.en`
built and transcribed one sample successfully. Container cold load and ongoing
throughput need repeat measurement. The [preliminary cost estimate](VOICE_COST_ESTIMATE.md)
is therefore still based on hypothetical processing time.

Reproduce this synthetic check with `scripts/voice.sh synthesize`, the ignored
corpus manifest `.local/voice/corpus/synthetic-comparison.json`, and
`scripts/voice-benchmark.py --prompt` as documented in the README. The raw
reports are `.local/voice/{base,small}{,-prompt}-synthetic-report.json`.

Before a production release, collect the planned 30 consented clips across
real speakers and accents, rerun both models on the target Linux CPU class,
check the silence controls and name/format accuracy, measure warm and cold
latency, and choose CPU/memory and a final model from those results.
