# Private Whisper CPU candidate — 2026-10-02

This is an engineering smoke test, not release acceptance. The existing
`agenticai-git` service and IAP traffic were unchanged. The user-facing pilot
app, Firebase Authentication, and Cloud SQL were not deployed.

| Item | Observed value |
| --- | --- |
| Project / region | `phonic-weaver-475017-n1` / `us-central1` |
| Worker service | `agenticai-voice-pilot` (IAM invocation required) |
| Image build | Cloud Build `61e8c17c-896f-4592-b6fd-757598d3f6a5`, succeeded |
| Image digest | `us-central1-docker.pkg.dev/phonic-weaver-475017-n1/agenticai-pilot/voice@sha256:fa0df5b1365de0759af07e5d1fa650329619c8bebc7b0dbd66987377a2347ee2` |
| Model | Multilingual `small`, SHA-256 `1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b`; build log says checksum OK |
| Source | whisper.cpp `v1.9.4`, commit `927cfce34f31707e17f2bff35c349632fb9e2c3a` |
| Build base | `debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251` |
| Privacy and capacity | Anonymous `/health` returned 403; authenticated proxy `/health` returned 200. Worker account has no app-data grants; service IAM has no public Invoker binding. Concurrency 1, service max 3, min 0, `/health` startup probe. |

The same two **synthetic** clips (about 4.2 s English, 3.1 s Hindi) were sent
from the Mac through an authenticated local proxy. These are too few, too
short, and not real-speaker recordings; the numbers cannot establish p95 or
accuracy for the proposed pilot. The script's reported host is the **client**;
the worker image was built for Linux amd64.

| Cloud Run revision/settings | Trial | Observed latency |
| --- | --- | --- |
| `00001-xm2`, 2 vCPU/2 GiB, two threads | Two subsequent parallel smoke calls after first startup | English 10.73 s; Hindi 11.46 s |
| `00002-zj8`, 2 vCPU/2 GiB, four threads | One warm-up, three runs per clip | Median 8.62 s; sample p95 9.67 s; [local JSON report](../.local/voice/cloud-smoke-2vcpu-4threads.json) |
| `00003-l7v`, 4 vCPU/4 GiB, four threads | One warm-up, three runs per clip | Median 6.06 s; sample p95 8.59 s; [local JSON report](../.local/voice/cloud-smoke-4vcpu-4threads.json) |
| `00005-6lf`, 8 vCPU/8 GiB, eight threads | One warm-up, three runs per clip | Median 3.28 s; sample p95 4.22 s; [local JSON report](../.local/voice/cloud-smoke-8vcpu-8threads.json) |
| `00006-tvb`, 8 vCPU/8 GiB, four threads | One warm-up, three runs per clip | Median 6.84 s; sample p95 7.72 s; [local JSON report](../.local/voice/cloud-smoke-8vcpu-4threads.json) |

Both languages returned text, but the synthetic Hindi transcript misspelled
Kohli and the English transcript garbled Joe Root/ODI. The two tiny synthetic
groups preserved only 9 of 18 listed entity occurrences across six runs at
either four-thread setting. This is a warning about quality, not a measured
real-speaker failure rate. No silence/noise, mixed speech, 60-second,
cancellation, peak RSS, full Stop-to-edit, or valid cold-start sample was
recorded. Eight CPU cores **and** eight inference threads brought this tiny
worker-only sample under five seconds, but the full browser path adds time and
the required 5–15-second and 60-second real-speaker groups are untested.

The comparison does **not** authorize a user-facing pilot. The private worker
was restored as revision `00007-nqx` to 2 vCPU/2 GiB, two threads, min 0 after
the experiment. The 8-vCPU allocation is four times the CPU and memory of the
2-vCPU baseline per active instance second; because it completed the tiny
sample faster, real per-recording cost must be calculated from measured billed
instance time, not this ratio alone. Next, prepare
the consented 90-clip corpus and decide whether to test a faster multilingual
model or another CPU configuration. Re-run the full target suite after any
revision. Do not silently relax thresholds or enable GPU.
