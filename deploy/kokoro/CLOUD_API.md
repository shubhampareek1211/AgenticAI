# Private Kokoro API on Cloud Run

## Deployment scope

Deploy only the English `af_heart` speech worker in project
`phonic-weaver-475017-n1`, region `us-central1`, service
`agenticai-kokoro-pilot`. This provides text-to-speech over HTTPS. Whisper
still handles speech-to-text; Gemini still produces the answer. This deployment
does not provision Cloud SQL, Firebase, or the new user-facing application.

The intended configuration is 2 vCPU, 2 GiB, two inference threads,
concurrency 1, request-based billing, service minimum 0 and maximum 3,
120-second request timeout, and a `/health` startup probe. Startup CPU boost
is disabled to keep the CPU allocation at two during this first benchmark.
The worker uses a dedicated service account with no application-data roles.
Cloud Run IAM authentication is required; no public Invoker binding is added.

At the [Cloud Run us-central1 list rates](https://cloud.google.com/run/pricing)
checked on 2026-10-03, worker active CPU and memory cost
`2 × $0.000024 + 2 × $0.0000025 = $0.000053/second`, plus
$0.40/million requests, before the shared free tier. At four billed seconds
per short synthesis, 1,000 requests would be about $0.21 in worker compute
and request charges. This is an illustrative assumption, not measured cost.
Build minutes, image storage, startup/shutdown, generated-audio transfer, and
the app's billed waiting time are additional. Minimum zero avoids an always-idle
instance charge; maximum three is not a guaranteed spending ceiling.

## Build and deploy

Run from the application checkout. The manual build is independent of the
existing app deployment trigger:

```bash
gcloud builds submit . --config cloudbuild.kokoro.yaml \
  --project phonic-weaver-475017-n1 --region us-central1
```

The build must pass the offline, 2-CPU/2-GiB HTTP synthesis check before it
pushes an image. The exact resolved Python dependency inventory is stored
at `/app/requirements-resolved.txt` inside the image. Use the resulting image
**digest**, rather than a moving tag, for deployment.

```bash
# One time only, after checking that this identity does not already exist:
gcloud iam service-accounts create agenticai-kokoro-pilot \
  --display-name='Isolated Kokoro speech worker' --project phonic-weaver-475017-n1

# Set KOKORO_IMAGE to the full Artifact Registry image@sha256:... from the build.
gcloud run deploy agenticai-kokoro-pilot \
  --project phonic-weaver-475017-n1 --region us-central1 \
  --image="$KOKORO_IMAGE" \
  --service-account=agenticai-kokoro-pilot@phonic-weaver-475017-n1.iam.gserviceaccount.com \
  --cpu=2 --memory=2Gi --concurrency=1 --min=0 --max=3 \
  --cpu-throttling --no-cpu-boost --timeout=120 --execution-environment=gen2 \
  --no-allow-unauthenticated --invoker-iam-check \
  --startup-probe=httpGet.path=/health,httpGet.port=8080,initialDelaySeconds=0,timeoutSeconds=3,periodSeconds=5,failureThreshold=36
```

## Call from your computer

Use your existing Google login and an authenticated local proxy. The proxy
listens on localhost and supplies credentials to the private cloud worker:

```bash
gcloud run services proxy agenticai-kokoro-pilot \
  --project phonic-weaver-475017-n1 --region us-central1 --port=8083
```

In another terminal:

```bash
curl --fail-with-body http://127.0.0.1:8083/synthesize \
  -H 'Content-Type: application/json' \
  -d '{"text":"Virat Kohli scored eighty two runs.","voice":"af_heart"}' \
  --output answer.wav
afplay answer.wav
```

`POST /synthesize` returns `audio/wav`, mono PCM16 at 24 kHz. The request
accepts `text` (1–1,800 characters) and optional `voice` (`af_heart` only).
Prefer short sentences of up to 220 characters for interactive playback.
Validation failures return 422; a busy worker may return 429 with
`Retry-After`; the app must retain its own admission, quota, and deadline.
An IAM-denied request returns 403 before it reaches the worker. The worker
does not enforce Columbia email policy; that belongs to the application API.

The deployed HTTPS endpoint can also be called directly from your terminal:

```bash
curl --fail-with-body \
  https://agenticai-kokoro-pilot-3d4n5heeaq-uc.a.run.app/synthesize \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H 'Content-Type: application/json' \
  -d '{"text":"Virat Kohli scored eighty two runs.","voice":"af_heart"}' \
  --output answer.wav
```

This uses your authorized developer identity. A deployed application uses
an audience-bound service-account ID token as described below; an API key or
a Firebase end-user token is not a substitute for Cloud Run IAM credentials.

To check real WAV data and save timings without handling tokens manually:

```bash
python3 scripts/kokoro_api_smoke.py --url http://127.0.0.1:8083 \
  --repeats 5 --output .local/kokoro-eval/cloud
```

## Connect the application

For local development with that proxy, set `TTS_ENABLED=true` and
`TTS_BASE_URL=http://127.0.0.1:8083` with `TTS_AUTH_MODE=none`. Only the
localhost hop is unauthenticated; the proxy authenticates the cloud call.

For a deployed backend, grant `roles/run.invoker` **on this service only**
to its dedicated app service account. Set:

```dotenv
TTS_ENABLED=true
TTS_BASE_URL=https://THE-KOKORO-SERVICE-URL
TTS_AUTH_MODE=google_id_token
TTS_AUDIENCE=https://THE-KOKORO-SERVICE-URL
TTS_TIMEOUT_SECONDS=90
```

The existing `KokoroClient` obtains an audience-bound Google ID token from
the Cloud Run runtime identity. Do not ship service-account keys or expose
the private worker directly from browser JavaScript. The frontend calls the
app's authorized saved-answer endpoint, which streams short WAV segments.

## Validation and rollback

Record the image digest, revision, IAM policy, resource settings, measured
request timings, and actual checks below after deployment. A first request
is not proof of a cold start; correlate startup logs for a cold measurement.
Inspect runtime memory before increasing text limits. Test two concurrent
requests and retain the application-level fail-fast limit. Cloud Run may
queue/scale requests before they reach the worker's semaphore.

Inference in this CPU worker is synchronous and can continue after a client
disconnect. The 90-second app timeout and 120-second Cloud Run request
timeout do not forcibly kill synthesis. Keep short chunks and capped text;
do not claim cancellation or production availability guarantees.

For later updates, shift traffic back to the recorded prior revision with
`gcloud run services update-traffic ... --to-revisions=PRIOR_REVISION=100`.
For this first isolated worker, disable `TTS_ENABLED` in the caller to return
to browser read-aloud. Deleting the worker service is a separate, deliberate
cleanup action; stored image/build artifacts are billed independently.

## Deployment record

Deployed on 2026-10-03:

| Item | Observed value |
| --- | --- |
| Endpoint | `https://agenticai-kokoro-pilot-3d4n5heeaq-uc.a.run.app` |
| Revision / traffic | `agenticai-kokoro-pilot-00001-vt5`, 100% of this isolated worker's traffic |
| Cloud Build | `f06d2835-b263-4843-bc69-0e8de837268d`, SUCCESS |
| Image | `us-central1-docker.pkg.dev/phonic-weaver-475017-n1/agenticai-pilot/kokoro@sha256:003cb75df871be5a97c0256037c5a87e6263e461fcdb7374476c98e960e27ee8` |
| Python base image resolved by build | `python:3.11-slim-bookworm@sha256:2333bd330d12de02514770b3585cad313644316047cdee24a7acfdece6de6efb` |
| Model revision | `f3ff3571791e39611d31c381e3a41a3af07b4987` |
| Model SHA-256 | `496dba118d1a58f5f3db2efc88dbdc216e0483fc89fe6e47ee1f2c53f18ad1e4` |
| Access check | Anonymous `/health`: 403. Service IAM policy has no public binding. Authorized proxy synthesis: 200. |
| Resource verification | 2 vCPU / 2 GiB; concurrency 1; service max 3; minimum 0; CPU boost disabled |
| Existing application | `agenticai-git-00003-c78` remains at 100% traffic; not connected to Kokoro |

The first build, `3ce02dea-f638-4155-89ea-3f51045a0361`, failed before
building the image because Cloud Build's Docker parser did not support the
inline Python heredoc. Model downloading was moved to `download_model.py`;
the succeeding build includes that correction. No failed image was deployed.

Ten serial synthetic requests, sent from the Mac through the authorized proxy:

| Sample | Generated audio | First request for that phrase | Four subsequent requests |
| --- | --- | --- | --- |
| Short cricket statistic | 3.90 s | 5.212 s | 2.274, 2.119, 2.017, 1.987 s |
| Longer cricket explanation | 10.85 s | 9.031 s | 6.730, 6.336, 6.286, 6.130 s |

All returned valid, non-silent, mono 24-kHz PCM16 WAVs. Empty text, oversized
text, unsupported voice, and Hindi text each returned 422. These are small
synthetic samples, not a meaningful production p95 or human listening result.
The initial deployment instance started at 08:37:13.365 UTC and passed its
startup probe at 08:37:43.462 UTC: approximately **30.1 seconds**. The first
synthesis was sent after readiness, so its 5.212-second timing excludes startup.

Two simultaneous longer-phrase calls both returned 200 in **6.433 s** and
**13.422 s**. This shows successful handling with waiting; it does not prove
instant parallel capacity or fail-fast overload at the Cloud Run ingress.
The real application `TTSService` then returned two correctly framed WAV
segments at **7.678 s** and **15.884 s**, including a successful end marker,
through the authenticated proxy. A separate direct HTTPS request using the
developer's Google ID token returned `200 audio/wav` in **2.903 s**. No token
was logged or saved. No cloud service-account-to-service-account request,
mobile playback, maximum-load, or client-disconnect cancellation claim is made.

**Release decision:** the private API is usable for developer/pilot calls,
but the ≤5-second warm and ≤20-second cold targets are not met by this
configuration. Keep the working local preview and public app defaults as they
are. The next resource/model experiment should compare an ONNX CPU runtime
on the same two CPUs and smaller initial audio chunks; cold-start optimization
needs a separate measurement. Do not silently increase CPU/GPU spending or
relax the targets. API integration is available independently of the later
authenticated application release.

Local evidence: `.local/kokoro-eval/cloud/report.json`, generated WAVs in the
same directory, `.local/kokoro-eval/cloud-service.json`, and
`.local/kokoro-eval/cloud-build.log`. Concurrency and app-client evidence is
in `.local/kokoro-eval/cloud/integration.json`; the direct call is recorded in
`direct-api.json` beside it. The authenticated local proxy is running on port
8083 for developer calls. Port 8001 continues to use the local worker on 8082.
