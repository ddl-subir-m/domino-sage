// What Browse Domino asks the platform for, and what it puts on screen while asking (#159).
//
// The modal is drawn from the listing the store already holds. That is not a claim source can be
// grepped for: it is a claim about which calls leave the browser as somebody types, and about what
// the list says in the window after a project switch when the store holds nothing yet. Both are
// only visible by running the effects the component registers and watching `fetch`.
//
// Input on stdin: `{ "act": "open" | "type" | "kind" | "cold" }` — what somebody does with the
// modal. Every act reports the URLs that left the browser during it and the list as drawn after.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { act } = JSON.parse(fs.readFileSync(0, 'utf8'));

// The listing the store already holds when the modal opens: two Datasets, one Data Source, one
// language model. `dataset:d1` is in the project, so a row that must NOT offer Add is in view.
const LISTING = {
  errors: {},
  groups: {
    dataset: [
      { id: 'dataset:d1', name: 'Sales rows', kind: 'dataset', description: 'in retail' },
      { id: 'dataset:d2', name: 'Risk history', kind: 'dataset', description: 'in risk' },
    ],
    datasource: [{ id: 'data_source:s1', name: 'Warehouse', kind: 'datasource' }],
    model_llm: [{ id: 'llm_alias:m1', name: 'Risk scorer', kind: 'model_llm' }],
    model_predictive: [],
  },
};

// What the platform answers the background refresh with: one Dataset nobody had seen yet. A row
// that appears only after the refresh lands is how "the refresh reached the screen" is told from
// "the modal drew the store and stopped".
const FRESH_ASSETS = {
  assets: [
    { id: 'd1', name: 'Sales rows', project: 'retail' },
    { id: 'd2', name: 'Risk history', project: 'risk' },
    { id: 'd3', name: 'Risk appetite', project: 'risk' },
  ],
};
const FRESH_RESOURCES = {
  data_sources: [{ id: 's1', name: 'Warehouse', connector: 'snowflake' }],
  llm_aliases: [{ id: 'm1', name: 'risk-scorer', display_name: 'Risk scorer' }],
  model_apis: [],
};

// The project's own membership, so the refresh an Add runs has something to apply. Mutable, and
// grown by the Add below the way the server grows it, so the row that was just added can be checked
// for reporting itself as a member afterwards.
let membership = [{ id: 'dataset:d1', kind: 'dataset', name: 'Sales rows' }];

// A listing read that is already out of date by the time it answers: it still carries a Dataset
// somebody has since deleted. Served slowly, so a newer read overtakes it.
const STALE_ASSETS = { assets: [{ id: 'd9', name: 'Deleted set', project: 'retail' }] };

const requests = [];
let assetReads = 0;
function answer(url, init) {
  if (url.endsWith('/api/assets')) {
    assetReads += 1;
    if (act === 'stale') return assetReads === 1 ? STALE_ASSETS : FRESH_ASSETS;
    return FRESH_ASSETS;
  }
  // `/api/resources` is the platform listing; `/api/project/resources` is this project's
  // membership. Two different reads whose paths end in the same word.
  if (url.endsWith('/api/resources')) return FRESH_RESOURCES;
  if (url.endsWith('/api/project/resources')) {
    if ((init && init.method) === 'POST') {
      const row = JSON.parse(init.body);
      if (!membership.some((m) => m.id === row.id)) membership = [...membership, row];
      return { added: true };
    }
    return { items: membership };
  }
  if (url.endsWith('/api/project')) return { scratch: [], attached: [] };
  if (url.endsWith('/api/members')) {
    return { members: [], directory: [], ownerId: '', self: '', connected: true };
  }
  return {};
}

// The platform refusing both legs of the listing. `fetchDominoListing` catches each and answers
// with error strings rather than throwing, which is the shape the modal has to read.
function refuses(url) {
  if (act === 'unreadable') return url.endsWith('/api/assets') || url.endsWith('/api/resources');
  if (act === 'partial' || act === 'partial-warm' || act === 'no-match') {
    return url.endsWith('/api/assets');
  }
  return false;
}

// The first listing read is held back only for `stale`, where the whole claim is which of two
// overlapping reads gets to write.
function delayFor(url) {
  return act === 'stale' && url.endsWith('/api/assets') && assetReads === 1 ? 40 : 0;
}

// One render's hook state. `render()` rewinds the cursor and replays the effects, so the several
// renders below sit over one persisting set of slots — the same thing React does to a modal that
// stays mounted while somebody types in it.
const slots = [];
let cursor = 0;
let effects = [];
const effectSlots = [];

const sandbox = {
  console, JSON, Object, String, Array, Error, Map, Set, Promise, Date, Math, Number, Boolean,
  RegExp, encodeURIComponent, decodeURIComponent, setTimeout, clearTimeout, setInterval,
  clearInterval, URLSearchParams, TextEncoder, TextDecoder, URL, Blob, ArrayBuffer, Uint8Array,
  fetch: (url, init) => {
    requests.push(url);
    if (refuses(url)) return Promise.reject(new Error('Domino did not answer'));
    const body = answer(url, init);
    const wait = delayFor(url);
    const res = {
      ok: true,
      status: 200,
      statusText: 'OK',
      headers: { get: () => 'application/json' },
      json: () => Promise.resolve(body),
    };
    return wait ? new Promise((resolve) => setTimeout(() => resolve(res), wait))
      : Promise.resolve(res);
  },
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  document: {
    title: '', documentElement: { style: { setProperty: () => {} } },
    addEventListener: () => {}, removeEventListener: () => {}, getElementById: () => ({}),
  },
  location: { search: '', pathname: '/', href: 'http://localhost/', hash: '#/chat' },
  addEventListener: () => {}, removeEventListener: () => {},
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    useState: (init) => {
      const i = cursor++;
      if (slots.length <= i) slots[i] = { v: typeof init === 'function' ? init() : init };
      const slot = slots[i];
      return [slot.v, (next) => { slots[i].v = typeof next === 'function' ? next(slot.v) : next; }];
    },
    useEffect: (fn, deps) => { effects.push({ fn, deps }); },
    useRef: () => ({ current: null }),
    useMemo: (fn) => fn(),
    Fragment: 'Fragment',
  },
  antd: {
    Modal: 'Modal', Input: 'Input', Button: 'Button', Tooltip: 'Tooltip', Tag: 'Tag',
    Skeleton: 'Skeleton', Empty: 'Empty',
    message: { info: () => {}, success: () => {}, error: () => {}, warning: () => {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  EventSource: function () {},
  SW: {},
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'prefs.js', 'router.js', 'store.js', 'api.js',
                 'components/resource-catalog.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;
// The drill-down is another component's claim; here it only has to exist.
SW.ResourceTree = function ResourceTree() { return null; };

// `partial-warm` is the same half-answer arriving on a store that already holds a good listing.
const cold = act === 'cold' || act === 'unreadable' || act === 'partial';
SW.store.set({
  scope: { id: 'p1', name: 'quick-start' },
  catalogOpen: true,
  catalogKind: null,
  // Right after a project switch the store holds no listing yet, and the members it is the
  // complement of are gone with it.
  resourceListing: cold ? null : LISTING,
  resourceListingScope: cold ? null : 'p1',
  resourceGroups: cold ? {} : { dataset: [LISTING.groups.dataset[0]] },
});

function walk(tree) {
  const nodes = [];
  (function step(node) {
    if (!node || typeof node !== 'object') return;
    if (Array.isArray(node)) return node.forEach(step);
    nodes.push(node);
    Object.values(node.p || {}).forEach(step);
    (node.c || []).forEach(step);
  })(tree);
  return nodes;
}

// Effects are replayed the way React replays them — only when their dependencies changed — because
// the modal's opening effect resets the search box, and an effect that re-ran on every render
// would wipe what somebody just typed before the next draw.
function render() {
  cursor = 0;
  effects = [];
  const tree = SW.ResourceCatalog();
  effects.forEach((entry, i) => {
    const prev = effectSlots[i];
    const changed = !prev || !entry.deps || !prev.deps
      || entry.deps.length !== prev.deps.length
      || entry.deps.some((d, k) => d !== prev.deps[k]);
    if (!changed) return;
    if (prev && typeof prev.cleanup === 'function') prev.cleanup();
    effectSlots[i] = { deps: entry.deps, cleanup: entry.fn() };
  });
  return tree;
}

const catalogRows = (tree) => walk(tree)
  .filter((n) => typeof n.t === 'function' && n.t.name === 'CatalogRow')
  .map((n) => n.p.resource);
const rowsOf = (tree) => catalogRows(tree).map((r) => r.name);
// The rows that offer no Add, because the project already has them. Read from the store's working
// set now rather than from a read of its own, so it is worth reporting.
const inProjectOf = (tree) => catalogRows(tree).filter((r) => r.inProject).map((r) => r.name);
const skeletonIn = (tree) => walk(tree).some((n) => n.t === 'Skeleton');
const emptyIn = (tree) => walk(tree).some((n) => n.t === 'Empty');
const searchInput = (tree) => walk(tree).find((n) => n.t === 'Input');
// Whatever the modal says when it has no rows — the sentence that must not read "nothing exists"
// while Sage is still looking, or while Domino refused to say.
const emptyTextIn = (tree) => ((walk(tree).find((n) => n.t === 'Empty') || { p: {} }).p.description) || null;
// The line above the list, which is where a listing that half-answered says so.
const noteIn = (tree) => {
  const note = walk(tree).find(
    (n) => (n.p || {}).className === 'sw-cat-note' && typeof (n.c || [])[0] === 'string');
  return note ? note.c[0] : null;
};
const sideButton = (tree, label) => walk(tree).find(
  (n) => n.t === 'button' && (n.p.className || '').includes('sw-cat-side-btn')
    && walk(n).some((c) => (c.c || []).includes(label))
);
const countsOf = (tree) => Object.fromEntries(walk(tree)
  .filter((n) => n.t === 'button' && (n.p.className || '').includes('sw-cat-side-btn'))
  .map((n) => [n.p.key, (walk(n).find((c) => (c.p || {}).className === 'sw-cat-side-count')
    || { c: [null] }).c[0]]));

// Opening. Everything after this point is somebody using a modal already on screen, so the
// requests are read per step rather than in total.
let tree = render();
const onOpen = { rows: rowsOf(tree), inProject: inProjectOf(tree), skeleton: skeletonIn(tree),
                 empty: emptyIn(tree), requests: requests.splice(0).length };

// A second read fired while the first is still out. Only `stale` does this, and only to see which
// of the two answers the store ends up holding.
if (act === 'stale') SW.store.refreshResourceListing();

// The background refresh is a promise chain; let it settle and redraw the way a notify would.
await new Promise((resolve) => setTimeout(resolve, act === 'stale' ? 80 : 0));
await new Promise((resolve) => setTimeout(resolve, 0));
tree = render();
const afterRefresh = { rows: rowsOf(tree), inProject: inProjectOf(tree),
                       skeleton: skeletonIn(tree), empty: emptyIn(tree),
                       emptyText: emptyTextIn(tree), note: noteIn(tree),
                       requests: requests.splice(0).length };

let afterAct = null;
if (act === 'type') {
  searchInput(tree).p.onChange({ target: { value: 'risk' } });
  tree = render();
  afterAct = { rows: rowsOf(tree), counts: countsOf(tree), requests: requests.splice(0) };
} else if (act === 'kind') {
  sideButton(tree, 'Language models').p.onClick();
  tree = render();
  afterAct = { rows: rowsOf(tree), counts: countsOf(tree), requests: requests.splice(0) };
} else if (act === 'no-match') {
  // A search that matches nothing while a leg of the listing is refusing. The filter is why the
  // list is empty; the refusal is a separate fact and must not steal the sentence.
  searchInput(tree).p.onChange({ target: { value: 'zzz' } });
  tree = render();
  afterAct = { rows: rowsOf(tree), emptyText: emptyTextIn(tree), note: noteIn(tree),
               requests: requests.splice(0) };
} else if (act === 'add') {
  // Adding reloads the scope, which is the moment the listing used to be dropped. Every frame
  // between the click and the reload landing is looked at, because a blank that lasts one round
  // trip is still a blank somebody watches.
  const row = catalogRows(tree).find((r) => r.name === 'Risk history');
  const frames = [];
  // Redrawn on every notify, the way `app.js` does it. A blank between two notifies is invisible
  // to a harness that only looks once the dust has settled, and it is exactly the blank somebody
  // watching the screen would see.
  const stop = SW.store.subscribe(() => {
    tree = render();
    frames.push({ skeleton: skeletonIn(tree), rows: rowsOf(tree).length });
  });
  walk(tree).find((n) => (n.p || {}).resource === row).p.onAdd(row);
  for (let i = 0; i < 12; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
  if (typeof stop === 'function') stop();
  tree = render();
  afterAct = {
    everBlanked: frames.some((f) => f.skeleton || f.rows === 0),
    rows: rowsOf(tree),
    inProject: inProjectOf(tree),
    requests: requests.splice(0),
  };
}

// A settle after the act too: a debounced fetch would land here rather than in the step above,
// and "no request" has to mean none arrived late either.
await new Promise((resolve) => setTimeout(resolve, 200));
const lateRequests = requests.splice(0);

console.log(JSON.stringify({ onOpen, afterRefresh, afterAct, lateRequests }));
