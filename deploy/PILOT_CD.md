# Pilot application build and GitHub deployment

The assignment requires a Cloud Run website continuously deployed from GitHub.
This release uses a public `agenticai-columbia-pilot` gateway and a private
`agenticai-data-pilot` backend. The gateway serves the login shell and frontend,
then forwards authenticated API calls to the backend using Cloud Run IAM. The
backend is the only app component with a database connection. The existing
`agenticai-git` service, IAP policy, and trigger on `gemini-test-project-1`
remain separate. The private Whisper and Kokoro worker images are unchanged.

## Current deployment, checked 2026-10-07

- Project `phonic-weaver-475017-n1`, Cloud Run region `us-central1`.
- Public gateway: `agenticai-columbia-pilot`, revision
  `agenticai-columbia-pilot-00001-h68`,
  <https://agenticai-columbia-pilot-3d4n5heeaq-uc.a.run.app>. Image digest:
  `sha256:072faa315bffadd22c2ece886ad2b35bbf4e84c11955ccd790afe4703c9c8318`.
  It runs as `agenticai-gateway-pilot@phonic-weaver-475017-n1.iam.gserviceaccount.com`
  with concurrency 2, maximum instances 2, and minimum instances 0.
- Private backend: `agenticai-data-pilot`, revision
  `agenticai-data-pilot-00001-5xb`. Image digest:
  `sha256:0ab332b654f1265bf5299aa2a13b0e6a88ef79d8c12c29f11b8ada17374926e8`.
  It runs as `agenticai-app-pilot@phonic-weaver-475017-n1.iam.gserviceaccount.com`
  with concurrency 4, maximum instances 2, and minimum instances 0. Cloud SQL
  is attached to this service only.
- Artifact Registry Docker repository `agenticai-pilot` exists in `us-central1`.
- The gateway identity invokes the backend through Cloud Run IAM. The gateway
  has no database URL or SQL client role; the backend alone connects to Cloud
  SQL and verifies the forwarded Firebase ID token. Firebase authorized domains
  include the public gateway host.
- The existing Cloud Build trigger is in `europe-west1`; it builds and deploys
  `agenticai-git` from `gemini-test-project-1`. It is not a pilot trigger.
- The pilot images were built from the local checkout and deployed by digest.
  The new code remains uncommitted on local branch `feat/cricket-analyst`;
  no pilot GitHub trigger exists. The cricket source import is still running.
  Signed-in end-to-end cloud use has not yet been verified.

## Cloud SQL configuration and cost

Cloud SQL instance `agenticai-pilot-pg16` is `RUNNABLE`. Its low-cost pilot
configuration is PostgreSQL 16,
`ENTERPRISE` edition, zonal `us-central1`, shared-core
`db-g1-small` (1.7 GiB memory), 10 GiB SSD, public IP used only through the
Cloud SQL Auth Proxy/Unix socket,
daily automated backups retained for seven days, no automatic storage
increase, deletion protection, and no regional HA. The instance reports a
public IPv4 address and no authorized external client networks. Check the
point-in-time recovery setting separately. A restricted database role and
Secret Manager connection URL are used by the private backend only. Monitor
free space against the 10 GiB ceiling and revisit it if imports grow. The shared-core tier has
no Cloud SQL SLA; this pilot accepts downtime.

At published `us-central1` list rates and 730 hours, instance compute is
`730 × $0.035 = $25.55/month`; 10 GiB SSD is
`730 × 10 × $0.000232877 = $1.70/month`, for a **$27.25/month baseline** before
backups and network. Backup storage is charged by actual retained bytes: one
GiB of average used backup storage would add
`730 × $0.000109589 ≈ $0.08/month`. Seven retained backups are not necessarily
seven full billable copies; measure actual used bytes after import. Network
egress, Secret Manager, Cloud Run gateway/backend/worker instance time, Gemini,
Firebase usage beyond allowances, builds, Artifact Registry storage, and logs
are additional. Both app services run with minimum instances zero;
instance caps and budget alerts reduce risk but do not guarantee a spend cap.
Verify the source import and total release cost before inviting users. See
[Cloud SQL pricing](https://cloud.google.com/sql/pricing?hl=en),
[instance creation options](https://docs.cloud.google.com/sql/docs/postgres/create-instance),
and [Cloud Run connection guidance](https://docs.cloud.google.com/sql/docs/postgres/connect-run).

## Rebuild a candidate

`cloudbuild.pilot-app.yaml` builds both `deploy/app/Dockerfile` (the private
backend) and `deploy/gateway/Dockerfile` (the public gateway). It publishes
unique `backend:$BUILD_ID` and `gateway:$BUILD_ID` tags. The Docker builds run
frontend tests and produce production assets. Ignored `.local/` credentials and
recordings are excluded from build context. The backend production Uvicorn
process disables its own access log so one-time email-link query parameters
are not written there. Check the gateway and Cloud Run platform request logs
during callback validation; that flag does not control platform logs.

The current full backend suite passed **246 tests, with one skipped**; the
frontend suite passed separately. Run these checks again when code changes.
For a new candidate from the intended checkout:

```bash
gcloud builds submit . --config cloudbuild.pilot-app.yaml \
  --region us-central1 --project phonic-weaver-475017-n1
```

Record the build ID and resolve both published tags to `sha256:` digests with
`gcloud artifacts docker images describe`. Deploy immutable digests. The
gateway must not have any `DATABASE_URL`; the backend must use only the Cloud
SQL connection URL, never a local development URL.

## Remaining release gate

The user's data rule is implemented through the private backend. It is the
sole database client, runs `APP_ENV=pilot` and `DATA_ENDPOINT_MODE=private`,
and verifies the Firebase token forwarded in `X-Firebase-Authorization`.
The public gateway has no database credentials. Its `BACKEND_BASE_URL` and
`BACKEND_AUDIENCE` point to the same private HTTPS origin, and it uses its own
IAM token to invoke the backend. The source import is still in progress;
complete it and verify intended table counts before testing cricket answers.

The public login shell and callback domain are configured. Verify a real
Columbia sign-in, anonymous API denial, conversation isolation and persistence,
three assignment sample queries, visible tool calls, charts, speech, gateway
overload/cancellation behavior, and rollback before enabling the trigger.
Confirm the grader has a permitted Columbia mailbox or agree on grader access
before submitting the URL. Record service revisions, image digests, runtime
identities, endpoint URL, and rollback revisions. Check that `agenticai-git`
traffic and IAM remain unchanged. `submission.json` must contain the tested
gateway URL and every team member's UNI or email.

## Enable continuous deployment after validation

`cloudbuild.pilot-cd.yaml` builds and pushes both images, updates only the
image of the already configured private backend, then updates only the image
of the public gateway. Cloud Run resolves each unique tag to an immutable
digest in its revision. The file does not set service environment, data
credentials, ingress, or IAM. A missing service makes the build fail rather
than creating an unconfigured public service. If gateway deployment fails
after backend deployment, route backend traffic to its previous good revision
before retrying.

Use a dedicated `pilot-release` GitHub branch. Create a separate Cloud Build
identity with the minimum access needed to write to the `agenticai-pilot`
Artifact Registry repository, update only these two Cloud Run services, write
build logs, and act as their runtime service accounts. Review the exact scoped
IAM bindings before granting them; do not reuse the legacy trigger's Compute
Engine default service account. The already connected GitHub repository is a
Developer Connect link in `europe-west1`, so a separate trigger can use:

```bash
gcloud beta builds triggers create developer-connect \
  --project phonic-weaver-475017-n1 \
  --region europe-west1 \
  --name agenticai-columbia-pilot-cd \
  --git-repository-link projects/phonic-weaver-475017-n1/locations/europe-west1/connections/connection-bvltgsl/gitRepositoryLinks/shubhampareek1211-AgenticAI \
  --branch-pattern '^pilot-release$' \
  --build-config cloudbuild.pilot-cd.yaml \
  --service-account projects/phonic-weaver-475017-n1/serviceAccounts/PILOT_BUILD_SERVICE_ACCOUNT_EMAIL
```

The exact branch pattern keeps other branches, including the legacy
deployment branch, out of this pipeline. Push a reviewed commit to
`pilot-release`, confirm a successful triggered build and both new revisions,
exercise the grader queries in a fresh browser, and verify the old service's
traffic again. Preserve previous good pilot revisions for rollback. Keep the
pilot running until grades are released.

Google Cloud references: [GitHub build triggers](https://docs.cloud.google.com/build/docs/automating-builds/create-manage-triggers),
[Cloud Build to Cloud Run deployment](https://docs.cloud.google.com/build/docs/deploying-builds/deploy-cloud-run),
[Developer Connect trigger command](https://docs.cloud.google.com/sdk/gcloud/reference/beta/builds/triggers/create/developer-connect).
