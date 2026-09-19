// Drives the real `SW.store.undoMerge` (#233, ADR-0053).
//
// Reading the source cannot show what this file is for. The claims are about ORDER and about which
// sentence a person ends up reading: the app list has to be reloaded even when the undo refused —
// that is what retires an offer the server has already stopped making — and a push git rejected has
// to reuse #347's sentence rather than growing a second one that drifts from it.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const spec = JSON.parse(fs.readFileSync(0, 'utf8'));

const sandbox = {
  console, JSON, Object, String, Array, Error, Map, Set, Promise, Date, Math, Number, Boolean,
  RegExp, encodeURIComponent, decodeURIComponent, setTimeout, clearTimeout, setInterval,
  clearInterval, URLSearchParams,
  fetch: () => Promise.reject(new Error('the harness makes no requests')),
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  document: {
    title: '',
    documentElement: { style: { setProperty: () => {} } },
    addEventListener: () => {},
  },
  location: { search: '', pathname: '/', href: 'http://localhost/' },
  antd: { message: { success: () => {}, error: () => {}, warning: () => {} }, Modal: {} },
  React: {},
  EventSource: function () {},
  SW: {},
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(ROOT + 'util.js', 'utf8'), sandbox, { filename: 'util.js' });
// prefs.js before store.js: the store asks the viewer's preference on a common path since
// #448, and a sandbox without `SW.prefs` throws where callers catch.
vm.runInContext(fs.readFileSync(ROOT + 'prefs.js', 'utf8'), sandbox, { filename: 'prefs.js' });
vm.runInContext(fs.readFileSync(ROOT + 'store.js', 'utf8'), sandbox, { filename: 'store.js' });
const SW = sandbox.SW;

const out = [];
for (const c of spec.cases || []) {
  const calls = [];
  // The row the server sends AFTER the act, which is the only thing that retires the offer: it is
  // derived from git there, so a refusal that reloaded nothing would leave a button pointing at a
  // merge the server has already stopped offering.
  const rows = (c.rowsAfter || [{ id: 'app_a', name: 'Desk dashboard', selected: true }]);
  SW.api = {
    undoMerge: () => {
      calls.push('undoMerge');
      return Promise.resolve(c.answer || { ok: true, sha: 'abc123def456', pushed: true,
                                           rejected: false, detail: 'undid the merge',
                                           pushDetail: 'pushed' });
    },
    apps: () => { calls.push('apps'); return Promise.resolve(rows); },
  };
  // The revert rewrites files under the selected app, so what Build is showing has to be read
  // again — the app list alone retires the offer without refreshing what the undo changed.
  SW.store.loadBuild = () => { calls.push('loadBuild'); return Promise.resolve(); };
  SW.store.set({
    apps: [{ id: 'app_a', name: 'Desk dashboard', resolvedMerge: c.merge || null }],
    activeApp: { id: 'app_a', name: 'Desk dashboard', resolvedMerge: c.merge || null },
    buildRunning: !!c.buildRunning,
  });

  const row = { calls: null, thrown: '', offerAfter: undefined };
  try {
    await SW.store.undoMerge();
  } catch (err) {
    row.thrown = String(err.message || err);
  }
  row.calls = calls;
  row.offerAfter = (SW.store.get().activeApp || {}).resolvedMerge || null;
  out.push(row);
}
console.log(JSON.stringify(out));
