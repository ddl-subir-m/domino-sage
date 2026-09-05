// What the Resource Browser's cascade does for a Data Source, now that it only looks (#142).
//
// It used to be where a Data Source was BOUND: #129 hung `Use in {app}` beside the crumb and on
// every leaf, and the Scope was wherever the walk had got to. ADR-0021 moved the act onto the Built
// App's own surface and left the walk here, so what this file asserts is a cascade that descends,
// remembers where it is, survives a listing that will not answer — and offers nothing that writes.
//
// Nothing is mounted, for the reason `build_header_harness.mjs` gives: every claim here is about
// WHICH CONTROL IS WHERE and WHAT A CLICK POSTS, and both are settled before antd draws anything.
//
// What this file does NOT share with the other harnesses is the `useState` stub. Theirs returns a
// no-op setter, which is right when a claim is settled by the first paint. It is useless here: the
// cascade IS a state machine — `database` and `schema` ARE the position the person is standing on —
// and #129's whole question is what the screen offers at each of the four positions. A no-op setter
// can never leave the top of the cascade, so the three positions that matter would be unreachable.
// Hooks are therefore real: slots per component instance, effects with dependency comparison and
// cleanup, and a paint loop that re-renders until the reads have landed and nothing more changes.
//
// The panel is driven rather than bypassed, all the way down. A step expands the Data Source row by
// clicking its own control, which is what puts the cascade on screen at all — and it is the panel
// that decides what to hand it. A harness that rendered `SW.DataSourceCascade` directly could pass
// with a door the panel had put back.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const steps = JSON.parse(fs.readFileSync(0, 'utf8'));

// --- the store behind the cascade ------------------------------------------
// Two databases so the top of the cascade is a real choice rather than a level with one answer,
// and two schemas under `DWH` for the same reason one level down. `SANDBOX` exists so a second walk
// can move the Scope somewhere else, which is what the door staying open is for.
const TREE = {
  ds_1: {
    DWH: {
      MARTS: ['DIM_ACCOUNT', 'FCT_USAGE_DAILY'],
      REPORTING: ['V_ARR_WATERFALL'],
    },
    SANDBOX: { PUBLIC: ['SCRATCH_FORECAST'] },
  },
  // The source Domino pins a database for in its own configuration. `FakeResourceProvider` keeps one
  // of these for the same reason (`provider.py`): such a cascade "opens on the schema level with the
  // database already answered", so its FIRST screen is a position that already has a Scope.
  ds_2: { underwriting: { dbo: ['policies', 'claims'] } },
};

const APP = { id: 'app_a', name: 'Desk dashboard' };

// The Project's rows, in the shape `SW.api.resources()` builds them. `bindingKey` carries the BARE
// id beside the kind; the row's own id is prefixed. `membershipParent` is what makes the row
// expandable, and expanding is the only way to the cascade.
const GROUPS = {
  dataset: [], table: [], mcp: [], file: [], pin: [], tool: [], agent: [], skill: [],
  model_predictive: [],
  datasource: [{
    id: 'data_source:ds_1',
    name: 'Market data EOD',
    kind: 'datasource',
    bindingKey: ['data_source', 'ds_1'],
    levels: ['database', 'schema', 'table'],
    membershipParent: true,
  }, {
    // Same three levels, but Domino answers the first one. `default_database` is a real field on a
    // Data Source, so "the top of the cascade" is not always the database stage — for this source
    // the top IS the schema stage, and it has a Scope the moment it opens.
    id: 'data_source:ds_2',
    name: 'Underwriting SQL',
    kind: 'datasource',
    bindingKey: ['data_source', 'ds_2'],
    levels: ['database', 'schema', 'table'],
    default_database: 'underwriting',
    membershipParent: true,
  }],
  // Beside it, because the split this ticket made to `canBind` has to leave the Alias exactly as it
  // was: the same sign, and a door still on its own row.
  model_llm: [{
    id: 'llm_alias:al_1',
    name: 'Claude Sonnet 4',
    kind: 'model_llm',
    bindingKey: ['llm_alias', 'al_1'],
  }],
};

// --- the server ------------------------------------------------------------
let bound = [];
let posted = [];
let calls = [];
// Which cascade level refuses to answer, so the "a store that will not answer" position can be
// reached by walking to it rather than by rendering it directly.
let failAt = null;

const json = (body, status = 200) => Promise.resolve({
  ok: status < 400,
  status,
  statusText: status === 200 ? 'OK' : 'Error',
  headers: { get: () => 'application/json' },
  json: async () => body,
});

const query = (path, key) => {
  const m = path.match(new RegExp(`[?&]${key}=([^&]*)`));
  return m ? decodeURIComponent(m[1]) : '';
};

function route(path, init) {
  let m;
  if ((m = path.match(/^\/data-sources\/([^/?]+)\/databases/))) {
    if (failAt === 'database') return json({ error: 'Snowflake answered 403.' }, 502);
    return json({ items: Object.keys(TREE[m[1]] || {}) });
  }
  if ((m = path.match(/^\/data-sources\/([^/?]+)\/schemas/))) {
    if (failAt === 'schema') return json({ error: 'Snowflake answered 403.' }, 502);
    return json({ items: Object.keys((TREE[m[1]] || {})[query(path, 'database')] || {}) });
  }
  if ((m = path.match(/^\/data-sources\/([^/?]+)\/tables/))) {
    if (failAt === 'table') return json({ error: 'Snowflake answered 403.' }, 502);
    const db = (TREE[m[1]] || {})[query(path, 'database')] || {};
    return json({ items: db[query(path, 'schema')] || [] });
  }
  // The real route replaces on kind+id and leaves the Scope out of the key, which is the whole
  // reason the door stays open after it is used. A fake that appended would let a second bind at a
  // different Scope read as success while the record said something else.
  if (path === '/bindings' && init && init.method === 'POST') {
    const body = JSON.parse(init.body || '{}');
    posted.push(body);
    const row = {
      kind: body.kind, id: body.id, name: 'Market data EOD', display_name: 'Market data EOD',
    };
    ['database', 'schema', 'table'].forEach((k) => { if (body[k]) row[k] = body[k]; });
    bound = [...bound.filter((b) => !(b.kind === body.kind && b.id === body.id)), row];
    return json({ bindings: bound });
  }
  if (path === '/bindings') return json({ bindings: bound });
  if (path === '/project') return json({ attached: [] });
  return json({});
}

// --- a React with working hooks --------------------------------------------
const slotsOf = new Map();   // instance id -> hook slots, kept between paints
let counts = null;           // per-paint instance counter, so an instance keeps its slots
let current = null;
let cursor = 0;
let dirty = false;
const pending = [];

function callComponent(fn, props) {
  const name = fn.name || 'anonymous';
  const n = counts.get(name) || 0;
  counts.set(name, n + 1);
  // Keyed on "the nth instance of this component in this paint". Stable because the components
  // that HOLD state keep their position between paints — the panel's rows are the panel's rows, and
  // the cascade is the one the expanded row rendered. What changes between paints is the level
  // BELOW them, which holds none.
  const id = `${name}#${n}`;
  if (!slotsOf.has(id)) slotsOf.set(id, []);
  const wasId = current;
  const wasCursor = cursor;
  current = id;
  cursor = 0;
  try {
    return fn(props);
  } finally {
    current = wasId;
    cursor = wasCursor;
  }
}

const React = {
  createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
  Fragment: 'Fragment',
  useState: (init) => {
    const slots = slotsOf.get(current);
    const i = cursor++;
    if (!(i in slots)) slots[i] = typeof init === 'function' ? init() : init;
    return [slots[i], (next) => {
      const value = typeof next === 'function' ? next(slots[i]) : next;
      // Compared, not assigned blindly: React bails out of an identical set, and without that a
      // component whose effect re-sets the same value would spin the paint loop forever.
      if (!Object.is(value, slots[i])) {
        slots[i] = value;
        dirty = true;
      }
    }];
  },
  // Collected during the walk and run after it, the way React runs them after a commit. The
  // dependency comparison is real and so is the cleanup, because the cascade leans on both: its
  // effect re-reads when the STAGE moves, and the cleanup is what sets `cancelled` on the read it
  // is leaving. Skip the cleanup and a landing read from the previous position overwrites the one
  // the person is looking at.
  useEffect: (fn, deps) => {
    pending.push({ slots: slotsOf.get(current), i: cursor++, fn, deps });
  },
  useRef: (init) => {
    const slots = slotsOf.get(current);
    const i = cursor++;
    if (!(i in slots)) slots[i] = { current: init === undefined ? null : init };
    return slots[i];
  },
};

function runEffects() {
  for (const e of pending.splice(0)) {
    const prev = e.slots[e.i];
    const same = prev && Array.isArray(e.deps) && Array.isArray(prev.deps)
      && prev.deps.length === e.deps.length
      && prev.deps.every((d, k) => Object.is(d, e.deps[k]));
    if (same) continue;
    if (prev && typeof prev.off === 'function') prev.off();
    const off = e.fn();
    e.slots[e.i] = { deps: e.deps, off: typeof off === 'function' ? off : null };
  }
}

// --- the browser -----------------------------------------------------------
const said = [];
const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, Infinity, setTimeout, clearTimeout, setInterval: () => 0,
  clearInterval: () => {}, requestAnimationFrame: (fn) => fn(),
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  location: { hash: '' },
  history: { replaceState() {} },
  addEventListener() {},
  removeEventListener() {},
  open() {},
  React,
  antd: {
    Input: Object.assign(function Input() { return null; }, { TextArea: 'Input.TextArea' }),
    Button: 'Button', Dropdown: 'Dropdown', Tag: 'Tag', Tooltip: 'Tooltip', Space: 'Space',
    Spin: 'Spin', Empty: 'Empty', Skeleton: 'Skeleton', Alert: 'Alert', Checkbox: 'Checkbox',
    Drawer: 'Drawer',
    Modal: { confirm: (cfg) => ({ update: (n) => Object.assign(cfg, n), destroy: () => {} }) },
    message: {
      success: (t) => said.push(String(t)), error: (t) => said.push(String(t)),
      info: (t) => said.push(String(t)), warning: (t) => said.push(String(t)),
    },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async (url, init) => {
    const path = String(url).replace(/^\.\/api/, '');
    calls.push(`${(init && init.method) || 'GET'} ${path.split('?')[0]}`);
    return route(path, init);
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

// `modes/builder.js` joins the list because the app's own list moved into it (#151): what the app
// reads of a Data Source is said there now, where the Scope door that chooses it already was.
for (const f of ['util.js', 'api.js', 'store.js', 'router.js', 'prefs.js',
                 'components/platform-error.js', 'components/resource-tree.js',
                 'components/resource-panel.js', 'modes/builder.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

// --- walking the tree ------------------------------------------------------
const SKIP = new Set(['Input', 'anonymous']);
const named = new Map();
for (const [k, v] of Object.entries(SW)) if (typeof v === 'function') named.set(v, k);

function tag(node) {
  if (typeof node.t === 'string') return node.t;
  return named.get(node.t) || node.t.name || 'anonymous';
}

function walk(node, out = [], depth = 0) {
  if (node === null || node === undefined || node === false || node === true || depth > 80) return out;
  if (Array.isArray(node)) { node.forEach((n) => walk(n, out, depth)); return out; }
  if (typeof node === 'string' || typeof node === 'number') {
    out.push({ text: String(node) });
    return out;
  }
  if (typeof node !== 'object' || !node.t) return out;

  const name = tag(node);
  const props = node.p || {};
  const entry = {
    el: name,
    className: props.className || '',
    label: props['aria-label'] || '',
  };
  if (props.resource && props.resource.id) entry.resourceId = props.resource.id;
  // The React key, which a stub can see because it is an ordinary prop here. The app's own rows
  // are keyed by the record they draw — a Binding's `bindingId`, an Attachment's path — which is
  // how a claim about one of them names it (#151).
  if (props.key !== undefined) entry.key = String(props.key);
  if (typeof props.onClick === 'function') entry.onClick = props.onClick;
  if (props.menu && props.menu.items) {
    entry.items = props.menu.items.map((i) => ({
      key: i.key || '', label: typeof i.label === 'string' ? i.label : '',
    }));
  }
  const direct = (Array.isArray(node.c) ? node.c : [node.c]).filter(
    (child) => typeof child === 'string' || typeof child === 'number'
  );
  if (direct.length) entry.texts = direct.map(String);
  out.push(entry);

  if (typeof node.t === 'function' && !SKIP.has(name)) {
    walk(callComponent(node.t, Object.assign({}, props, { children: node.c })), out, depth + 1);
  }
  // A Tooltip's words are a PROP, so they are recorded beside the control rather than walked into
  // the text stream. Walked, the tooltip on a truncating subtitle would land in the row's texts
  // next to the subtitle itself, and "the row says DWH.MARTS" would pass on a row that only ever
  // said it in a hover — which is the half a truncation tooltip is supposed to rescue, not replace.
  if (typeof props.title === 'string' && props.title) entry.tip = props.title;
  else walk(props.title, out, depth + 1);
  walk(node.c, out, depth + 1);
  return out;
}

const settle = () => new Promise((res) => setTimeout(res, 0));

// One paint, then as many more as the reads that land during it ask for. The cascade fetches from
// an effect and sets state when the answer arrives, so the first tree is always a spinner: a
// harness that asserted on it would be asking what the screen says before it says anything.
async function paint() {
  let nodes = [];
  for (let i = 0; i < 15; i += 1) {
    counts = new Map();
    dirty = false;
    nodes = walk(callComponent(SW.ResourcePanel, {}));
    runEffects();
    await settle();
    if (!dirty) break;
  }
  return nodes;
}

const click = (node) => node.onClick({ preventDefault() {}, stopPropagation() {} });

// Any control on screen offering the act that is no longer this panel's, found the way a person
// would find one: by its words. It should come back empty at every position (#142).
const doorsIn = (nodes) => nodes.filter(
  (n) => (n.texts || []).some((t) => t === `Use in ${APP.name}`)
);
const stepNamed = (nodes, name) => nodes.find(
  (n) => n.className === 'sw-tree-step' && (n.texts || []).includes(name)
);

// The panel's rows, under the section head each one sits below — `build_header_harness.mjs` builds
// the same shape for the same reason: a flat list of every string on the panel cannot tell a
// subtitle from a section head, and the SAME Resource appears under two sections at once. Which of
// the two carries the sign and which carries the removal is half of what #129 asks.
//
// Text stops being collected as soon as the walk leaves the row's own `sw-res-*` elements, so the
// cascade hanging underneath an expanded row is not read as that row's subtitle. What the cascade
// says is reported on its own, above.
function rowsOf(nodes) {
  const out = [];
  let section = null;
  let row = null;
  let pendingId = null;
  for (const n of nodes) {
    const cls = String(n.className || '');
    if (n.resourceId) { pendingId = n.resourceId; continue; }
    if (cls.startsWith('sw-res-row')) {
      row = { section, className: cls, id: pendingId, texts: [], items: [], tips: [] };
      pendingId = null;
      out.push(row);
      continue;
    }
    if (row && n.items) { row.items = n.items; continue; }
    if (row && n.tip) { row.tips.push(n.tip); continue; }
    // The panel's list is divided by group labels — `Data (3)` — where it used to carry section
    // heads for two different scopes (#151). The count is dropped: a section name that changed with
    // its own length would be no name at all.
    if (cls === 'sw-group-label') {
      const head = /^(.*) \((?:\d+|…)\)$/.exec((n.texts || []).join(''));
      if (head) { section = head[1]; row = null; }
      continue;
    }
    if (cls && !cls.startsWith('sw-res-')) { row = null; continue; }
    if (row && n.text) row.texts.push(n.text);
  }
  return out;
}

// The app's own rows, off the App dependencies modal. Same shape as `rowsOf` above — id, texts,
// items, tips — because every question this file asks of one list it also asks of the other.
function appRowsOf(nodes) {
  const out = [];
  let section = null;
  let row = null;
  for (const n of nodes) {
    const cls = String(n.className || '');
    if (cls.includes('sw-app-group')) { section = (n.texts || []).join(''); row = null; continue; }
    if (cls === 'sw-appdeps-row') {
      row = { section, className: cls, id: n.key || null, texts: [], items: [], tips: [] };
      out.push(row);
      continue;
    }
    if (row && n.items) { row.items = n.items; continue; }
    if (row && n.tip) { row.tips.push(n.tip); continue; }
    if (cls === 'sw-appdeps-foot') { row = null; continue; }
    if (row && n.text) row.texts.push(n.text);
  }
  return out;
}

// --- the run ---------------------------------------------------------------
async function arrive(step) {
  bound = step.bound ? JSON.parse(JSON.stringify(step.bound)) : [];
  posted = [];
  calls = [];
  said.length = 0;
  failAt = step.fail || null;
  slotsOf.clear();
  // Seeded rather than fetched: `/api/project/resources` is not what this file is about, and the
  // only thing the panel needs from it is rows in the id space Bindings join on.
  SW.store.set({
    resourceGroups: GROUPS,
    resourceErrors: {},
    resourcesLoading: false,
    activeApp: step.app === false ? null : APP,
    panelFilter: null,
    projectPlan: null,
    bindings: bound,
    attachments: [],
    appAttachments: [],
    appRemoval: null,
    datasetTargets: [],
  });
  const at = `#/${step.mode || 'build'}/thr_1?app=${APP.id}`;
  sandbox.location.hash = at;
  SW.router.go(at);
}

const report = [];
for (const step of steps) {
  await arrive(step);
  let nodes = await paint();

  // Into the cascade, through the row's own control. The panel lists more than one Data Source, so
  // the control is found under the row the step names rather than by being the only one on screen.
  if (step.expand !== false) {
    const want = `data_source:${step.source || 'ds_1'}`;
    const at = nodes.findIndex((n) => n.resourceId === want);
    if (at < 0) throw new Error(`no row on screen for ${want}`);
    const opener = nodes.slice(at).find((n) => n.className === 'sw-res-expand');
    if (!opener) throw new Error(`${want} offered no way to expand it`);
    click(opener);
    nodes = await paint();
  }

  // One pass through the cascade: back up a level if asked, then down to the named position. Two of
  // these run for a step with a `then`, which is how "a walk can be retraced" is asked as a question
  // rather than assumed.
  async function pass(spec) {
    if (spec.back) {
      const up = nodes.find(
        (n) => n.className === 'sw-tree-crumb-btn' && (n.texts || []).includes(spec.back)
      );
      if (!up) throw new Error(`no crumb named ${spec.back} to go back to`);
      click(up);
      nodes = await paint();
    }
    // Down the cascade, one named level at a time — the walk the creator makes.
    for (const name of spec.walk || []) {
      const into = stepNamed(nodes, name);
      if (!into) throw new Error(`no step named ${name} at this level`);
      click(into);
      nodes = await paint();
    }
    const at = {
      doors: doorsIn(nodes).length,
      crumb: nodes.filter((n) => n.className === 'sw-tree-crumb-btn').flatMap((n) => n.texts || []),
    };
    return at;
  }

  const before = await pass(step);
  // A second walk, when the step asks for one. Where the cascade ENDS UP is what the report carries
  // — a `then` exists to move it, and reporting the first position would be reporting the thing the
  // step went on to change.
  const after = step.then ? await pass(step.then) : before;

  report.push({
    step: `${step.mode || 'build'} ${(step.walk || []).join('.') || 'top'}`,
    mode: SW.router.get().mode,
    crumb: after.crumb,
    // Every `Use in {app}` on screen, by the class of the control that carries it. The cascade's
    // own was `sw-tree-bind`, and it is gone (#142); what may still show up here is the Alias row's
    // MENU item, which is data on a prop rather than words on the screen — so this list being empty
    // and `rows[].items` still holding the Alias's act are two different claims.
    doors: doorsIn(nodes).map((n) => n.className),
    doorsBefore: before.doors,
    steps: nodes.filter((n) => n.className === 'sw-tree-step').flatMap((n) => n.texts || []),
    leaves: nodes.filter((n) => n.className === 'sw-tree-leaf-name').flatMap((n) => n.texts || []),
    rows: rowsOf(nodes),
    // The app's own list, on the surface that owns it (#151). Same shape as the rows above, so the
    // two scopes can still be read against each other: which one carries the sign and which
    // carries the removal is half of what #129 asks.
    appRows: appRowsOf(walk(callComponent(SW.AppDependenciesModal, {}))),
    posted,
    bindings: bound,
    said: said.slice(),
    calls,
    words: nodes.flatMap((n) => n.texts || []),
  });
}

console.log(JSON.stringify(report));
