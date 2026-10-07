# Preliminary speech worker cost estimate

Date: 2026-10-01. Currency: USD. This is a planning estimate for a new,
request-billed Cloud Run speech service in `us-central1`, separate from the
existing chat service. It is not a quote or a measurement of this application.

The assumed starting configuration is 2 vCPU, 2 GiB memory, one request per
instance, minimum instances 0, and 10 seconds of billed instance time per
transcription. The 10-second figure is hypothetical; the V0 benchmark must
replace it. The [official Cloud Run pricing page](https://cloud.google.com/run/pricing)
currently lists, for this tier, $0.000024 per active vCPU-second,
$0.0000025 per active GiB-second, and $0.40 per million requests.

| Monthly recordings | Gross speech-worker run cost at 10 seconds each |
|---:|---:|
| 1,000 | $0.53 |
| 10,000 | $5.30 |
| 100,000 | $53.04 |

Formula: recordings × billed seconds × (vCPU × CPU price + GiB × memory
price), plus the request charge. Cold starts, retries, extra billable seconds,
traffic spikes, and chosen resources change this result.

For comparison, one minimum instance idle for **every second** of a 30-day month
at 2 vCPU and 2 GiB would cost roughly $25.92 at the listed idle rates. Active
intervals use active rates instead, so a full idle month must not be added to
full active costs. Minimum instances 0 is the initial proposal. A warm instance
may reduce cold-start delay, but should be selected using the measured latency
and expected request volume.

The published request-based free tier is 180,000 vCPU-seconds, 360,000
GiB-seconds, and 2 million requests per month. It is shared by the billing
account across projects. These gross figures deliberately do not subtract free
tier or credits because the account's remaining allowance is unknown.

This excludes Cloud Build, Artifact Registry image/model storage, logs,
networking outside applicable same-region service-to-service allowances, and
the existing app's billed time while it waits for the worker, Firebase Auth,
Gemini, and Cloud SQL costs. The selected model might require more
CPU or memory, and a longer billed processing time. Final release estimates
must use the V0 measured workload and current region prices. The local `gcloud`
credential could not refresh non-interactively during this implementation
session, so account-specific billing and service configuration were not read.
