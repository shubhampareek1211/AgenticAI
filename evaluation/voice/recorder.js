'use strict';
const ids = ['speaker', 'group', 'prompt', 'reference', 'instruction', 'consent', 'record', 'stop', 'cancel', 'status', 'playback', 'save', 'next', 'manifest', 'count'];
const ui = Object.fromEntries(ids.map(id => [id, document.getElementById(id)]));
const storageKey = 'cricket-voice-evaluation-manifest-v1';
let prompts = [], entries = {}, recorder = null, stream = null, timer = null;
let generation = 0, blob = null, audioUrl = null, started = 0, active = false;
try { entries = JSON.parse(localStorage.getItem(storageKey) || '{}'); } catch { entries = {}; }
function count() {
  ui.count.textContent = `${Object.keys(entries).length} recordings listed in the manifest on this browser.`;
  ui.manifest.disabled = !Object.keys(entries).length || active;
}
function selected() { return prompts.find(item => item.id === ui.prompt.value); }
function busy(value) {
  active = value;
  for (const id of ['speaker', 'group', 'prompt', 'reference', 'consent', 'next']) ui[id].disabled = value;
  ui.record.disabled = value;
  ui.stop.disabled = !value;
  ui.cancel.disabled = !value;
  ui.save.disabled = value || !blob;
  count();
}
function discardPreview() {
  blob = null;
  ui.playback.pause();
  ui.playback.removeAttribute('src');
  ui.playback.hidden = true;
  if (audioUrl) URL.revokeObjectURL(audioUrl);
  audioUrl = null;
  ui.save.disabled = true;
}
function choose() {
  discardPreview();
  const item = selected();
  ui.instruction.textContent = item.instruction;
  ui.reference.value = item.prompt;
  ui.reference.lang = item.language === 'en' ? 'en' : 'hi';
  ui.status.textContent = 'Ready to record.';
}
function changeGroup() {
  ui.prompt.replaceChildren();
  for (const item of prompts.filter(item => item.group === ui.group.value)) {
    const option = document.createElement('option');
    option.value = item.id;
    option.textContent = `${item.id} · ${item.length}`;
    ui.prompt.append(option);
  }
  choose();
}
function cleanupMic() {
  clearInterval(timer);
  timer = null;
  if (stream) stream.getTracks().forEach(track => track.stop());
  stream = null;
}
function cancel() {
  generation++;
  if (recorder && recorder.state !== 'inactive') recorder.stop();
  cleanupMic();
  busy(false);
  ui.status.textContent = 'Recording cancelled; no audio saved.';
}
ui.record.addEventListener('click', async () => {
  if (!ui.consent.checked) { ui.status.textContent = 'Please confirm permission before recording.'; return; }
  if (!/^[A-Za-z0-9_-]{1,30}$/.test(ui.speaker.value)) { ui.status.textContent = 'Use a short speaker label with letters, numbers, underscores or hyphens.'; return; }
  if (!navigator.mediaDevices || !window.MediaRecorder) { ui.status.textContent = 'This browser does not support recording. Open the localhost page in Chrome or Safari.'; return; }
  discardPreview();
  const current = ++generation;
  busy(true);
  ui.status.textContent = 'Waiting for microphone permission…';
  ui.stop.disabled = true;
  try {
    const acquired = await navigator.mediaDevices.getUserMedia({ audio: true });
    if (current !== generation) { acquired.getTracks().forEach(track => track.stop()); return; }
    stream = acquired;
    const mimeType = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/ogg;codecs=opus'].find(type => MediaRecorder.isTypeSupported(type));
    if (!mimeType) throw new Error('No supported recording format is available.');
    recorder = new MediaRecorder(stream, { mimeType });
    const parts = [];
    let bytes = 0;
    recorder.ondataavailable = event => {
      if (current !== generation) return;
      bytes += event.data.size;
      if (bytes > 8 * 1024 * 1024) { cancel(); ui.status.textContent = 'Recording exceeded 8 MiB. Please record a shorter clip.'; return; }
      parts.push(event.data);
    };
    recorder.onerror = () => { cancel(); ui.status.textContent = 'Recording failed. Please try again.'; };
    recorder.onstop = () => {
      if (current !== generation) return;
      cleanupMic();
      blob = new Blob(parts, { type: mimeType });
      if (!blob.size) { blob = null; busy(false); ui.status.textContent = 'No audio was captured.'; return; }
      audioUrl = URL.createObjectURL(blob);
      ui.playback.src = audioUrl;
      ui.playback.hidden = false;
      busy(false);
      ui.status.textContent = 'Listen, check the reference text, then download this recording.';
    };
    started = performance.now();
    recorder.start(250);
    ui.stop.disabled = false;
    ui.status.textContent = 'Recording…';
    timer = setInterval(() => {
      const seconds = Math.floor((performance.now() - started) / 1000);
      ui.status.textContent = `Recording: ${seconds} seconds`;
      if (seconds >= 59 && recorder.state === 'recording') recorder.stop();
    }, 250);
  } catch (error) {
    if (current !== generation) return;
    cleanupMic(); busy(false);
    ui.status.textContent = `Could not start recording: ${error.message}`;
  }
});
ui.stop.addEventListener('click', () => {
  if (recorder && recorder.state === 'recording') { ui.stop.disabled = true; recorder.stop(); }
});
ui.cancel.addEventListener('click', cancel);
function download(contents, filename) {
  const link = document.createElement('a');
  const url = URL.createObjectURL(contents);
  link.href = url; link.download = filename;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}
ui.save.addEventListener('click', () => {
  if (!blob || !ui.consent.checked) { ui.status.textContent = 'Confirm permission to save this recording.'; return; }
  const item = selected();
  if (item.kind === 'speech' && !ui.reference.value.trim()) { ui.status.textContent = 'Enter the words you actually spoke.'; return; }
  const extension = blob.type.includes('mp4') ? 'm4a' : blob.type.includes('ogg') ? 'ogg' : 'webm';
  const key = `${ui.speaker.value}-${item.id}`;
  const filename = `${key}-${Date.now()}.${extension}`;
  download(blob, filename);
  entries[key] = { file: filename, speaker: ui.speaker.value, prompt_id: item.id, group: item.group,
    language: item.language, kind: item.kind, length: item.length, reference: ui.reference.value.trim(), mime_type: blob.type.split(';')[0] };
  try { localStorage.setItem(storageKey, JSON.stringify(entries)); } catch { ui.status.textContent = 'Download the manifest now; browser storage is unavailable.'; count(); return; }
  ui.status.textContent = 'Recording downloaded and added to the manifest.';
  count();
});
ui.manifest.addEventListener('click', () => {
  const manifest = { source: 'human', cloud_transcription_consent: true, clips: Object.values(entries) };
  download(new Blob([JSON.stringify(manifest, null, 2)], { type: 'application/json' }), 'manifest.json');
});
ui.group.addEventListener('change', changeGroup);
ui.prompt.addEventListener('change', choose);
ui.next.addEventListener('click', () => { ui.prompt.selectedIndex = (ui.prompt.selectedIndex + 1) % ui.prompt.options.length; choose(); });
document.addEventListener('visibilitychange', () => { if (document.hidden && active) cancel(); });
window.addEventListener('pagehide', () => { if (active) cancel(); });
ui.record.disabled = true;
fetch('prompts.json').then(response => { if (!response.ok) throw new Error('Could not load prompts.'); return response.json(); })
  .then(items => { prompts = items; changeGroup(); busy(false); })
  .catch(error => { ui.status.textContent = error.message; });
count();
