// What Build draws once the rail stops swapping (#82).
//
// Nothing is mounted. `createElement` is stubbed to a plain object, so calling a component returns
// a tree of data and the whole file stops short of antd — which is the point: every claim this
// ticket makes is about WHICH CONTROL IS WHERE and WHAT A CLICK WRITES, and both are settled
// before React is asked to draw anything. Mounting would test antd.
//
// `useEffect` is recorded rather than ignored, because one criterion is about a timer: something
// mounted in Build has to go on refreshing the app list, and the only way to ask that of a
// function component is to run its effects and watch what they schedule.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const steps = JSON.parse(fs.readFileSync(0, 'utf8'));

// --- the server ------------------------------------------------------------
// Four apps, chosen for the four things a row has to be able to say: built, not built yet,
// mid-build (#77), and behind a teammate's push (#78). A control that names only the selected app
// cannot show any of the last three, which is why the list is the criterion.
const APPS = [
  { id: 'app_a', name: 'Desk dashboard', built: true, building: false, behind: false, published: true },
  { id: 'app_b', name: 'P&L report', built: false, building: false, behind: false, published: false },
  { id: 'app_c', name: 'Rate curve viewer', built: true, building: true, behind: false, published: false },
  { id: 'app_d', name: 'Risk monitor', built: true, building: false, behind: true, published: true },
];

const THREADS = {
  // One Conversation that changed three of them. Two are "other" while app_a is in the preview,
  // which is the number the header has to say out loud.
  thr_many: {
    id: 'thr_many', title: 'Desks', artifacts: [], history: [],
    touched: [
      { appId: 'app_a', appName: 'Desk dashboard', kind: 'built' },
      { appId: 'app_b', appName: 'P&L report', kind: 'changed' },
      { appId: 'app_d', appName: 'Risk monitor', kind: 'changed' },
    ],
  },
  // Changed the app in the preview and nothing else: there is no "other", so nothing is said.
  thr_one: {
    id: 'thr_one', title: 'Just the one', artifacts: [], history: [],
    touched: [{ appId: 'app_a', appName: 'Desk dashboard', kind: 'built' }],
  },
  // Two apps, so with either one in the preview the other is exactly one — which is the count that
  // has to read as English rather than as `1 other apps`.
  thr_two: {
    id: 'thr_two', title: 'Two of them', artifacts: [], history: [],
    touched: [
      { appId: 'app_a', appName: 'Desk dashboard', kind: 'built' },
      { appId: 'app_b', appName: 'P&L report', kind: 'changed' },
    ],
  },
  thr_none: { id: 'thr_none', title: 'Nothing built here', artifacts: [], history: [], touched: [] },
};

// What each app has RECORDED, per app, because the row's whole claim is that two apps under the
// same conversation ship different things (#92). Read off disk by the server in real life — here,
// two flat tables keyed by app, served the way `/api/bindings` and `/api/project` serve them.
// A Model API sits beside the Alias and the Data Source because the three do not cost the same to
// re-pick (ADR-0011): the Data Source's Scope goes with the record, and the Model API's access
// token does NOT — it lives in its own store keyed by model id — so the confirm has to say
// different things over them and a fixture with one kind could not tell.
//
// `used` is the advisory label the end-of-turn scan leaves (#93), served exactly as the backend
// serves it: `true`/`false` once a build turn has looked at that app, and ABSENT for an app no
// turn has scanned — `app_c`, which is the case that must draw no mark rather than call its one
// Binding unused. Only `app_a` has a mixed answer, which is the only one that can show that the
// mark lands on the right name.
const BINDINGS = {
  app_a: [
    { kind: 'llm_alias', id: 'al_1', name: 'claude-sonnet-4', display_name: 'Claude Sonnet 4', used: true },
    { kind: 'data_source', id: 'ds_1', name: 'market-data-eod', display_name: 'Market data EOD', used: false },
    { kind: 'model_api', id: 'ma_1', name: 'churn-risk', display_name: 'Churn risk', used: true },
  ],
  app_c: [{ kind: 'llm_alias', id: 'al_2', name: 'qwen-2-5', display_name: 'Qwen 2.5' }],
};
// `app_d` carries files and no Bindings, `app_c` the reverse: a kind with nothing in it is not the
// same state as an app with nothing at all, and only the second one gets the empty state.
//
// `legacy.csv` is the rehydrated entry `detach_file`'s docstring records: no `dataset_id`, so there
// is no source to name. It carries a `dataset` all the same, because `_rehydrate_attached` fills
// that from the symlink's PARENT DIRECTORY — a fixture without one would be a shape the backend
// never writes, and the sentence that must not name a source would never be asked the real question.
const ATTACHED = {
  app_a: [
    { path: 'public/data/desks/margins.csv', file: 'margins.csv',
      dataset: 'desks', dataset_id: 'as_desks', size: 12 },
    { path: 'public/data/rehydrated/legacy.csv', file: 'legacy.csv',
      dataset: 'rehydrated', dataset_id: null, size: 7 },
  ],
  app_d: [{ path: 'public/data/risk/limits.csv', file: 'limits.csv',
            dataset: 'risk', dataset_id: 'as_risk', size: 34 }],
};

// What the app's own source still says about a record, keyed the way the route is asked for it.
// This is the answer `unbind` reads from `_resource_usage` and `detach_file` from `_data_usage`,
// BOTH taken before the record goes — a Data Source's queries are found THROUGH the record.
const USES = {
  'data_source:ds_1': ['src/queries.py', 'public/panel.js'],
  'llm_alias:al_1': [],
  'model_api:ma_1': [],
  'public/data/desks/margins.csv': ['src/load.py'],
  'public/data/rehydrated/legacy.csv': [],
};

// The raw copies the agent leaked into the app tree, which `detach_file` deletes on the way out —
// as distinct from the inlined-into-code uses above, which it leaves in place and reports.
const LEAKED = { 'public/data/desks/margins.csv': ['src/data/margins.csv'] };

// One Conversation's chips, in the id space the server actually answers in (#99). `resourceId` is
// the prefixed Project Resource id — `data_source:ds_1`, not the bare `ds_1` a Binding carries —
// which is the whole reason a Binding has to be joined on `${kind}:${id}` rather than on `id`.
//
// `ctx_source` names the Data Source `app_a` is bound to and `ctx_dataset` names nothing any app
// records, so dropping one chip and dropping the other are the two different sentences.
const CONTEXT = {
  thr_many: [
    { id: 'ctx_source', kind: 'data_source', name: 'Market data EOD',
      resourceId: 'data_source:ds_1', bindingKey: ['data_source', 'ds_1'], addedBy: 'user' },
    { id: 'ctx_dataset', kind: 'dataset', name: 'Desk margins',
      resourceId: 'dataset:desks', addedBy: 'user' },
  ],
};

// What the Project holds, in the same id space, so the panel's Project rows and the app's Bindings
// are joinable at all. `data_source:ds_1` and `llm_alias:al_1` are both `app_a`'s; `data_source:ds_9`
// is in the Project and bound by nobody, which is what stops "Required by" reading as decoration.
const RESOURCE_GROUPS = {
  dataset: [], table: [], datasource: [
    { id: 'data_source:ds_1', name: 'Market data EOD', kind: 'datasource' },
    { id: 'data_source:ds_9', name: 'Risk warehouse', kind: 'datasource' },
  ],
  model_llm: [{ id: 'llm_alias:al_1', name: 'Claude Sonnet 4', kind: 'model_llm' }],
  model_predictive: [], tool: [], agent: [], skill: [], mcp: [], file: [], pin: [],
};

const calls = [];
let selected = 'app_a';
// A 500 on the app list, which is not the same answer as a Project with no apps (#95).
let appsFail = false;
// Emptied by the `noapps` step: a brand-new Project, which is the one state the empty state is
// written for and the one state a picker cannot show it in.
let apps = APPS;
// Chips come off the server and go back to it, so dropping one is a DELETE the next read sees.
let context = {};
// The app's two manifests, which removal WRITES. Copies rather than the fixtures themselves, so a
// step that unbinds does not leave the next step's app short a Binding.
let bound = {};
let attached = {};

const json = (body, status = 200) => ({
  ok: status < 400, status,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

// Requests parked instead of answered, so a step can resolve two app-scoped writers in the order it
// names rather than the order they were asked (#101). Held by exact path; the body is still built
// WHEN THE REQUEST ARRIVES, which is the whole point — a read taken before an app switch has to go
// on answering what was true when it was taken, however long it is held for.
let holding = null;
const held = [];
// A task turn, which drains the whole microtask queue behind it — so every promise the store can
// settle without the server has settled, and it is parked on a held request, before a step acts.
const settle = () => new Promise((res) => setTimeout(res, 0));

function serve(url, init) {
  const path = String(url).replace(/^\.\/api/, '');
  const key = `${(init && init.method) || 'GET'} ${path}`;
  calls.push(key);
  const body = route(path, init);
  if (holding && holding.has(path)) {
    return new Promise((release) => { held.push({ key, release: () => release(body) }); });
  }
  return body;
}

function route(path, init) {
  let m;
  if ((m = path.match(/^\/apps\/([^/?]+)\/select$/))) {
    selected = m[1];
    return json({});
  }
  if (path === '/apps' && init && init.method === 'POST') {
    // Minted, not in the fixture: the route under test is built from the id the SERVER answers
    // with, so a fixture app would let the assertion pass on the wrong value.
    return json({ id: 'app_new', name: 'Untitled app' });
  }
  if (path === '/apps') {
    if (appsFail) return json({ error: 'unavailable' }, 500);
    return json({ items: apps.map((a) => ({ ...a, selected: a.id === selected })), selected });
  }
  // Both are app-scoped and both are read off disk, so the answer follows `selected` rather than
  // being a fixture the whole run shares.
  if (path === '/bindings') return json({ bindings: bound[selected] || [] });
  if (path === '/project') return json({ attached: attached[selected] || [] });
  // The two removal routes, answering what the real ones answer. Both report the app source that
  // still uses what just went, and both report it AFTER the act — there is no route here that a
  // pre-warning could have asked, which is the point (ADR-0010).
  if ((m = path.match(/^\/bindings\/([^/]+)\/([^/?]+)$/)) && init && init.method === 'DELETE') {
    const kind = decodeURIComponent(m[1]);
    const id = decodeURIComponent(m[2]);
    const gone = (bound[selected] || []).find((b) => b.kind === kind && b.id === id) || null;
    bound[selected] = (bound[selected] || []).filter((b) => b !== gone);
    return json({
      bindings: bound[selected],
      refs: USES[`${kind}:${id}`] || [],
      kind,
      name: gone ? gone.display_name || gone.name : id,
    });
  }
  if (path === '/project/files/detach' && init && init.method === 'POST') {
    const p = JSON.parse(init.body || '{}').path;
    attached[selected] = (attached[selected] || []).filter((a) => a.path !== p);
    return json({ detached: p, removed_copies: LEAKED[p] || [], refs: USES[p] || [], status: 'ok' });
  }
  if (path.match(/^\/threads\/([^/]+)\/conversation$/)) return json({ history: [] });
  if ((m = path.match(/^\/threads\/([^/]+)\/context$/))) return json({ items: context[m[1]] || [] });
  if ((m = path.match(/^\/threads\/([^/]+)\/context\/([^/]+)$/))) {
    context[m[1]] = (context[m[1]] || []).filter((i) => i.id !== decodeURIComponent(m[2]));
    return json({});
  }
  if ((m = path.match(/^\/threads\/([^/?]+)$/))) {
    return json(THREADS[m[1]] || { id: m[1], history: [], touched: [] });
  }
  // A bare list, the way the control API answers it.
  if (path === '/threads') return json(Object.values(THREADS));
  return json({});
}

// --- the browser -----------------------------------------------------------
const timers = [];
// Long `setTimeout`s are recorded and NOT scheduled, so a test can fire the 90-second give-up
// without waiting 90 seconds — and without leaving a real timer pending, which would hold node
// open long after the assertions were done. Short ones still run, because promise scheduling
// elsewhere in this file leans on them.
const timeouts = [];
const backing = new Map();
const effects = [];
const modals = [];
// Every toast, in order. A message is the only place some of these decisions land.
const said = [];

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, Infinity, clearTimeout,
  setTimeout: (fn, ms) => {
    if (ms >= 5000) { timeouts.push({ ms, fn }); return -timeouts.length; }
    return setTimeout(fn, ms);
  },
  // Recorded, not run. The claim is that Build schedules a repeat read of the app list; actually
  // firing it would only prove `setInterval` works.
  setInterval: (fn, ms) => { timers.push({ ms, fn }); return timers.length; },
  clearInterval: () => {},
  requestAnimationFrame: (fn) => fn(),
  localStorage: {
    getItem: (k) => (backing.has(k) ? backing.get(k) : null),
    setItem: (k, v) => backing.set(k, String(v)),
    removeItem: (k) => backing.delete(k),
  },
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  location: { hash: '' },
  history: { replaceState() {} },
  addEventListener() {},
  removeEventListener() {},
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
    useEffect: (fn, deps) => { effects.push({ fn, deps }); },
    useRef: () => ({ current: null }),
    Fragment: 'Fragment',
  },
  antd: {
    Input: Object.assign(function Input() {}, { TextArea: 'Input.TextArea' }),
    Button: 'Button', Dropdown: 'Dropdown', Tag: 'Tag', Tooltip: 'Tooltip', Space: 'Space',
    Checkbox: 'Checkbox', Modal: { confirm: (cfg) => { modals.push(cfg); } },
    // Recorded, because one of #99's two claims is a SENTENCE: dropping a chip for a Resource the
    // selected app is bound to has to say the app still needs it, and nothing on screen says that.
    message: {
      success: (t) => said.push(String(t)), error: (t) => said.push(String(t)),
      info: (t) => said.push(String(t)), warning: (t) => said.push(String(t)),
    },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async (url, init) => serve(url, init),
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

// `resource-panel.js` joins the list: since #99 the panel's "Required by {app}" subtitle reads the
// same `bindings` the header does, so the two surfaces are one claim and belong in one harness.
for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js', 'router.js',
                 'components/conversation-list.js', 'components/resource-panel.js',
                 'modes/builder.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

// The components Build mounts that this file is not about. Stubbed rather than left undefined,
// because an undefined element type is indistinguishable from a missing one when all you have is
// the tree — and one of them, the composer, is asserted on by its props.
SW.Composer = function Composer() { return null; };
SW.Message = function Message() { return null; };
SW.TypingIndicator = function TypingIndicator() { return null; };
SW.PlanSheet = function PlanSheet() { return null; };

// --- walking the tree ------------------------------------------------------
// Function components are called rather than stepped over, which is what makes a header assembled
// out of three private components still one thing to assert on. `dropdownRender` is called too:
// the app list lives behind a click, and "behind a click" is not the same as "not on the page".
const SKIP = new Set(['Message', 'TypingIndicator', 'Composer', 'PlanSheet', 'ConversationRail',
                      'Input', 'anonymous']);
const named = new Map();
for (const [k, v] of Object.entries(SW)) if (typeof v === 'function') named.set(v, k);

function tag(node) {
  if (typeof node.t === 'string') return node.t;
  const own = named.get(node.t);
  return own || node.t.name || 'anonymous';
}

// Every element the tree holds, flattened, each with the strings directly under it. Assertions ask
// about controls, and a control is an element plus its label.
function flatten(node, out = [], depth = 0) {
  if (node === null || node === undefined || node === false || node === true || depth > 60) return out;
  if (Array.isArray(node)) { node.forEach((n) => flatten(n, out, depth)); return out; }
  if (typeof node === 'string' || typeof node === 'number') { out.push({ text: String(node) }); return out; }
  if (typeof node !== 'object' || !node.t) return out;

  const name = tag(node);
  const props = node.p || {};
  const entry = {
    el: name,
    className: props.className || '',
    label: props['aria-label'] || '',
    title: typeof props.title === 'string' ? props.title : '',
    placeholder: props.placeholder || '',
    danger: !!props.danger,
    type: typeof props.type === 'string' ? props.type : '',
  };
  // Menus are data, not children — the `…` overflow's items never appear in the tree otherwise,
  // and the whole criterion is about their order and their styling.
  if (props.menu && props.menu.items) {
    entry.items = props.menu.items.map((i) => ({
      key: i.key || '', label: typeof i.label === 'string' ? i.label : '', danger: !!i.danger,
      divider: i.type === 'divider',
    }));
    // Kept beside the labels so a step can CLICK an item rather than only read it: "reachable from
    // this section" is a claim about what the item does, and a label proves half of it.
    entry.onMenu = props.menu.onClick;
  }
  // Same reason, for the controls that are buttons rather than menu items — the notice's cleanup
  // offer and its Dismiss. Dropped by `JSON.stringify` on the way into the report.
  if (typeof props.onClick === 'function') entry.onClick = props.onClick;
  // The id a row carries, which is what "Add to this conversation" POSTs. The app's rows build
  // theirs rather than being handed one, so whether it is an id the Project answers in is a
  // question that has to be asked of the value itself (#96).
  if (props.resource && props.resource.id) entry.resourceId = props.resource.id;
  if (props.mode) entry.mode = props.mode;
  // The strings directly under this element, so an assertion can ask WHICH CONTROL said a word
  // rather than only whether the screen holds it somewhere. Two things say "Starting preview\u2026"
  // once the header reports the preview \u2014 the canvas overlay and the header \u2014 and #87's
  // criterion is about the second one.
  const direct = (Array.isArray(node.c) ? node.c : [node.c]).filter(
    (child) => typeof child === 'string' || typeof child === 'number'
  );
  if (direct.length) entry.texts = direct.map(String);
  out.push(entry);

  if (typeof node.t === 'function' && !SKIP.has(name)) {
    flatten(node.t(Object.assign({}, props, { children: node.c })), out, depth + 1);
  }
  if (typeof props.dropdownRender === 'function') flatten(props.dropdownRender(), out, depth + 1);
  flatten(props.title, out, depth + 1);
  flatten(node.c, out, depth + 1);
  return out;
}

// The strings a person would read, in order.
const words = (nodes) => nodes.filter((n) => n.text).map((n) => n.text);

// Every handler a click could reach, by the app it acts on. `data-app` is what makes a row in the
// header's list findable without mounting it.
function rowsOf(node, rows = [], depth = 0) {
  if (!node || depth > 60) return rows;
  if (Array.isArray(node)) { node.forEach((n) => rowsOf(n, rows, depth)); return rows; }
  if (typeof node !== 'object' || !node.t) return rows;
  const props = node.p || {};
  if (props.onClick && props['data-app']) rows.push({ id: props['data-app'], onClick: props.onClick });
  if (typeof node.t === 'function' && !SKIP.has(tag(node))) {
    rowsOf(node.t(Object.assign({}, props, { children: node.c })), rows, depth + 1);
  }
  if (typeof props.dropdownRender === 'function') rowsOf(props.dropdownRender(), rows, depth + 1);
  rowsOf(node.c, rows, depth + 1);
  return rows;
}

// The panel's rows, each under the section head it was drawn beneath and with the words it drew.
// Grouped rather than flattened, because every #99 claim is about WHICH row said "Required by" —
// a flat list of every string on the panel cannot tell a subtitle from a section head.
//
// Text is collected only while the walk is still inside a `sw-res-*` element, so a group label or a
// section count between two rows cannot be read as the previous row's subtitle.
function panelContents(tree) {
  const out = [];
  let section = null;
  let row = null;
  // The `ResourceRow` element is walked just before the `sw-res-row` div it renders, so the id it
  // was handed is read off the element and carried onto the row below it.
  let pendingId = null;
  for (const n of flatten(tree)) {
    const cls = String(n.className || '');
    if (n.resourceId) { pendingId = n.resourceId; continue; }
    if (cls.startsWith('sw-res-row')) {
      row = { section, className: cls, texts: [], id: pendingId };
      pendingId = null;
      out.push(row);
      continue;
    }
    // The overflow menu hangs inside the row it acts on and carries no class of its own, so it is
    // recognised by having items at all. Which row holds which removal is the whole question.
    if (row && n.items) { row.items = n.items; row.onMenu = n.onMenu; }
    if (cls === 'sw-panel-section-title') {
      section = (n.texts || []).join('');
      row = null;
      continue;
    }
    if (cls && !cls.startsWith('sw-res-')) { row = null; continue; }
    if (row && n.text) row.texts.push(n.text);
  }
  return out;
}

async function arrive(threadId, appId) {
  apps = APPS;
  context = JSON.parse(JSON.stringify(CONTEXT));
  bound = JSON.parse(JSON.stringify(BINDINGS));
  attached = JSON.parse(JSON.stringify(ATTACHED));
  modals.length = 0;
  said.length = 0;
  effects.length = 0;
  timers.length = 0;
  timeouts.length = 0;
  calls.length = 0;
  holding = null;
  held.length = 0;
  await SW.store.openThread(threadId);
  if (appId) await SW.store.selectApp(appId);
  await SW.store.loadApps();
  await SW.store.loadBuild();
  sandbox.location.hash = `#/build/${threadId}?app=${selected}`;
  SW.router.go(`#/build/${threadId}?app=${selected}`);
}

// --- the run ---------------------------------------------------------------
const report = [];
for (const step of steps) {
  if (step.build) {
    await arrive(step.build, step.select);
    if (step.noapps) {
      apps = [];
      await SW.store.loadApps();
    }
    // Apps in the Project, none of them named — first paint before the reads land, and wherever
    // the store drops the selection. The header has to hold that state without claiming things.
    if (step.unselected) SW.store.clearApp();
    if (step.preview) SW.store.set({ previewStatus: step.preview });
    // Emptied here rather than in `arrive`, so what survives is the traffic the RENDER itself
    // caused. A row that reads a written record answers out of the store; one that fetches shows
    // up as a call between these two lines (#92).
    calls.length = 0;
    let tree = SW.BuildMode({ conversationId: step.build, appId: selected });
    let nodes = flatten(tree);
    const renderCalls = calls.slice();
    // The effects Build schedules, run so the timer it wants is a fact rather than a reading of
    // the source. `loadApps` is counted rather than awaited: what matters is that Build asks.
    let loadAppCalls = 0;
    const realLoad = SW.store.loadApps;
    SW.store.loadApps = () => { loadAppCalls += 1; return realLoad(); };
    // `giveUp` fires the 90-second timeout Build arms while the preview is starting, then paints
    // again — the whole question in #90 is what the screen says AFTER Build has stopped checking,
    // and a tree rendered before that has not been asked it. The effects' cleanups are skipped for
    // this step because one of them is the `clearTimeout` that would take the timer away first.
    effects.forEach((e) => {
      try {
        const off = e.fn();
        if (typeof off === 'function' && !step.giveUp) off();
      } catch (err) { /* the store's own fetches, which this step is not about */ }
    });
    SW.store.loadApps = realLoad;

    if (step.giveUp) {
      const waited = timeouts.find((t) => t.ms >= 90000);
      if (!waited) throw new Error('Build armed no give-up timer while the preview was starting');
      waited.fn();
      tree = SW.BuildMode({ conversationId: step.build, appId: selected });
      nodes = flatten(tree);
    }

    const rail = nodes.find((n) => n.el === 'ConversationRail');
    const composer = nodes.find((n) => n.el === 'Composer');
    report.push({
      step: `build ${step.build}`,
      app: (SW.store.get().activeApp || {}).id || null,
      renderCalls,
      railMode: rail ? rail.mode || null : null,
      appRails: nodes.filter((n) => n.el === 'AppRail').length,
      composerPlaceholder: composer ? composer.placeholder : null,
      words: words(nodes),
      parts: nodes.filter((n) => n.className && n.texts).map((n) => ({ className: n.className, texts: n.texts })),
      classes: nodes.map((n) => n.className).filter(Boolean),
      menus: nodes.filter((n) => n.items).map((n) => ({ label: n.label, title: n.title, items: n.items })),
      labels: nodes.filter((n) => n.label).map((n) => n.label),
      titles: nodes.filter((n) => n.title).map((n) => n.title),
      buttons: nodes.filter((n) => n.el === 'Button').map((n) => n.type),
      placeholders: nodes.filter((n) => n.placeholder).map((n) => n.placeholder),
      timers: timers.map((t) => t.ms),
      waits: timeouts.map((t) => t.ms),
      previewStatus: SW.store.get().previewStatus,
      loadAppCalls,
    });
    continue;
  }
  // The resource panel, drawn over the app the step selected. The Project's own rows are seeded
  // here rather than served, because `/project/resources` is not what this harness is about and the
  // only thing the panel needs from it is ids in the space Bindings can be joined on (#99).
  if (step.panel) {
    await arrive(step.panel, step.select);
    await SW.store.reloadAttachments();
    SW.store.set({ resourceGroups: RESOURCE_GROUPS, resourcesLoading: false });
    // Emptied here rather than in `arrive`, for the reason the build step gives: what survives is
    // the traffic the RENDER caused, and the section reports two written records (ADR-0010).
    calls.length = 0;
    const tree = SW.ResourcePanel();
    const rows = panelContents(tree);
    const nodes = flatten(tree);
    report.push({
      step: `panel ${step.select || selected}`,
      app: (SW.store.get().activeApp || {}).name || null,
      rows,
      renderCalls: calls.slice(),
      // Section heads in the order they are drawn, taken off the heads themselves rather than off
      // the rows under them — a section whose list is empty still has a head, and that is the one
      // the question is about.
      sections: nodes
        .filter((n) => n.className === 'sw-panel-section-title')
        .map((n) => (n.texts || []).join('')),
      parts: nodes.filter((n) => n.className && n.texts).map((n) => ({ className: n.className, texts: n.texts })),
      words: words(nodes),
    });
    continue;
  }

  // A removal driven the way a person drives it: find the row in the app's section, open its menu,
  // and click the item whose label names a scope other than the Conversation. The step knows no
  // menu keys — an item that stopped naming its scope would not be found at all, which is the
  // glossary's Remove rule asked as a question rather than asserted as a string.
  if (step.removeFrom) {
    await arrive(step.thread, step.select);
    await SW.store.reloadAttachments();
    SW.store.set({ resourceGroups: RESOURCE_GROUPS, resourcesLoading: false });
    said.length = 0;
    modals.length = 0;
    calls.length = 0;
    const rows = panelContents(SW.ResourcePanel());
    const inSection = rows.filter(
      (r) => r.section && r.section !== 'Project resources' && r.section !== 'In context'
    );
    const row = inSection.find((r) => r.texts.includes(step.removeFrom));
    if (!row) {
      throw new Error(
        `no row ${step.removeFrom} in the app's section — found ${JSON.stringify(inSection.map((r) => r.texts))}`
      );
    }
    const item = (row.items || []).find(
      (i) => i.label.startsWith('Remove from ') && i.label !== 'Remove from this conversation'
    );
    const chipsBefore = SW.store.get().attachments.map((a) => a.id);
    const acted = item ? row.onMenu({ key: item.key }) : null;
    // Whatever the click raised, answered before the tree is read again. A removal that confirms has
    // done nothing yet and is waiting on this; one that does not has already gone to the server.
    // The click's own promise is awaited AFTER the answer, or a confirming removal would deadlock
    // on the modal nobody had replied to.
    const confirm = modals.length ? modals[modals.length - 1] : null;
    // Move the selection while the modal sits open, which is the whole of the hazard: the removal
    // routes carry no app id, so the act would land on whichever app the server now has.
    if (confirm && step.switchTo) await SW.store.selectApp(step.switchTo);
    if (confirm && step.confirm) await confirm.onOk();
    if (confirm && !step.confirm) confirm.onCancel();
    await acted;
    const actCalls = calls.slice();

    let cleanupCalls = null;
    let seeded = null;
    let after = flatten(SW.ResourcePanel());
    const control = (re) => after.find((n) => n.onClick && (n.texts || []).some((t) => re.test(t)));
    if (step.cleanup) {
      const offer = control(/clean/i);
      if (!offer) throw new Error('the notice offered no cleanup');
      calls.length = 0;
      offer.onClick();
      cleanupCalls = calls.slice();
      seeded = SW.store.get().composerSeed || null;
      after = flatten(SW.ResourcePanel());
    }
    if (step.dismiss) {
      const off = control(/^Dismiss$/);
      if (!off) throw new Error('the notice could not be dismissed');
      off.onClick();
      after = flatten(SW.ResourcePanel());
    }

    report.push({
      step: `removeFrom ${step.removeFrom}`,
      item: item ? { key: item.key, label: item.label, danger: item.danger } : null,
      confirm: confirm
        ? {
            title: String(confirm.title || ''),
            content: String(confirm.content || ''),
            okText: String(confirm.okText || ''),
            danger: !!(confirm.okButtonProps || {}).danger,
          }
        : null,
      calls: actCalls,
      said: said.slice(),
      cleanupCalls,
      seeded,
      // Both lists after the act, plus everything the section said. The chips are the assertion
      // that the two scopes move on their own.
      rows: panelContents(SW.ResourcePanel()).map((r) => ({ section: r.section, texts: r.texts })),
      parts: after.filter((n) => n.className && n.texts).map((n) => ({ className: n.className, texts: n.texts })),
      words: words(after),
      chipsBefore,
      chips: SW.store.get().attachments.map((a) => a.id),
      bindings: (SW.store.get().bindings || []).map((b) => b.display_name || b.name),
      attachments: (SW.store.get().appAttachments || []).map((a) => a.file),
    });
    continue;
  }

  // A chip leaving the Conversation. The claim is the sentence it draws: the Resource is out of
  // context, and whether the selected app is still bound to it changes what the second half says.
  if (step.dropChip) {
    await arrive(step.thread, step.select);
    await SW.store.reloadAttachments();
    said.length = 0;
    const chip = SW.store.get().attachments.find((a) => a.id === step.dropChip);
    if (!chip) throw new Error(`no chip ${step.dropChip} in this conversation`);
    await SW.store.removeFromConversation(chip);
    report.push({
      step: `dropChip ${step.dropChip}`,
      said: said.slice(),
      left: SW.store.get().attachments.map((a) => a.id),
    });
    continue;
  }

  if (step.rail) {
    // The rail itself, in each mode, so "the same rows in Build as in Chat" is a comparison rather
    // than a promise.
    await SW.store.reloadThreads();
    const nodes = flatten(SW.ConversationRail({ mode: step.rail }));
    report.push({ step: `rail ${step.rail}`, words: words(nodes), labels: nodes.filter((n) => n.label).map((n) => n.label) });
    continue;
  }
  if (step.pick) {
    // What a click on a row in the header's list writes. The criterion is that it writes the
    // ROUTE and nothing else: `activeApp` is the store's answer to the route, never the picker's.
    await arrive(step.thread, step.select);
    const before = (SW.store.get().activeApp || {}).id || null;
    sandbox.location.hash = '';
    const rows = rowsOf(SW.BuildMode({ conversationId: step.thread, appId: before }));
    const row = rows.find((r) => r.id === step.pick);
    if (row) row.onClick({ stopPropagation() {} });
    report.push({
      step: `pick ${step.pick}`,
      rows: rows.map((r) => r.id),
      hash: sandbox.location.hash,
      // Unchanged is the assertion. The route says which app; the store follows it on the next
      // render, not on the click.
      appAfterClick: (SW.store.get().activeApp || {}).id || null,
      appBefore: before,
    });
    continue;
  }
  // One tick of the 30s poll Build arms (#95). The server's selection is moved WITHOUT a request,
  // because that is what another tab selecting a different app looks like from here: `/apps`
  // simply starts answering differently. `step.poll` names the app the server now reports, so
  // passing the one already selected is the tick that changes nothing.
  if (step.poll) {
    await arrive(step.thread, step.select);
    calls.length = 0;
    selected = step.poll;
    appsFail = !!step.readFails;
    await SW.store.loadApps();
    appsFail = false;
    const s = SW.store.get();
    // Taken before the render, so the row's own reads cannot land in the tick's ledger (#92).
    const ticked = calls.slice();
    const nodes = flatten(SW.BuildMode({ conversationId: step.thread, appId: selected }));
    report.push({
      step: `poll ${step.select} -> ${step.poll}`,
      calls: ticked,
      activeApp: (s.activeApp || {}).id || null,
      activeName: (s.activeApp || {}).name || null,
      bindings: (s.bindings || []).map((b) => b.display_name || b.name),
      attachments: (s.appAttachments || []).map((a) => a.file),
      parts: nodes
        .filter((n) => n.className && n.texts)
        .map((n) => ({ className: n.className, texts: n.texts })),
    });
    continue;
  }

  // The other way the selected app moves: the header's app control, which reloads the whole of
  // Build. The cascade has to stay OFF down that path — `loadBuild` refreshes what hangs off the
  // app itself — so this step counts reads rather than looking at the screen (#95).
  if (step.switchTo) {
    await arrive(step.thread, step.select);
    calls.length = 0;
    await SW.store.selectApp(step.switchTo);
    const s = SW.store.get();
    report.push({
      step: `switch ${step.select} -> ${step.switchTo}`,
      calls: calls.slice(),
      // The lists too, because the sequencing has to cost the single-writer case nothing — not
      // one extra read, and not one write dropped for having been ticketed (#101).
      activeApp: (s.activeApp || {}).id || null,
      bindings: (s.bindings || []).map((b) => b.display_name || b.name),
      attachments: (s.appAttachments || []).map((a) => a.file),
    });
    continue;
  }

  // Two app-scoped writers in flight at once, resolved in the order this step names rather than the
  // order they were asked (#101). Each race is spelled out rather than driven by a mini-language:
  // the interleaving IS the claim, and a step that hid it behind a list of holds and releases would
  // assert on a shape nobody could read.
  if (step.race) {
    await arrive(step.thread, step.select);
    // What the app's own section said before the rest of the step happened, for the races whose
    // claim is about the notice: it has to be drawn first for its going or staying to mean
    // anything.
    let noticeBefore = null;
    const noticeNow = () => {
      const said = SW.store.get().appRemoval;
      return said ? said.text : null;
    };

    // A removal driven all the way through. This is the SETUP three of these races share, not the
    // claim any of them makes — the interleave below each one is the claim, and pulling the setup
    // out is what leaves it visible.
    const removeBinding = async (display) => {
      const gone = SW.store.get().bindings.find((b) => (b.display_name || b.name) === display);
      if (!gone) throw new Error(`no Binding ${display} on this app`);
      const acted = SW.store.removeBindingFromApp(gone);
      await settle();
      await modals[modals.length - 1].onOk();
      await acted;
      return gone;
    };

    // A second tab binding something, which from here is `/bindings` starting to answer
    // differently — the same way `/apps` does when a second tab moves the selection.
    const boundElsewhere = () => {
      bound[selected] = [
        ...(bound[selected] || []),
        { kind: 'llm_alias', id: 'al_9', name: 'gpt-oss-120b', display_name: 'GPT OSS 120B' },
      ];
    };

    if (step.race === 'remove-then-switch') {
      // Nothing overlaps here. The removal finishes, and only then does the app change — by hand,
      // down the path `selectApp` takes, which is the one path the notice was never cleared on.
      await removeBinding(step.remove);
      noticeBefore = noticeNow();
      calls.length = 0;
      await SW.store.selectApp(step.raceTo);
    }

    if (step.race === 'read-then-tick') {
      // NOTHING competes for the Bindings here: same app, no act, no switch. The 2s build tick
      // calls `loadAppList`, which writes `activeApp` and nothing else — and a read of this same
      // app's `/bindings` is still out behind it. One shared high-water mark for all four fields
      // would let that tick supersede the read and throw a good answer away, with nothing
      // re-reading until the build ends.
      await removeBinding(step.remove);
      boundElsewhere();
      holding = new Set(['/bindings']);
      calls.length = 0;
      const read = SW.store.loadBuild();
      await settle();
      noticeBefore = noticeNow();
      await SW.store.loadApps();
      held.shift().release();
      await read;
    }

    if (step.race === 'dismiss-mid-read') {
      // A notice on screen, a read of the app's Bindings in flight behind it, and the person
      // clicking Dismiss while it is still out. The click is about the notice and nothing else,
      // so it must not take the read down with it.
      await removeBinding(step.remove);
      boundElsewhere();
      holding = new Set(['/bindings']);
      calls.length = 0;
      const read = SW.store.loadBuild();
      await settle();
      noticeBefore = noticeNow();
      SW.store.dismissAppRemoval();
      held.shift().release();
      await read;
    }

    if (step.race === 'read-then-switch') {
      // A build read that starts under the app you are about to leave, and lands after a poll has
      // moved the selection. Its answer is older than the poll's however late it arrives.
      holding = new Set(['/project', '/bindings']);
      calls.length = 0;
      const read = SW.store.loadBuild();
      await settle();
      // `loadBuild`'s own `/project`, let through so the read gets as far as asking for
      // `/bindings` while the app it is describing is still the selected one.
      held.shift().release();
      await settle();
      // The selection moves the way a second tab moves it: `/apps` simply starts answering
      // differently, and the poll cascades onto the new app.
      selected = step.raceTo;
      const poll = SW.store.loadApps();
      await settle();
      const stale = held.shift();
      // The NEWER writer answers first and the older one last, which is the interleave: the one
      // that used to win was whichever resolved last, and that is this one.
      held.splice(0).forEach((h) => h.release());
      await poll;
      stale.release();
      await read;
    }

    if (step.race === 'read-then-act') {
      // A read of the app's Bindings, started before a removal and landing after it. The route
      // the removal called has already written the manifest; the read answers what was true
      // before it, so installing it would put the Binding back on screen.
      holding = new Set(['/bindings']);
      calls.length = 0;
      const read = SW.store.loadBuild();
      await settle();
      await removeBinding(step.remove);
      held.shift().release();
      await read;
    }

    if (step.race === 'act-then-switch') {
      // The other side of the same rule. An act claims its place at the head of the queue, which
      // is what keeps it ahead of a read — but the app it acted on can be gone by the time the
      // route answers, and then its list belongs to nobody on screen.
      const gone = SW.store.get().bindings.find((b) => (b.display_name || b.name) === step.remove);
      if (!gone) throw new Error(`no Binding ${step.remove} on this app`);
      const acted = SW.store.removeBindingFromApp(gone);
      await settle();
      const confirm = modals[modals.length - 1];
      holding = new Set([`/bindings/${gone.kind}/${gone.id}`]);
      calls.length = 0;
      const ok = confirm.onOk();
      await settle();
      selected = step.raceTo;
      await SW.store.loadApps();
      held.shift().release();
      await ok;
      await acted;
    }

    holding = null;
    held.length = 0;
    const s = SW.store.get();
    const nodes = flatten(
      SW.BuildMode({ conversationId: step.thread, appId: (s.activeApp || {}).id })
    );
    report.push({
      step: `race ${step.race}`,
      calls: calls.slice(),
      activeApp: (s.activeApp || {}).id || null,
      activeName: (s.activeApp || {}).name || null,
      bindings: (s.bindings || []).map((b) => b.display_name || b.name),
      attachments: (s.appAttachments || []).map((a) => a.file),
      // The notice is app-scoped too, and it is the one field whose sentence names its own app
      // out loud — so a stale one is readable as a wrong pairing rather than inferred from a list.
      notice: s.appRemoval ? s.appRemoval.text : null,
      noticeBefore,
      parts: nodes
        .filter((n) => n.className && n.texts)
        .map((n) => ({ className: n.className, texts: n.texts })),
    });
    continue;
  }

  if (step.newapp) {
    // Where New app leaves you. Asserted because the route is built by interpolation and a template
    // that silently loses its expression still renders a valid-looking URL — `#/build?app=` points
    // Build at no app at all, and nothing else in the suite reads this line.
    const went = [];
    const realGo = SW.router.go;
    SW.router.go = (h) => { went.push(h); };
    await SW.store.createApp();
    SW.router.go = realGo;
    report.push({ step: 'newapp', went });
    continue;
  }

  if (step.route) {
    // `SW.appRoute` after the move: same grammar, and still there for the callers outside the rail.
    const app = APPS.find((a) => a.id === step.route);
    if (step.thread) await SW.store.openThread(step.thread);
    else SW.store.clearConversation();
    report.push({ step: `route ${step.route}`, path: SW.appRoute(app) });
    continue;
  }
  throw new Error(`unknown step ${JSON.stringify(step)}`);
}
console.log(JSON.stringify(report));
