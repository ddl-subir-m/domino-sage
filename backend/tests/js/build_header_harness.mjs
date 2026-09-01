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
// `url` is what `Open app` opens, and the server builds it from the id each app recorded — so a
// published app has one and an unpublished app has "", which is the same answer `published` gives.
// Held in the fixture rather than derived here, because a harness that computed it would let the
// row ship no URL at all and still pass.
const APPS = [
  { id: 'app_a', name: 'Desk dashboard', built: true, building: false, behind: false,
    published: true, url: '/modelproducts/da_a?scope=project' },
  { id: 'app_b', name: 'P&L report', built: false, building: false, behind: false,
    published: false, url: '' },
  { id: 'app_c', name: 'Rate curve viewer', built: true, building: true, behind: false,
    published: false, url: '' },
  { id: 'app_d', name: 'Risk monitor', built: true, building: false, behind: true,
    published: true, url: '/modelproducts/da_d?scope=project' },
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
    { id: 'data_source:ds_1', name: 'Market data EOD', kind: 'datasource',
      bindingKey: ['data_source', 'ds_1'] },
    { id: 'data_source:ds_9', name: 'Risk warehouse', kind: 'datasource',
      bindingKey: ['data_source', 'ds_9'] },
  ],
  // `bindingKey` is what `SW.api.resources()` puts on every bindable row, and it is the only place
  // the BARE id survives the prefixing — so a fixture without it could not ask the id-space question
  // at all. The Data Source rows above have theirs since their own door was hung (#129): it is what
  // switches their subtitle to the selected app, and a fixture without it would have gone on
  // reporting the Project-wide answer the server stopped sending.
  model_llm: [{ id: 'llm_alias:al_1', name: 'Claude Sonnet 4', kind: 'model_llm',
    bindingKey: ['llm_alias', 'al_1'] }],
  model_predictive: [], tool: [], agent: [], skill: [], mcp: [], file: [], pin: [],
};

// Each app's own build log, as `/api/project/history` hands it back with no conversation named:
// raw rows off the app's directory, in the order they were appended (#88).
//
// Four things this fixture has to be able to show, and a simpler one could not:
//   - `app_a`'s two runs were asked for in DIFFERENT conversations, so a list that filtered by
//     conversation would be short one build — the failure #72 makes possible.
//   - the first run carries NO `at` on any row. Stamping was added recently, and a row with no
//     time has to show none rather than borrow one.
//   - the trailing `plan-proposed` belongs to no run at all (a confirmed handoff writes one with
//     no user row above it), and a LIST OF BUILDS has nothing to list it as.
//   - `app_c` has its own, so "the builds of the app you are looking at" is a claim with two
//     possible answers rather than one.
// No `order` on any row, because the route stamps none: `order` is added by the merged read (#56),
// and a log read straight off disk has never had it.
const AGO = (ms) => new Date(Date.now() - ms).toISOString();
const HISTORY = {
  app_a: [
    { type: 'user', text: 'Add a margin column', app: 'app_a', conversation: 'thr_many' },
    { type: 'agent', kind: 'tool', tool: 'edit', detail: 'src/App.tsx',
      app: 'app_a', conversation: 'thr_many' },
    { type: 'done', ok: true, decision: 'built', app: 'app_a', conversation: 'thr_many' },
    { type: 'user', text: 'Sort the desks by P&L', app: 'app_a', conversation: 'thr_two',
      at: AGO(2 * 3600e3) },
    { type: 'agent', kind: 'text', text: 'Sorted them.', app: 'app_a', conversation: 'thr_two',
      at: AGO(2 * 3600e3 - 4000) },
    { type: 'done', ok: true, decision: 'built', app: 'app_a', conversation: 'thr_two',
      at: AGO(2 * 3600e3 - 5000) },
    { type: 'plan-proposed', plan: '# Desk dashboard', planId: 'pl_1', steps: 3, app: 'app_a' },
  ],
  app_c: [
    { type: 'user', text: 'Draw the rate curve', app: 'app_c', conversation: 'thr_many',
      at: AGO(3 * 3600e3) },
    { type: 'done', ok: true, decision: 'built', app: 'app_c', conversation: 'thr_many',
      at: AGO(3 * 3600e3 - 9000) },
  ],
};

const calls = [];
// The BODIES posted to `/bindings`, which `calls` cannot hold: it keys on method and path, and a
// bind names its Resource in the body. The id space is the whole hazard (#99) — a Project row
// carries `llm_alias:al_1` and a Binding carries the bare `al_1` — so a test that only knew the
// request happened would pass on the prefixed id that binds nothing.
const binds = [];
let selected = 'app_a';
// Every `useState(false)` starts open, for the step that reads what a fold holds.
let expanded = false;
// A 500 on the app's build log, which is not the same answer as an app nobody has built in.
let historyFails = false;
// A 500 on the app list, which is not the same answer as a Project with no apps (#95).
let appsFail = false;
// A refused publish, as the sentence the server would send with the 409. Nothing is published on
// this path, and the confirm has to stay open on it.
let publishFails = '';
// What the two pre-publish reads answer (#35), and whether either one answers at all. Both are set
// per step, because the notice's whole behaviour is a function of these two — including the case
// where a read fails, which must leave the confirm exactly as it opened.
let checkAnswer = { checked: true, queries: [] };
let egressAnswer = { checked: true, notice: null };
let checkFails = false;
let egressFails = false;
// A read that never answers — the gateway hanging rather than refusing. It is the case the two
// routes exist for: the query check is local disk and must render whatever the listing is doing,
// so a hang here has to be expressible or "fired in parallel" is untested prose.
let checkHangs = false;
let egressHangs = false;
const NEVER = new Promise(() => {});
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
  // The publish route carries NO app id, the way the real one does not: the server ships the app
  // it has selected. So this writes to `selected` and to nothing else, which is what makes "the
  // publish reached the selected app and no other" a claim the fixture can be asked about
  // afterwards rather than a request path a test could match and be satisfied by.
  if (path === '/publish' && init && init.method === 'POST') {
    if (publishFails) return json({ error: publishFails }, 409);
    const row = apps.find((a) => a.id === selected);
    const again = !!(row && row.published);
    if (row) {
      row.published = true;
      row.url = `/modelproducts/da_${row.id.replace('app_', '')}?scope=project`;
    }
    return json({ published: true, app_id: `da_${selected}`, url: row ? row.url : '', republished: again });
  }
  // The two reads behind the pre-publish notice (#35). Two routes rather than one, the way the
  // server has them: the query check is local disk and the egress read may reach the gateway, so a
  // slow listing must not hold up warnings that were already on the disk. A failure is a 502 here
  // and nothing on screen there.
  if (path === '/publish-check') {
    if (checkHangs) return NEVER;
    return checkFails ? json({ error: 'no' }, 502) : json(checkAnswer);
  }
  if (path === '/publish-egress') {
    if (egressHangs) return NEVER;
    return egressFails ? json({ error: 'no' }, 502) : json(egressAnswer);
  }
  // The app's whole build log, which is the question this route answers when no conversation is
  // named. Filtered when one IS named, exactly as the server filters — so a caller that started
  // naming one would come back short a build rather than answering the same list either way.
  if (path.startsWith('/project/history')) {
    if (historyFails) return json({ error: 'unavailable' }, 500);
    const rows = HISTORY[selected] || [];
    const named = path.match(/[?&]conversation=([^&]+)/);
    return json({
      history: named
        ? rows.filter((r) => r.conversation === decodeURIComponent(named[1]))
        : rows,
    });
  }
  // Both are app-scoped and both are read off disk, so the answer follows `selected` rather than
  // being a fixture the whole run shares.
  // Recorded before the read below, which matches any method: a POST that fell through to it would
  // answer with the unchanged list and look exactly like a bind that worked.
  if (path === '/bindings' && init && init.method === 'POST') {
    const body = JSON.parse(init.body || '{}');
    binds.push(body);
    const row = RESOURCE_GROUPS.model_llm.find((r) => r.id === `${body.kind}:${body.id}`);
    bound[selected] = [
      ...(bound[selected] || []),
      { kind: body.kind, id: body.id, name: row ? row.name : body.id,
        display_name: row ? row.name : body.id },
    ];
    return json({ bindings: bound[selected] });
  }
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
// Every new tab, in order.
const opened = [];
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
  // Recorded, because the two open controls are told apart by WHERE each one went and by nothing
  // else: `./preview/` is the local pane and the app's URL is the deployed App, and a single
  // control that changed between them would read identically in every other way.
  open: (u) => { opened.push(String(u)); },
  location: { hash: '' },
  history: { replaceState() {} },
  addEventListener() {},
  removeEventListener() {},
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    // The setter is a no-op, so a control whose only effect is its own `useState` cannot be driven
    // by clicking it here. `expanded` is how a step asks for the opened branch instead: the build
    // history's turns are rendered behind one, and a branch nothing ever renders is a branch
    // nothing tests.
    //
    // Only a state that STARTS `false` flips. A blunter switch turns `AppPicker`'s search query
    // from `''` into `true` and the header stops rendering at all, which is a harness fault
    // reported as a product one.
    useState: (init) => [
      expanded && init === false ? true : (typeof init === 'function' ? init() : init),
      () => {},
    ],
    useEffect: (fn, deps) => { effects.push({ fn, deps }); },
    // Called through, not cached: a stub that remembered would have to model React's dep
    // comparison, and every claim here is about WHAT IS DRAWN rather than about how often.
    useMemo: (fn) => fn(),
    useRef: () => ({ current: null }),
    Fragment: 'Fragment',
  },
  antd: {
    Input: Object.assign(function Input() {}, { TextArea: 'Input.TextArea' }),
    Button: 'Button', Dropdown: 'Dropdown', Tag: 'Tag', Tooltip: 'Tooltip', Space: 'Space',
    // The overlay the build history opens in, and what it draws while its read is out. Strings,
    // like every other antd element here: what is asserted is which props it was given — the mask,
    // the close button, the Escape key — and mounting would test antd.
    Drawer: 'Drawer', Skeleton: 'Skeleton',
    Checkbox: 'Checkbox', Alert: 'Alert',
    // `confirm` answers with the instance antd answers with, because #35's notice arrives AFTER the
    // modal is on screen and `update` is the only way in: `Modal.confirm` renders its config once,
    // outside any tree this file can re-render, and the stubbed `useState` setter below is a no-op —
    // so a component nested in `content` could never show a second answer here. `update` merges into
    // the recorded config the way antd's does, which is what makes the filled-in content readable.
    Modal: {
      confirm: (cfg) => {
        modals.push(cfg);
        return { update: (next) => Object.assign(cfg, next), destroy: () => {} };
      },
    },
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
                 'components/build-history.js', 'modes/builder.js']) {
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
  // An overlay's own props. "There is a way out" is three separate ones — the backdrop, the close
  // button and the Escape key — and a drawer can be missing any one of them and look identical in
  // the tree otherwise. `open` is here for the sharper reason: children are in the tree whether or
  // not the thing is open, so without it "the drawer shows X" would pass on a closed drawer.
  ['open', 'mask', 'maskClosable', 'closable', 'keyboard', 'width', 'placement'].forEach((k) => {
    if (k in props) entry[k] = props[k];
  });
  // The React key, which a stub can see because it is an ordinary prop here. Two rows in one list
  // sharing a key is not a rendering detail — React draws them as one — and a list built off a
  // field the server does not send is exactly how every row ends up with the same one.
  if (props.key !== undefined) entry.key = String(props.key);
  // Menus are data, not children — the `…` overflow's items never appear in the tree otherwise,
  // and the whole criterion is about their order and their styling.
  if (props.menu && props.menu.items) {
    entry.items = props.menu.items.map((i) => ({
      key: i.key || '', label: typeof i.label === 'string' ? i.label : '', danger: !!i.danger,
      divider: i.type === 'divider',
      // An item that cannot act yet is still a control, and the claim about it is that it SAYS
      // why rather than disappearing — a claim about the label and the flag together.
      disabled: !!i.disabled,
    }));
    // Kept beside the labels so a step can CLICK an item rather than only read it: "reachable from
    // this section" is a claim about what the item does, and a label proves half of it.
    entry.onMenu = props.menu.onClick;
  }
  // Same reason, for the controls that are buttons rather than menu items — the notice's cleanup
  // offer and its Dismiss. Dropped by `JSON.stringify` on the way into the report.
  if (typeof props.onClick === 'function') entry.onClick = props.onClick;
  // The id a row carries, which is what "Use in this chat" POSTs. The app's rows build
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
  // An `Alert` carries its words in `message` and `description` rather than in children, and #35's
  // whole notice is two of them. Walked like `title` for the same reason: a claim about what a
  // screen SAYS cannot be asked of a prop the walk steps over.
  flatten(props.message, out, depth + 1);
  flatten(props.description, out, depth + 1);
  flatten(node.c, out, depth + 1);
  return out;
}

// The strings a person would read, in order.
const words = (nodes) => nodes.filter((n) => n.text).map((n) => n.text);

// The first node a question accepts, walking through function components the way `flatten` does.
// Two things ask it — an element by name, and an element by the class it carries — and the walk is
// the hard half of both.
function findNode(node, match, depth = 0) {
  if (!node || depth > 60) return null;
  if (Array.isArray(node)) {
    for (const n of node) {
      const found = findNode(n, match, depth);
      if (found) return found;
    }
    return null;
  }
  if (typeof node !== 'object' || !node.t) return null;
  if (match(node)) return node;
  if (typeof node.t === 'function' && !SKIP.has(tag(node))) {
    const found = findNode(node.t(Object.assign({}, node.p || {}, { children: node.c })), match, depth + 1);
    if (found) return found;
  }
  return findNode(node.c, match, depth + 1);
}

// One element's own subtree, found by name. The build history's claims are about what THE DRAWER
// says — a flat list of every string on the screen holds the transcript, the composer and the
// header too, and could not tell "this app has no builds" from the greeting behind it.
function subtree(node, el) {
  return findNode(node, (n) => tag(n) === el);
}

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

// What the build history drawer IS, and what it says. Its overlay props are read off the element
// itself — the mask, the X and the Escape key are the criterion, and each is its own prop — and its
// list is read out of its own subtree so the pane behind it cannot answer for it.
function readDrawer(tree) {
  const node = subtree(tree, 'Drawer');
  if (!node) return null;
  const nodes = flatten(node);
  const head = nodes[0];
  const textsOf = (cls) => nodes.filter((n) => n.className === cls).flatMap((n) => n.texts || []);
  return {
    open: !!head.open,
    mask: head.mask,
    maskClosable: head.maskClosable,
    closable: head.closable,
    keyboard: head.keyboard,
    width: head.width,
    placement: head.placement,
    title: head.title,
    words: words(nodes),
    // One entry per run, headed by the prompt that started it. The times are separate because the
    // claim about them is that a row with no `at` has NO time element rather than an empty one.
    runs: nodes.filter((n) => n.className === 'sw-bh-run').length,
    // One per entry, in order. A log read straight off disk carries no `order`, so a list that
    // built its keys from one would hand every row the same key.
    keys: nodes.filter((n) => n.el === 'BuildRunRow').map((n) => n.key),
    prompts: textsOf('sw-bh-run-prompt'),
    times: textsOf('sw-bh-run-at'),
    // The control that opens a run's turns, and the count in its label — the entry is the build,
    // and the turns are behind it rather than listed as builds of their own.
    folds: nodes
      .filter((n) => n.el === 'Button' && (n.texts || []).some((t) => /turn/.test(t)))
      .flatMap((n) => n.texts || []),
    // The turns themselves, once a step has asked for the opened branch. They are the transcript's
    // own rows, drawn by the reader that already knows how — which is what makes "the turns are
    // behind the entry" a claim about where they are rather than about whether they exist.
    turns: nodes.filter((n) => n.el === 'Message').length,
    skeletons: nodes.filter((n) => n.el === 'Skeleton').length,
    // Every control the drawer offers, so a failed read can be asked whether it left a way back.
    buttons: nodes.filter((n) => n.el === 'Button').flatMap((n) => n.texts || []),
  };
}

// The same walk asked the other question, so a claim about the filter chip is read off the chip
// rather than off every word in the rail. `subtree` finds by element name and the chip is a plain
// div, which is the only reason both questions exist.
function nodeByClass(node, cls) {
  return findNode(node, (n) => String((n.p || {}).className || '') === cls);
}

// The rail beside Build, read as rows rather than as a bag of every string on it: every claim about
// the filter is about WHICH conversations survive it, and a flat word list cannot tell a row that is
// gone from one that is merely further down.
function railOf(mode) {
  const tree = SW.ConversationRail({ mode: mode || 'build' });
  const nodes = flatten(tree);
  const chip = nodeByClass(tree, 'sw-rail-filter');
  const note = nodeByClass(tree, 'sw-rail-note');
  return {
    rows: nodes.filter((n) => n.className === 'sw-thread-title').flatMap((n) => n.texts || []),
    // The whole sentence, joined, because "Only " and the app name are two elements and the
    // criterion is what the chip reads as one line.
    chip: chip ? words(flatten(chip)).join('') : null,
    // The chip's way out, by the label it says out loud — a filter you cannot drop is a mode.
    chipClear: chip ? flatten(chip).filter((n) => n.label).map((n) => n.label) : [],
    note: note ? words(flatten(note)).join('') : null,
  };
}

// A click on a row in the header's app list, found by the id the row carries. Shared, because two
// steps need the pick to have HAPPENED before the thing they are actually about.
function pickInHeader(thread, appId) {
  const rows = rowsOf(
    SW.BuildMode({ conversationId: thread, appId: (SW.store.get().activeApp || {}).id || null })
  );
  const row = rows.find((r) => r.id === appId);
  if (!row) throw new Error(`no row ${appId} in the header's app list`);
  row.onClick({ stopPropagation() {} });
  return rows;
}

// The chip's way out, pressed. Read by its label rather than its class, because the label is what
// says which control this is — and dropping a filter must leave the previewed app alone, which is
// only a claim if something actually presses it.
function clearFilter(mode) {
  const node = flatten(SW.ConversationRail({ mode }))
    .find((n) => n.onClick && n.label === 'Show all conversations');
  if (!node) throw new Error('the rail filter offered no way out');
  node.onClick({ stopPropagation() {} });
}

// A click on a tag in the rail, driven the way a person drives it: find the tag by the app name it
// says, and press it. The step knows no store key — a chip that stopped writing the filter would
// leave the rail unchanged rather than be asserted around.
function clickTag(mode, appName) {
  const node = flatten(SW.ConversationRail({ mode }))
    .find((n) => n.className === 'sw-conv-tag' && (n.texts || []).includes(appName));
  if (!node) throw new Error(`no tag ${appName} in the rail`);
  node.onClick({ stopPropagation() {} });
}

// `mode` is Build unless a step says otherwise. It is the router's, not a flag the panel reads, so
// a step asking for Chat gets the mode the same way a person does — by being on that URL (#127).
async function arrive(threadId, appId, mode) {
  // Copies, for the reason `bound` and `attached` are copies: publishing WRITES to a row, and the
  // fixture is shared by every step in the run.
  apps = APPS.map((a) => ({ ...a }));
  context = JSON.parse(JSON.stringify(CONTEXT));
  bound = JSON.parse(JSON.stringify(BINDINGS));
  attached = JSON.parse(JSON.stringify(ATTACHED));
  modals.length = 0;
  said.length = 0;
  effects.length = 0;
  timers.length = 0;
  timeouts.length = 0;
  calls.length = 0;
  binds.length = 0;
  holding = null;
  held.length = 0;
  // Every step arrives at Build with the history shut, the way a page load does. The store is one
  // object for the whole run, so a step that opened it would otherwise leave it open for the next.
  SW.store.closeBuildHistory();
  // And with the rail unfiltered, for the same reason: a step that picked an app leaves a filter
  // behind, and the next step would open on a rail somebody else had narrowed.
  SW.store.set({ railAppFilter: null });
  await SW.store.openThread(threadId);
  if (appId) await SW.store.selectApp(appId);
  await SW.store.loadApps();
  await SW.store.loadBuild();
  const at = `#/${mode || 'build'}/${threadId}?app=${selected}`;
  sandbox.location.hash = at;
  SW.router.go(at);
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
    await arrive(step.panel, step.select, step.mode);
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

  // The mirror of the removal below, on the other list. `Use in {app}` lives on a PROJECT row,
  // because the app's own section holds only what it already binds — so the row this drives is the
  // one that has no Binding yet. Found by the label's prefix for the same reason the removal is.
  if (step.useIn) {
    await arrive(step.thread, step.select);
    await SW.store.reloadAttachments();
    SW.store.set({ resourceGroups: RESOURCE_GROUPS, resourcesLoading: false });
    said.length = 0;
    calls.length = 0;
    binds.length = 0;
    const rows = panelContents(SW.ResourcePanel());
    const row = rows.find((r) => r.section === 'In this project' && r.texts.includes(step.useIn));
    if (!row) {
      throw new Error(
        `no Project row ${step.useIn} — found ${JSON.stringify(rows.map((r) => r.texts))}`
      );
    }
    const item = (row.items || []).find((i) => i.label.startsWith('Use in ')
      && !i.label.startsWith('Use in this chat'));
    if (item) await row.onMenu({ key: item.key });
    report.push({
      step: `useIn ${step.useIn}`,
      item: item ? { key: item.key, label: item.label } : null,
      // What reached the wire, which is the claim: `calls` proves a request happened, `posted`
      // proves it named the right Resource.
      posted: binds.slice(),
      calls: calls.slice(),
      app: (SW.store.get().activeApp || {}).name || null,
      rows: panelContents(SW.ResourcePanel()),
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
      (r) => r.section && r.section !== 'In this project' && r.section !== 'In context'
    );
    const row = inSection.find((r) => r.texts.includes(step.removeFrom));
    if (!row) {
      throw new Error(
        `no row ${step.removeFrom} in the app's section — found ${JSON.stringify(inSection.map((r) => r.texts))}`
      );
    }
    const item = (row.items || []).find(
      (i) => i.label.startsWith('Remove from ')
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
    // This step does not `arrive`, so it clears the filter itself — see the note there.
    SW.store.set({ railAppFilter: null });
    // A tag clicked before the read, for the half of the one-way rule that is about the chip: it
    // narrows the rail and must leave the previewed app exactly where it was.
    if (step.chip) clickTag(step.rail, step.chip);
    const nodes = flatten(SW.ConversationRail({ mode: step.rail }));
    report.push({
      step: `rail ${step.rail}`,
      words: words(nodes),
      labels: nodes.filter((n) => n.label).map((n) => n.label),
      railFilter: SW.store.get().railAppFilter,
      activeApp: (SW.store.get().activeApp || {}).id || null,
      rail: railOf(step.rail),
    });
    continue;
  }
  if (step.pick) {
    // What a click on a row in the header's list writes. The criterion is that it writes the
    // ROUTE and nothing else: `activeApp` is the store's answer to the route, never the picker's.
    // Since the filter moved, it writes one more thing — `railAppFilter` — and the rail beside it
    // is read here so "the header and the rail name the same app" is one step rather than two.
    await arrive(step.thread, step.select);
    await SW.store.reloadThreads();
    // A chip filter set BEFORE the pick, by clicking a tag, which is the case where the two used to
    // disagree: the rail went on naming the app the tag named while the header named another.
    if (step.chip) clickTag('build', step.chip);
    const railBefore = railOf('build');
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
      railFilter: SW.store.get().railAppFilter,
      railBefore,
      rail: railOf('build'),
      // The chip dropped again, by its own control. Dropping a filter is a question about the rail
      // and nothing else, so the app under the preview has to sit still through it.
      cleared: step.clear
        ? (clearFilter('build'),
          {
            railFilter: SW.store.get().railAppFilter,
            activeApp: (SW.store.get().activeApp || {}).id || null,
            rail: railOf('build'),
          })
        : null,
    });
    continue;
  }
  // The build history, opened the way a person opens it: find the control in the Build header and
  // click it. The step knows no store method and no route — a control that stopped being in the
  // header would not be found at all, rather than quietly asserted around.
  if (step.history) {
    await arrive(step.history, step.select);

    // Rendering is what CALLS the drawer, and calling it is what records the effect that makes its
    // read. So a paint is: draw, run what the draw scheduled, let it settle, draw again.
    const paint = async () => {
      effects.length = 0;
      flatten(SW.BuildMode({ conversationId: step.history, appId: selected }));
      for (const e of effects) {
        try {
          const off = e.fn();
          if (typeof off === 'function') off();
        } catch (err) { /* the store's own fetches, which this step is not about */ }
      }
      await settle();
      await settle();
      return SW.BuildMode({ conversationId: step.history, appId: selected });
    };

    expanded = !!step.expand;
    historyFails = !!step.readFails;
    calls.length = 0;
    const control = flatten(SW.BuildMode({ conversationId: step.history, appId: selected }))
      .find((n) => n.onClick && n.label === 'Build history');
    if (!control && !step.closed) {
      throw new Error('nothing in the Build header opens the build history');
    }
    if (control && !step.closed) control.onClick();

    let tree = await paint();
    // What the drawer said before whatever the step does next. Null unless the step moves the
    // selection, which is the only case with a "before" worth reporting.
    let mid = null;

    if (step.moveTo) {
      // The selection moving with the drawer OPEN, and nobody clicking: a second tab choosing
      // another app is enough (#95). Nothing is held here — this is the plain case, and the
      // question is whether the list on screen follows the header or goes on describing the app
      // that has gone.
      mid = readDrawer(tree);
      calls.length = 0;
      selected = step.moveTo;
      await SW.store.loadApps();
      tree = await paint();
    }

    if (step.switchTo) {
      // The read still out, the selection moving underneath it, and the app you LEFT answering
      // last. `/project/history` carries no app id, so its answer is only ever "whichever app was
      // selected when it was asked" — and it is asked while `step.select` still is.
      holding = new Set(['/project/history']);
      calls.length = 0;
      SW.store.openBuildHistory();
      const read = SW.store.readAppHistory();
      await settle();
      const stale = held.shift();
      if (!stale) throw new Error('the drawer never asked for the app history');
      // The selection moves the way a second tab moves it: `/apps` simply starts answering
      // differently, and the poll cascades onto the new app.
      selected = step.switchTo;
      await SW.store.loadApps();
      stale.release();
      await read;
      holding = null;
      held.length = 0;
      mid = readDrawer(SW.BuildMode({ conversationId: step.history, appId: selected }));
      tree = await paint();
    }

    if (step.reopen) {
      // Shut, then opened again on the SAME app. Nothing dropped the list on the way — the
      // selection never moved, so the app-scope gate had no reason to — which leaves
      // `openBuildHistory`'s own clear as the only thing that can send the drawer back to the log.
      // Without it the second look IS the first look, and a build that finished in between is
      // missing from a list whose whole job is to hold every build.
      mid = readDrawer(tree);
      SW.store.closeBuildHistory();
      await paint();
      calls.length = 0;
      const again = flatten(SW.BuildMode({ conversationId: step.history, appId: selected }))
        .find((n) => n.onClick && n.label === 'Build history');
      if (!again) throw new Error('nothing in the Build header re-opens the build history');
      again.onClick();
      tree = await paint();
    }

    const whole = flatten(tree);
    report.push({
      step: `history ${step.select || selected}`,
      app: (SW.store.get().activeApp || {}).id || null,
      control: control ? { label: control.label, texts: control.texts || [] } : null,
      calls: calls.slice(),
      drawer: readDrawer(tree),
      mid,
      // Both still on screen behind the overlay, which is the whole of "it does not displace the
      // preview to be read".
      previewFrames: whole.filter((n) => n.className === 'sw-preview-frame').length,
      transcripts: whole.filter(
        (n) => String(n.className || '').startsWith('sw-builder-chat-messages')
      ).length,
    });
    // After the report, not before it: `readDrawer` calls the components again to read the tree,
    // so a switch turned off here would be off for the render the report is built from.
    expanded = false;
    historyFails = false;
    continue;
  }

  // One tick of the 30s poll Build arms (#95). The server's selection is moved WITHOUT a request,
  // because that is what another tab selecting a different app looks like from here: `/apps`
  // simply starts answering differently. `step.poll` names the app the server now reports, so
  // passing the one already selected is the tick that changes nothing.
  if (step.poll) {
    await arrive(step.thread, step.select);
    await SW.store.reloadThreads();
    // A filter this person set themselves, before the server's selection moves underneath them.
    // `pickFirst` rather than a write to the store, because the claim is about what a CLICK left
    // behind — a filter seeded by hand would pass even if nothing wrote it.
    if (step.pickFirst) pickInHeader(step.thread, step.pickFirst);
    const railFilterBefore = SW.store.get().railAppFilter;
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
      // The rail's filter across the tick. It is set by a click and by nothing else, so a poll
      // moving it would be another tab re-filtering this tab's rail.
      railFilterBefore,
      railFilter: s.railAppFilter,
      rail: railOf('build'),
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

  // Publishing, driven the way a person drives it: open the `…` beside the app the header names,
  // click the item, answer the confirm. The step knows no menu key beyond `publish` — an item that
  // stopped being reachable from the header would not be found at all rather than quietly asserted
  // around.
  if (step.publish) {
    await arrive(step.publish, step.select);
    said.length = 0;
    modals.length = 0;
    opened.length = 0;
    publishFails = step.refuse || '';
    checkAnswer = { checked: true, queries: step.queries || [] };
    egressAnswer = { checked: true, notice: step.notice || null };
    checkFails = !!step.checkFails;
    egressFails = !!step.egressFails;
    checkHangs = !!step.checkHangs;
    egressHangs = !!step.egressHangs;
    if (step.buildRunning) SW.store.set({ buildRunning: true });
    calls.length = 0;
    const menuOf = () => {
      const found = flatten(SW.BuildMode({ conversationId: step.publish, appId: selected }))
        .find((n) => n.items && n.items.some((i) => i.key === 'publish'));
      if (!found) throw new Error('nothing in the Build header offers to publish');
      return found;
    };
    const menu = menuOf();
    const item = menu.items.find((i) => i.key === 'publish');
    // A disabled item is not clicked, the way antd would not click it. What it says is the claim.
    if (!item.disabled) menu.onMenu({ key: 'publish', domEvent: { stopPropagation() {} } });
    const confirm = item.disabled ? null : modals[modals.length - 1];
    // What the confirm said BEFORE the two reads landed. Kept, because #35's criterion is that the
    // modal opens without waiting on either of them — a notice already present here would mean the
    // click had awaited a network read, which is the control-that-did-nothing this must not be.
    const openedWith = confirm ? words(flatten(confirm.content)).join(' ') : '';
    // Two turns of the queue: one for the pair of reads, one for the `.then` that updates. After
    // this the notice has arrived or has decided not to, and either way the modal is settled.
    await settle();
    await settle();
    // A modal can sit open for as long as somebody leaves it there, and the 30-second app poll
    // moves the selection underneath — which the request cannot notice, because it carries no id.
    // Its own key: `switchTo` is the switch step's, one branch above this one.
    if (confirm && step.movesTo) await SW.store.selectApp(step.movesTo);
    let acted = null;
    if (confirm && step.confirm) acted = await confirm.onOk().then(() => 'ok', () => 'held open');
    if (confirm && !step.confirm && confirm.onCancel) confirm.onCancel();
    publishFails = '';
    // Read after the act, so the row's own state is what the publish left rather than what it
    // found: `published`, the URL and the confirm's own sentence all move together or not at all.
    const after = menuOf();
    report.push({
      step: `publish ${step.select}`,
      item,
      confirm: confirm ? {
        title: String(confirm.title || ''),
        okText: String(confirm.okText || ''),
        cancelText: String(confirm.cancelText || ''),
        danger: !!(confirm.okButtonProps || {}).danger,
        content: words(flatten(confirm.content)).join(' '),
        openedWith,
        // Every Alert the settled content holds, by the kind it is drawn as: a query that will fail
        // is a warning and where the data goes is not, and one type standing in for the other is a
        // change nothing else here would catch.
        alerts: flatten(confirm.content).filter((n) => n.el === 'Alert').map((n) => n.type),
      } : null,
      acted,
      calls: calls.slice(),
      said: said.slice(),
      // Every app afterwards, because the criterion is about the ones that did NOT move.
      apps: SW.store.get().apps.map((a) => ({ id: a.id, published: !!a.published, url: a.url || '' })),
      items: after.items,
      // Whether the question survived being answered — a void one takes its modal with it.
      openAfter: modals[modals.length - 1] === confirm && acted === 'held open',
    });
    if (step.buildRunning) SW.store.set({ buildRunning: false });
    continue;
  }

  // The other door. Two controls, two destinations — so the step clicks each and reports where it
  // went, which is the only way one control wearing two words would be caught.
  if (step.openapp) {
    await arrive(step.openapp, step.select);
    opened.length = 0;
    const nodes = flatten(SW.BuildMode({ conversationId: step.openapp, appId: selected }));
    const menu = nodes.find((n) => n.items && n.items.some((i) => i.key === 'open'));
    if (!menu) throw new Error('nothing in the Build header offers to open the app');
    const item = menu.items.find((i) => i.key === 'open');
    if (!item.disabled) menu.onMenu({ key: 'open', domEvent: { stopPropagation() {} } });
    const appOpened = opened.slice();
    opened.length = 0;
    // The preview control, from the toolbar, by the label it says out loud.
    const preview = nodes.find((n) => n.onClick && n.label === 'Open preview in a new tab');
    if (preview) preview.onClick();
    report.push({
      step: `openapp ${step.select}`,
      item,
      appOpened,
      previewOpened: opened.slice(),
      labels: nodes.filter((n) => n.label).map((n) => n.label),
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
