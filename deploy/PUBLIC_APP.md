# Public app copy

`agenticai-public` is a separate, no-login Cloud Run deployment in
`phonic-weaver-475017-n1`, `us-central1`, based on main commit `874d4c0`.
The authenticated `agenticai-git` deployment is unchanged.

URL: https://agenticai-public-405277086961.us-central1.run.app

Set `APP_ENV=public` only on service `agenticai-public`. This mode advertises
authentication disabled and assigns new conversations to
`public:agenticai-public`. Firebase-owned and legacy anonymous conversations
are not adopted. Public conversation links are shareable: possession of a
public session UUID permits access to that public conversation.

The copy reuses the existing app service account, database URL secret,
Cloud SQL attachment, Gemini settings, and private Whisper/Kokoro connections.
Voice quota enforcement is retained; anonymous visitors share the public
namespace quota. Cloud Run has minimum instances 1, CPU throttling disabled,
and the source application's CPU, memory, concurrency, and timeout settings.
Public invocation is granted only on the new service; the speech workers stay
private. The two stopped pilot application services remain disabled.

The initial image is an overlay of the exact deployed application digest,
changing only `app.py` and `cricket/auth.py`. Immutable image:
`us-central1-docker.pkg.dev/phonic-weaver-475017-n1/agenticai-pilot/public@sha256:85be69c5b79e13e6e3bb163e31a18247117fe0e0153db2c1f5e0143234ed0084`.
Cloud Build ID: `f8681fbf-e194-48aa-afaa-de455f100592`.

For subsequent builds, use this branch's root Dockerfile and deploy the image
only to `agenticai-public`. This branch has no automatic main deployment
trigger. Do not change the authenticated deployment or its `APP_ENV`.

Validation: four public-mode checks and Ruff passed. The live smoke check
creates a public Cloud SQL session and queries a Virat Kohli chart without
an authentication token, verifies both voice configurations, and checks that
a known Firebase-owned conversation returns 404 through the public copy.
