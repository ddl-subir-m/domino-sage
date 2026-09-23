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
for (const f of ['util.js', 'api.js', 'store.js', 'components/build-history.js']) {
  vm.runInContext(fs.readFileSync(root + f, 'utf8'), sandbox, { filename: f });
}
const { SW } = sandbox;
const rows = [
  { type: 'user', text: 'Old request', turnId: 'turn_old', app: 'app_a', conversation: 'thr_old' },
  { type: 'done', ok: false, turnId: 'turn_old', app: 'app_a', conversation: 'thr_old' },
  { type: 'user', text: 'New request', turnId: 'turn_new', app: 'app_a', conversation: 'thr_new' },
  { type: 'done', ok: true, turnId: 'turn_new', app: 'app_a', conversation: 'thr_new' },
  { type: 'user', text: 'Legacy request', app: 'app_a', conversation: 'thr_legacy' },
  { type: 'done', ok: true, app: 'app_a', conversation: 'thr_legacy' },
];
SW.store.get = () => ({ buildHistoryOpen: true, appHistory: { rows }, activeApp: { id: 'app_a', name: 'A' } });
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
const buttons = nodes.filter((n) => n.t === 'Button' && n.c.includes('Download diagnostics'));
assert.equal(buttons.length, 3);
assert.equal(buttons[0].p.disabled, true); // newest display row is the legacy fixture
await buttons[2].p.onClick(); // select the older failed turn, not the latest or active conversation
assert.deepEqual(requested, ['./api/project/build-diagnostics/turn_old?app_id=app_a&conversation_id=thr_old']);
const resolved = new URL(requested[0], sandbox.location.href);
assert.equal(resolved.pathname, '/owner/project/notebookSession/run/api/project/build-diagnostics/turn_old');
assert.deepEqual(downloaded, ['build-turn_old.json']);
assert.equal(JSON.parse(await blobs[0].text()).turn.turnId, 'turn_old');
console.log(JSON.stringify({ ok: true, requested, downloaded }));
