// Which model Build says it will run on, and what a pick writes.
//
// The picker existed in the pre-Workbench UI (`<select id="pick">`) and the backend never stopped
// supporting it: `POST /api/project/model` still takes `pick`, and `llm_router` still honours a
// picked model in Plan and Implement. What was lost is in between — `applyModelStatus` read
// `catalog.ask` and threw the rest of the status away, so nothing in the Workbench could have
// drawn a picker even if one had been written.
//
// That gap is why this harness drives the store rather than assigning to it: the modes and the
// picks go in through `setBuildMode`/`setBuildModel`, which means the catalog reaching the menu
// has to survive the real `applyModelStatus`. Seeding `state.catalog` by hand would test the menu
// against a fact the product does not actually keep.
//
// Input on stdin: a list of steps. `{ "mode": "plan" }` switches modes and reports what the
// composer bar draws; `{ "mode": "plan", "pick": "<id>" }` also clicks that row and reports what
// it wrote. `{ "health": true }` reports the URL the open-weight list is read from.
//
// Nothing is mounted. `createElement` is stubbed to a plain object, so calling the component
// returns the tree it would draw — which is where a menu item and its key are settled.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const steps = JSON.parse(fs.readFileSync(0, 'utf8'));

// --- the server ------------------------------------------------------------
// Four slots, and two of them deliberately share a model: the menu offers MODELS, not slots, so a
// picker that listed one row per slot would show `claude-asker` twice and make the person choose
// between two identical rows.
const CATALOG = {
  plan: 'anthropic/claude-planner',
  implement: 'anthropic/claude-builder',
  ask: 'anthropic/claude-planner',
  sovereign_plan: 'sovereign/plan',
  sovereign_implement: 'sovereign/implement',
  sovereign_ask: 'sovereign/ask',
};
// What an `openai` gateway adds. One of them is already a configured slot, so the extras list has
// to drop it — offering the same model under two headings is the same duplicate as above.
const OPEN_WEIGHT = [
  { id: 'deepseek/deepseek-v3', provider: 'DeepSeek' },
  { id: 'qwen/qwen-2-5', provider: 'Qwen' },
  { id: 'anthropic/claude-planner', provider: 'Anthropic' },
];

let mode = 'auto';
let phase = 'plan';
let picked = null;
const calls = [];
const fetched = [];

const json = (body) => ({
  ok: true, status: 200,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

// The shape `Project.status()` returns, narrowed to the model block this reads. `selected_mode` is
// where the picker sits and `mode` is what a running turn is pinned to; they agree here because
// nothing in this file starts a turn.
const status = () => ({
  model: {
    mode, selected_mode: mode, phase, picked_model: picked,
    chat_model: null, reasoning_effort: null, catalog: CATALOG,
  },
});

function serve(url, options = {}) {
  const method = (options.method || 'GET').toUpperCase();
  const path = String(url).replace(/^\.\/api/, '');
  fetched.push(String(url));
  if (path === '/project/model' && method === 'POST') {
    const body = JSON.parse(options.body);
    calls.push(body);
    if ('mode' in body) {
      mode = body.mode;
      // What ModelControl._sync_phase does for the pinned modes, so Auto's "which phase am I in"
      // question is asked of a phase that actually moves.
      if (mode === 'plan' || mode === 'implement') phase = mode;
    }
    if ('pick' in body) picked = body.pick;
    return json(status());
  }
  return json({});
}

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, setTimeout, clearTimeout,
  setInterval: () => 1, clearInterval: () => {},
  requestAnimationFrame: (fn) => fn(),
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    // Read-once: nothing here is decided by the composer's own state. The mode and the pick both
    // live in the store, and a menu that could only be right after a local re-render has not made
    // the claim this file is about.
    useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
    useEffect: () => {},
    useMemo: (fn) => fn(),
    useRef: () => ({ current: null }),
    Fragment: 'Fragment',
  },
  antd: {
    Input: Object.assign(function Input() {}, { TextArea: 'Input.TextArea' }),
    Button: 'Button', Dropdown: 'Dropdown', Tag: 'Tag', Tooltip: 'Tooltip', Space: 'Space',
    Modal: { confirm: () => ({ update: () => {}, destroy: () => {} }) },
    message: { success() {}, error() {}, info() {}, warning() {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async (url, options) => serve(url, options),
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js', 'components/composer.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

function* walk(node) {
  if (!node || typeof node !== 'object') return;
  if (Array.isArray(node)) { for (const c of node) yield* walk(c); return; }
  yield node;
  yield* walk(node.c);
}
const find = (tree, pred) => [...walk(tree)].find(pred);
const strings = (node) => [...walk(node)].flatMap((n) => (n.c || []).flat(Infinity))
  .filter((c) => typeof c === 'string');

const settle = async () => { for (let i = 0; i < 40; i += 1) await new Promise((r) => setTimeout(r, 0)); };

// Build's props, as `builder.js` mounts them. `showMode` is the flag that says this is Build, and
// it is already true there — the picker's absence was never about the flag.
const BUILD = { onSend() {}, placeholder: 'Describe a change…', disabled: false, showMode: true };
const mount = () => SW.Composer(BUILD);

// The control by the words a person reads on it, not by position: `aria-label` is the same in both
// arms, so "is there a picker at all" and "can it be opened" stay separate questions.
const pickerButton = (tree) => find(tree, (n) => n.p['aria-label'] === 'Build model');
const pickerMenu = (tree) => find(tree, (n) => n.t === 'Dropdown' && n.p.menu
  && [...walk(n.c)].some((x) => x.p && x.p['aria-label'] === 'Build model'));
// The composer bar holds more than one Tooltip — the attach button has its own, and it comes
// first in the tree. Found by what it is wrapped around, so this never reports on that one.
const pickerTip = (tree) => find(tree, (n) => n.t === 'Tooltip'
  && [...walk(n.c)].some((x) => x.p && x.p['aria-label'] === 'Build model'));

SW.store.set({
  thread: { id: 'conv_1', title: 'A conversation', artifacts: [] },
  scope: { id: 'proj', name: 'Demo Project' },
  messages: [], resourceGroups: {},
  openWeightModels: OPEN_WEIGHT,
});

const report = [];
for (const step of steps) {
  if (step.health) {
    // Where the open-weight list comes from. Its own step because /healthz is the one route the
    // Workbench reads off `BASE` — a path that quietly became `./api/healthz` would 404 and leave
    // the picker silently short of every extra option, with nothing else here to notice.
    fetched.length = 0;
    await SW.api.healthz();
    report.push({ step: 'health', fetched: fetched.slice() });
    continue;
  }

  await SW.store.setBuildMode(step.mode);
  await settle();
  // A build in flight. `pick` is read live out of ModelControl.snapshot — it has no per-turn pin
  // the way the mode does — so what this control offers mid-turn is its own claim.
  SW.store.set({ buildRunning: !!step.running });
  calls.length = 0;

  const before = mount();
  const menu = pickerMenu(before);
  const button = pickerButton(before);
  const row = {
    step: step.pick ? `${step.mode} → pick ${step.pick}` : step.mode,
    mode: step.mode,
    // Whether an override is OFFERED, which is the Ask claim. A disabled button is not an offer.
    offered: !!menu,
    label: button ? strings(button).join(' ') : null,
    disabled: button ? !!button.p.disabled : null,
    items: menu ? menu.p.menu.items.map((i) => (i.type === 'group'
      ? { group: i.label, children: i.children.map((c) => ({ key: c.key, label: c.label })) }
      : { key: i.key, label: i.label })) : null,
    selectedKeys: menu ? menu.p.menu.selectedKeys : null,
    // What a mode with no override says instead, so "you cannot change this" is not silence.
    why: menu ? null : String((pickerTip(before) || { p: {} }).p.title || '') || null,
    // A browser dispatches no mouse events on a disabled button, so a Tooltip wrapped straight
    // round one never opens. What sits between them is the difference between an explanation and
    // silence, and it is invisible to every other assertion here.
    wrapsDisabledIn: menu ? null : (() => {
      const tip = pickerTip(before);
      const child = tip && [...walk(tip.c)].find((n) => n.t);
      return child ? String(child.t) : null;
    })(),
  };

  if (step.pick) {
    if (!menu) throw new Error(`${step.mode} offers no model menu to pick from`);
    const target = [...menu.p.menu.items].flatMap((i) => (i.type === 'group' ? i.children : [i]))
      .find((i) => i.key === step.pick);
    if (!target) throw new Error(`${step.mode} has no row keyed ${step.pick}`);
    menu.p.menu.onClick({ key: target.key });
    await settle();
    row.wrote = calls.slice();
    row.serverPick = picked;
    row.afterLabel = strings(pickerButton(mount())).join(' ');
  }
  report.push(row);
}
console.log(JSON.stringify(report));
