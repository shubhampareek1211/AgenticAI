# Columbia email-link authentication setup — 2026-10-04

## Current result

Firebase is attached to the existing `phonic-weaver-475017-n1` Google Cloud
project, with Authentication initialized through the Identity Platform API.
The user explicitly approved Firebase terms and confirmed the Blaze plan on
the project's existing billing account. Optional Google Analytics was disabled.
Speech worker resource settings and the legacy IAP application are unchanged.
Worker IAM now includes the explicitly approved app identity described below.

- Web app: `Columbia Cricket Pilot`.
- App ID: `1:405277086961:web:cf7eb308dd150c5f1f4550`.
- Auth domain: `phonic-weaver-475017-n1.firebaseapp.com`.
- Email authentication enabled; `passwordRequired=false` enables email links.
  Firebase omits false-valued fields from its response. This setting also
  permits email/password at the provider; the app only presents email links
  and independently requires a verified exact Columbia domain on every API.
- Authorized domains: the project's `firebaseapp.com` and `web.app` domains,
  plus `localhost` for this temporary local test. Add only the eventual pilot
  app hostname before cloud testing; remove localhost when local testing ends.
- Public client configuration and operation evidence are saved under ignored
  `.local/pilot-auth/`. No service-account private key was created.

The first add-Firebase API call returned 403 despite project Owner access.
The console showed unaccepted Firebase terms. After the user's explicit
approval, the console setup succeeded and the project API returned HTTP 200.

## Real login preview

```bash
.venv/bin/python scripts/pilot-auth-preview.py \
  --config .local/pilot-auth/web-config.json \
  --speech-config .local/pilot-auth/voice-config.json
```

Open <http://localhost:8004/>. This is loopback-only, uses `APP_ENV=pilot`
and real Firebase token verification, and uses the existing local development
PostgreSQL database. It is not a cloud app deployment. The optional speech
configuration now enables Whisper and Kokoro directly over HTTPS with audience-
bound IAM tokens. Omitting `--speech-config` still selects login-only mode.
The current combined preview is PID 68352 on localhost:8004.
Uvicorn access logging is disabled to avoid recording one-time email codes
from callback query strings. Do not paste login links or tokens into reports.

The login UI successfully submitted one email-link request for the user-selected
Columbia mailbox and displayed its check-email confirmation. The user subsequently confirmed delivery and completed login. Browser inspection
showed the verified Columbia account signed in with saved cricket queries and
charts, exercising real backend token verification and conversation ownership. Open the email on
this Mac because the callback is `http://localhost:8004/auth/finish`.

## Validation completed

- Authentication/ownership backend tests: **7 passed**, using the dedicated
  local test database; provider verification is mocked in these tests.
- Login UI tests: **3 passed**; Firebase SDK calls are mocked.
- Frontend TypeScript/Vite build passed, with existing large-chunk warnings.
- Live preview: health and public auth configuration returned 200; the config
  advertised authentication enabled. Anonymous session, voice and TTS APIs
  returned 401. A malformed bearer token on chat returned 401.
- New preview launcher passes Ruff lint and formatting checks.

Real mailbox login, token verification, owned chat and cloud voice were
subsequently exercised successfully. Expired/reused links, revocation,
cross-user cloud access and deployed persistence still need live release
coverage. No user was administratively marked email-verified.

## Remaining sequence

1. Completed locally: real mailbox login, signed-in backend access, saved
   cricket queries/charts, and authenticated cloud voice. Confirm these again
   on the eventual deployed app URL.
2. Exercise invalid/reused link and expired/revoked-token behavior without
   logging credentials or reusing another person's account.
3. Price the complete zonal Cloud SQL configuration, then provision/migrate
   and import only intended cricket data for the separate cloud application.
   Local database size was 1,048,599,575 bytes. The local development database
   was subsequently upgraded from 0003 to `0004_voice_daily_usage` to support
   authenticated voice quotas; existing conversation data was preserved.
4. Deploy the separate pilot app with its own service account, exact callback
   hostname and private IAM calls to the unchanged speech workers. Exclude
   credential-bearing callback URLs from both application and Cloud Run logs.

Firebase's published Blaze email-link sending limit is 25,000/day, subject to
scaling and abuse controls; successful submission does not prove mailbox
delivery or account-specific effective quota. Identity Platform's applicable
email authentication tier lists no charge for the first 50,000 monthly active
users; this is not a cap on total project spending.

Sources checked 2026-10-04:
[Firebase project setup](https://firebase.google.com/docs/projects/api/workflow_set-up-and-manage-project),
[email links](https://firebase.google.com/docs/auth/web/email-link-auth),
[provider configuration](https://docs.cloud.google.com/identity-platform/docs/reference/rest/v2/Config),
[limits](https://firebase.google.com/docs/auth/limits),
[pricing](https://firebase.google.com/pricing).

## Follow-up: authenticated voice wiring

The user confirmed login and requested recording/Kokoro on port 8004. The
initial preview deliberately disabled both generated speech features; browser
read-aloud fallback could still appear. The launcher now accepts an optional
`--speech-config` file containing private HTTPS worker origins and a local
impersonated ADC path. It enforces IAM audiences and rejects HTTP proxies and
service-account private-key files. Five launcher checks pass; Ruff passes.

A one-clip synthetic test on the existing 8002 app succeeded: HTTP 200, correct
transcript, 17.86 seconds. Both the microphone failure and browser permission
state remain unreproduced; no live microphone audio was captured by the agent.

A dedicated `agenticai-app-pilot` service account was created. Automatic approval
review initially rejected persistent IAM grants; the user then explicitly
approved their exact recipients and scope. The following grants were applied:

- `roles/run.invoker` on only `agenticai-voice-pilot` and
  `agenticai-kokoro-pilot` for the app service account.
- Project `roles/firebaseauth.viewer` and `roles/aiplatform.user` for that app
  identity (Firebase revocation/user checks and Vertex reasoning).
- `roles/iam.serviceAccountTokenCreator` on only that app identity for
  `sp4553@columbia.edu`, allowing the local preview to impersonate it.

The local ignored `app-impersonated-adc.json` is mode 0600 and uses the existing
user ADC as its source. It contains sensitive refresh credentials: never publish
it, include it in a build, or print it. Global ADC was not changed; no private
service-account key was created. Deployed Cloud Run should use its attached
service identity rather than this local file. Remove the user's impersonation
grant when local testing no longer needs it.

The first check failed while the IAM change propagated. Subsequent verification
confirmed the source ADC belongs to the approved user and impersonation succeeds.
The app identity then read the user's verified/non-disabled Firebase status,
transcribed a synthetic WAV in 15.33 seconds, and generated a non-silent
24-kHz Kokoro WAV in 45.20 seconds (3.92 seconds of audio). That first synthesis
request's startup state was not independently traced. Raw evidence is under
`.local/pilot-auth/app-identity-smoke.json` and `app-identity-speech.wav`.

The preview was restarted with both services enabled. In the signed-in browser:
English/Hindi/Auto recording controls appeared; a new short cricket query
returned an answer; Read aloud displayed **Playing Kokoro voice**. A brief
microphone check reached Recording and was cancelled back to idle without
uploading microphone audio. The short test conversation remains available for
review. This confirms playback state, not the Mac's physical output volume.

The restarted app still returns 401 for anonymous voice/TTS configuration;
both private workers still return 403 for anonymous health requests. Worker
resource settings remain 2 vCPU/2 GiB. Five launcher tests and nine combined
login/recording UI tests passed; Ruff lint/format passed. No model/CPU benchmark
or public application deployment was performed in this connection step.
