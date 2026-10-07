const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('../../frontend/node_modules/jsdom');
const tick = () => new Promise(resolve => setImmediate(resolve));

async function page(getUserMedia) {
  const dom = new JSDOM(fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8'), {
    url: 'http://127.0.0.1:8003', runScripts: 'outside-only',
  });
  const w = dom.window;
  const downloads = [], urls = new Map();
  let requested = 0, stopped = 0;
  w.Blob = Blob;
  w.URL.createObjectURL = blob => { const url = `blob:test-${urls.size}`; urls.set(url, blob); return url; };
  w.URL.revokeObjectURL = () => {};
  w.HTMLAnchorElement.prototype.click = function () { downloads.push({name: this.download, blob: urls.get(this.href)}); };
  w.HTMLMediaElement.prototype.pause = () => {};
  w.fetch = async url => {
    assert.equal(url, 'prompts.json', 'No audio/network upload is allowed from the recorder');
    return {ok: true, json: async () => JSON.parse(fs.readFileSync(path.join(__dirname, 'prompts.json')))};
  };
  const media = {getTracks: () => [{stop: () => { stopped++; }}]};
  Object.defineProperty(w.navigator, 'mediaDevices', {value: {getUserMedia: async () => {
    requested++;
    return getUserMedia ? getUserMedia(media) : media;
  }}});
  w.MediaRecorder = class {
    static isTypeSupported(type) { return type.startsWith('audio/webm'); }
    constructor() { this.state = 'inactive'; }
    start() { this.state = 'recording'; }
    stop() {
      this.state = 'inactive';
      this.ondataavailable({data: new Blob(['synthetic test bytes'])});
      this.onstop();
    }
  };
  w.eval(fs.readFileSync(path.join(__dirname, 'recorder.js'), 'utf8'));
  await tick();
  return {dom, w, downloads, requested: () => requested, stopped: () => stopped,
    el: id => w.document.getElementById(id)};
}

test('consent is required before any microphone request', async () => {
  const p = await page();
  try {
    p.el('record').click(); await tick();
    assert.equal(p.requested(), 0);
    assert.match(p.el('status').textContent, /confirm permission/);
  } finally { p.dom.window.close(); }
});

test('recording download and manifest agree on file, reference, consent and language', async () => {
  const p = await page();
  try {
    p.el('group').value = 'mixed';
    p.el('group').dispatchEvent(new p.w.Event('change'));
    p.el('consent').checked = true;
    p.el('record').click(); await tick();
    p.el('stop').click();
    assert.equal(p.stopped(), 1);
    p.el('reference').value = 'The actual mixed words I spoke';
    p.el('save').click(); p.el('manifest').click();
    const manifest = JSON.parse(await p.downloads[1].blob.text());
    assert.equal(manifest.source, 'human');
    assert.equal(manifest.cloud_transcription_consent, true);
    assert.equal(manifest.clips[0].file, p.downloads[0].name);
    assert.equal(manifest.clips[0].language, 'auto');
    assert.equal(manifest.clips[0].reference, 'The actual mixed words I spoke');
  } finally { p.dom.window.close(); }
});

test('cancelling pending microphone permission stops tracks when permission arrives', async () => {
  let resolve;
  const p = await page(media => new Promise(done => { resolve = () => done(media); }));
  try {
    p.el('consent').checked = true;
    p.el('record').click(); await tick();
    p.el('cancel').click(); resolve(); await tick();
    assert.equal(p.stopped(), 1);
    assert.equal(p.el('save').disabled, true);
    assert.equal(p.el('record').disabled, false);
  } finally { p.dom.window.close(); }
});
