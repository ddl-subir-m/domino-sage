// Drives the real store.js and the real card through a failed turn's Continue with another model
// action (#570), on Chat and on Build, and reports what was drawn and what was sent.
//
// Both halves break silently, which is why they are driven rather than read. A `done` row that
// carries `cause` (ADR-0069) reaches the transcript through a chain of `ev.type === ...` branches,
// and a row no branch reads simply disappears; a card that renders without its action is a
// sentence about a turn nobody can pick up. The click is the other half: it must send #569's
// server-owned reference and the pick, and nothing else — never a task written in the browser.
//
// Nothing is mounted. `createElement` is stubbed to a plain object, so calling the component
// returns the tree it would draw. The picker's state lives in the store (`continuePicker`), so the
// harness can open, fill and cancel it without React.
//
// Input on stdin, one JSON object:
//   history   rows of the Thread (Chat) or of the app's log (Build, when `app` is set)
//   app       null for Chat, or the rail row of the selected Built App
//   get       what GET /api/project/turn/continue answers, or "error" for a 500
//   post      what POST answers: {status: 409, body}, {status: 200, frames}, {status: 500},
//             or {throw: true} for a request that never reaches the server
//   act       "none" | "open" | "cancel" | "continue"
//   pick      {model, effort} the picker is filled with before Continue
//   aliases   the Project's `model_llm` rows (the capability source the picker reads)
//   live      frames a Chat turn streams instead of reading `history` (the live path)
//   before    the transcript rows the live turn is read over (default: one user row)
// Output on stdout: one JSON line.
import fs from 'node:fs';
import vm from 'node:vm';
import { unrefTimeout } from './sandbox_timeout.mjs';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const {
  history = [], app = null, get = null, post = null, act = 'none',
  pick = { model: 'gpt-5.4', effort: 'none' }, live = null, before = null,
} = input;
const aliases = input.aliases || [
  { id: 'llm_1', kind: 'model_llm', alias: 'gpt-5.4', name: 'GPT 5.4', capabilities: ['chat'],
    reasoning_efforts: ['none', 'low', 'high'], reasoning_efforts_with_tools: ['none'],
    serving: true },
  { id: 'llm_2', kind: 'model_llm', alias: 'embed-1', name: 'Embed', capabilities: ['embeddings'],
    reasoning_efforts_with_tools: [], serving: true },
  { id: 'llm_3', kind: 'model_llm', alias: 'claude-x', name: 'Claude X', capabilities: ['chat'],
    reasoning_efforts_with_tools: [], serving: false },
];

const THREAD = { id: 'thr_1', title: 'A failed question', artifacts: [], handoff: null };
const CONVERSATION = 'thr_1';

const json = (body, status = 200) => ({
  ok: status < 400, status,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

// A turn stream: every frame in one read, then EOF, the way the ceiling harness streams.
const stream = (frames, turnId = 'turn_new') => {
  const text = frames.map((f) => `data: ${JSON.stringify(f)}\n\n`).join('');
  let sent = false;
  const header = { 'X-Sage-Turn-Id': turnId, 'X-Sage-Turn-State': 'running',
                   'X-Sage-Turn-Sequence': '7', 'X-Sage-Turn-Epoch': 'e1',
                   'content-type': 'text/event-stream' };
  return { ok: true, status: 200,
    headers: { get: (k) => header[k] || null },
    json: async () => ({}),
    body: { getReader: () => ({ read: async () => (sent
      ? { done: true }
      : (sent = true, { done: false, value: new TextEncoder().encode(text) })) }) } };
};

const calls = [];
const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, TypeError, TextEncoder, TextDecoder, URL, URLSearchParams, Blob, ArrayBuffer, Uint8Array,
  Infinity,
  setTimeout: unrefTimeout, clearTimeout, setInterval: () => 1, clearInterval: () => {},
  requestAnimationFrame: (fn) => fn(),
  localStorage: (() => {
    const backing = new Map();
    return {
      getItem: (k) => (backing.has(k) ? backing.get(k) : null),
      setItem: (k, v) => backing.set(k, String(v)),
      removeItem: (k) => backing.delete(k),
    };
  })(),
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null,
              body: {}, visibilityState: 'visible' },
  location: { hash: '' },
  history: { replaceState() {} },
  addEventListener() {}, removeEventListener() {},
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
    useEffect: () => {}, useMemo: (fn) => fn(), useRef: () => ({ current: null }),
    Fragment: 'Fragment',
  },
  // Named, not stubbed away: the rendered tree says what a node IS, and a `Button` somebody can
  // press is the difference from a `span` they can only read.
  antd: {
    message: { success() {}, info() {}, warning() {}, error() {} },
    Modal: { confirm() {} },
    Button: 'Button', Table: 'Table', Tooltip: 'Tooltip', Tag: 'Tag', Space: 'Space',
    Input: 'Input', Spin: 'Spin', Alert: 'Alert', Collapse: 'Collapse', Popconfirm: 'Popconfirm',
    Select: 'Select', Dropdown: 'Dropdown',
  },
  icons: new Proxy({}, { get: (_t, name) => String(name) }),
  fetch: async (url, opts = {}) => {
    const path = String(url).replace(/^\.\/api/, '');
    const method = opts.method || 'GET';
    calls.push({ url: String(url), method, body: opts.body ? JSON.parse(opts.body) : null });
    if (path.startsWith('/threads/thr_1/chat/stream')) return stream(live || [], 'turn_live');
    if (path.startsWith('/project/turn/continue')) {
      if (method === 'GET') return get === 'error' ? json({ error: 'boom' }, 500) : json(get || {});
      if (!post || post.throw) throw new TypeError('Failed to fetch');
      if (post.status === 200) return stream(post.frames || []);
      return json(post.body || { error: 'server error' }, post.status);
    }
    await new Promise((r) => setTimeout(r, 0));
    if (path === '/threads/thr_1') {
      return json({ ...THREAD, history: app ? [] : history, context: { items: [] } });
    }
    if (path.startsWith('/threads/thr_1/context')) return json({ items: [] });
    if (path.startsWith('/threads')) return json({ threads: [] });
    if (path.startsWith('/apps')) return json({ items: app ? [{ ...app, selected: true }] : [] });
    if (path.startsWith('/bindings')) return json({ bindings: [] });
    if (path.startsWith('/project/history')) return json({ history });
    if (path.startsWith('/project/build/state')) return json({ running: false });
    if (path.startsWith('/plans')) return json({ items: [] });
    if (path.startsWith('/preview/status')) return json({ state: 'ready' });
    return json({});
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js', 'components/message-blocks.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

async function settle() {
  for (let i = 0; i < 60; i += 1) await new Promise((r) => setTimeout(r, 0));
}

// Every string, button and select the card drew, in the order they are drawn. Function
// components are stepped INTO with their props and children, the way React calls them.
function walk(node, out) {
  if (node === null || node === undefined || node === false) return out;
  if (Array.isArray(node)) { node.forEach((n) => walk(n, out)); return out; }
  if (typeof node === 'string' || typeof node === 'number') { out.text.push(String(node)); return out; }
  if (typeof node !== 'object') return out;
  if (typeof node.t === 'function') {
    return walk(node.t({ ...(node.p || {}), children: node.c }), out);
  }
  if (node.t === 'Button') {
    out.buttons.push({ label: label(node.c), disabled: !!(node.p || {}).disabled,
                       onClick: (node.p || {}).onClick });
    return out;
  }
  if (node.t === 'Select') {
    out.selects.push({ label: (node.p || {})['aria-label'] || '', value: (node.p || {}).value,
                       options: ((node.p || {}).options || []).map((o) => ({
                         value: o.value, label: String(o.label), disabled: !!o.disabled })),
                       onChange: (node.p || {}).onChange });
    return out;
  }
  if ((node.p || {})['data-reason'] !== undefined) out.reason = node.p['data-reason'];
  walk(node.c, out);
  return out;
}
function label(node) {
  if (node === null || node === undefined || node === false) return '';
  if (Array.isArray(node)) return node.map(label).join('');
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (typeof node === 'object') return label(node.c);
  return '';
}

const transcript = () => (app ? SW.store.get().buildTranscript : SW.store.get().messages);
const cards = () => transcript()
  .flatMap((m) => m.blocks || [])
  .filter((b) => b.type === 'continue_model');
const drawn = (block) => walk(SW.MessageBlock({ block }), { text: [], buttons: [], selects: [],
                                                             reason: undefined });
const said = () => transcript().map((m) => ({
  role: m.role,
  blocks: (m.blocks || []).map((b) => (b.type === 'continue_model' ? 'continue_model'
    : b.type === 'status' ? `status:${b.value}` : b.type === 'text' ? `text:${b.value}` : b.type)),
}));

SW.store.set({ scope: { id: 'proj', name: 'Demo Project' }, threads: [], attachments: [] });
SW.store.set({ me: { id: 'u1' } });
SW.prefs.set('dataAccessShown', true);

if (app) {
  SW.store.set({ activeApp: app, apps: [{ ...app, selected: true }] });
  await SW.store.openThread(CONVERSATION);
  await SW.store.loadBuild();
} else if (live) {
  // The live path: the failed turn streams its `done` into the open Thread rather than being read
  // off it. `before` is what the Thread already holds when the question goes out.
  sandbox.__before = before;
  await SW.store.openThread(CONVERSATION);
  await settle();
  await SW.store.sendMessage('summarize the file');
} else {
  await SW.store.openThread(CONVERSATION);
}
await settle();
SW.store.set({ resourceGroups: { model_llm: aliases } });

const first = () => cards()[0];
const before_act = cards().map((b) => ({ ...b, drawn: drawn(b) }));
const callsBeforeAct = calls.length;

let picker = null;
if (act !== 'none' && first()) {
  const open = drawn(first()).buttons.find((b) => b.label === 'Continue with another model');
  if (open) await open.onClick();
  await settle();
  const filled = drawn(first());
  const model = filled.selects.find((s) => s.label === 'Model');
  if (model && pick.model) model.onChange(pick.model);
  const effort = drawn(first()).selects.find((s) => s.label === 'Reasoning effort');
  if (effort && pick.effort !== undefined) effort.onChange(pick.effort);
  picker = drawn(first());
  const press = picker.buttons.find((b) => b.label === (act === 'cancel' ? 'Cancel' : 'Continue'));
  if (press && !press.disabled) await press.onClick();
  await settle();
  await settle();
}

const state = SW.store.get();
console.log(JSON.stringify({
  cards: cards().map((b) => ({ turnId: b.turnId, conversation: b.conversation, app: b.app,
                               cause: b.cause, stage: b.stage, record: !!b.record })),
  before: before_act.map((b) => ({ turnId: b.turnId, text: b.drawn.text,
                                   buttons: b.drawn.buttons.map((x) => x.label),
                                   reason: b.drawn.reason })),
  picker: picker ? { text: picker.text, buttons: picker.buttons.map((b) => ({ label: b.label, disabled: b.disabled })),
                     selects: picker.selects.map((s) => ({ label: s.label, value: s.value, options: s.options })) }
                 : null,
  after: cards().map((b) => { const d = drawn(b); return { turnId: b.turnId, text: d.text,
    buttons: d.buttons.map((x) => x.label), reason: d.reason }; }),
  offers: state.continueOffers || {},
  pickerState: state.continuePicker || null,
  calls: calls.slice(callsBeforeAct).map((c) => ({
    url: c.url.replace(/^.*\/api\//, 'api/'), method: c.method, body: c.body })),
  allRoutes: calls.map((c) => `${c.method} ${c.url.replace(/^.*\/api\//, 'api/').split('?')[0]}`),
  model: state.model, reasoningEffort: state.reasoningEffort,
  buildModel: state.buildModel, buildEffort: state.buildEffort,
  transcript: said(),
}));
