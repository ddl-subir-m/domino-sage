// Drives SW.ModelAssignmentsDrawer against a fake control plane, and reports what it drew.
//
// Input on stdin: a list of steps. `{}` just opens the panel; `{ "running": true }` opens it during
// a build; `{ "listing": "down" }` opens it when the gateway will not list Aliases; `{ "set":
// ["plan", "opus"] }` also changes one row and reports what was written and how many times the
// lock was re-read afterwards; `{ "setEffort": ["plan", "high"] }` writes through the row's SECOND
// control, the one that carries a reasoning effort (ADR-0049); `{ "sensitivity": {...} }`
// opens it with the sensitivity lock holding, served from the route the panel actually reads;
// `{ "signing": "implement" }` opens it with that slot holding a model that signs, so the other
// rows arrive carrying the shadow the pin casts over them (#276).
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
// The deployment's own effort per slot. All null, as every deployment's are today: nothing outside
// `model_overrides.json` writes a `<slot>_effort`, so "Use the default" on this control means no
// field at all. Named anyway, because the panel reports `default_effort` beside `effort` and a
// fixture that simply omitted it would leave the one branch that names a default undrawn.
const DEFAULT_EFFORTS = { plan: null, implement: null, ask: null };
// `{slot: {model, effort}}`, the shape `model_overrides.json` holds since ADR-0049 — not a bare id.
// Both halves are here because the rule this file is exercising is what happens to one when the
// other changes.
const overrides = {};

// One Alias that will not answer. `/v1/models` filters on permission alone, so a granted Alias whose
// Hosted GenAI Endpoint is stopped is listed anyway (#21) — which is the row the panel has to offer
// and refuse at the same time.
//
// `reasoning_efforts` is the server's narrowed answer per Alias — the gateway's published enum cut
// down by the measured table (`alias_reasoning_efforts`, #280) — and the rows here are the measured
// ones, so the fixture disagrees with the product in no direction that matters. The spread is the
// point: gpt-5.4 and gemini accept DIFFERENT levels, which is the only way a row can be retargeted
// at a model that will not take the level it is carrying; `coder` and `opus` accept none, which is
// the majority case on the real gateway and the one where no control may be drawn at all.
const ALIASES = [
  {
    name: 'gpt-5.4', display_name: 'GPT-5.4', capabilities: ['chat'], serving: true, problem: null,
    reasoning_efforts: ['none', 'low', 'medium', 'high', 'xhigh'],
  },
  {
    name: 'gemini-3.7-flash', display_name: 'Gemini 3.7 Flash', capabilities: ['chat'],
    serving: true, problem: null,
    // `max` is here and `minimal` is not, which is the narrowing itself: the gateway advertises
    // both, and `minimal` 400s at Vertex, so the table takes it off before the panel ever sees it.
    reasoning_efforts: ['low', 'medium', 'high', 'max'],
  },
  {
    name: 'coder', display_name: 'Qwen3 Coder', capabilities: ['chat'], serving: true,
    problem: null, reasoning_efforts: [],
  },
  {
    name: 'opus', display_name: 'Claude Opus', capabilities: ['chat'], serving: true,
    problem: null, reasoning_efforts: [],
  },
  {
    name: 'local-llm', display_name: 'Mistral (Domino-hosted)', capabilities: ['chat'],
    serving: false, reasoning_efforts: [],
    problem: 'This model is Stopped, so turns using it will fail. Start that endpoint, or pick a different model.',
  },
  // Never offered: an embeddings-only Alias cannot hold a conversation, and the panel reuses the
  // same rule the Chat picker applies rather than growing a second copy of it.
  { name: 'embed-3', display_name: 'Embeddings', capabilities: ['embeddings'], serving: true, problem: null, reasoning_efforts: [] },
];
// What `reasoning_efforts_for` answers — the MEASURED table, which is what `_merge_assignment`
// validates a saved level against. Read off the Alias rows above rather than written twice: the two
// agree on every alias on this gateway, because `inference_params` is `{}` for all of them (#284),
// and a second literal here would be this file inventing the one case where they differ.
const accepts = (name) => ((ALIASES.find((a) => a.name === name) || {}).reasoning_efforts || []);

let listing = 'up';
let failReloadAfterSave = false;
// A save the server refuses outright — the turn lock's 409, which is the refusal a person can
// actually provoke by changing a row while a build runs.
let failSave = false;
// The assignable slot holding a model that signs its tool calls, or null. Set by a step and served,
// never derived here: the server owns `signing_slot` (ADR-0032) and a copy of that rule in this file
// would let the panel agree with a fixture rather than with the product (#276).
let signingSlot = null;
// The lock, exactly as `/api/project/sensitivity` answers it. Served rather than written into the
// store, because opening the drawer re-reads it (`openAssignments`) — a value set by hand would be
// overwritten by that read before the panel drew a single row.
let sensitivity = null;
// Counted, not just served: an assignment is an input to the lock's per-slot answer (#285), so
// "did the panel re-read the lock after the save" is a wiring fact this file can check without
// copying `locked_runs_on` into a fixture and letting the panel agree with the copy.
let sensitivityReads = 0;
const calls = [];

const json = (body, status = 200) => ({
  ok: status < 400, status,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

const model = (slot) => (overrides[slot] || {}).model || DEFAULTS[slot];
const effort = (slot) => (overrides[slot] || {}).effort || DEFAULT_EFFORTS[slot];
const status = () => ({
  model: {
    mode: 'auto', selected_mode: 'auto', phase: 'plan', picked_model: null,
    chat_model: null, reasoning_effort: null,
    catalog: { plan: model('plan'), implement: model('implement'), ask: model('ask') },
  },
});

// What `shadowed_slots` sends for a slot the pin has taken out of play, and the `shadowed` flag
// that says which kind of verdict `problem` is carrying. The sentence is the server's, copied; what
// this file is asking the panel is whether it DRAWS it, and when it drops it.
// `ask` gets the other sentence, as it does from the server: the pin does not reach Chat, so that
// row still drives a model even while Build's Ask mode runs on the holder.
const shadow = (slot) => (signingSlot && slot !== signingSlot
  && model(slot) !== model(signingSlot)
  ? `The ${signingSlot} model (${model(signingSlot)}) runs every Turn in this session, so this `
    + (slot === 'ask' ? 'model only runs in Chat. ' : "model won't run. ")
    + `Change the ${signingSlot} model to release the session.`
  : null);

const panel = () => ({
  slots: ['plan', 'implement', 'ask'].map((slot) => ({
    slot, model: model(slot), default: DEFAULTS[slot],
    effort: effort(slot), default_effort: DEFAULT_EFFORTS[slot],
    // The MODEL inside the entry, not the entry's presence: since ADR-0049 a row can exist carrying
    // a level alone, with its model still following the deployment default.
    assigned: Boolean((overrides[slot] || {}).model),
    shadowed: shadow(slot) !== null,
    // Preflight's verdict, which the server recomputes on every read — so a slot assigned to a
    // model that will not answer reports it the moment the panel re-reads after the save.
    problem: shadow(slot)
      || ((ALIASES.find((a) => a.name === model(slot)) || {}).serving === false
        ? `The ${slot} model (${model(slot)}) is Stopped. Turns that use it will fail. Start that endpoint, or pick a different model.`
        : null),
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
  if (path.startsWith('/project/sensitivity')) {
    sensitivityReads += 1;
    return json(sensitivity || { enabled: false, locked: false, group: '', approved: [],
      datasets: [], refusal: null, model: null, chat_model: null, slot_models: {}, reason: '' });
  }
  if (path === '/project/model' && method === 'POST') {
    const body = JSON.parse(options.body);
    calls.push(body);
    if (failSave) return json({ error: 'A build is running. Wait for the turn to finish.' }, 409);
    for (const [slot, value] of Object.entries(body.catalog || {})) {
      // `service._merge_assignment`'s contract, in its own order, because the behaviour under test
      // is what the DRAWER does with each outcome. A copy of only the four rules the panel can
      // reach: a null clears the whole entry, a bare id is exactly `{model: id}`, an absent key
      // means "leave it", and a level the row's model will not take is REFUSED when this call asked
      // for it and DROPPED when the call only carried it along behind a model change. The rest of
      // that function guards shapes no control here can send.
      if (!value) { delete overrides[slot]; continue; }
      const sent = typeof value === 'string' ? { model: value } : value;
      const stored = overrides[slot] || {};
      const merged = { model: stored.model || null, effort: stored.effort || null };
      if ('model' in sent) merged.model = sent.model || null;
      if ('effort' in sent) merged.effort = sent.effort || null;
      const against = merged.model || DEFAULTS[slot];
      if (merged.effort && !accepts(against).includes(merged.effort)) {
        // Did this call CHANGE the level, not did it mention one. The panel sends the two halves on
        // separate calls, so in practice only the effort control reaches the refusal — but keying
        // on the change rather than on the key is the server's rule, and a fixture keyed on
        // presence would certify a client that had to avoid ever sending the field.
        if (sent.effort && sent.effort !== (stored.effort || null)) {
          const takes = accepts(against);
          return json({ error: `${against} does not accept the reasoning effort `
            + `'${merged.effort}' — `
            + (takes.length ? `it accepts ${takes.join(', ')}` : 'it accepts no effort') }, 400);
        }
        merged.effort = null;
      }
      if (merged.model || merged.effort) overrides[slot] = merged; else delete overrides[slot];
    }
    // The narrow window the panel has to survive: the write lands, and the read that verifies it
    // does not. Flipped here rather than by the step so the FIRST read still succeeds.
    if (failReloadAfterSave) listing = 'throw';
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

// The strings under one class, in the order a person reads them down the panel.
const text = (tree, className) => all(tree, (n) => n.p && n.p.className === className)
  .map((n) => (n.c || []).flat(Infinity).filter((c) => typeof c === 'string').join(''));
const selects = (tree) => all(tree, (n) => n.t === 'Select');
const alerts = (tree) => all(tree, (n) => n.t === 'Alert');
const mount = () => SW.ModelAssignmentsDrawer();

const report = [];
for (const step of steps) {
  listing = step.listing || 'up';
  failReloadAfterSave = !!step.failReload;
  failSave = false;
  // The override as it stands on disk, written PAST the merge. Not a shortcut around the contract:
  // it is the one state the merge cannot produce and the product still has to survive — a level
  // saved legally, under a model that stopped accepting it afterwards, because the deployment
  // default moved or that alias was probed (`service._effective_catalog` names both).
  for (const [slot, entry] of Object.entries(step.seed || {})) overrides[slot] = entry;
  sensitivity = step.sensitivity || null;
  signingSlot = step.signing || null;
  SW.store.set({ buildRunning: !!step.running, catalog: status().model.catalog });
  await SW.store.openAssignments(true);
  await settle();
  calls.length = 0;
  sensitivityReads = 0;

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
    labels: text(tree, 'sw-assignment-label'),
    rows,
    problems: text(tree, 'sw-assignment-problem'),
    details: text(tree, 'sw-assignment-detail'),
    // Why a level a person set is gone (ADR-0049). Its own list rather than a fourth entry in
    // `details`, for the reason it has its own class on the row: it answers what was SAVED, where
    // all three lists above answer what will RUN, and folding it in would make the one assertion
    // that tells them apart impossible to write.
    effortNotes: text(tree, 'sw-assignment-effort-note'),
    alerts: alerts(tree).map((a) => ({
      type: a.p.type,
      message: a.p.message,
      description: typeof a.p.description === 'string' ? a.p.description : null,
      // One entry per paragraph, so a sentence dropped from the notice is a shorter list rather
      // than a substring that happens still to match.
      paragraphs: typeof a.p.description === 'string' ? [a.p.description]
        : all(a.p.description, (n) => n.t === 'p')
          .map((n) => (n.c || []).flat(Infinity).filter((c) => typeof c === 'string').join('')),
      hasAction: !!a.p.action,
    })),
  };

  // The two controls on a row write on separate calls, so they are separate verbs. A step naming
  // both would hide the very thing the calls are split for — that touching one leaves the other
  // alone — behind whichever order this loop happened to fire them in.
  const act = step.set ? ['assign', step.set] : step.setEffort ? ['effort', step.setEffort] : null;
  if (act) {
    const [which, [slot, value]] = act;
    const id = which === 'assign' ? `assign-${slot}` : `effort-${slot}`;
    const select = selects(tree).find((s) => s.p.id === id);
    if (!select) throw new Error(`no ${which} control for slot ${slot}`);
    select.p.onChange(value);
    await settle();
    row.wrote = calls.slice();
    row.sensitivityReads = sensitivityReads;
    // A FRESH mount, so what is reported is what the drawer redraws after the save and the re-read.
    // Everything above is the tree the click was taken FROM and stays that way — several tests read
    // a verdict in a second step precisely because of it — so the post-act tree gets its own keys
    // rather than quietly replacing them.
    // A second act without reopening the drawer, which is how a person actually edits two rows —
    // and the only way to observe what one row's save does to another row's note, since reopening
    // clears every note by design.
    if (step.also) {
      failSave = !!step.also.failSave;
      const [which2, [slot2, value2]] = step.also.set
        ? ['assign', step.also.set] : ['effort', step.also.setEffort];
      const id2 = which2 === 'assign' ? `assign-${slot2}` : `effort-${slot2}`;
      const second = selects(mount()).find((s) => s.p.id === id2);
      if (!second) throw new Error(`no ${which2} control for slot ${slot2}`);
      second.p.onChange(value2);
      await settle();
      failSave = false;
    }
    const after = mount();
    row.after = selects(after).find((s) => s.p.id === `assign-${slot}`).p.value;
    // `null` where the control is gone, which is a different answer from "back to the default" and
    // the one a model that accepts no level at all produces.
    const effortSelect = selects(after).find((s) => s.p.id === `effort-${slot}`);
    row.afterEffort = effortSelect ? effortSelect.p.value : null;
    row.afterProblems = text(after, 'sw-assignment-problem');
    row.afterDetails = text(after, 'sw-assignment-detail');
    row.afterEffortNotes = text(after, 'sw-assignment-effort-note');
  }
  report.push(row);
}
console.log(JSON.stringify(report));
