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
  `agenticai-columbia-pilot-00007-zgw`,
  <https://agenticai-columbia-pilot-3d4n5heeaq-uc.a.run.app>. Image digest:
  `sha256:3a91152d31972a92a06e1fe4ad75e2d1227748c89640a9752c0d12318f5a4b0f`.
  It runs as `agenticai-gateway-pilot@phonic-weaver-475017-n1.iam.gserviceaccount.com`
  with concurrency 2, maximum instances 2, and minimum instances 0.
- Private backend: `agenticai-data-pilot`, revision
  `agenticai-data-pilot-00007-h7b`. Image digest:
  `sha256:b66a9c024c67e90a7fad548168ad7d3ebc87fc2411861620ad2322b71266f300`.
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
- `pilot-release` is the GitHub repository default branch. The pilot workflow
  deploys code pushes to that branch and skips documentation-only pushes.
- GitHub Actions [run 37707987074](https://github.com/shubhampareek1211/AgenticAI/actions/runs/37707987074)
  passed. It built both images from the
  `pilot-release` branch and deployed those immutable digests to the two pilot
  services. The ESPN-only public gateway returns `200` for `/health`, `/`, and
  `/auth/config`; anonymous session creation and `/voice/config` return `401`,
  and the private backend returns `403` without IAM. Signed-in ESPN-tool
  end-to-end use still needs release-gate verification.
- A dedicated Workload Identity Federation provider and resource-scoped pilot
  deployer permissions are configured for GitHub Actions. The old Cloud Build
  trigger and `agenticai-git-00003-c78` revision remain unchanged.
- Cloud Logging exclusion `agenticai-pilot-email-link-callbacks` is configured
  to drop gateway Cloud Run request logs for `/auth/finish`, because a synthetic
  callback probe showed its query string in those logs. A later synthetic
  probe verified the callback request was excluded while an ordinary request
  log remained available. The probe value is not recorded here.

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
free space against the 10 GiB ceiling. The Cricsheet import was stopped on
request, with partial data retained and no match/delivery rows. The
shared-core tier has no Cloud SQL SLA; this pilot accepts downtime.

At published `us-central1` list rates and 730 hours, instance compute is
`730 × $0.035 = $25.55/month`; 10 GiB SSD is
`730 × 10 × $0.000232877 = $1.70/month`, for a **$27.25/month baseline** before
backups and network. Backup storage is charged by actual retained bytes: one
GiB of average used backup storage would add
`730 × $0.000109589 ≈ $0.08/month`. Seven retained backups are not necessarily
seven full billable copies; measure actual used backup bytes. Network
egress, Secret Manager, Cloud Run gateway/backend/worker instance time, Gemini,
Firebase usage beyond allowances, builds, Artifact Registry storage, and logs
are additional. Both app services run with minimum instances zero;
instance caps reduce risk but do not guarantee a spend cap. The user chose not
to configure a budget alert for this pilot.
Verify live ESPN tool behavior and total release cost before wider pilot use. See
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
are not written there. The separately configured Cloud Logging exclusion was
verified with a synthetic callback probe; the Uvicorn flag does not control
platform request logs.

The current full backend suite passed **246 tests, with one skipped** while
the database test fixture was available; the frontend suite passed separately.
Without the database proxy, a local replay passed 102 tests and skipped 145.
The GitHub workflow uses that non-database test run and Dockerfile frontend
tests; run authenticated database and end-to-end checks before release.
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
IAM token to invoke the backend. Cricket answers now use ESPN calls from that
private backend; the retained partial Cricsheet import is inactive.

The public login shell and callback domain are configured. Verify a real
Columbia sign-in, anonymous API denial, conversation isolation and persistence,
three ESPN-only assignment sample queries, visible tool calls, speech, gateway
overload/cancellation behavior, and rollback before releasing through the
workflow. An ESPN scorecard-based visualization is optional follow-up work,
not a course release requirement.
Confirm the grader has a permitted Columbia mailbox or agree on grader access
before submitting the URL. Record service revisions, image digests, runtime
identities, endpoint URL, and rollback revisions. Check that `agenticai-git`
traffic and IAM remain unchanged. `submission.json` must contain the tested
gateway URL and every team member's UNI or email.

## Enable continuous deployment after validation

The release pipeline is `.github/workflows/pilot-deploy.yml` on the dedicated,
protected `pilot-release` branch. One PR approval is required before merge;
force pushes and deletion are blocked. A push to that branch runs backend tests,
builds both Dockerfiles (which also run the frontend tests), and publishes
`linux/amd64` images to the existing `agenticai-pilot` Artifact Registry
repository. Tags combine the commit SHA, GitHub run ID, and run attempt so
reruns cannot silently reuse a mutable tag. The workflow resolves each tag to
an immutable image digest, updates only `agenticai-data-pilot` and then
`agenticai-columbia-pilot`, and checks the public `/health` endpoint. It passes
no environment, identity, ingress, Cloud SQL, or IAM flags. A missing service
causes the update to fail rather than creating a new service. If the gateway
update fails after the backend succeeds, roll the backend back to its previous
good revision before retrying.

Authentication uses GitHub OIDC and Google Workload Identity Federation; no
service-account key or GitHub secret is stored. The provider accepts only push
events for immutable GitHub repository ID `1391460454`, owner ID `53017570`,
`refs/heads/pilot-release`, and the exact
`shubhampareek1211/AgenticAI/.github/workflows/pilot-deploy.yml` workflow.
The existing `agenticai-pilot-build` account can write only to the
`agenticai-pilot` Artifact Registry repository, has Cloud Run Developer on
the two existing pilot services, and can act as only their two runtime service
accounts. The prior project-level Cloud Build create and Logging Writer grants
were removed. The old Developer Connect/Cloud Build trigger still deploys only
the separate legacy branch and service.

The workflow obtains one short-lived token for registry pushes and a fresh
federated credential for the Cloud Run updates. `gha-creds-*.json` is ignored
by Git and excluded from Docker build contexts. The current GitHub
deployment is [run 37707987074](https://github.com/shubhampareek1211/AgenticAI/actions/runs/37707987074),
which passed and produced the revisions and digests above. Exercise the grader queries in a fresh signed-in browser,
then verify the legacy service traffic and IAM again. Preserve previous good
pilot revisions for rollback and keep the pilot running until grades are
released. GitHub Actions runner time, image storage, and Cloud Run revision
startup can incur charges.

References: [Google Workload Identity Federation](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-deployment-pipelines),
[GitHub authentication action](https://github.com/google-github-actions/auth),
[Cloud Run deployment permissions](https://docs.cloud.google.com/run/docs/deploying),
and [Artifact Registry access control](https://docs.cloud.google.com/artifact-registry/docs/access-control).
