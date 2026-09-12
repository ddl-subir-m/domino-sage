// Which reads the Conversation-context doors make, and what the picker holds afterwards (ADR-0043).
//
// The lock narrows the composer's pickers off `state.sensitivity` alone (composer.js), so a door
// that moves a Dataset in or out of a Conversation's scope and does not re-ask leaves the picker
// describing the turn before it. Both doors did exactly that: `refreshSensitivity` ran on a scope
// load, a Binding change, a mode change and a Conversation open, and attaching to a Conversation is
// none of them. The lock then landed on whatever read happened next for an unrelated reason — in
// practice the trip to Build, whose `refreshBindings` re-asks, which is why the narrowing looked
// like it needed a tab change.
//
// The fake server below applies the REAL rule rather than locking on any chip at all: a Dataset
// reaches a Conversation's scope in two chip shapes — a file carrying `datasetId`, and a Dataset
// pinned whole whose id sits behind a `dataset:` prefix — and `_datasets_in_scope` reads both
// through `binding_from_context`. A fake that locked on `pinned.length` passed this file while the
// server answered "unlocked" for the second shape, which is the one arrangement a harness must not
// have: a green test certifying a leak.
//
// Driven through the real store rather than a rendered tree, because the claim is about which
// requests a store method issues — the same seam `working_set_refresh_harness` uses for #162.
//
// Input on stdin: `{ "act": "attach" | "attach-gate-off" | "attach-whole-dataset"
//                          | "attach-new-chat" | "remove" | "remove-whole-dataset"
//                          | "remove-gate-off" | "remove-sticky" | "remove-after-attach"
//                          | "attach-then-failed-read" }`.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { act } = JSON.parse(fs.readFileSync(0, 'utf8'));

const GROUP = 'sensitive-approved';
const DECLARED = 'ds_claims';
// The gate is OFF by default and nearly every deployment leaves it there, so a door that read on
// every attach would put a round trip on all of them for an answer that cannot change.
const gateOff = act === 'attach-gate-off' || act === 'remove-gate-off';
// Once a turn has run under the lock the transcript carries the rows, so removing every chip lifts
// nothing and the way out is a new chat. The server decides that, never the browser.
const sticky = act === 'remove-sticky';

// The Conversation's context rows, as the server holds them. Mutated by the doors below exactly as
// the server mutates them, so the lock is read off what was actually written.
let context = [];

// `_datasets_in_scope`, in the two lines of it this file depends on.
const datasetsInScope = () => context
  .map((row) => row.datasetId
    || (row.kind === 'dataset'
      ? String(row.parentId || row.resourceId || '').replace(/^dataset:/, '')
      : ''))
  .filter(Boolean);

function sensitivityRead() {
  if (gateOff) {
    return { enabled: false, locked: false, approved: [], datasets: [], group: '', reason: '' };
  }
  const declared = datasetsInScope().includes(DECLARED);
  if (!declared && !sticky) {
    return { enabled: true, locked: false, approved: ['qwen-2-5'], datasets: [], group: GROUP,
             reason: '' };
  }
  return {
    enabled: true, locked: true, approved: ['qwen-2-5'],
    datasets: declared ? ['claims'] : [], group: GROUP,
    reason: declared ? 'declared' : 'session',
  };
}

// Both races hold back the FIRST sensitivity read, and they are the same race seen from each end.
// In `attach-new-chat` that read is the one `newThread` fires on the way in — asked before the chip
// exists, so it answers unlocked, and landing last it used to overwrite the locked answer the
// attach then read. In `remove-after-attach` it is the attach's own read, still in flight and about
// to answer LOCKED, while the person closes the chip again: a removal that gated on what was on
// screen saw "nothing locked", asked nobody, and let that answer land over a chip already gone.
let sensitivityReads = 0;
function delayFor(path) {
  if (!path.endsWith('/api/project/sensitivity')) return 0;
  if (act !== 'attach-new-chat' && act !== 'remove-after-attach'
    && act !== 'attach-then-failed-read') return 0;
  sensitivityReads += 1;
  return sensitivityReads === 1 ? 150 : 0;
}

const requests = [];
function answer(path, init) {
  const method = (init && init.method) || 'GET';
  if (path.endsWith('/api/project/sensitivity')) return sensitivityRead();
  if (/\/api\/threads\/[^/]+\/context$/.test(path)) {
    if (method === 'POST') {
      const row = JSON.parse(init.body);
      context.push({ ...row, id: `att_${context.length + 1}` });
      return {
        id: `att_${context.length}`, name: row.name, addedBy: 'user',
        resourceId: row.resourceId, datasetId: row.datasetId,
        datasetRelPath: row.datasetRelPath,
      };
    }
    return { items: context };
  }
  if (/\/api\/threads\/[^/]+\/context\/[^/]+$/.test(path) && method === 'DELETE') {
    const id = path.split('/').pop();
    context = context.filter((row) => row.id !== id);
    return { removed: true };
  }
  if (path.endsWith('/api/threads') && method === 'POST') return { id: 'thr_new' };
  if (path.endsWith('/api/project')) return { scratch: [], attached: [] };
  if (path.endsWith('/api/apps')) return { apps: [] };
  if (path.endsWith('/api/threads')) return { threads: [] };
  return {};
}

const sandbox = {
  console, JSON, Object, String, Array, Error, Map, Set, Promise, Date, Math, Number, Boolean,
  RegExp, encodeURIComponent, decodeURIComponent, URL, URLSearchParams,
  setTimeout, clearTimeout, setInterval, clearInterval,
  TextEncoder, TextDecoder, Blob, ArrayBuffer, Uint8Array, FormData,
  fetch: (url, init) => {
    const path = url.split('?')[0];
    requests.push(`${(init && init.method) || 'GET'} ${path}`);
    const body = answer(path, init);
    const res = {
      ok: true, status: 200, statusText: 'OK',
      headers: { get: () => 'application/json' },
      json: () => Promise.resolve(body),
    };
    const wait = delayFor(path);
    // The read that 5xx's while an older, slower one is still out. A guard that dropped the older
    // answer because a NEWER read existed left both on the floor and the stale one on screen —
    // worse than no guard, because the answer it dropped is the locked one.
    if (act === 'attach-then-failed-read' && path.endsWith('/api/project/sensitivity')
      && sensitivityReads === 2) {
      return Promise.reject(new Error('the gateway listing failed'));
    }
    return wait ? new Promise((r) => setTimeout(() => r(res), wait)) : Promise.resolve(res);
  },
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  document: {
    title: '', documentElement: { style: { setProperty: () => {} } },
    addEventListener: () => {}, removeEventListener: () => {}, getElementById: () => ({}),
  },
  location: { search: '', pathname: '/', href: 'http://localhost/', hash: '#/chat' },
  addEventListener: () => {}, removeEventListener: () => {},
  React: { createElement: () => ({}) },
  EventSource: function () {},
  antd: {
    message: { success: () => {}, error: () => {}, info: () => {}, warning: () => {} },
    Modal: { confirm: () => {} },
  },
  SW: {},
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'prefs.js', 'router.js', 'store.js', 'api.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

const settle = async () => {
  // Long enough for the held-back read to land, because the write that must NOT happen rides in
  // with it. A settle that only drained microtasks would report the race as won.
  await new Promise((r) => setTimeout(r, 300));
  for (let i = 0; i < 20; i += 1) await new Promise((r) => setTimeout(r, 0));
};

// The Dataset's rail row. `declared` rides here (api.js maps it off the server's listing and
// nothing re-derives it), and the leaf below points at it through `parentId`.
const DATASET_ROW = {
  id: `dataset:${DECLARED}`, name: 'claims', kind: 'dataset', declared: !gateOff,
  bindingKey: ['dataset', DECLARED],
};
// The click the Chat file row makes. In Chat there is no app to attach to, so the row's only act is
// `onMention`, and that is `addToContext` (resource-tree.js).
const LEAF = {
  id: `dsfile:${DECLARED}:claims.csv`,
  name: 'claims.csv',
  kind: 'file',
  datasetId: DECLARED,
  datasetRelPath: 'claims.csv',
  datasetName: 'claims',
  parentId: DATASET_ROW.id,
};

// `remove-after-attach` is not seeded: it performs the attach itself, then closes what it attached.
const removing = act.startsWith('remove') && act !== 'remove-after-attach';
// The chip already in the Conversation, in whichever of the two shapes this act is about. The
// whole-Dataset row is here because `namesDataset` reads it through a different branch, and a
// regression that dropped that branch from the removal path alone would leave a whole-Dataset
// chip's lock stuck on after it was closed.
if (removing) context = [act === 'remove-whole-dataset'
  ? { id: 'att_1', kind: 'dataset', name: 'claims', resourceId: DATASET_ROW.id }
  : { id: 'att_1', kind: 'file', name: 'claims.csv', resourceId: LEAF.id,
      datasetId: DECLARED, datasetName: 'claims' }];

SW.store.set({
  scope: { id: 'p1', name: 'quick-start' },
  ready: true,
  // `attach-new-chat` starts with no Conversation, so `attach` opens one on the way in.
  thread: act === 'attach-new-chat' ? null : { id: 'thr_a' },
  messages: [],
  attachments: removing
    ? [{ id: 'att_1', resourceId: context[0].resourceId, resourceName: context[0].name,
         resourceKind: context[0].kind, datasetId: context[0].datasetId }]
    : [],
  sensitivity: sensitivityRead(),
  resourceIndex: { [DATASET_ROW.id]: DATASET_ROW },
  resourceGroups: { dataset: [DATASET_ROW] },
});

const before = requests.length;
if (removing) {
  await SW.store.removeFromConversation(SW.store.get().attachments[0]);
} else {
  await SW.store.addToContext(act === 'attach-whole-dataset' ? DATASET_ROW : LEAF, { quiet: true });
}
// The undo, taken while the attach's own read is still out. Deliberately NOT settled in between —
// the whole point is that the screen has not caught up yet.
if (act === 'remove-after-attach') {
  await SW.store.removeFromConversation(SW.store.get().attachments[0]);
}
// A second read, issued while the attach's own is still in flight, that fails. Any of the doors
// that re-ask for their own reasons — the assignments drawer, a mode change, a Binding change —
// is this call.
if (act === 'attach-then-failed-read') await SW.store.reloadSensitivity();
await settle();

const after = SW.store.get().sensitivity || {};
console.log(JSON.stringify({
  requests,
  // The whole symptom, in one flag: does the picker match the turn without leaving Chat?
  locked: !!after.locked,
  approved: after.approved || [],
  // What the gates must not cost a deployment the act cannot possibly move the lock for.
  sensitivityReads: requests.slice(before).filter((r) => r.includes('/api/project/sensitivity'))
    .length,
}));
