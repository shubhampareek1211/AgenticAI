# Speech worker release notes

This directory builds a Linux CPU `whisper-server` using whisper.cpp `v1.9.4`
(commit `927cfce34f31707e17f2bff35c349632fb9e2c3a`). The build checks that revision and the
SHA-256 of a model from Hugging Face repo revision
`5359861c739e955e79d9a303bcbc70fb988958b1`. The only accepted build
model is multilingual `small`, with
`MODEL=small` and
`MODEL_SHA256=1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b`.
The default command uses two threads; compare `WHISPER_THREADS=4` on 2 vCPU
before selecting the release setting. The image runs as an
unprivileged user with one model loaded at startup and expects only normalized
WAV from FastAPI. It does not enable the worker's FFmpeg conversion route.
The image carries the upstream whisper.cpp MIT license; the model repository
declares an MIT license in its model card.

The Dockerfile pins the Debian base-image index digest. Apt package versions
are resolved during build; record them and the final worker image digest, and
build and smoke it in the target Linux environment. A source tag and model hash
alone do not make the entire container byte-for-byte reproducible.

Do not route browser requests directly to this server. It includes model
management endpoints such as `/load`. For Cloud Run, require authentication on
the entire worker service; grant Invoker only to the FastAPI service account.
FastAPI uses an ID token with this service URL as the audience when
`STT_AUTH_MODE=google_id_token`. The separate pilot app uses verified Firebase
email-link identity for browser APIs; the existing Cloud Run app retains IAP.
See [pilot release procedure](../PILOT_RELEASE.md) for the remaining validation
gates and current implementation state.

Start capacity evaluation with CPU, request concurrency 1, min instances 0,
and a small max instance cap. Measure CPU and peak resident memory during 60s
audio, model load/cold start, p50/p95 inference latency for 5–15s and 30–60s
clips, and queue/rejection behavior with two simultaneous users. Include FFmpeg
and temporary WAV memory/storage in the FastAPI service budget. Do not set a
Cloud Run memory tier from the model file size alone.

Before selecting a service tier, use the current Cloud Run regional CPU,
memory, request, and network prices. For a scale-to-zero estimate, multiply
requests per month by measured billable seconds per request and the allocated
CPU/memory rates; include cold starts, requests, build/artifact storage, and
network charges. For a warm-instance estimate, bill idle CPU and memory only
for idle intervals; active intervals use active rates. Record the region,
prices/date, measured traffic, resource settings, both estimates, and an expected upper bound. Prices and
quotas change, so fetch them at release time.

Candidate smoke after IAM setup: authenticated browser Record → Stop → edit →
Send, follow-up question, chart, and refresh; verify direct unauthenticated
worker access fails. Save the image/model digests and a rollback command that
sets `VOICE_ENABLED=false` for the app revision.
