// Whether the model drawer's rows stay true while it sits open under a running build (#294).
//
// A store-level harness rather than a step in `model_assignments_harness.mjs`, because the thing
// under test is an interval and that file stubs `setInterval` to a no-op. Bending it to reach one
// would make every other step in it pay for this one.
//
// It exists to pin a fact about the CALL GRAPH as much as a behaviour. #294 and ADR-0043 both
// decided the re-read should hang off the build watch's own 2s tick. That tick is started in
// exactly one place — the last line of `loadBuild` — so it runs only in a tab that loaded while a
// build was ALREADY going: a reload, or a second Workbench. The tab that STREAMS the build sets
// `buildRunning` itself and never reaches it, and that is the tab where somebody presses Build and
// then opens the drawer. The `watch: "stream"` steps below are what stops that fix being re-derived
// from the call site, where the absence is invisible.
//
// Input on stdin: a list of steps.
//   `watch`       — "stream" (this tab ran the turn) or "reload" (it loaded into one already going)
//   `locked`      — whether the sensitivity lock is holding (default true)
//   `open`        — whether the drawer is opened (default true)
//   `ticks`       — how many 2s rounds to fire (default 3)
//   `closeAfter`  — close the drawer, then fire `ticks` more
//   `pick`        — seed the browser's MIRROR of the pick, which stands in for a pick the person
//                   made through the composer. Left empty for #294's own case, where the
//                   orchestrator picks and nothing writes the mirror.
//   `chatPicked`  — serve the Chat half of the pick instead of the Build half, which is the fork
//                   the `ask` row takes
//   `failFromBoot` — how many sensitivity reads REJECT before one lands, counted from the setup, so
//                   the drawer opens with `state.sensitivity` still NULL
//   `unavailableFor` / `unavailableFromOpen` — how many reads answer 200 with the route's
//                   never-500 payload, armed after the drawer opens or from its own open read
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const steps = JSON.parse(fs.readFileSync(0, 'utf8'));

// Every interval the product opens, kept rather than run. `setTimeout` stays real, because `settle`
// below is how a step waits for the reads an interval fired.
let nextTimer = 0;
const timers = new Map();
// Which of them the build watch owns, told apart by WHO registered it rather than by its period:
// both are 2s, and a step asserting on the period would pass whichever one happened to exist.
let registrar = 'boot';
const owners = new Map();

// The lock, and the one field this is about. `escalated` is flipped by hand once the drawer is
// already open, which is the window: the turn stalls, the orchestrator picks the strong plan-tier
// model with no human act, and a row that never re-reads goes on naming `coder`. Flipped rather
// than keyed off the read count, so a step that reads the lock twice during setup does not spend
// the escalation before anybody is looking at it.
// Every slot moves to something its own row does NOT hold, `ask` included — its row holds
// `gpt-5.4`, so leaving it there would make "the ask row ignored the pick" and "the ask row's answer
// equals its own model" the same read, and the fork below could not be wrong in a visible way.
const BEFORE = { plan: 'coder', implement: 'coder', ask: 'gpt-5.4' };
const AFTER = { plan: 'gpt-5.4', implement: 'gpt-5.4', ask: 'coder' };
// How many reads answer with the route's never-500 payload: a 200 carrying the UNLOCKED shape,
// which is what `app.py` sends when `sensitivity_state` throws. Distinct from `failFromBoot`, where
// the fetch itself rejects and nothing lands at all — that is the whole point of the pair.
let unavailableFor = 0;
let locked = true;
let escalated = false;
// Which half of the pick the server reports. The `ask` row reads the Chat one and the two Build
// rows read the Build one — the same fork `_locked_slot_models` takes with `chat_thread_id` — so a
// step can put the pick on one side and see which rows open.
let chatPicked = false;
// Whether the payload carries the pick flags at all. A step turns them off to stand in for the two
// states that are NOT "no pick": a server that has just cleared the pick on a save while the
// browser's mirror still holds it, and a deployment whose payload predates the field.
let servesPick = true;
let sensitivityReads = 0;
const lock = () => {
  sensitivityReads += 1;
  if (unavailableFor > 0) {
    unavailableFor -= 1;
    return { enabled: false, locked: false, group: '', approved: [], datasets: [], refusal: null,
             model: null, chat_model: null, slot_models: {},
             picked: false, chat_picked: false, unavailable: true, reason: '' };
  }
  if (!locked) {
    return { enabled: false, locked: false, approved: [], datasets: [], refusal: null,
             unavailable: false };
  }
  const answer = {
    enabled: true, locked: true, reason: 'dataset', group: 'restricted',
    approved: ['coder', 'gpt-5.4'], datasets: [{ id: 'd1', name: 'Claims' }], refusal: null,
    slot_models: escalated ? AFTER : BEFORE,
    // Served beside `slot_models` and off the same fact, which is how the server sends it: ONE
    // `control.snapshot()` decides both (`sensitivity_state` takes it and hands it down), so a
    // fixture that moved the models without the flag would describe a state the product cannot
    // produce. That was not true when this comment was first written — the server took three
    // separate snapshots — and the fixture asserting it is what made the skew invisible.
    picked: escalated && !chatPicked,
    chat_picked: escalated && chatPicked,
  };
  // "false" is a server that HAS no pick standing — a save has just cleared it. "absent" is a
  // deployment whose payload predates the field, which is the only reading the browser's mirror is
  // a fallback for. The two must not behave the same, which is the whole of the fresher-first rule.
  if (servesPick === 'absent') { delete answer.picked; delete answer.chat_picked; }
  else if (!servesPick) { answer.picked = false; answer.chat_picked = false; }
  return answer;
};

// What the panel route answers. The plan row holds an APPROVED model of its own, which is the
// shape #294 is about: nothing on the row explains the difference except the pick, so a gate that
// cannot see the pick draws nothing.
const PANEL = {
  slots: [
    { slot: 'plan', model: 'coder', default: 'coder', effort: null, default_effort: null,
      problem: null, shadowed: false },
    { slot: 'implement', model: 'coder', default: 'coder', effort: null, default_effort: null,
      problem: null, shadowed: false },
    { slot: 'ask', model: 'gpt-5.4', default: 'gpt-5.4', effort: null, default_effort: null,
      problem: null, shadowed: false },
  ],
  aliases: [
    { name: 'coder', display_name: 'Qwen3 Coder', capabilities: ['chat'], serving: true,
      problem: null, reasoning_efforts: [] },
    { name: 'gpt-5.4', display_name: 'GPT-5.4', capabilities: ['chat'], serving: true,
      problem: null, reasoning_efforts: [] },
  ],
  error: null,
};

let buildStateRunning = false;
let appReads = 0;
// How many sensitivity reads fail before one lands, counted from the setup so the drawer opens with
// `state.sensitivity` still NULL. The lock read is the only one on this surface that can leave it
// null, and null is not "unlocked" — it is a read that has not landed (`util.isLocked`).
let failSensitivityFor = 0;

const json = (body) => ({
  ok: true, status: 200,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

function sseResponse(frames) {
  const enc = new TextEncoder();
  const chunks = frames.map((ev) => enc.encode(`data: ${JSON.stringify(ev)}\n\n`));
  let i = 0;
  return {
    ok: true, status: 200,
    headers: { get: () => 'text/event-stream' },
    body: { getReader: () => ({
      read: async () => (i < chunks.length
        ? { done: false, value: chunks[i++] }
        : { done: true, value: undefined }),
    }) },
  };
}

function serve(url) {
  const path = String(url).replace(/^\.\/api/, '');
  if (path.startsWith('/project/sensitivity')) {
    if (failSensitivityFor > 0) { failSensitivityFor -= 1; sensitivityReads += 1; throw new Error('Domino did not answer'); }
    return json(lock());
  }
  if (path.startsWith('/project/build/stream')) {
    return sseResponse([{ type: 'status', value: 'Working…' }, { type: 'done', ok: true }]);
  }
  if (path.startsWith('/project/build/state')) return json({ running: buildStateRunning });
  if (path.startsWith('/project/model/assignments')) return json(PANEL);
  if (path.startsWith('/project/history')) return json({ history: [] });
  if (path.startsWith('/apps')) { appReads += 1; return json({ items: [] }); }
  if (path.startsWith('/bindings')) return json({ bindings: [] });
  return json({});
}

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, TextEncoder, TextDecoder, URLSearchParams,
  setTimeout, clearTimeout,
  setInterval: (fn, ms) => {
    nextTimer += 1;
    timers.set(nextTimer, { fn, ms });
    owners.set(nextTimer, registrar);
    return nextTimer;
  },
  clearInterval: (id) => { timers.delete(id); },
  encodeURIComponent, decodeURIComponent,
  requestAnimationFrame: (fn) => fn(),
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  location: { search: '', pathname: '/', href: 'http://localhost/', hash: '#/build' },
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  addEventListener() {}, removeEventListener() {},
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
    useEffect: () => {}, useMemo: (fn) => fn(), useRef: () => ({ current: null }),
    Fragment: 'Fragment',
  },
  antd: {
    Input: Object.assign(function Input() {}, { TextArea: 'Input.TextArea' }),
    Button: 'Button', Dropdown: 'Dropdown', Tag: 'Tag', Tooltip: 'Tooltip', Space: 'Space',
    Drawer: 'Drawer', Select: 'Select', Alert: 'Alert', Spin: 'Spin',
    Typography: { Paragraph: 'Typography.Paragraph' },
    Modal: { confirm: () => ({ update: () => {}, destroy: () => {} }) },
    message: { success() {}, info() {}, warning() {}, error() {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async (url) => {
    await new Promise((r) => setTimeout(r, 0));
    return serve(url);
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js', 'components/model-assignments.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

const settle = async () => { for (let i = 0; i < 40; i += 1) await new Promise((r) => setTimeout(r, 0)); };

// One round of every live interval, in registration order, awaited. Not `Promise.all`: the build
// watch's tick and the drawer's read both touch `state.sensitivity`, and a step that let them race
// would report whichever won rather than what each one does.
async function fire(rounds) {
  for (let i = 0; i < rounds; i += 1) {
    for (const [, t] of [...timers]) await t.fn();
    await settle();
  }
}

// What the DRAWER draws, not what the store holds. The two are different answers: a row only shows
// the server's per-slot model where its own `moved` gate opens, so a test that reads
// `state.sensitivity` is green on a fresh answer the Select never puts on screen (#294, found in
// review of the first version of this file).
const walkAll = (node, out = []) => {
  if (!node || typeof node !== 'object') return out;
  if (Array.isArray(node)) { node.forEach((c) => walkAll(c, out)); return out; }
  out.push(node);
  return walkAll(node.c, out);
};
const drawnModel = (slot) => {
  const tree = SW.ModelAssignmentsDrawer();
  const row = walkAll(tree).find((n) => n.t === 'Select' && n.p.id === `assign-${slot}`);
  return row ? row.p.value : null;
};
const drawnPlanModel = () => drawnModel('plan');

const buildWatchTimers = () => [...owners]
  .filter(([id, who]) => timers.has(id) && who === 'loadBuild').length;
const drawerTimers = () => [...owners]
  .filter(([id, who]) => timers.has(id) && who === 'openAssignments').length;

// Every `notify()` the store makes, which is a whole-shell redraw in the product. Counted rather
// than reasoned about: the drawer's cadence is the only recurring notify an idle Workbench has.
let notifies = 0;
SW.store.subscribe(() => { notifies += 1; });

const report = [];
for (const step of steps) {
  // Both watches let out through their own exits, not by clearing the table underneath them.
  // Each refuses to start a second interval while it believes one is live, so a step that dropped
  // the fake timer by hand would silently get no cadence and report an absence it had caused.
  // The build watch's exit is a tick that finds the turn over; the drawer's is a close.
  buildStateRunning = false;
  await fire(1);
  await SW.store.openAssignments(false);
  await settle();
  timers.clear();
  owners.clear();
  sensitivityReads = 0;
  escalated = false;
  locked = step.locked !== false;
  chatPicked = !!step.chatPicked;
  servesPick = step.servesPick === undefined ? true : step.servesPick;
  failSensitivityFor = step.failFromBoot || 0;
  unavailableFor = 0;
  buildStateRunning = false;

  SW.store.set({
    thread: { id: 'conv_1', title: 'The desk talk', artifacts: [] },
    scope: { id: 'proj', name: 'Demo Project' },
    threads: [], activeApp: { id: 'app_a' }, sensitivity: null, assignmentsOpen: false,
    buildRunning: false,
    // The browser's mirror of the pick. A step sets it to stand in for a pick the person made
    // THEMSELVES through the composer, which is the only way this field is ever written — and the
    // control case for #294, where nothing writes it because no human act made the pick.
    buildModel: step.pick || '',
  });

  if (step.watch === 'stream') {
    // The turn this tab ran itself. `loadBuild` happens BEFORE it, with nothing running — which is
    // every tab that was sitting on the Build surface when somebody typed into it.
    registrar = 'loadBuild';
    await SW.store.loadBuild();
    await settle();
    registrar = 'stream';
    await SW.store.sendBuildPrompt('build me a dashboard');
    await settle();
    // What the stream leaves behind while the turn runs, which is where the drawer gets opened.
    SW.store.set({ buildRunning: true });
  } else {
    // The tab that loaded into a turn already going: a reload mid-build, or a second Workbench.
    buildStateRunning = true;
    registrar = 'loadBuild';
    await SW.store.loadBuild();
    await settle();
  }
  registrar = 'other';

  const buildWatch = buildWatchTimers();
  sensitivityReads = 0;

  if (step.unavailableFromOpen) unavailableFor = step.unavailableFromOpen;
  if (step.open !== false) {
    registrar = 'openAssignments';
    await SW.store.openAssignments(true);
    await settle();
    registrar = 'other';
  }
  const readsOnOpen = sensitivityReads;
  const runsOnOpen = ((SW.store.get().sensitivity || {}).slot_models || {}).plan || null;
  const drawnOnOpen = drawnPlanModel();
  const lockedOnOpen = !!(SW.store.get().sensitivity || {}).locked;

  // The escalation, fired with the drawer already open and no human act behind it.
  escalated = true;
  // Armed HERE and not with the other step state, so the failures land on the TICKS. Set during
  // setup they were spent on `loadBuild`'s own read before the drawer opened, and a plant on the
  // gate they exist to test stayed green — the fixture answered every tick correctly, so there was
  // nothing for the assertion to see. `unavailableFromOpen` is the other arrangement and it is a
  // different question: there the drawer's OWN read is the one the server could not answer, which
  // is what decides whether an unusable answer counts as one having landed.
  if (!step.unavailableFromOpen) unavailableFor = step.unavailableFor || 0;
  sensitivityReads = 0;
  appReads = 0;
  notifies = 0;
  await fire(step.ticks || 3);
  const row = {
    appReads,
    activeApp: !!SW.store.get().activeApp,
    step: JSON.stringify(step),
    buildWatch,
    drawerWatch: drawerTimers(),
    readsOnOpen,
    runsOnOpen,
    drawnOnOpen,
    lockedOnOpen,
    readsWhileOpen: sensitivityReads,
    lockedAfterTicks: !!(SW.store.get().sensitivity || {}).locked,
    notifiesWhileOpen: notifies,
    drawnAfterTicks: drawnPlanModel(),
    drawnAskAfterTicks: drawnModel('ask'),
    runsAfterTicks: ((SW.store.get().sensitivity || {}).slot_models || {}).plan || null,
  };

  if (step.closeAfter) {
    await SW.store.openAssignments(false);
    await settle();
    row.drawerWatchAfterClose = drawerTimers();
    sensitivityReads = 0;
    await fire(step.ticks || 3);
    row.readsAfterClose = sensitivityReads;
  }
  report.push(row);
}
console.log(JSON.stringify(report));
