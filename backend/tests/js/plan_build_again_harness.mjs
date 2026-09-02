// Mounts the real plan page and reports the "Build this again" offer it draws (#149, ADR-0024).
//
// Three things about that offer can only be seen by rendering it: whether it appears at all, what
// it says when it is disabled, and what it warns it will destroy before you press it. All three are
// driven by the eligibility the server sends on the document, so the harness takes that verbatim
// from the caller and reports what the page did with it.
//
// The click is reported as the store call it makes and the ORDER of the calls that call makes,
// because the sequence is the load-bearing part: the route is named off the conversation the store
// has open, so opening it late names the wrong one.
//
// `useEffect` runs for real, like the sibling back-links harness: the page holds no plan at all
// until its load effect has run, so a no-op stub would only ever report the skeleton.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { plan, variant = 'page', mode = 'plan', thread = null } = JSON.parse(fs.readFileSync(0, 'utf8'));

const json = (body) => ({
  ok: true, status: 200,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

// What the click did, in order. `selectApp` before `openThread` before the route before the turn is
// the whole contract of `store.buildPlanAgain`.
const acted = [];

let cells = [];
let cursor = 0;
let effects = [];

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, setTimeout, clearTimeout, setInterval, clearInterval,
  requestAnimationFrame: (fn) => fn(),
  localStorage: (() => {
    const backing = new Map();
    return {
      getItem: (k) => (backing.has(k) ? backing.get(k) : null),
      setItem: (k, v) => backing.set(k, String(v)),
      removeItem: (k) => backing.delete(k),
    };
  })(),
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  // Enough window for `router.js` to load; its `SW.router` is replaced below.
  location: { hash: '' },
  history: { replaceState() {} },
  addEventListener() {}, removeEventListener() {},
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
    const path = String(url).replace(/^\.\/api/, '');
    return json(path.endsWith(`/plans/${plan.id}`) ? plan : {});
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js', 'components/conversation-list.js',
                 'router.js', 'components/plan.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

SW.router = {
  go: (path) => acted.push(`route ${path}`),
  get: () => ({ mode, a: null, b: null, query: {} }),
};
// The two store calls `buildPlanAgain` makes before it starts the turn, and the turn itself. Stubbed
// rather than served over the stubbed fetch, because what is under test here is the order and the
// payload — the turn's own behaviour is `test_build_this_again.py`'s job.
SW.store.selectApp = async (app) => { acted.push(`select ${app.id}`); };
SW.store.openThread = async (id) => { acted.push(`open ${id}`); };
SW.store.approveBuild = async (answers, edits, planId, options) => {
  acted.push(`approve ${planId} again=${!!(options || {}).buildAgain} edits=${JSON.stringify(edits)}`);
};

function* walk(node) {
  if (!node || typeof node !== 'object') return;
  if (Array.isArray(node)) { for (const c of node) yield* walk(c); return; }
  yield node;
  yield* walk(node.c);
}
const all = (tree, pred) => [...walk(tree)].filter(pred);
const labelled = (tree, label) =>
  all(tree, (n) => n.t === 'Button' && (n.c || []).flat(Infinity).includes(label))[0];
const strings = (node) => [...walk(node)].flatMap((n) => (n.c || []).flat(Infinity))
  .filter((c) => typeof c === 'string');

// The tooltip and the caption both hang off the button's own block, so they are read from there
// rather than from the whole page: the page has other tooltips and other captions.
function offerBlock(tree) {
  return all(tree, (n) => n.p && n.p.className === 'sw-plan-again')[0] || null;
}

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
  thread,
  activeApp: { id: 'app_open' },
});

const props = { planId: plan.id, variant, onClose() {} };
// First mount fetches, second renders what came back.
await mount(props);
const tree = await mount(props);

const again = labelled(tree, 'Build this again');
if (again && again.p.onClick) {
  again.p.onClick();
  await settle();
}
const block = offerBlock(tree);
const tip = block ? all(block, (n) => n.t === 'Tooltip')[0] : null;
const caption = block ? all(block, (n) => n.p && n.p.className === 'sw-caption')[0] : null;

console.log(JSON.stringify({
  offered: Boolean(again),
  disabled: Boolean(again && again.p.disabled),
  tooltip: (tip && tip.p && tip.p.title) || null,
  caption: caption ? strings(caption).join('') : null,
  builder: Boolean(labelled(tree, 'Open in Builder')),
  acted,
}));
