# Columbia cricket pilot release

The pilot application is deployed as two Cloud Run services in project
`phonic-weaver-475017-n1`, region `us-central1`. The public
[`agenticai-columbia-pilot`](https://agenticai-columbia-pilot-3d4n5heeaq-uc.a.run.app)
gateway serves the frontend and Firebase email-link login. It has no database
credentials or local durable storage. It invokes private `agenticai-data-pilot`
over Cloud Run IAM; the private backend alone connects to Cloud SQL, verifies
the forwarded Firebase ID token, and owns conversations. Only verified email
addresses with the exact domain `columbia.edu` can use application APIs. The
existing `agenticai-git` service and its IAP/GitHub trigger remain unchanged.
The two speech workers remain private and at 2 vCPU each.

See [PILOT_CD.md](PILOT_CD.md) for current revisions, image digests, Cloud SQL
configuration/cost, and the isolated GitHub continuous deployment procedure.

## Validation completed as of 2026-10-07

- The Cloud SQL PostgreSQL 16 Enterprise instance `agenticai-pilot-pg16` is
  `RUNNABLE`, with zonal `db-g1-small`, 10 GiB SSD, seven retained automated
  backups, no automatic storage increase, and deletion protection. Schema
  migration through `0004_voice_daily_usage` has been applied. At the user's
  request, the Cricsheet import was stopped and the partial data retained:
  18,554 Register players, 28,456 external IDs, and no imported matches or
  deliveries. A signed-in conversation also remains. Those cricket tables are
  not part of the selected ESPN-only tool path; nothing was deleted.
- Private backend and public gateway revisions are ready. Gateway concurrency
  is 2 and maximum instances 2; backend concurrency is 4 and maximum instances
  2. Both have minimum instances 0. The gateway accepts one transcription
  upload at a time per instance before reading its body, caps it at 10 MiB,
  and enforces a 30-second upload window. The backend retains cross-replica
  voice quota checks and its post-upload deadline.
- Both images include production frontend assets and disable Uvicorn access
  logs. The gateway has no `DATABASE_URL` or Cloud SQL client role. The private
  backend uses `DATA_ENDPOINT_MODE=private`, and reads the forwarded end-user
  token from `X-Firebase-Authorization`; the gateway's IAM token occupies the
  upstream `Authorization` header. The public gateway hostname has been added
  to Firebase authorized domains.
- The full backend test suite passed **246 tests, with one skipped**. The
  frontend test suite and production build also passed. These are code/build
  checks, not a signed-in cloud end-to-end test.
- GitHub Actions [run 37706091533](https://github.com/shubhampareek1211/AgenticAI/actions/runs/37706091533)
  passed. It built and deployed immutable images to backend revision
  `agenticai-data-pilot-00005-9n5` and gateway revision
  `agenticai-columbia-pilot-00005-dlx`. The old
  `agenticai-git-00003-c78` revision was untouched. Image digests are in
  [PILOT_CD.md](PILOT_CD.md).
- A synthetic post-propagation probe verified that the Cloud Logging exclusion
  drops `/auth/finish` request logs while retaining an ordinary request log.
- The selected cricket design is now live ESPN tool calls: player search,
  player profiles, current match listing, scorecards, and a scorecard-derived
  top-three batting contribution metric. This code change requires a new deployment and
  signed-in validation; the revisions listed above predate the ESPN switch.
  ESPN's current header is not a historical archive, and the new tool set
  offers neither career totals nor ball-by-ball worm or wicket charts.

## Accepted speech baseline

On 2026-10-04, the user reviewed the real English, Hindi, and mixed-language
recording results, accepted the current speech behavior for this pilot, and
asked to proceed without larger CPU benchmarks. Preserve both speech workers
at 2 vCPU. The numerical speech targets in the earlier evaluation were not
met; this exception permits the pilot work but does not turn those results into
passing benchmarks. See
[real recording results](../evaluation/voice/REAL_RECORDINGS_2026-10-04.md).
Kokoro read-aloud currently supports English; Hindi can use the browser's
device voice fallback.

## Remaining release checks

1. Deploy and verify the ESPN-only code, then exercise all five advertised
   tools against live ESPN responses through the private backend. Check
   provenance, empty/ambiguous results, incomplete scorecards, provider
   failures, and the top-three contribution denominator. Confirm the private
   backend does not query retained Cricsheet tables for these answers. Do not
   restart the import or assign anonymous local conversations to pilot users.
2. Sign in through the public URL with a real verified Columbia mailbox. Test
   chat, three README grader queries, tool call display, English and
   Hindi recording, English read-aloud, refresh/persistence, and a second
   signed-in user. Check anonymous access denial, wrong-user session/chart
   denial, expired/revoked tokens, and worker failure handling.
3. Test two concurrent users, gateway upload admission and 429/Retry-After,
   oversized/slow uploads, memory under the gateway's two-instance cap,
   cancellation, and text chat while speech is busy. Confirm both private
   services reject anonymous calls.
4. Record a working rollback to the previous pilot revisions, confirm
   `agenticai-git` traffic and IAM remain unchanged, and check the total bill
   against the priced pilot estimate. Shared-core Cloud SQL has no SLA; this
   pilot accepts downtime. The user declined a budget alert; instance caps
   are safeguards, not guaranteed spending ceilings.
5. The `pilot-release` GitHub Actions workflow has successfully built and
   deployed both pilot services. After the signed-in cloud checks pass, put
   the tested gateway URL and every team member's UNI or email in
   `submission.json`. Confirm graders can use a permitted Columbia mailbox
   before submitting the URL. Keep the service running until grades are
   released.

An ESPN scorecard-based chart could be added later, but the course requirements
do not make a chart a release gate. The retained Cricsheet worm and wicket
charts require delivery data unavailable from these live ESPN tools.
