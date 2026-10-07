# Cloud voice deployment evaluation — Columbia email pilot

Reviewed: 2026-10-01. This is a deployment decision and release plan, not a
record of a cloud release. The goal is a low-cost pilot available to anyone who
can complete sign-in through a verified `@columbia.edu` mailbox, including people
whose mailbox is not a Columbia-managed Google account. This proves control of
that mailbox, not current university enrollment or employment. No Cloud Run
service, IAM policy, database, build trigger, or production traffic changed as
part of this evaluation.

Implementation update: the pilot code and release build definitions described
below are now present. Firebase API authorization and UID ownership, email-link
frontend, PostgreSQL voice quotas, early upload admission, the post-upload
deadline, and pinned multilingual worker/app Dockerfiles are implemented.
The evidence table below is the **pre-implementation review snapshot**; its
"verified local code" rows identify defects that prompted these changes, not
current behavior. See [pilot release procedure](deploy/PILOT_RELEASE.md) for
current state. A private worker was built and smoke-tested in Cloud Run;
[its candidate record](deploy/VOICE_CANDIDATE_2026-10-02.md) contains preliminary
synthetic results; an 8-vCPU/eight-thread trial got under five seconds on two
short clips at the worker, while quality and full-path latency remain open. Release-grade
speech quality/latency gates, real Columbia email delivery, Cloud SQL pricing,
and the user-facing pilot release remain unverified.

## Decision

The existing dictation flow can run in Google Cloud. Use a separate, private
Cloud Run `whisper.cpp` service called by FastAPI with a Google service-account
ID token. Benchmark a Linux x86-64 CPU image before preparing the user-facing
release. If its measured quality and latency pass the gates below, release a
**separate pilot app service** with Firebase Authentication email-link sign-in.
The pilot app exposes a login shell publicly and verifies a Firebase ID token
for every data/API request. Keep the existing `agenticai-git` service and its
IAP policy intact during pilot validation.

The current browser **Read aloud** control uses `speechSynthesis`. It does not
need the Whisper worker; available voices and pronunciation depend on the
browser/device. This evaluation covers speech input, not a server-side spoken
answer service.

| Hosting option | Assessment |
| --- | --- |
| Separate Cloud Run CPU worker | Recommended first candidate: isolates model memory and scales to zero; CPU latency and cold starts need measurement. |
| Model in the app container | Every app instance would load the model and compete with chat; unsuitable at the current 512 MiB app setting. |
| Always-on CPU VM | Consider only if measured steady demand or cold-start latency warrants the added VM operations and idle cost. |
| Cloud Run L4 GPU | Requires a CUDA image, at least 4 vCPU/16 GiB, and instance-based billing; revisit only if CPU misses the release targets. [GPU requirements](https://docs.cloud.google.com/run/docs/configuring/services/gpu) |

## Evidence and what remains unverified

| Type | Finding |
| --- | --- |
| Historical cloud observation | Read-only inspection found one Cloud Run service, `agenticai-git` in `us-central1`, serving old revision `agenticai-git-00003-c78` (image tag `31798f36b5064cc2d7c5e5936a3f5dfe5e10b79a`) at 100% traffic. It used 1 vCPU/512 MiB, concurrency 80, a 300-second timeout, maximum 20 instances, the Compute default service account, and IAP. No speech worker was deployed. Recheck before any release. |
| Historical cloud observation | The active Cloud Build trigger watched `gemini-test-project-1`; pushing that branch could deploy the old build. Cloud SQL Admin API was disabled, so SQL instance inventory could not be verified through it. Neither user entitlements nor billing/quota balance was verified. |
| Verified local code | FastAPI accepts 8 MiB recordings up to 60 decoded seconds, bounds the request body at 10 MiB, allows 30 seconds to upload, converts with FFmpeg, and calls the worker's `/inference`. `STT_AUTH_MODE=google_id_token` and `STT_AUDIENCE` already support service-to-service authentication. The app has no user authentication or owner binding at its route call sites. |
| Verified local code | `deploy/voice/Dockerfile` pins whisper.cpp source and checks model SHA-256, but its default is **English-only `base.en`** and its command hardcodes `--threads 4`. The local multilingual `small` model is not the Docker build default. Base image and OS packages still need release pinning. |
| Verified local code | `/transcribe` buffers and parses the body before acquiring the process-local voice semaphore. The existing cloud app's 80-request concurrency and 512 MiB are unsafe assumptions for simultaneous large uploads. `/voice/config` reports configured state, not worker readiness. |
| Unmeasured | Mac M4 Metal latency does not predict Linux x86-64 CPU performance. One synthetic Hindi sentence had a player-name error; real-speaker Hindi, mixed-language, noise, peak memory, cold load, Cloud Run timeout behavior, and account-specific total cost remain unmeasured. [Cloud Run's x86-64 contract](https://docs.cloud.google.com/run/docs/container-contract) |

## User access and data boundaries

Use [Firebase Authentication email-link sign-in](https://firebase.google.com/docs/auth/web/email-link-auth)
for the **new pilot service**. Enable both Email/Password and Email link
(passwordless) in Firebase, while offering only the email-link UI. Authorize its
HTTPS callback domain. Add a public SPA route such as `/auth/finish` to complete
`isSignInWithEmailLink` / `signInWithEmailLink`; the present FastAPI app only
serves the SPA at `/`, so the callback must be added explicitly. A user opening
the link on another device supplies the same address again. Never put the
address in the link's return URL. Keep the login shell, its assets, the callback,
and a narrow health endpoint public. Disable or protect development API docs.

For every API/data request, the React client sends the Firebase ID token in
`Authorization: Bearer`. FastAPI verifies it with the Firebase Admin SDK against
the expected project, including signature, issuer, audience, and expiry. It
then requires `email_verified` to be true and the email domain after the **last
`@`** to equal `columbia.edu` after case folding. This excludes subdomains and
lookalikes. Do not use a Google `hd`/hosted-domain claim: the requirement is
verified mailbox ownership, which can include non-Google accounts. Use the
verified Firebase `uid` (namespaced in `owner_id`) as the conversation owner,
never the email, a UUID alone, or a browser-supplied identity header. Check
revocation/disabled-user behavior deliberately during implementation; the
chosen pilot policy is to reject revoked tokens at API verification, accepting
its added verification latency. [Firebase token verification](https://firebase.google.com/docs/auth/admin/verify-id-tokens)

Protect `/voice/config`, `/transcribe`, chat, session allocation, transcripts,
charts, recovery, and deletion consistently. The current frontend sends no
bearer token, and all `ConversationRepository` calls in `app.py` omit `owner_id`;
those are required application changes. Existing anonymous conversations have
`owner_id=NULL`: deny them to pilot users rather than assigning them to the
first person with the UUID. On a 403 for a stored session, clear that local
session reference and offer a new session. Preserve the transcription request
and response bodies, the editable-draft flow, and text chat when voice fails.

A public login shell means the pilot Cloud Run app accepts unauthenticated
platform invocation; **FastAPI authorization is the data boundary**. Test that
all API routes reject missing and invalid tokens before expensive work. The
existing IAP-protected service remains a separate deployment. Configure the
worker with no public invoker grant; only the pilot app's dedicated service
account gets `roles/run.invoker`, and `STT_AUDIENCE` matches the worker service
URL. The browser never calls Whisper directly. The upstream server has a
model-loading route, so keep the whole service private. [Cloud Run
service-to-service authentication](https://docs.cloud.google.com/run/docs/authenticating/service-to-service)

Email-link sign-in verifies control of the mailbox, but public email sending
can be abused or throttled. Apply exact-domain checks in the sign-in UI and
server; the UI check alone is not access control. Test delivery to a real
Columbia address and confirm provider anti-abuse behavior and quota. Firebase
currently lists **5 sign-in emails/day** on Spark and **25,000/day** on Blaze;
configure an appropriate billed plan before a wider pilot. [Firebase Auth
limits](https://firebase.google.com/docs/auth/limits)

## Worker build, admission, and deadlines

Build a `linux/amd64` worker image with explicit `MODEL=small` and
`MODEL_SHA256=1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b`;
fail the release if the checksum or expected multilingual model is absent.
Pin the release base-image digest, record the image digest and complete run
command, and smoke-test Hindi/Auto-detect before publishing. Begin benchmarking
at **2 vCPU/2 GiB, `--threads 2`, concurrency 1, minimum instances 0, and
service-level maximum instances 3**. Compare four threads on the same 2-vCPU
class rather than assuming more threads help. Adjust final resources only from
measured peak memory, warm/cold latency, and throughput. Verify the pinned
server's `/health` response after model load with a startup probe; verify its
disconnect cancellation in the deployed candidate rather than assuming that a
browser Cancel stops computation. Its `/load` route must remain inaccessible to
users. Cloud Run can briefly exceed a maximum-instance setting, and excess
requests may wait up to 30 seconds then fail. [Startup probes](https://docs.cloud.google.com/run/docs/configuring/healthchecks),
[maximum instances](https://docs.cloud.google.com/run/docs/configuring/max-instances-limits)

Acquire bounded app-side voice admission **before** reading or parsing the
upload, release it on every exit path, and cap concurrent body memory. Keep the
existing fail-fast `429 voice_busy` and `Retry-After` behavior for overload;
do not add a durable queue or automatic audio replay to the pilot. A process
semaphore is not a user quota across replicas, so enforce per-user limits in
shared storage. Test text chat under voice load.

Keep the existing 30-second upload deadline. Add a **60-second wall-clock
post-upload deadline** that includes FFmpeg conversion, obtaining the worker ID
token, waiting for downstream capacity, upload to the worker, and inference.
The present `httpx.AsyncClient(timeout=45)` is a per-operation timeout, not a
45-second whole-request deadline. Keep conversion at 10 seconds and bound
credential acquisition and connect/write/read phases within the overall
budget. Return the existing bounded 504 response if that deadline expires.
Check cancellation at FastAPI and the worker; a Cloud Run request timeout
closes the connection but can leave application code running. Preserve the app
service's longer chat timeout independently. [HTTPX timeouts](https://www.python-httpx.org/advanced/timeouts/),
[Cloud Run request timeout](https://docs.cloud.google.com/run/docs/configuring/request-timeout)

## Cost and availability

At the published `us-central1` request-based list rates, a hypothetical
2-vCPU/2-GiB worker costs `$0.000053` per active instance second plus
`$0.0000004` per request, before discounts/free tier. The shared billing
account's remaining free tier is unknown. These **worker-only** scenarios
assume one recording occupies one worker instance for exactly the stated
billable time and omit startup/shutdown and retries:

| Recordings/month | 10 billed seconds each | 30 billed seconds each |
| ---: | ---: | ---: |
| 1,000 | $0.53 | $1.59 |
| 10,000 | $5.30 | $15.90 |
| 100,000 | $53.04 | $159.04 |

A 2-vCPU/2-GiB minimum instance **idle for every second** of a 30-day month
would cost about **$25.92 gross** at the listed idle rates. Active intervals
are billed at active rates instead of idle rates: do not add a full idle month
to a full month's active charges. Begin with minimum instances 0 and revisit
only after cold-start measurement. [Cloud Run pricing](https://cloud.google.com/run/pricing)

The complete pilot estimate must include both worker and app instance time.
FastAPI remains active while waiting for Whisper; concurrent requests can share
one app instance, so calculate billed **instance time**, not a naive sum of
request durations. Include startup/shutdown, Cloud SQL, Firebase
Authentication/email quota, Gemini, build and model-image storage, logging,
app-to-internet/cross-region egress where charged, and monitoring. Same-region
Cloud Run-to-Cloud Run transfer has no networking charge. Budget alerts and a
service-level instance cap reduce exposure but do not guarantee a hard bill
ceiling. [Cloud Run pricing](https://cloud.google.com/run/pricing),
[budget guidance](https://docs.cloud.google.com/billing/docs/how-to/budgets)

Database provisioning is **not part of the isolated speech benchmark**. For a
user-facing pilot, choose and price a zonal, dedicated Cloud SQL PostgreSQL 16
configuration with backups and accepted zone downtime before provisioning.
Select the edition explicitly; PostgreSQL 16 can default to Enterprise Plus.
Document machine tier, 10+ GiB storage choice, backup retention, connections,
region, network path, and monthly price. Google's shared-core `db-f1-micro` and
`db-g1-small` are for development/test and lack the Cloud SQL SLA. Regional HA
is a later availability decision, not an automatic pilot requirement.
[Cloud SQL editions](https://docs.cloud.google.com/sql/docs/postgres/choose-edition),
[instance guidance](https://docs.cloud.google.com/sql/docs/postgres/instance-settings)

## Release order and go/no-go

1. **Isolated worker experiment:** build the pinned multilingual x86 image,
   deploy a private candidate in `us-central1`, verify IAM denial for an
   unauthenticated caller, `/health`, Hindi smoke, and cancellation. Run the
   corpus below and record image digest, threads, RSS, p50/p95, cold load,
   error rate, throughput, and a revised cost calculation. If CPU fails, revise
   model/resources and rerun; do not silently relax targets or enable GPU.
2. **Pilot app preparation:** add Firebase email-link login and API ownership
   enforcement, bounded upload admission, the total deadline, FFmpeg and built
   React assets in a reproducible app image, direct auth dependencies, and
   explicit Cloud SQL migration/import jobs. Keep the old IAP service and its
   branch-triggered release path separate. Price and prepare the database only
   after the speech experiment passes.
3. **Candidate validation:** deploy the new pilot app without moving old-service
   traffic; test desktop and mobile sign-in and recording, database persistence,
   wrong-user denial, text chat, budgets, observability, and rollback. Start with
   a small Columbia-user cohort before inviting the wider domain.

Use consented real-speaker clips in **three separate groups**: English, Hindi,
and Hindi-English mixed. Each group has at least 20 short questions, five long
recordings, and five silence/noise controls. For each group, at least **18/20**
short questions must preserve the intended player, format, date, and metric;
all five controls must produce no transcript. Measure word error rate and
entity errors separately, including names and numbers. Warm p95 from Stop to
editable text must be **at most 5 seconds** for 5–15-second clips; cold short
clip target is **at most 20 seconds**. Exercise 60-second recordings under the
post-upload deadline. These targets are provisional release gates, not measured
claims. Two simultaneous users must complete without mixed transcripts or
unbounded degradation; overload must produce a clear bounded response.

Before opening the pilot, test verified Columbia login, non-Columbia and
unverified email rejection, expired/reused links, expired/revoked tokens,
missing-token API requests, cross-user sessions, stale anonymous UUIDs,
private-worker direct denial, malformed/large uploads, worker failure,
concurrent upload memory, cancellation, mobile browser support, and rollback.
Recheck the project resources, current prices, auth email quota, full monthly
cost, Cloud SQL configuration, and Cloud Run quota at that point. No published
availability or latency promise follows from local Mac tests.
