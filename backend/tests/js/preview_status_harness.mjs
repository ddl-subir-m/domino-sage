// Drive the real store and PreviewPane through deferred responses and Retry, without a browser.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const root = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const calls = [];
const effects = [];
const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, encodeURIComponent, setTimeout, clearTimeout,
  setInterval: () => 1, clearInterval() {},
  requestAnimationFrame: (fn) => fn(),
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    useState: (v) => [typeof v === 'function' ? v() : v, () => {}],
    useEffect: (fn) => effects.push(fn), useRef: () => ({ current: null }), Fragment: 'Fragment',
  },
  antd: { Button: 'Button', Tooltip: 'Tooltip', Input: 'Input', Dropdown: 'Dropdown', Modal: {},
    Checkbox: 'Checkbox', Alert: 'Alert', Tag: 'Tag', message: { error() {} } },
  icons: new Proxy({}, { get: (_, name) => name }),
  fetch: (url, options) => new Promise((resolve) => calls.push({ url, options, resolve })),
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const file of ['util.js', 'store.js', 'prefs.js', 'modes/builder.js']) {
  vm.runInContext(fs.readFileSync(root + file, 'utf8'), sandbox, { filename: file });
}
const { SW } = sandbox;
SW.brand = { text: (s) => s };
SW.prefs = { get: () => ({}) };
const reply = (body, status = 200) => ({ ok: status < 400, status,
  json: async () => body, headers: { get: () => 'application/json' } });
const failed = (appId, error) => ({ appId, generation: 'proc:2', server: 'uvicorn',
  state: 'failed', error, attempt: 1, output: ['Traceback:', error] });
SW.store.set({ scope: { id: 'project' }, activeApp: { id: 'a', name: 'A' } });

// A late success for app A must not clear app B's error or replace its iframe source.
const old = SW.store.refreshPreview();
SW.store.set({ activeApp: { id: 'b', name: 'B' } });
const recent = SW.store.refreshPreview();
calls[1].resolve(reply({ preview: failed('b', 'ModuleNotFoundError: missing_b') }, 502));
await recent;
calls[0].resolve(reply({}));
await old;
assert.equal(SW.store.get().previewStatus, 'err');
assert.equal(SW.store.get().previewDetail.appId, 'b');
assert.equal(SW.store.get().previewSrc, './preview/');

function* walk(node) {
  if (!node || typeof node !== 'object') return;
  if (Array.isArray(node)) { for (const c of node) yield* walk(c); return; }
  yield node;
  yield* walk(node.c);
}
function pane() {
  const tree = SW.BuildMode({});
  const node = [...walk(tree)].find((n) => n.t && n.t.name === 'PreviewPane');
  assert.ok(node, 'Build must mount the real preview pane');
  return node.t(node.p);
}
const drawn = pane();
const nodes = [...walk(drawn)];
const words = nodes.flatMap((n) => n.c || []).flat(Infinity).filter((s) => typeof s === 'string');
assert.ok(words.includes('ModuleNotFoundError: missing_b'));
assert.ok(words.includes('Server output'));
assert.ok(nodes.some((n) => n.t === 'details'));
assert.ok(!words.some((s) => s.includes('Vite')));
const retryButton = nodes.find((n) => n.t === 'Button' && n.c.includes('Retry'));
assert.ok(retryButton);
const retry = retryButton.p.onClick();
assert.ok(calls[2].url.startsWith('./api/preview/retry?appId=b'));
assert.equal(calls[2].options.method, 'POST');
// Keep the last cause visible while the Retry request itself is pending.
assert.equal(SW.store.get().previewDetail.error, 'ModuleNotFoundError: missing_b');
calls[2].resolve(reply({ ...failed('b', null), state: 'starting', attempt: 2 }));
await retry;
assert.equal(SW.store.get().previewStatus, 'starting');

// A status read reports a failed reload even when the iframe had been healthy.
SW.store.set({ previewStatus: 'ok' });
const poll = SW.store.refreshPreview({ statusOnly: true });
const ignoredPoll = SW.store.refreshPreview({ statusOnly: true });
assert.equal(calls.length, 4, 'status polls must not overlap');
calls[3].resolve(reply(failed('b', 'ImportError: failed reload')));
await Promise.all([poll, ignoredPoll]);
assert.equal(SW.store.get().previewStatus, 'err');
assert.equal(SW.store.get().previewDetail.error, 'ImportError: failed reload');

// A healthy status alone does not clear a proxy failure: the page must answer too.
const recovery = SW.store.refreshPreview({ statusOnly: true });
calls[4].resolve(reply({ ...failed('b', null), state: 'ready' }));
await new Promise((resolve) => setTimeout(resolve, 0));
assert.equal(SW.store.get().previewStatus, 'err');
assert.ok(calls[5].url.startsWith('./preview/'));
calls[5].resolve(reply({}));
await recovery;
assert.equal(SW.store.get().previewStatus, 'ok');
assert.equal(SW.store.get().previewDetail, null);

// The healthy interval is a status read, not a page reload or another restart.
const healthy = SW.store.refreshPreview({ statusOnly: true });
const src = SW.store.get().previewSrc;
calls[6].resolve(reply({ ...failed('b', null), state: 'ready' }));
await healthy;
assert.equal(calls.length, 7);
assert.equal(SW.store.get().previewSrc, src);
console.log('preview status, stale response, Retry, reload failure, and recovery checks passed');
