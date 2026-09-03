// Drives the real store.js through init() with one of its boot reads dead, and reports what the
// Workbench looked like afterwards (ADR-0027).
//
// Reading the source cannot answer this one. The interesting state is the whole app AFTER a service
// it does not need has failed: `ready` is set inside init() but the Promise.all that sets it is the
// same one that can reject, so whether somebody sees a Workbench or the full-page "The workspace
// could not load" depends on which of the eight reads threw and whether init() itself resolved.
// Both facts have to be read from outside init, together, and only running it gives them.
//
// Input on stdin: `{ "mode": "listing-502" | "listing-unreadable" | "soft-reads-unreadable"
//                            | "viewer-unreadable" | "thread-index-502" }`.
//
//   listing-502            The real 502 the control plane returns when it cannot list this viewer's
//                          Sage Projects. Driven all the way through api.js, because the layer that
//                          survives it matters: this proves the whole stack, not one catch.
//   listing-unreadable     `SW.api.projects` rejects outright. This is the store's OWN catch and
//                          the scope fallback behind it — the one path where `projects[0]` is
//                          absent, so the chip has to name the bound Project off /project instead.
//   soft-reads-unreadable  The chart registry, the starter deck and the bell reject. All three are
//                          static resolved promises in api.js today, so there is no URL to fail; the
//                          reject is installed on the api surface, which is the seam store.js
//                          actually reads and the seam a real endpoint behind them would fail at.
//   viewer-unreadable      `/api/me` answers 500. The second half of this one is not the greeting:
//                          `prefs` keys the viewer's whole preference record on their id and falls
//                          back to the literal `me` a container with no identity answers with, so a
//                          nameless boot that still read prefs would open on a record belonging to
//                          somebody else — and write this session's panel choices over it. Storage
//                          is seeded with such a record here, so a boot that read it is visible.
//   thread-index-502       `/api/threads` answers 502. Not one of the eight reads above — this one
//                          is in the deferred tail, which the first pass at this left uncaught. It
//                          is the mode that shows why `ready` alone proves nothing: the Workbench
//                          paints, and `app.js` then replaces it with the wall, because its error
//                          branch is tested before `ready` is.
//
// Every antd call is recorded rather than dropped, because "and it says nothing" is an assertion
// here: ADR-0027 puts a Problem in the chip, and a missing starter list is not a Problem at all.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { mode } = JSON.parse(fs.readFileSync(0, 'utf8'));

// What /api/project answers: built from this container, so it does not go near the control plane
// that holds the listing. `name` is deliberately not the id, so a chip that fell back to the id
// would read as a failure rather than as a pass.
const PROJECT = {
  id: 'p-acme-risk',
  name: 'Acme Risk Review',
  untitled: false,
  workspace: '/mnt/code',
  attached: [],
  scratch: [],
  model: {
    mode: 'ask',
    selected_mode: 'plan',
    phase: 'idle',
    picked_model: 'claude-sonnet-4',
    chat_model: 'claude-sonnet-4',
    reasoning_effort: null,
    catalog: { plan: ['claude-sonnet-4'], implement: ['claude-sonnet-4'], ask: ['claude-sonnet-4'] },
  },
  cost: { url: 'https://gw.example/#mine', project: 'acme' },
  manage: 'https://domino.example/manage',
};

const said = [];
const record = (channel) => (arg) => {
  said.push({ channel, text: typeof arg === 'string' ? arg : (arg && (arg.content || arg.message)) });
};

const json = (body) => ({
  ok: true, status: 200, headers: { get: () => 'application/json' },
  json: async () => body, text: async () => JSON.stringify(body),
});

// A preference record already on this origin, under the key a container with no identity to report
// answers with. Both values are the OPPOSITE of what `state` holds by default, so a boot that read
// this record is told apart from one that did not by looking at the panels.
const SEEDED_PREFS = JSON.stringify({ me: { railHidden: false, dockTab: 'activity' } });

const sandbox = {
  console, JSON, Math, Date, process, Set, Map, Promise, Array, Object, String, Number, Boolean,
  RegExp, Error, TextEncoder, TextDecoder, URL, URLSearchParams, setTimeout, clearTimeout,
  setInterval, clearInterval, Blob, ArrayBuffer, Uint8Array,
  localStorage: {
    getItem: (k) => (k === 'sw.prefs' ? SEEDED_PREFS : null),
    setItem() {},
    removeItem() {},
  },
  requestAnimationFrame: (fn) => fn(),
  document: { addEventListener() {}, querySelector: () => null, body: {}, documentElement: { style: { setProperty() {} } } },
  React: { createElement: (t, p, ...c) => ({ t, p, c }), useState: () => [null, () => {}],
           useEffect: () => {}, useRef: () => ({ current: null }), Fragment: 'Fragment' },
  antd: {
    message: { success: record('message'), error: record('message'), info: record('message'),
               warning: record('message') },
    notification: { open: record('notification'), error: record('notification'),
                    warning: record('notification'), info: record('notification') },
    Modal: { confirm: record('modal'), info: record('modal'), error: record('modal') },
  },
  fetch: async (url) => {
    const href = String(url);
    // The one read this mode kills, and the status the control plane really returns for it.
    if (mode === 'listing-502' && href.includes('/api/projects')) {
      return { ok: false, status: 502, statusText: 'Bad Gateway',
               headers: { get: () => 'application/json' },
               json: async () => ({ error: 'control plane unreachable' }), text: async () => '' };
    }
    // Matched on the whole path, never a substring: `./api/members` contains `/api/me`, and a stub
    // that answered the viewer's record for the member listing rejects init() for a reason that has
    // nothing to do with the outage under test — which is how a harness reports a pass as a failure.
    const path = href.split('?')[0].replace(/^\.\/api/, '');
    // Every shape below is one init() or its deferred tail assigns or walks straight out. The empty
    // object at the end covers the rest, which is what those reads do with an empty answer anyway.
    if (path === '/project') return json(PROJECT);
    if (path === '/projects') return json({ items: [], provisioning: true });
    if (path === '/me') {
      if (mode === 'viewer-unreadable') {
        return { ok: false, status: 500, statusText: 'Internal Server Error',
                 headers: { get: () => 'application/json' },
                 json: async () => ({ error: 'identity service unreachable' }), text: async () => '' };
      }
      return json({ id: 'u1', name: 'Dana Reed' });
    }
    if (path === '/threads') {
      if (mode === 'thread-index-502') {
        return { ok: false, status: 502, statusText: 'Bad Gateway',
                 headers: { get: () => 'application/json' },
                 json: async () => ({ error: 'thread index unreachable' }), text: async () => '' };
      }
      return json([]);
    }
    if (path === '/members') return json({ members: [], directory: [] });
    if (path === '/assets') return json({ assets: [] });
    return json({});
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

for (const f of ['util.js', 'prefs.js', 'api.js', 'store.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

const dead = (name) => { SW.api[name] = () => Promise.reject(new Error(`${name} is unreadable`)); };
if (mode === 'listing-unreadable') dead('projects');
if (mode === 'soft-reads-unreadable') { dead('charts'); dead('starters'); dead('notifications'); }

// Resolved or rejected, both recorded. app.js turns a rejected init() into the full-page error
// whatever `ready` says, so the two together are the only honest answer to "did they get a
// Workbench" — and the error is kept so a failure here names the read that broke rather than only
// that something did.
let initError = null;
await SW.store.init().catch((err) => { initError = String((err && err.message) || err); });

const state = SW.store.get();
console.log(JSON.stringify({
  initResolved: initError === null,
  initError,
  ready: state.ready,
  scopeId: state.scope && state.scope.id,
  scopeName: state.scope && state.scope.name,
  projectCount: state.projects.length,
  canProvision: state.canProvision,
  // The bound Project's own links come off /project either way, so they are the control: a mode
  // that lost them lost the read the chip fallback depends on.
  manageUrl: state.manageUrl,
  // The model block reaches Build's picker through applyModelStatus. Empty here means the picker
  // opens with no slot marked current, which is the second thing `projects[0]` was carrying.
  buildMode: state.buildMode,
  buildModel: state.buildModel,
  catalogKeys: Object.keys(state.catalog || {}).sort(),
  chartKeys: Object.keys(state.charts || {}),
  starters: state.starters,
  notificationCount: state.notifications.length,
  threadCount: state.threads.length,
  me: state.me,
  // The two panel preferences, which are the observable half of "did this boot open somebody else's
  // preference record". `SEEDED_PREFS` holds the opposite of both defaults.
  railHidden: state.railHidden,
  dockTab: state.dockTab,
  said,
}));
process.exit(0);
