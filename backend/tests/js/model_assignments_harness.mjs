// Drives SW.ModelAssignmentsDrawer against a fake control plane, and reports what it drew.
//
// Input on stdin: a list of steps. `{}` just opens the panel; `{ "running": true }` opens it during
// a build; `{ "listing": "down" }` opens it when the gateway will not list Aliases; `{ "set":
// ["plan", "opus"] }` also changes one row and reports what was written.
//
// Nothing is mounted. `createElement` is stubbed to a plain object, so calling the component returns
// the tree it would draw — which is where a Select's options and disabled state are settled.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const steps = JSON.parse(fs.readFileSync(0, 'utf8'));

// --- server -------------------------------------------------------------------------------------
// Three slots, and `ask` deliberately holding the same model as `plan`: the panel lists SLOTS, not
// models, so unlike the override menu it must draw that as two rows rather than collapsing them.
const DEFAULTS = { plan: 'gpt-5.4', implement: 'coder', ask: 'gpt-5.4' };
const overrides = {};

// One Alias that will not answer. `/v1/models` filters on permission alone, so a granted Alias whose
// Hosted GenAI Endpoint is stopped is listed anyway (#21) — which is the row the panel has to offer
// and refuse at the same time.
const ALIASES = [
  { name: 'gpt-5.4', display_name: 'GPT-5.4', capabilities: ['chat'], serving: true, problem: null },
  { name: 'coder', display_name: 'Qwen3 Coder', capabilities: ['chat'], serving: true, problem: null },
  { name: 'opus', display_name: 'Claude Opus', capabilities: ['chat'], serving: true, problem: null },
  {
    name: 'local-llm', display_name: 'Mistral (Domino-hosted)', capabilities: ['chat'],
    serving: false,
    problem: 'Its Hosted GenAI Endpoint mistral-ep is Stopped, so turns using it will fail. Start that endpoint, or pick a different model.',
  },
  // Never offered: an embeddings-only Alias cannot hold a conversation, and the panel reuses the
  // same rule the Chat picker applies rather than growing a second copy of it.
  { name: 'embed-3', display_name: 'Embeddings', capabilities: ['embeddings'], serving: true, problem: null },
];

let listing = 'up';
const calls = [];

const json = (body) => ({
  ok: true, status: 200,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

const model = (slot) => overrides[slot] || DEFAULTS[slot];
const status = () => ({
  model: {
    mode: 'auto', selected_mode: 'auto', phase: 'plan', picked_model: null,
    chat_model: null, reasoning_effort: null,
    catalog: { plan: model('plan'), implement: model('implement'), ask: model('ask') },
  },
});

const panel = () => ({
  slots: ['plan', 'implement', 'ask'].map((slot) => ({
    slot, model: model(slot), default: DEFAULTS[slot], assigned: slot in overrides,
    // Preflight's verdict, which the server recomputes on every read — so a slot assigned to a
    // model that will not answer reports it the moment the panel re-reads after the save.
    problem: (ALIASES.find((a) => a.name === model(slot)) || {}).serving === false
      ? `Sage's ${slot} model is set to the LLM Alias ${model(slot)}, whose Hosted GenAI Endpoint mistral-ep is Stopped. Turns that route to ${slot} will fail. Start that endpoint, or pick a different model for that slot.`
      : null,
  })),
  aliases: listing === 'up' || listing === 'unchecked' ? ALIASES : [],
  error: listing === 'down' ? 'The LLM Gateway is not answering.'
    : listing === 'unchecked' ? 'The Hosted GenAI Endpoint listing timed out.' : null,
});

function serve(url, options = {}) {
  const method = (options.method || 'GET').toUpperCase();
  const path = String(url).replace(/^\.\/api/, '');
  // Not the same thing as a gateway that answered "I cannot list": this is the read never landing
  // at all, which leaves the panel with no slots of its own to draw.
  if (path === '/project/model/assignments' && listing === 'throw') throw new Error('network down');
  if (path === '/project/model/assignments') return json(panel());
  if (path === '/project/model' && method === 'POST') {
    const body = JSON.parse(options.body);
    calls.push(body);
    for (const [slot, value] of Object.entries(body.catalog || {})) {
      if (value) overrides[slot] = value; else delete overrides[slot];
    }
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
    useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
    useEffect: () => {},
    useMemo: (fn) => fn(),
    useRef: () => ({ current: null }),
    Fragment: 'Fragment',
  },
  antd: {
    Input: Object.assign(function Input() {}, { TextArea: 'Input.TextArea' }),
    Button: 'Button', Dropdown: 'Dropdown', Tag: 'Tag', Tooltip: 'Tooltip', Space: 'Space',
    Drawer: 'Drawer', Select: 'Select', Alert: 'Alert', Spin: 'Spin',
    Typography: { Paragraph: 'Typography.Paragraph' },
    Modal: { confirm: () => ({ update: () => {}, destroy: () => {} }) },
    message: { success() {}, error() {}, info() {}, warning() {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async (url, options) => serve(url, options),
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'api.js', 'store.js', 'components/model-assignments.js']) {
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
const all = (tree, pred) => [...walk(tree)].filter(pred);
const settle = async () => { for (let i = 0; i < 40; i += 1) await new Promise((r) => setTimeout(r, 0)); };

const selects = (tree) => all(tree, (n) => n.t === 'Select');
const alerts = (tree) => all(tree, (n) => n.t === 'Alert');
const mount = () => SW.ModelAssignmentsDrawer();

const report = [];
for (const step of steps) {
  listing = step.listing || 'up';
  SW.store.set({ buildRunning: !!step.running, catalog: status().model.catalog });
  await SW.store.openAssignments(true);
  await settle();
  calls.length = 0;

  const tree = mount();
  const rows = selects(tree).map((s) => ({
    label: s.p['aria-label'],
    value: s.p.value,
    disabled: !!s.p.disabled,
    options: (s.p.options || []).map((o) => ({
      value: o.value, label: o.label, disabled: !!o.disabled, title: o.title || null,
    })),
  }));
  const row = {
    step: JSON.stringify(step),
    // The labels a person reads down the panel, in order.
    labels: all(tree, (n) => n.p && n.p.className === 'sw-assignment-label')
      .map((n) => (n.c || []).flat(Infinity).filter((c) => typeof c === 'string').join('')),
    rows,
    problems: all(tree, (n) => n.p && n.p.className === 'sw-assignment-problem')
      .map((n) => (n.c || []).flat(Infinity).filter((c) => typeof c === 'string').join('')),
    alerts: alerts(tree).map((a) => ({
      type: a.p.type,
      message: a.p.message,
      description: a.p.description,
      hasAction: !!a.p.action,
    })),
  };

  if (step.set) {
    const [slot, value] = step.set;
    const select = selects(tree).find((s) => s.p.id === `assign-${slot}`);
    if (!select) throw new Error(`no row for slot ${slot}`);
    select.p.onChange(value);
    await settle();
    row.wrote = calls.slice();
    row.after = selects(mount()).find((s) => s.p.id === `assign-${slot}`).p.value;
  }
  report.push(row);
}
console.log(JSON.stringify(report));
