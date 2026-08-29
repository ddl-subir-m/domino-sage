// Reloads a Build conversation that arrived through a handoff, and reports the plan card (#60).
//
// The receipt is only worth recording if the card reads it, so this drives the real path a person
// takes to get back there — `store.loadBuild()` re-reads the conversation's transcript from the
// server — rather than hand-building the block the card renders. The server rows under test are
// the ones `_confirm_handoff`, `_recross_handoff` and `cancel_plan` append.
//
// `press` is a `|`-separated script of things to do to the card: a button by its label, a checkbox
// by its name, or the Change sheet's own confirm by its label. Every press redraws from cell zero,
// like a real render, so what the LAST draw shows is what gets reported.
//
// The Change sheet is an element in the card's tree, so its component is called here rather than
// by `createElement`. It is called on every draw, in the same order, so its `useState` cells stay
// the cells it had on the draw before — the same reason the card itself is drawn twice.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { history, press = '' } = JSON.parse(fs.readFileSync(0, 'utf8'));

const CONVERSATION = 'conv_first';

const json = (body) => ({
  ok: true, status: 200,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

function serve(url) {
  const path = String(url).replace(/^\.\/api/, '');
  if (path.startsWith('/project/history')) return json({ history });
  // The app the seed below says is selected, because an empty list here made `loadAppList` put the
  // selection down before anything under test could — which hid #95's recross bug rather than
  // proving its absence.
  if (path.startsWith('/apps')) return json({ items: [{ id: 'app_a', name: 'Desk exposure', selected: true }] });
  if (path.startsWith('/bindings')) return json({ bindings: [] });
  return json({});
}

// Every write the card made, in order, as the server would have seen it. Undo has to go through
// the cancel route that already archives plans rather than a route of its own, and Change has to
// carry the answers and nothing else — both are claims about what was POSTed.
const posts = [];

let cells = [];
let cursor = 0;

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
  React: {
    createElement: (t, p, ...c) => ({ t, p, c }),
    useState: (init) => {
      const i = cursor;
      cursor += 1;
      if (!(i in cells)) cells[i] = typeof init === 'function' ? init() : init;
      return [cells[i], (v) => { cells[i] = typeof v === 'function' ? v(cells[i]) : v; }];
    },
    useEffect: () => {}, useRef: () => ({ current: null }), Fragment: 'Fragment',
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
  fetch: async (url, options = {}) => {
    await new Promise((r) => setTimeout(r, 0));
    if (String(options.method || 'GET').toUpperCase() === 'POST') {
      let body = null;
      try { body = options.body ? JSON.parse(options.body) : null; } catch (err) { body = null; }
      posts.push({ path: String(url).replace(/^\.\/api/, ''), body });
    }
    return serve(url);
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
// `handoff.js` comes along with the card: since #60 the card mounts the Change sheet, which lives
// there beside the confirm sheet it is the sequel to. `prefs.js` comes along with the store, and
// the sheet writes through it when the person asks to keep the answers.
for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js',
                 'components/handoff.js', 'components/message-blocks.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

// Which preferences the sheet wrote, if any. Redoing a crossing must not move a preference on its
// own — the answer is about this handoff until the person says otherwise.
const prefs = {};
const setPref = SW.prefs.set;
SW.prefs.set = (name, value) => {
  prefs[name] = value;
  return setPref.call(SW.prefs, name, value);
};

function* walk(node) {
  if (!node || typeof node !== 'object') return;
  if (Array.isArray(node)) { for (const c of node) yield* walk(c); return; }
  yield node;
  yield* walk(node.c);
}
const all = (tree, pred) => [...walk(tree)].filter(pred);
const strings = (node) => [...walk(node)].flatMap((n) => (n.c || []).flat(Infinity))
  .filter((c) => typeof c === 'string');
const buttons = (tree) => all(tree, (n) => n.t === 'Button')
  .map((n) => (n.c || []).flat(Infinity).filter((c) => typeof c === 'string').join(''));
const named = (t) => (typeof t === 'function' ? t.name : String(t));
const checkboxes = (tree) => all(tree, (n) => named(n.t) === 'Checkbox' && n.p && n.p.name);

async function settle() {
  for (let i = 0; i < 40; i += 1) await new Promise((r) => setTimeout(r, 0));
}

SW.store.set({
  thread: { id: CONVERSATION, title: 'The desk talk', artifacts: [] },
  scope: { id: 'proj', name: 'Demo Project' },
  threads: [],
  activeApp: { id: 'app_a' },
});

await SW.store.loadBuild();
await settle();

const block = SW.store.get().buildMessages
  .flatMap((m) => m.blocks || [])
  .find((b) => b.type === 'build_plan');

const props = { block };
const Card = SW.MessageBlock(props).t;

function draw() {
  cursor = 0;
  const tree = Card(props);
  const mounted = [...walk(tree)].find((n) => n.t === SW.CrossingSheet);
  const sheet = mounted ? mounted.t(mounted.p) : null;
  return { tree, sheet };
}

// A button by its label, a checkbox by its name, the sheet's confirm by the word on it, or
// `close` for the sheet's own dismiss — which is not a button, it is the modal's.
function act(step, tree, sheet) {
  if (step === 'close') {
    const modal = all(sheet, (n) => named(n.t) === 'Modal')[0];
    if (modal && modal.p.onCancel) return modal.p.onCancel();
    throw new Error('there is no sheet open to close');
  }
  for (const scope of [tree, sheet]) {
    if (!scope) continue;
    const button = all(scope, (n) => n.t === 'Button'
      && (n.c || []).flat(Infinity).includes(step))[0];
    if (button && button.p.onClick) return button.p.onClick();
    const box = checkboxes(scope).find((n) => n.p.name === step);
    if (box) return box.p.onChange({ target: { checked: !box.p.checked } });
    const modal = all(scope, (n) => named(n.t) === 'Modal' && n.p && n.p.okText === step)[0];
    if (modal && modal.p.onOk) return modal.p.onOk();
  }
  throw new Error(`nothing on the card answers to "${step}"`);
}

draw();                                   // one throwaway draw, so state is a second render's
for (const step of press.split('|').filter(Boolean)) {
  const { tree, sheet } = draw();
  await act(step, tree, sheet);
  await settle();
}
const { tree, sheet } = draw();

const boxes = sheet ? checkboxes(sheet) : [];
const fields = boxes.filter((n) => n.p.name !== 'remember');
const remember = boxes.find((n) => n.p.name === 'remember');

console.log(JSON.stringify({
  buttons: buttons(tree),
  text: strings(tree).join(' '),
  posted: posts.map((p) => p.path),
  cancelled: posts.filter((p) => p.path === '/project/plan/cancel').map((p) => p.body),
  recrossed: posts.filter((p) => p.path.endsWith('/handoff/recross')).map((p) => p.body),
  // The app the rail is highlighting, after everything the press set off has settled. A recross
  // refreshes what the app ships, and refreshing that must not put the selection down.
  activeApp: (SW.store.get().activeApp || {}).id || null,
  prefs,
  sheet: {
    open: Boolean(sheet),
    fields: fields.map((n) => n.p.name),
    values: Object.fromEntries(fields.map((n) => [n.p.name, Boolean(n.p.checked)])),
    remember: Boolean(remember && remember.p.checked),
    // Anything that could pick a Built App. The target is settled once, on the confirm sheet, and
    // never remembered (ADR-0008) — so an empty list here is the criterion.
    choosers: all(sheet, (n) => /^(Radio|Select)/.test(named(n.t))).map((n) => named(n.t)),
    text: strings(sheet).join(' '),
  },
}));
