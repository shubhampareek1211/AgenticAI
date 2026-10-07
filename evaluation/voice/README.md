# Voice workflow evaluation

This is a local evaluation setup, not the public Columbia pilot. The working
local preview remains on 8001. A separate app on 8002 uses both private cloud
workers through authenticated local proxies; its database is the existing
local development database. Each scripted chat uses a newly allocated
evaluation conversation and clears only that conversation afterwards.

## Start the evaluation services

From the `AgenticAI` checkout, in separate terminals:

```bash
gcloud run services proxy agenticai-voice-pilot \
  --project phonic-weaver-475017-n1 --region us-central1 --port=8084

gcloud run services proxy agenticai-kokoro-pilot \
  --project phonic-weaver-475017-n1 --region us-central1 --port=8083

bash scripts/voice-cloud-preview.sh

.venv/bin/python -m http.server 8003 --bind 127.0.0.1 --directory evaluation/voice
```

The launcher checks FFmpeg, including the existing local bundled binary. It
binds to loopback only and deliberately runs local authentication mode. The
proxies supply Google IAM credentials on the cloud hop. Do not expose this
local app/proxy setup publicly. Real cloud backend service-account invocation
and Columbia Firebase sign-in are separate release checks.

## Record real speech

Open <http://127.0.0.1:8003/>. Start with the first two prompts in English,
Hindi, and mixed-language groups: six diagnostic recordings. Use your normal
voice and accent. Confirm consent, click Record, read, Stop, listen, and
download the clip. If you change the words, edit the reference to reflect
what you actually said before downloading. Download the manifest when done.
Use a speaker pseudonym; the collector does not upload audio. It keeps only
download metadata/reference text in browser storage and releases microphone
tracks on Stop/Cancel/backgrounding. Actual browser/device recording still
needs the user's check; automated recorder tests use synthetic media.

The complete prompt bank is 90 clips: per group, 20 short questions, five long
recordings, and five no-speech controls. Aim for 5–15 seconds for short clips.
The collector stops at 59 seconds to stay within the app's 60-second cap;
long recordings should be about 55–59 seconds and the decoded duration must be
checked in the results. Noise controls must not contain background speech.
Use different natural speakers/conditions where practical and record their
pseudonyms; one speaker does not establish broad accent coverage.

Put `manifest.json` and its downloaded audio files together in an ignored
folder such as `.local/voice/recordings/`. Filenames are unique; preserve them.
The evaluator rejects missing files and real recordings without the manifest's
explicit cloud-transcription consent. Consent must reflect actual permission,
not just a manually changed flag.

## Run and interpret

Transcription and no-speech checks only:

```bash
.venv/bin/python scripts/voice_workflow_eval.py \
  .local/voice/recordings/manifest.json --runs 1 \
  --output .local/voice/recordings/results
```

To also send up to two transcribed questions through real Gemini and Kokoro,
use an English-only manifest and add `--chat-limit 2`. This consumes cloud
inference, persists temporary evaluation chats, then clears those chats.
The evaluator uses the actual transcript without substituting the reference.
Hindi generated speech is not enabled; the HTTP evaluator records that TTS
rejection, whereas the browser can use its device-voice fallback.

Reports checkpoint after every clip. They include transcription time, chat/
tool time, restored answer/chart checks, time to first complete WAV frame,
total speech time, and failure stage. Audio segments are saved locally.
An explicit end marker is required; truncated/failed streams cannot be counted
as successful playback. HTTP times exclude microphone capture, manual editing,
and audible browser output. The first request is not assumed to be cold, and
the aggregate sample p95 mixes clip lengths; it is diagnostic, not a release
metric. Group the real short recordings by language and duration for acceptance.

`passed` means the API behavior succeeded, not that the words are accurate.
`manual_meaning_pass` starts unset. For each real short question, score whether
the transcript preserves the requested player(s), action, format, dates,
numbers, and negation; record entity errors separately. Do not penalize merely
writing “2019” instead of “twenty nineteen.” Mark uncertainty for a speaker
to resolve instead of guessing Hindi/mixed-language meaning.

Release gates remain: at least 18/20 short questions retain essential meaning
per group; no false transcripts on the controls; warm Stop-to-edit p95 ≤5 s
for 5–15-second recordings and cold short-clip latency ≤20 s. Collect complete
browser/device, long-recording, cancellation, memory and concurrent-use evidence
separately. A six-clip diagnostic batch cannot establish these gates. Stop-to-edit
browser timing also includes work outside the HTTP harness.

## Verification commands

```bash
bash scripts/test.sh -q
node --test evaluation/voice/recorder.test.cjs
node --check evaluation/voice/recorder.js
bash -n scripts/voice-cloud-preview.sh
```

The recorder tests use the frontend's installed JSDOM dependency. Current
measurements and pending gates are in `STEP1_RESULTS_2026-10-04.md`.
