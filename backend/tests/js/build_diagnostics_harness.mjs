// Exercise the real Build history action, grouping, and prefix-relative API together.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const root = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const requested = [];
const downloaded = [];
const blobs = [];
const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, Infinity, setTimeout, clearTimeout,
  setInterval: () => 1, clearInterval() {},
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null,
    body: { appendChild() {} }, createElement: () => ({ click() { downloaded.push(this.download); }, remove() {} }) },
  location: { hash: '', href: 'https://host/owner/project/notebookSession/run/' },
  history: { replaceState() {} }, addEventListener() {}, removeEventListener() {},
  URL: { createObjectURL(blob) { blobs.push(blob); return 'blob:test'; }, revokeObjectURL() {} },
  React: { createElement: (t, p, ...c) => ({ t, p: p || {}, c }), useState: (v) => [v, () => {}],
    useEffect() {}, useMemo: (f) => f() },
  antd: { Drawer: 'Drawer', Button: 'Button', Skeleton: 'Skeleton' },
  fetch: async (url) => {
    requested.push(url);
    return { ok: true, headers: { get: () => "application/json" }, json: async () => ({ schemaVersion: 1, turn: { turnId: 'turn_old' } }) };
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'api.js', 'prefs.js', 'store.js', 'components/build-history.js']) {
  vm.runInContext(fs.readFileSync(root + f, 'utf8'), sandbox, { filename: f });
}
const { SW } = sandbox;
const rows = [
  { type: 'user', text: 'Old request', at: 1, turnId: 'turn_old', app: 'app_a', conversation: 'thr_old' },
  { type: 'done', ok: false, turnId: 'turn_old', app: 'app_a', conversation: 'thr_old' },
  { type: 'user', text: 'New request', at: 2, turnId: 'turn_new', app: 'app_a', conversation: 'thr_new' },
  { type: 'done', ok: true, turnId: 'turn_new', app: 'app_a', conversation: 'thr_new' },
  { type: 'user', text: 'Legacy request', app: 'app_a', conversation: 'thr_legacy' },
  { type: 'done', ok: true, app: 'app_a', conversation: 'thr_legacy' },
];
const diagnostic = (turnId, conversationId, phase, status, startedAt) => ({
  turn: { turnId, appId: 'app_a', conversationId, phase, startedAt },
  buildOutcome: { status }, capture: { status: 'finished', complete: true },
});
const diagnostics = [
  diagnostic('turn_old', 'thr_old', 'planning', 'error', 1),
  diagnostic('turn_new', 'thr_new', 'planning', 'success', 2),
  // Stop rolled this implementation out of the transcript. Its persisted summary still owns a row.
  diagnostic('turn_impl', 'thr_new', 'implementation', 'user_stop', 3),
];
SW.store.get = () => ({ buildHistoryOpen: true, appHistory: { rows, diagnostics },
  activeApp: { id: 'app_a', name: 'A' } });
function flatten(node) {
  if (node == null || node === false) return [];
  if (Array.isArray(node)) return node.flatMap(flatten);
  if (typeof node === 'object') {
    if (typeof node.t === 'function') return flatten(node.t(node.p));
    return [node, ...node.c.flatMap(flatten)];
  }
  return [];
}
const nodes = flatten(SW.BuildHistoryDrawer());
const labels = nodes.filter((n) => n.p && n.p.className === 'sw-bh-diagnostic-label')
  .map((n) => n.c[0]);
assert.deepEqual(labels.slice(0, 3), [
  'Implementation · Stopped by user',
  'Planning · Succeeded',
  'Planning · Failed',
]);
const buttons = nodes.filter((n) => n.t === 'Button'
  && n.c.some((value) => typeof value === 'string' && value.startsWith('Download')));
assert.equal(buttons.length, 4);
assert.equal(buttons[0].c[0], 'Download implementation diagnostics');
assert.equal(buttons[0].p.disabled, undefined);
assert.equal(buttons[3].p.disabled, true); // the transcript-only legacy row remains explicit
await buttons[2].p.onClick(); // select the older failed turn, not the newest or active conversation
assert.deepEqual(requested, ['./api/project/build-diagnostics/turn_old?app_id=app_a&conversation_id=thr_old']);
const resolved = new URL(requested[0], sandbox.location.href);
assert.equal(resolved.pathname, '/owner/project/notebookSession/run/api/project/build-diagnostics/turn_old');
assert.deepEqual(downloaded, ['build-turn_old.json']);
assert.equal(JSON.parse(await blobs[0].text()).turn.turnId, 'turn_old');
console.log(JSON.stringify({ ok: true, requested, downloaded }));
