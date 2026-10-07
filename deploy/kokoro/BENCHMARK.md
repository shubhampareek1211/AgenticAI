# Kokoro pilot benchmark — 2026-10-03

These measurements are exploratory. They do not establish Cloud Run latency,
cost, quality, or 2 GiB memory safety. Audio files and the full JSON results
are in `.local/kokoro-eval/` on the development machine and can be regenerated
with `scripts/kokoro_samples.py`.

## Local CPU synthesis

Apple M4 Mac, 10 CPU cores, 16 GiB RAM, macOS ARM64, Python 3.12, Kokoro
0.9.4, model revision `f3ff3571791e39611d31c381e3a41a3af07b4987`,
PyTorch CPU with **two inference threads**. Three serial runs per phrase,
after the model was loaded; startup measured separately at about 2.4 seconds.
The test included no network, browser, authentication, or Cloud Run cold start.

| Phrase | Voice | Audio duration | Synthesis time, three runs |
|---|---|---:|---:|
| English cricket statistic | `af_heart` | 5.13 s | 1.13, 1.07, 1.01 s |
| English analysis | `af_heart` | 7.03 s | 1.53, 1.54, 1.42 s |
| Hindi cricket statistic | `hf_alpha` | 6.98 s | 1.29, 1.46, 1.45 s |
| Hindi with “ODI” | `hf_alpha` | 5.90 s | 1.18, 1.09, 1.10 s |

The Hindi rows prove only that the pipeline generated WAV files. No speaker
has judged intelligibility, accents, name/number pronunciation, or code
switching. The app offers generated audio only for English and retains browser
speech for Hindi. The official [voice list](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md)
grades the Hindi voices C, so enabling Hindi requires a listening gate.

## Local container smoke

The Docker image built successfully for native `linux/arm64`, loaded the model
offline, passed `/health`, and returned a playable 24 kHz mono WAV for the
English cricket statistic. A warm HTTP request with the intended two threads
took **3.55 seconds** without another benchmark running. One concurrent-load
observation took 4.33 seconds. These are single requests, not percentile
estimates. The arm64 image is about **1.88 GB** uncompressed and its idle
Docker memory reading was **1.26 GiB**. With Docker limited to 2 CPUs and
2 GiB, the worker became healthy and produced the same short WAV in
**3.78 seconds**. A memory sample during a longer synthesis was **1.47 GiB**;
two simultaneous requests returned one `200` and one fail-fast `429` with
`Retry-After: 1`. These spot checks do not establish peak memory under varied
answers or Cloud Run startup. Build and test `linux/amd64` for Cloud Run;
these ARM results cannot be substituted for that benchmark.

An end-to-end local check used a saved assistant answer in the dedicated
PostgreSQL test database. FastAPI retrieved the owned answer, called the
resource-limited worker, and returned `200 audio/wav` with a valid RIFF body.
This did not test Firebase or Cloud Run IAM; those paths have separate tests
and remain cloud release gates.

## Release decision

The integration is ready for an **opt-in local pilot**, with browser fallback.
It is not ready for cloud activation. Benchmark the exact amd64 image on a
private Cloud Run service at 2 vCPU/2 GiB and concurrency 1, including cold
startup, peak memory, 5–15-second answers, longer answers, two users, overload,
and app-to-worker IAM calls. Set provisional warm p95 ≤5 seconds and cold
short-answer latency ≤20 seconds; collect enough requests to estimate p95.
Have speakers review English output against the actual cricket answer text.
Do not enable Hindi generation until its own listening test passes. Confirm
cost for the worker and app request time, then decide whether 2 GiB is adequate
or the model/architecture should change.

## Illustrative Cloud Run arithmetic

At the [published us-central1 request-based rates](https://cloud.google.com/run/pricing),
2 vCPU plus 2 GiB costs `2 × $0.000024 + 2 × $0.0000025 =
$0.000053` per billed active second, plus $0.40 per million requests.
If a **cloud** worker eventually averaged four active seconds per warm short
answer, 1,000 nonoverlapping answers would be about **$0.21 gross worker
compute/request charges** before the shared free tier. This is hypothetical;
the Docker timing is not a cloud measurement. Cold startup/shutdown, longer
answers, the waiting FastAPI instance, authentication, database, Artifact
Registry storage, and operations add cost. With minimum instances zero there
is no fully idle worker baseline, but each cold startup is billable. Budget
alerts and maximum instances limit exposure but do not cap the bill exactly.
