// Whether a working-set row still names something Domino holds, and what a row that does not says
// (#161, ADR-0034).
//
// Every claim here is about a subtraction that happens in the browser: the membership file and the
// platform listing are two reads that answer separately, and `applyListing` is the one place that
// puts them side by side. Nothing on the server ever computes this, and nothing writes it down —
// so a test that read a route, or the membership file, would be reading the wrong thing. The store
// is booted through its own `setScope`, so the two reads race the way they really do and the
// liveness on screen is the one the real function wrote.
//
// The surfaces are the three readers of that one answer: the rail's rows and group heads, the row
// menu's removal door, and the @ menu — where a missing row has to still be OFFERED, because a
// Problem informs and never blocks (ADR-0027).
//
// Input on stdin: `{ "act": "listed" | "errored" | "cold" | "unreadable" | "unbind" }`.
//   listed     — every leg of the listing answers.
//   errored    — the language-model leg refuses while the rail still holds two of its rows.
//   cold       — the listing is held back, so nothing has been checked yet.
//   unreadable — the listing READ itself faults, so the store writes a synthetic listing that names
//                no kind and holds no groups, and a working-set change re-applies it.
//   unbind     — the app that made a dead row stuck gives the Binding back.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { act } = JSON.parse(fs.readFileSync(0, 'utf8'));

// The Built App that binds one of the dead rows. Its unbind door is the act a stuck row points at.
const APP = { id: 'app_a', name: 'Sales trends', selected: true };

const PIN = { database: 'analytics', schema: 'public', table: 'orders' };
const OLD_PIN = { database: 'analytics', schema: 'public', table: 'legacy_orders' };

// The project's working set, as the membership file holds it. Four kinds, and in each kind one row
// the platform still has beside one it does not — so every claim below is read off a rail that has
// a live row to be wrong about as well as a dead one.
const MEMBERS = [
  { id: 'dataset:d1', name: 'Sales rows', kind: 'dataset' },
  { id: 'dataset:d9', name: 'Retired rows', kind: 'dataset' },
  { id: 'data_source:s1', name: 'Warehouse', kind: 'datasource', pins: [PIN] },
  { id: 'data_source:s9', name: 'Old warehouse', kind: 'datasource', pins: [OLD_PIN] },
  { id: 'llm_alias:m1', name: 'Risk scorer', kind: 'model_llm' },
  { id: 'llm_alias:m9', name: 'Retired scorer', kind: 'model_llm' },
  // Absent from a listing that answers 200 with no error, and it must STILL not be marked: the
  // fan-out behind `list_model_apis` skips a member project that fails and stops at twenty-five,
  // so absence in this kind is not evidence of anything (ADR-0034).
  { id: 'model_api:k9', name: 'Churn scorer', kind: 'model_predictive' },
  // Gone from Domino and still bound by a Built App, so `remove_project_resource` would answer 409.
  // `usedBy` is the server's own computed field, in the shape `list_project_resources` writes it.
  { id: 'dataset:bound', name: 'Bound rows', kind: 'dataset',
    usedBy: [{ appId: APP.id, name: APP.name, scope: '' }] },
];

// What Domino answers with. `d1`, `s1` and `m1` are here; nothing else in the working set is, and
// `model_apis` is a successful empty answer rather than a failure.
const ASSETS = { assets: [{ id: 'd1', name: 'Sales rows', project: 'retail' }] };
const RESOURCES = {
  data_sources: [{ id: 's1', name: 'Warehouse', connector: 'snowflake' }],
  llm_aliases: act === 'errored'
    ? []
    : [{ id: 'm1', name: 'risk-scorer', display_name: 'Risk scorer' }],
  model_apis: [],
  // A leg that refused answers with an error string and no rows. The rail keeps its two membership
  // rows either way — membership is a different read — so this is the case the group head exists
  // for: a kind that errored AND has rows on screen.
  errors: act === 'errored' ? { llm_aliases: 'Could not list language models.' } : {},
};

// Every request that left the browser, so the claim "liveness is never written down" can be read
// off the writes rather than asserted about the source.
const requests = [];

// Whether the Built App still binds `dataset:bound`. `usedBy` is computed per read by
// `list_project_resources` off the apps' own manifests, so an unbind changes what the NEXT read of
// the membership answers — which is the whole reason the act has to re-read it.
let stillBound = true;

function serve(url, init) {
  const path = String(url).replace(/^\.\/api/, '').split('?')[0];
  if (path === '/assets') return ASSETS;
  if (path === '/resources') return RESOURCES;
  if (path.startsWith('/bindings/')) {
    stillBound = false;
    return { bindings: [], name: 'Bound rows', refs: [] };
  }
  if (path === '/project/resources') {
    return {
      items: MEMBERS.map(
        (m) => (m.id === 'dataset:bound' && !stillBound ? { ...m, usedBy: [] } : m)
      ),
    };
  }
  if (path === '/project') return { attached: [], scratch: [] };
  if (path === '/apps') return { items: [APP] };
  if (path === '/bindings') return { bindings: [] };
  if (path === '/members') {
    return { members: [], directory: [], ownerId: '', self: '', connected: true };
  }
  if (path === '/threads') return { threads: [] };
  return {};
}

// `cold` holds the platform listing back for good, which is the window between a project switch and
// the deferred read landing. The membership answers at once, so the rail is drawn from rows nothing
// has been able to check yet. Held on a promise that never settles rather than a long timer: a
// timer is a handle node waits on, and the process would print its report and then sit there.

// Real hooks, by call order, per mount — the @ menu only exists while `mention` holds a value, and
// only the composer's own `changeText` puts one there.
let hooks = [];
let cursor = 0;
function hookState(init) {
  const at = cursor;
  cursor += 1;
  if (!(at in hooks)) hooks[at] = typeof init === 'function' ? init() : init;
  return [hooks[at], (next) => {
    hooks[at] = typeof next === 'function' ? next(hooks[at]) : next;
  }];
}

// Where a navigation lands. The stuck row's act is a route, so this is what it has to be read off.
const routes = [];

// The confirm an unbind opens. Held so the harness can press Remove, which is where the act is.
let confirmed = null;

const sandbox = {
  console, JSON, Object, String, Array, Error, Map, Set, Promise, Date, Math, Number, Boolean,
  RegExp, encodeURIComponent, decodeURIComponent, setTimeout, clearTimeout,
  setInterval: () => 1, clearInterval: () => {}, requestAnimationFrame: (fn) => fn(),
  URLSearchParams, TextEncoder, TextDecoder, URL, Blob, ArrayBuffer, Uint8Array,
  fetch: async (url, init) => {
    const path = String(url).replace(/^\.\/api/, '').split('?')[0];
    requests.push(`${(init && init.method) || 'GET'} ${path}`);
    if (act === 'cold' && (path === '/assets' || path === '/resources')) {
      await new Promise(() => {});
    }
    await new Promise((resolve) => setTimeout(resolve, 0));
    const body = serve(url, init);
    return {
      ok: true, status: 200, statusText: 'OK',
      headers: { get: () => 'application/json' },
      json: async () => body,
      text: async () => JSON.stringify(body),
    };
  },
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  document: {
    title: '', documentElement: { style: { setProperty: () => {} } },
    addEventListener: () => {}, removeEventListener: () => {},
    getElementById: () => ({}), querySelector: () => null, body: {},
  },
  location: { search: '', pathname: '/', href: 'http://localhost/#/build', hash: '#/build' },
  history: { replaceState() {}, pushState() {} },
  addEventListener: () => {}, removeEventListener: () => {},
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    useState: hookState,
    useEffect: () => {}, useMemo: (fn) => fn(), useCallback: (fn) => fn,
    useRef: () => ({ current: null }),
    Fragment: 'Fragment',
  },
  antd: {
    Input: Object.assign(function Input() {}, { TextArea: 'Input.TextArea' }),
    Button: 'Button', Dropdown: 'Dropdown', Tag: 'Tag', Tooltip: 'Tooltip', Space: 'Space',
    Drawer: 'Drawer', Skeleton: 'Skeleton', Empty: 'Empty', Checkbox: 'Checkbox', Alert: 'Alert',
    Modal: Object.assign(function Modal() {}, {
      confirm: (opts) => { confirmed = opts; }, info: () => {},
    }),
    message: { info: () => {}, success: () => {}, error: () => {}, warning: () => {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  EventSource: function () {},
  SW: {},
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const file of ['util.js', 'prefs.js', 'router.js', 'store.js', 'api.js',
                    'components/resource-tree.js', 'components/resource-panel.js',
                    'components/composer.js']) {
  vm.runInContext(fs.readFileSync(ROOT + file, 'utf8'), sandbox, { filename: file });
}
const SW = sandbox.SW;
SW.router.go = (hash) => { routes.push(hash); };

const settle = async () => {
  for (let i = 0; i < 60; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

function flatten(node, out = [], depth = 0) {
  if (!node || depth > 60) return out;
  if (Array.isArray(node)) {
    node.forEach((child) => flatten(child, out, depth));
    return out;
  }
  if (typeof node !== 'object' || !node.t) return out;
  out.push(node);
  if (typeof node.t === 'function' && node.t.name !== 'Input') {
    flatten(node.t(Object.assign({}, node.p, { children: node.c })), out, depth + 1);
  }
  flatten(node.c, out, depth + 1);
  return out;
}

const text = (node) => (node.c || []).flat(Infinity).filter((c) => typeof c === 'string').join('');

// The panel, rendered the way the shell renders it.
const panel = () => { cursor = 0; hooks = []; return flatten(SW.ResourcePanel()); };

// One entry per row on screen: its name, and the mark beside it if it wears one. Read off the
// drawn tree rather than off the store, because the mark is what somebody looking at the rail sees.
function railRows(nodes) {
  return nodes
    .filter((n) => typeof n.t === 'function' && n.t.name === 'ResourceRow')
    .map((n) => {
      const drawn = flatten(n);
      const name = drawn.find((d) => (d.p || {}).className === 'sw-res-name');
      const mark = drawn.find((d) => String((d.p || {}).className || '').includes('sw-sens'));
      return { name: name ? text(name) : '', mark: mark ? text(mark) : null };
    });
}

// The sentence a kind that would not list puts above its rows.
const groupNotes = (nodes) => nodes
  .filter((n) => (n.p || {}).className === 'sw-group-note')
  .map(text);

// The removal door one row offers, and where pressing it goes.
function removalFor(nodes, name) {
  const row = nodes.find((n) => typeof n.t === 'function' && n.t.name === 'ResourceRow'
    && ((n.p.resource || {}).name === name));
  if (!row) return null;
  const menu = flatten(row).find((n) => n.t === 'Dropdown' && (n.p.menu || {}).items);
  const items = (menu.p.menu.items || []).filter((i) => i.danger).map((i) => i.label);
  return { items, onClick: menu.p.menu.onClick, keys: (menu.p.menu.items || []).map((i) => i.key) };
}

// The listing read faulting outright, which is a different fact from a leg refusing: a refused leg
// answers with an error string keyed on its own kind, while this leaves the store writing a
// synthetic listing that names no kind and holds no group at all. Stubbed at `SW.api` rather than
// at `fetch`, because `fetchDominoListing` catches both of its own legs — a fault that reaches
// `refreshResourceListing`'s own catch cannot be produced at the wire, which is why that branch
// exists.
if (act === 'unreadable') SW.api.resourceListing = () => Promise.reject(new Error('no answer'));

await SW.store.setScope({ id: 'p1', name: 'quick-start' }, { silent: true });
await settle();

if (act === 'unreadable') {
  // Writes the synthetic listing, then re-applies it: an Add is the cheap working-set change that
  // re-applies the listing already in hand rather than re-reading Domino (#162), which is the path
  // that would mark every row in three kinds dead at once.
  await SW.store.refreshResourceListing();
  await SW.store.addToProject({ id: 'dataset:new', name: 'New rows', kind: 'dataset' });
  await settle();
}

if (act === 'unbind') {
  // The act the stuck row points at, run to its end: the app gives the Binding back, and the
  // Project row's `usedBy` — which only a fresh membership read can change — has to follow.
  SW.store.set({ activeApp: APP });
  // Not awaited here: the promise it returns settles only once the confirm is answered, so awaiting
  // it before pressing Remove is a deadlock.
  const done = SW.store.removeBindingFromApp({ kind: 'dataset', id: 'bound', name: 'Bound rows' });
  await confirmed.onOk();
  await done;
  await settle();
}

const nodes = panel();
const rows = railRows(nodes);

// The @ menu, opened by typing into the composer's own box, so the query and the picker's open
// condition are both the real ones. `rows` is what it offers for a query that reaches a dead row.
SW.store.set({ thread: { id: 'conv_1', title: 'sales', artifacts: [] }, messages: [] });
function mentionRows(query) {
  hooks = [];
  cursor = 0;
  SW.Composer({ showMode: true, onSend: () => {} });
  cursor = 0;
  const box = flatten(SW.Composer({ showMode: true, onSend: () => {} }))
    .find((n) => n.t === 'Input.TextArea');
  const value = `@${query}`;
  box.p.onChange({ target: { value, selectionStart: value.length }, nativeEvent: {} });
  cursor = 0;
  return flatten(SW.Composer({ showMode: true, onSend: () => {} }))
    .filter((n) => String((n.p || {}).className || '').startsWith('sw-mention-item'))
    .map((n) => {
      const drawn = flatten(n);
      const name = drawn.find((d) => (d.p || {}).className === 'sw-mention-name');
      const mark = drawn.find((d) => (d.p || {}).className === 'sw-mention-missing');
      return { name: name ? text(name) : '', mark: mark ? text(mark) : null };
    });
}

const report = {
  rows,
  notes: groupNotes(nodes),
  // What the membership rows carry in the store, kind by kind — the answer the three surfaces read.
  // A row nothing has looked at yet carries no field at all, and that IS `unchecked` rather than a
  // fourth state: the listing is null until the deferred read lands, and every reader treats the
  // absence as "we have not checked". Named here so the report speaks the ADR's three words.
  liveness: Object.fromEntries(
    ['dataset', 'datasource', 'model_llm', 'model_predictive', 'pin'].flatMap(
      (kind) => (SW.store.get().resourceGroups[kind] || [])
        .map((r) => [r.name, r.liveness || 'unchecked'])
    )
  ),
  // A dead row is still offered, and says so where it is picked.
  mentionRetired: mentionRows('Retired'),
  mentionLive: mentionRows('Sales'),
  // Every write that left the browser. Liveness is computed on read and must reach none of them.
  writes: requests.filter((r) => !r.startsWith('GET ')),
};

// The stuck row: gone from Domino, still bound. Its removal must be the app's, and pressing it must
// land on that app rather than on a 409.
const stuck = removalFor(nodes, 'Bound rows');
const free = removalFor(nodes, 'Retired rows');
report.stuckRemovals = stuck && stuck.items;
report.freeRemovals = free && free.items;
if (stuck) {
  const key = stuck.keys.find((k) => String(k || '').startsWith('unbind-app:'));
  if (key) {
    await stuck.onClick({ key });
    await settle();
  }
}
report.routes = routes;

console.log(JSON.stringify(report));
