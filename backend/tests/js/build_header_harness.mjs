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
const BINDINGS = {
  app_a: [
    { kind: 'llm_alias', id: 'al_1', name: 'claude-sonnet-4', display_name: 'Claude Sonnet 4' },
    { kind: 'data_source', id: 'ds_1', name: 'market-data-eod', display_name: 'Market data EOD' },
  ],
  app_c: [{ kind: 'llm_alias', id: 'al_2', name: 'qwen-2-5', display_name: 'Qwen 2.5' }],
};
// `app_d` carries files and no Bindings, `app_c` the reverse: a kind with nothing in it is not the
// same state as an app with nothing at all, and only the second one gets the empty state.
const ATTACHED = {
  app_a: [{ path: 'public/data/desks/margins.csv', file: 'margins.csv', dataset: 'desks', size: 12 }],
  app_d: [{ path: 'public/data/risk/limits.csv', file: 'limits.csv', dataset: 'risk', size: 34 }],
};

const calls = [];
let selected = 'app_a';
// Emptied by the `noapps` step: a brand-new Project, which is the one state the empty state is
// written for and the one state a picker cannot show it in.
let apps = APPS;

const json = (body, status = 200) => ({
  ok: status < 400, status,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

function serve(url, init) {
  const path = String(url).replace(/^\.\/api/, '');
  calls.push(`${(init && init.method) || 'GET'} ${path}`);
  let m;
  if ((m = path.match(/^\/apps\/([^/?]+)\/select$/))) {
    selected = m[1];
    return json({});
  }
  if (path === '/apps') {
    return json({ items: apps.map((a) => ({ ...a, selected: a.id === selected })), selected });
  }
  // Both are app-scoped and both are read off disk, so the answer follows `selected` rather than
  // being a fixture the whole run shares.
  if (path === '/bindings') return json({ bindings: BINDINGS[selected] || [] });
  if (path === '/project') return json({ attached: ATTACHED[selected] || [] });
  if (path.match(/^\/threads\/([^/]+)\/conversation$/)) return json({ history: [] });
  if (path.match(/^\/threads\/([^/]+)\/context$/)) return json({ items: [] });
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
    message: { success() {}, error() {}, info() {}, warning() {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async (url, init) => serve(url, init),
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js', 'router.js',
                 'components/conversation-list.js', 'modes/builder.js']) {
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
  }
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

async function arrive(threadId, appId) {
  apps = APPS;
  effects.length = 0;
  timers.length = 0;
  timeouts.length = 0;
  calls.length = 0;
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
