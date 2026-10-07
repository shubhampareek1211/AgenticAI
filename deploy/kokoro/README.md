# Kokoro read-aloud pilot

The optional [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) worker turns
saved **English** assistant answers into 24 kHz mono WAV. It is separate from
Whisper dictation and Gemini's reasoning. The default app still uses the
browser's speech engine. When `TTS_ENABLED=true`, the app first asks the
private worker for an English answer; it falls back to browser speech if that
request fails. Hindi/Devanagari stays on the browser voice. Romanized Hindi is
not detected reliably and must be reviewed before widening language support.

The frontend sends only the conversation and message IDs. FastAPI checks
Firebase authorization, conversation ownership, the assistant role, and the
shared per-user/global daily voice quota, then strips Markdown and sends text
to the worker. The worker does not call Gemini or see user credentials. It
allows one synthesis at a time and returns `429` with `Retry-After` under load.
The app has its own bounded admission and a 90-second total synthesis deadline.
It streams short WAV segments to the browser so playback starts before a long
answer has finished generating. The page labels the active voice and reports
when it falls back to browser speech.
Responses are not cached. The 1,800-character answer and 180-second audio
limits cap CPU work. A browser cancellation does not guarantee that an already
running worker synthesis stops; verify cancellation and cost under load.

## Run locally

The Dockerfile pins Python 3.11, CPU PyTorch, Kokoro, the English spaCy data,
and the model revision `f3ff3571791e39611d31c381e3a41a3af07b4987`.
The build downloads that exact revision and verifies the model SHA-256;
an update requires reviewing and recording a new revision. The image preloads the model and `af_heart`
voice and runs offline afterward. The eSpeak data layout is set at startup;
this also works around a macOS wheel path issue observed during testing.

From the repo root, with Docker running:

```bash
docker build -f deploy/kokoro/Dockerfile -t cricket-kokoro .
docker run --rm -p 127.0.0.1:8082:8080 cricket-kokoro
curl -fsS http://127.0.0.1:8082/health
```

Then start the app with `TTS_ENABLED=true` and
`TTS_BASE_URL=http://127.0.0.1:8082`. The default local app permits an
unprotected localhost worker for development only. To prepare listening
samples from a separate Python 3.12 environment, install `kokoro==0.9.4`,
`transformers>=4.51,<5`, NumPy, and the
[`en_core_web_sm` 3.8.0](https://github.com/explosion/spacy-models/releases/tag/en_core_web_sm-3.8.0)
wheel, then run `python scripts/kokoro_samples.py --repeats 3` in that
environment. The script writes WAVs and timings to `.local/kokoro-eval/`.
The measured local results and release gates are in [BENCHMARK.md](BENCHMARK.md).

## Pilot deployment and release checks

Benchmark the isolated worker before enabling TTS on the app. Build a
`linux/amd64` image for Cloud Run; the Docker command above builds the host's
native architecture. Start at 2 vCPU,
2 GiB, concurrency 1, minimum instances 0, maximum instances 3; these are
experiment settings, not validated capacity. Keep the worker private to the
FastAPI service account using Cloud Run IAM. Set `TTS_AUTH_MODE=google_id_token`,
`TTS_AUDIENCE` to its service URL, and `TTS_BASE_URL` to the same URL. Do not
place the worker behind a public unauthenticated route. Pilot/production app
configuration refuses to enable generated speech without HTTPS and an IAM ID
token audience. Set the Cloud Run
request timeout above the app's 90-second synthesis deadline and examine cold
starts separately. Startup CPU and memory, both services' billed time while
the app waits, and generated WAV transfer all affect cost.

Before enabling it for users, check an actual Cloud Run image build, cold and
warm latency, two simultaneous users, overload, worker failure, quota behavior,
cross-user access, and mobile playback. Have English and Hindi speakers listen
to the generated samples, especially player names, statistics, long answers,
and code-switched speech. Hindi quality is not approved for generated playback.
The browser fallback should work without the worker. The model card lists
Apache-2.0 for Kokoro; confirm the image dependency inventory and license
notices before distribution. The cloud build, API usage, and deployment record
are in [CLOUD_API.md](CLOUD_API.md).
