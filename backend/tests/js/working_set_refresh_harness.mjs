// What leaves the browser when the working set changes, and what the store holds afterwards (#162).
//
// Putting a Resource into the Project cannot change what Domino holds, so an Add has no reason to
// re-read `/api/resources` and `/api/assets` — the pair that measures 5.1 s on a real deployment
// (#160). That is a claim about which requests a store method issues, not about anything in the
// rendered tree, so this drives `SW.store` directly and reports the URLs each act produced.
//
// It also reports the two lists an Add has to move without that read: the rail's working set, and
// `catalogueParents` — the platform's rows MINUS the working set, which is the catalogue half of
// the @ menu. A refresh that skipped the listing and left those alone would be fast and wrong.
//
// Input on stdin: `{ "act": "add" | "remove" | "pin" | "unpin" | "switch" | "race" | "overlap" |
// "stale-load" | "switch-race" }`.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { act } = JSON.parse(fs.readFileSync(0, 'utf8'));

// The platform's rows. Two Datasets and a Data Source, of which only `d1` is in the project — so
// there is a catalogue parent to lose on an Add and one to get back on a Remove.
const PLATFORM_ASSETS = {
  assets: [
    { id: 'd1', name: 'Sales rows', project: 'retail' },
    { id: 'd2', name: 'Risk history', project: 'risk' },
  ],
};
const PLATFORM_RESOURCES = {
  data_sources: [{ id: 's1', name: 'Warehouse', connector: 'snowflake' }],
  llm_aliases: [],
  model_apis: [],
};

// The project's own membership, served from a local file and mutated by the acts below the way the
// server mutates it. Starting state: the one Dataset.
let membership = [{ id: 'dataset:d1', kind: 'dataset', name: 'Sales rows' }];

// The Project's Uploads, which `/api/project` answers with. `race` grows this between the scope
// load's read of it and the mutation's, so the two snapshots can be told apart on screen.
let scratch = [];

// `race` holds back the platform listing, which is the 5.1 s the scope load defers its `/project`
// read behind and the window a mutation lands in. `stale-load` holds back the scope load's
// MEMBERSHIP read instead — the first of them — so its pre-Add answer arrives after the refresh has
// already written the post-Add one.
let membershipReads = 0;
function delayFor(url) {
  if (act === 'race') return url.endsWith('/api/assets') ? 60 : 0;
  // Long enough that the switch's deferred listing is still out when the sample below is taken,
  // short enough that it has landed by the settle at the end.
  if (act === 'switch-race') return url.endsWith('/api/assets') ? 120 : 0;
  if (act === 'stale-load' && url.endsWith('/api/project/resources')) {
    membershipReads += 1;
    return membershipReads === 1 ? 80 : 0;
  }
  return 0;
}

const requests = [];
function answer(url, init) {
  const method = (init && init.method) || 'GET';
  if (url.endsWith('/api/assets')) return PLATFORM_ASSETS;
  // `/api/resources` is the platform listing; `/api/project/resources` is this project's
  // membership. Two different reads whose paths end in the same word.
  if (url.endsWith('/api/resources')) return PLATFORM_RESOURCES;
  if (url.split('?')[0].endsWith('/api/project/resources')) {
    if (method === 'GET') return { items: membership };
    if (method === 'POST') {
      const row = JSON.parse(init.body);
      if (!membership.some((m) => m.id === row.id)) membership = [...membership, row];
      return { added: true };
    }
    if (method === 'DELETE') {
      const id = decodeURIComponent(new URL(url, 'http://x/').searchParams.get('id') || '');
      membership = membership.filter((m) => m.id !== id);
      return { removed: true };
    }
    return { items: membership };
  }
  // Unpinning a leaf drops the parent's pin, and with no pins left the parent leaves the project.
  if (url.split('?')[0].endsWith('/api/project/resources/pins')) {
    if (method === 'DELETE') {
      const id = decodeURIComponent(new URL(url, 'http://x/').searchParams.get('id') || '');
      membership = membership.filter((m) => m.id !== id);
    }
    return { ok: true };
  }
  if (url.endsWith('/api/project')) return { scratch, attached: [] };
  if (url.endsWith('/api/threads')) return { threads: [] };
  if (url.endsWith('/api/members')) {
    return { members: [], directory: [], ownerId: '', self: '', connected: true };
  }
  return {};
}

// The confirm the removal opens. Held so the harness can press Remove, which is where the act is.
let confirmed = null;

const sandbox = {
  console, JSON, Object, String, Array, Error, Map, Set, Promise, Date, Math, Number, Boolean,
  RegExp, encodeURIComponent, decodeURIComponent, setTimeout, clearTimeout, setInterval,
  clearInterval, URLSearchParams, TextEncoder, TextDecoder, URL, Blob, ArrayBuffer, Uint8Array,
  fetch: (url, init) => {
    requests.push(`${(init && init.method) || 'GET'} ${url}`);
    const body = answer(url, init);
    const res = {
      ok: true,
      status: 200,
      statusText: 'OK',
      headers: { get: () => 'application/json' },
      json: () => Promise.resolve(body),
    };
    const wait = delayFor(url);
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
    useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
    useEffect: () => {}, useRef: () => ({ current: null }), useMemo: (fn) => fn(),
    Fragment: 'Fragment',
  },
  antd: {
    Modal: {
      confirm: (opts) => { confirmed = opts; },
      info: () => {},
    },
    message: { info: () => {}, success: () => {}, error: () => {}, warning: () => {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  EventSource: function () {},
  SW: {},
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'prefs.js', 'router.js', 'store.js', 'api.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

// A store as it stands after a project has finished loading: the listing read once, and the rail
// holding the one Dataset the membership names.
const LISTING = {
  errors: {},
  groups: {
    dataset: [
      { id: 'dataset:d1', name: 'Sales rows', kind: 'dataset', description: 'in retail' },
      { id: 'dataset:d2', name: 'Risk history', kind: 'dataset', description: 'in risk' },
    ],
    datasource: [{ id: 'data_source:s1', name: 'Warehouse', kind: 'datasource' }],
    model_llm: [],
    model_predictive: [],
  },
};
SW.store.set({
  scope: { id: 'p1', name: 'quick-start' },
  ready: true,
  resourceListing: LISTING,
  resourceListingScope: 'p1',
  resourceGroups: { dataset: [LISTING.groups.dataset[0]] },
  catalogueParents: [
    LISTING.groups.dataset[1],
    LISTING.groups.datasource[0],
    // A row the platform listing does not hold, so it can only be here because it was carried over
    // from the project being left. Nothing recomputed from a listing can put it back.
    { id: 'dataset:gone', name: 'Left behind', kind: 'dataset' },
  ],
});

const names = (list) => (list || []).map((r) => r.name).sort();
const railNames = () => {
  const { resourceGroups } = SW.store.get();
  return names(Object.values(resourceGroups || {}).flat());
};
const settle = async () => {
  // Long enough for a held-back listing to land, because the write that must NOT happen is the one
  // riding in with it. A settle that only drains microtasks would report the race as won.
  await new Promise((resolve) => setTimeout(resolve, 200));
  for (let i = 0; i < 20; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

const PIN = { database: 'analytics', schema: 'public', table: 'orders' };

// What the catalogue half of the @ menu holds partway through an act, for the acts that have a
// partway worth looking at.
let mid = null;

if (act === 'add') {
  await SW.store.addToProject({ id: 'dataset:d2', name: 'Risk history', kind: 'dataset' });
} else if (act === 'remove') {
  const done = SW.store.removeFromProject({ id: 'dataset:d1', name: 'Sales rows' });
  await confirmed.onOk();
  await done;
} else if (act === 'pin') {
  await SW.store.pinLeaf({ id: 'data_source:s1', name: 'Warehouse', kind: 'datasource' }, PIN);
} else if (act === 'unpin') {
  await SW.store.unpinLeaf({ id: 'data_source:s1', name: 'Warehouse', kind: 'datasource' }, PIN);
} else if (act === 'switch') {
  await SW.store.setScope({ id: 'p2', name: 'other' });
} else if (act === 'overlap' || act === 'stale-load') {
  // A same-scope `loadScopeData` in flight when the viewer clicks Add. It is the refresh of the
  // four Dataset-folder acts, reached here through the store's own handle so the act is the
  // function itself rather than a stand-in. Not awaited: the point is that it is still out.
  //
  // `overlap` lets its membership read answer first, so the scope load holds the pre-Add snapshot
  // and must not write it. `stale-load` holds that read back instead, so the same snapshot arrives
  // after the refresh has written — the mirror ordering, which a ticket claimed at the scope load's
  // SECOND phase could not have covered.
  const load = SW.store.reloadScopeData();
  await SW.store.addToProject({ id: 'dataset:d2', name: 'Risk history', kind: 'dataset' });
  await load;
} else if (act === 'switch-race') {
  // A mutation inside the switch's own phase-1 window, which is the case that supersedes the scope
  // load's membership write. The catalogue is cleared BY that write, so a clear tied to it does not
  // happen — and the @ menu goes on offering the rows of the project the viewer has just left for
  // the length of the deferred listing.
  const load = SW.store.setScope({ id: 'p2', name: 'other' });
  await SW.store.addToProject({ id: 'dataset:d2', name: 'Risk history', kind: 'dataset' });
  // Sampled before that listing lands, because self-healing five seconds later is not the claim.
  await new Promise((resolve) => setTimeout(resolve, 40));
  mid = names(SW.store.get().catalogueParents);
  await load;
} else if (act === 'race') {
  // A mutation inside the window the scope load defers its listing behind. `setScope` returns once
  // the membership and the people are read; the `/project` half is still out, carrying a snapshot
  // taken before the Upload below exists.
  await SW.store.setScope({ id: 'p2', name: 'other' });
  scratch = [{ path: 'public/data/new.csv', name: 'new.csv' }];
  await SW.store.addToProject({ id: 'dataset:d2', name: 'Risk history', kind: 'dataset' });
}
// Late arrivals count: `loadScopeData` defers its listing read rather than awaiting it, so an act
// that looked quiet while it ran can still fan out to the platform a tick later.
await settle();

const state = SW.store.get();
console.log(JSON.stringify({
  requests,
  mid,
  files: names((SW.store.get().resourceGroups || {}).file),
  rail: railNames(),
  catalogueParents: names(state.catalogueParents),
  listingHeld: !!state.resourceListing,
}));
