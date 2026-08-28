// Mounts the real plan page and reports the ways back it offers.
//
// A plan has two ends to link back to and they are independent (#54): the Conversation that
// produced it, and the Built App it stands in. A plan may carry either, both, or neither, so the
// page is driven here once per shape and what it drew is what gets asserted.
//
// `useEffect` runs for real, unlike the sibling feedback harness: the page has no plan at all
// until its load effect has run, so a no-op stub would only ever report a skeleton.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
// `variant` and `mode` are not free of each other in the real app — the page is only ever mounted
// on `#/plan/<id>`, and the sheet only from inside Chat or Build — so the caller passes the pair
// and the tests only ever pass one the app can produce. `thread` is the conversation already open
// behind the sheet, which the routes have to carry rather than drop.
const { plan, mode, variant = 'page', thread = null } = JSON.parse(fs.readFileSync(0, 'utf8'));

const json = (body) => ({
  ok: true, status: 200,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

// Where SW.router.go was sent. The back-links are routes, so this IS what the person gets.
const routed = [];

let cells = [];
let cursor = 0;
let effects = [];

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, setTimeout, clearTimeout, setInterval, clearInterval,
  requestAnimationFrame: (fn) => fn(),
  // `openThread` asks the viewer which conversation view they are in (#56), so the real prefs.js
  // comes along with the store. An in-memory backing map: this harness is not about what persists.
  localStorage: (() => {
    const backing = new Map();
    return {
      getItem: (k) => (backing.has(k) ? backing.get(k) : null),
      setItem: (k, v) => backing.set(k, String(v)),
      removeItem: (k) => backing.delete(k),
    };
  })(),
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  React: {
    createElement: (t, p, ...c) => ({ t, p, c }),
    useState: (init) => {
      const i = cursor;
      cursor += 1;
      if (!(i in cells)) cells[i] = typeof init === 'function' ? init() : init;
      return [cells[i], (v) => { cells[i] = typeof v === 'function' ? v(cells[i]) : v; }];
    },
    useEffect: (fn) => { effects.push(fn); },
    useRef: () => ({ current: null }),
    Fragment: 'Fragment',
  },
  antd: {
    Input: Object.assign(function Input() {}, { TextArea: 'Input.TextArea' }),
    Button: 'Button', Dropdown: 'Dropdown', Tag: 'Tag', Tooltip: 'Tooltip', Space: 'Space',
    Modal: Object.assign(function Modal() {}, { confirm() {} }),
    Checkbox: Object.assign(function Checkbox() {}, { Group: 'Checkbox.Group' }),
    Radio: Object.assign(function Radio() {}, { Group: 'Radio.Group', Button: 'Radio.Button' }),
    Select: 'Select', Alert: 'Alert', Avatar: 'Avatar', Divider: 'Divider',
    Skeleton: 'Skeleton', Segmented: 'Segmented',
    message: { success() {}, info() {}, warning() {}, error() {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async (url) => {
    await new Promise((r) => setTimeout(r, 0));
    return json(String(url).endsWith(`/plans/${plan.id}`) ? plan : {});
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js', 'components/conversation-list.js',
                 'components/app-list.js', 'components/plan.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

// The rail's route grammar is real (conversation-list.js above); only the hash it would be written
// into is stubbed, so what a click asks for is readable.
SW.router = { go: (path) => routed.push(path), get: () => ({ mode, a: null, b: null, query: {} }) };

function* walk(node) {
  if (!node || typeof node !== 'object') return;
  if (Array.isArray(node)) { for (const c of node) yield* walk(c); return; }
  yield node;
  yield* walk(node.c);
}
const all = (tree, pred) => [...walk(tree)].filter(pred);
const labelled = (tree, label) =>
  all(tree, (n) => n.t === 'Button' && (n.c || []).flat(Infinity).includes(label))[0];
const tooltips = (tree) =>
  all(tree, (n) => n.t === 'Tooltip' && n.p && typeof n.p.title === 'string').map((n) => n.p.title);

async function settle() {
  for (let i = 0; i < 40; i += 1) await new Promise((r) => setTimeout(r, 0));
}

async function mount(props) {
  cursor = 0;
  effects = [];
  const tree = SW.PlanDoc(props);
  effects.forEach((fn) => fn());
  await settle();
  return tree;
}

SW.store.set({
  scope: { id: 'proj', name: 'Demo Project' },
  userIndex: { 'u-me': { id: 'u-me', name: 'Me' } },
  me: { id: 'u-me' },
  members: [],
  // The conversation and the app Build already has open. Both route grammars read them, and the
  // point of going through those grammars is that a click does not silently drop either.
  thread,
  activeApp: { id: 'app_open' },
});

const props = { planId: plan.id, variant, onClose() {} };
// First mount fetches, second renders what came back.
await mount(props);
const tree = await mount(props);

const conversation = labelled(tree, 'From this conversation');
const builder = labelled(tree, 'Open in Builder');
const build = labelled(tree, 'Build this');
[conversation, builder, build].forEach((b) => b && b.p.onClick && b.p.onClick());

console.log(JSON.stringify({
  offers: [conversation && 'conversation', builder && 'builder', build && 'build'].filter(Boolean),
  routed,
  buildDisabled: Boolean(build && build.p.disabled),
  tooltips: tooltips(tree),
}));
