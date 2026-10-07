// Exercise the shipped inline script with deterministic network and reload timing.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8')
  .match(/<script>([\s\S]*?)<\/script>/)[1];
class Element {
  constructor() {
    this.children = [];
    this.value = '';
    this.disabled = false;
    this.textContent = '';
    this.className = '';
    this.classList = {remove() {}};
  }
  append(...items) { this.children.push(...items); }
  appendChild(item) { this.children.push(item); }
  insertBefore(item, before) {
    const index = this.children.indexOf(before);
    this.children.splice(index < 0 ? this.children.length : index, 0, item);
  }
  replaceChildren() { this.children = []; }
  querySelector(selector) { return this.children.find(item => item.className === selector.slice(1)); }
  addEventListener() {}
  focus() {}
}
function page(storage, fetch, setTimeout = global.setTimeout, plotly, hash = '') {
  const elements = Object.fromEntries(
    ['messages', 'user-input', 'send-btn', 'clear-btn'].map(id => [id, new Element()]),
  );
  const replaced = [];
  const context = vm.createContext({
    document: {
      getElementById: id => elements[id],
      createElement: () => new Element(),
      createTextNode: text => ({textContent: text}),
    },
    sessionStorage: {
      getItem: key => storage.get(key) || null,
      setItem: (key, value) => storage.set(key, value),
      removeItem: key => storage.delete(key),
    },
    location: {hash, pathname: '/', search: ''},
    history: {replaceState: (...args) => replaced.push(args)},
    fetch, setTimeout, Plotly: plotly,
  });
  vm.runInContext(source, context);
  return {context, elements, replaced};
}
function text(element) {
  return [element.textContent || '', ...(element.children || []).map(text)].join('\n');
}
const tick = () => new Promise(resolve => setImmediate(resolve));
const ok = data => ({ok: true, status: 200, json: async () => data});
const storageKey = 'cricket_session_id';

test('first request stores its allocated UUID before provider work and reload', async () => {
  const storage = new Map();
  let entered = false;
  let complete;
  const initial = page(storage, async (url, options) => {
    if (url === '/sessions') return ok({session_id: 'allocated-session'});
    assert.equal(url, '/chat');
    assert.equal(storage.get(storageKey), 'allocated-session');
    assert.equal(JSON.parse(options.body).session_id, 'allocated-session');
    entered = true;
    return new Promise(resolve => { complete = resolve; });
  });
  initial.elements['user-input'].value = 'First request';
  const pending = vm.runInContext('send()', initial.context);
  await tick();
  assert.equal(entered, true);
  const restored = page(storage, async url => {
    assert.equal(url, '/sessions/allocated-session');
    return ok({messages: [{role: 'user', payload: {content: 'First request'}}], tool_calls: []});
  });
  await tick();
  assert.match(text(restored.elements.messages), /First request/);
  complete(ok({session_id: 'allocated-session', response: 'Saved answer', tool_calls: []}));
  await pending;
});

test('failed allocation starts no chat or model request', async () => {
  const requests = [];
  const storage = new Map();
  const browser = page(storage, async url => {
    requests.push(url);
    return {ok: false, status: 503, json: async () => ({detail: 'Storage unavailable'})};
  });
  browser.elements['user-input'].value = 'First';
  await vm.runInContext('send()', browser.context);
  assert.deepEqual(requests, ['/sessions']);
  assert.equal(storage.has(storageKey), false);
  assert.match(text(browser.elements.messages), /Storage unavailable/);
  assert.equal(browser.elements['send-btn'].disabled, false);
});

for (const phase of ['model', 'tool']) {
  test(`reload follows active ${phase} work until its answer is saved`, async () => {
    const storage = new Map([[storageKey, 'existing-session']]);
    const timers = [];
    let requests = 0;
    const browser = page(storage, async url => {
      assert.equal(url, '/sessions/existing-session');
      const done = ++requests === 3;
      const messages = [{role: 'user', payload: {content: 'Pending question'}}];
      const calls = phase === 'tool' ? [{
        id: 'call1', name: 'get_weather', args: {location: 'Delhi'},
        result: JSON.stringify({ok: done, data: done ? {temp_f: 80} : null}),
      }] : [];
      if (phase === 'tool') messages.push({role: 'tool', payload: {tool_call_id: 'call1'}});
      if (done) messages.push({role: 'assistant', payload: {content: 'Saved final answer'}});
      return ok({messages, tool_calls: calls, request_state: done ? 'idle' : 'running'});
    }, callback => { timers.push(callback); });
    await tick();
    assert.equal(browser.elements['send-btn'].disabled, true);
    assert.equal(browser.elements['clear-btn'].disabled, true);
    assert.match(text(browser.elements.messages), /still running/);
    for (let round = 0; round < 2; round++) {
      assert.equal(timers.length, 1);
      timers.shift()();
      await tick();
    }
    assert.equal(requests, 3);
    assert.match(text(browser.elements.messages), /Saved final answer/);
    assert.equal(text(browser.elements.messages).match(/Pending question/g).length, 1);
    assert.equal(browser.elements['send-btn'].disabled, false);
    if (phase === 'tool') assert.match(text(browser.elements.messages), /80/);
    assert.equal(timers.length, 0);
  });
}

test('reload recovers an interrupted request without reposting chat', async () => {
  const requests = [];
  const browser = page(new Map([[storageKey, 'existing-session']]), async (url, options) => {
    requests.push({url, method: options?.method || 'GET'});
    if (options?.method === 'POST') return ok({status: 'ok'});
    const interrupted = requests.length === 1;
    return ok({
      request_state: interrupted ? 'interrupted' : 'idle', tool_calls: [],
      messages: [{role: 'assistant', payload: {content: interrupted ? '' : 'Request interrupted; retry.'}}],
    });
  });
  await tick();
  assert.deepEqual(requests, [
    {url: '/sessions/existing-session', method: 'GET'},
    {url: '/sessions/existing-session/recover', method: 'POST'},
    {url: '/sessions/existing-session', method: 'GET'},
  ]);
  assert.match(text(browser.elements.messages), /Request interrupted/);
  assert.equal(browser.elements['send-btn'].disabled, false);
});

test('a recovery race follows the new running request instead of retrying tools', async () => {
  const timers = [];
  let reads = 0, recoveries = 0;
  const browser = page(new Map([[storageKey, 'existing-session']]), async (url, options) => {
    if (options?.method === 'POST') {
      recoveries++;
      return {ok: false, status: 409};
    }
    const state = ['interrupted', 'running', 'idle'][reads++];
    return ok({request_state: state, tool_calls: [], messages: [
      {role: 'assistant', payload: {content: state === 'idle' ? 'New request completed' : ''}},
    ]});
  }, callback => { timers.push(callback); });
  await tick();
  assert.equal(browser.elements['send-btn'].disabled, true);
  for (let round = 0; round < 2; round++) {
    timers.shift()();
    await tick();
  }
  assert.equal(recoveries, 1);
  assert.match(text(browser.elements.messages), /New request completed/);
  assert.equal(browser.elements['send-btn'].disabled, false);
});

test('restored chart tool result fetches and renders its saved figure', async () => {
  const plotted = [];
  const requests = [];
  const browser = page(new Map([[storageKey, 'chart-session']]), async url => {
    requests.push(url);
    if (url === '/sessions/chart-session') return ok({
      request_state: 'idle',
      tool_calls: [{
        id: 'chart-call', name: 'create_cricket_chart', args: {},
        result: JSON.stringify({ok: true, data: {chart_id: 'saved-chart'}}),
      }],
      messages: [{role: 'tool', payload: {tool_call_id: 'chart-call'}}],
    });
    assert.equal(url, '/sessions/chart-session/charts/saved-chart');
    return ok({
      figure: {data: [{type: 'bar', x: ['2026'], y: [17]}], layout: {}},
      coverage: {scope: 'Available imported matches', plotted: {
        batting_innings: 1, matches: 1, date_start: '2026-01-01', date_end: '2026-01-01',
      }},
      provenance: [{provider: 'cricsheet', url: 'https://cricsheet.org/downloads/'}],
    });
  }, global.setTimeout, {newPlot: async (...args) => plotted.push(args)});
  await tick();
  assert.deepEqual(requests, [
    '/sessions/chart-session', '/sessions/chart-session/charts/saved-chart',
  ]);
  assert.equal(plotted.length, 1);
  assert.equal(plotted[0][1][0].y[0], 17);
  assert.equal(plotted[0][3].showSendToCloud, false);
  assert.match(text(browser.elements.messages), /1 complete innings from 1 match/);
  assert.match(text(browser.elements.messages), /cricsheet.org/);
});

test('a local session link restores its chart and removes the UUID from the address', async () => {
  const storage = new Map();
  const id = '613a4be1-3595-4521-bf4e-6e45b8f46710';
  const chart = '4cb1dfec-b675-4973-b384-cbcc28884d0f';
  const plotted = [];
  const browser = page(storage, async url => {
    if (url === `/sessions/${id}`) return ok({
      request_state: 'idle',
      tool_calls: [{
        id: 'call', name: 'create_cricket_chart', args: {},
        result: JSON.stringify({ok: true, data: {chart_id: chart}}),
      }],
      messages: [{role: 'tool', payload: {tool_call_id: 'call'}}],
    });
    assert.equal(url, `/sessions/${id}/charts/${chart}`);
    return ok({figure: {data: [{type: 'bar', x: ['2026'], y: [17]}], layout: {}}});
  }, global.setTimeout, {newPlot: async (...args) => plotted.push(args)}, `#session=${id}`);
  await tick();
  assert.equal(storage.get(storageKey), id);
  assert.equal(browser.replaced.length, 1);
  assert.equal(browser.replaced[0][2], '/');
  assert.equal(plotted.length, 1);
});
