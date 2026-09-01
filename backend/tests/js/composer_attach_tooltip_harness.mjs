// What the attach button's Tooltip says its `open` is, at rest, on hover, and with its menu up.
//
// The "+" button carries both a Tooltip and the attach Dropdown, and the click that opens the menu
// happens under the pointer that is already holding the hint open. Ant closes a Tooltip on
// mouseleave, and there is no mouseleave — so an uncontrolled Tooltip stays up and covers the menu
// it was pointing at. Only a controlled `open` can say "not while the menu is up", which is the
// same fix the Send button already carries for the same reason.
//
// `useState` here is a real cell, not the read-once stub the context harness uses, because the
// claim is about what the SECOND render draws after a handler has run: hovering and opening the
// menu are both handler calls, and a component that only settled this on the first pass has not
// made it. Nothing is mounted — the assertion is on the props of the tree the component returns.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const ATTACH = 'Attach a file or resource';

const json = (body, status = 200) => ({
  ok: status < 400, status,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

// One cell per useState call, in call order, kept across renders — the same shape the feedback
// harness uses. Remounting clears them, which is what a real unmount does.
let cells = [];
let cursor = 0;

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, setTimeout, clearTimeout,
  setInterval: () => 1, clearInterval: () => {},
  requestAnimationFrame: (fn) => fn(),
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    useState: (init) => {
      const i = cursor;
      cursor += 1;
      if (!(i in cells)) cells[i] = typeof init === 'function' ? init() : init;
      return [cells[i], (v) => { cells[i] = typeof v === 'function' ? v(cells[i]) : v; }];
    },
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
  fetch: async (url) => (String(url).endsWith('/context') ? json({ items: [] }) : json({})),
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

SW.store.set({
  thread: { id: 'conv_1', title: 'A conversation', artifacts: [] },
  scope: { id: 'proj', name: 'Demo Project' },
  messages: [], resourceGroups: {},
});

const PROPS = { onSend() {}, placeholder: 'Describe a change…', disabled: false, showMode: true };
const mount = () => { cursor = 0; return SW.Composer(PROPS); };

// The Tooltip by the words a person reads, and the Dropdown by the fact that it holds that
// Tooltip — so neither is found by position, and moving the button does not turn this into an
// assertion about nothing.
const tooltip = (tree) => {
  const node = find(tree, (n) => n.t === 'Tooltip' && n.p.title === ATTACH);
  if (!node) throw new Error(`the composer no longer draws a Tooltip titled ${ATTACH}`);
  return node;
};
const attachDropdown = (tree) => {
  const node = find(tree, (n) => n.t === 'Dropdown' && [...walk(n.c)]
    .some((x) => x.t === 'Tooltip' && x.p.title === ATTACH));
  if (!node) throw new Error('the attach Tooltip is no longer inside a Dropdown');
  return node;
};

// `open` is read as a tri-state on purpose: `undefined` is an UNCONTROLLED Tooltip, which is the
// bug, and reporting it as `false` would let the fix look like it was already there.
const openOf = (node) => (node.p.open === undefined ? 'uncontrolled' : node.p.open);

const report = { resting: openOf(tooltip(mount())) };

// The pointer arrives on the button. Ant tells a controlled Tooltip through `onOpenChange`.
const hover = tooltip(mount());
if (typeof hover.p.onOpenChange !== 'function') {
  throw new Error('the attach Tooltip has no onOpenChange, so hover can never open it');
}
hover.p.onOpenChange(true);
report.hovered = openOf(tooltip(mount()));

// The click lands while the pointer is still there. The menu opens; no mouseleave ever comes.
const menu = attachDropdown(mount());
if (typeof menu.p.onOpenChange !== 'function') {
  throw new Error('the attach Dropdown is uncontrolled, so nothing can know its menu is up');
}
menu.p.onOpenChange(true);
report.menuOpen = openOf(tooltip(mount()));
report.menuOpenDropdown = attachDropdown(mount()).p.open;

// The menu closes with the pointer still on the button — by Escape, or by a second click on the
// button itself. Still no mouseleave, so a hint left armed pops straight back up over the spot the
// menu just left.
attachDropdown(mount()).p.onOpenChange(false);
report.afterClose = openOf(tooltip(mount()));

// A fresh pointer arrival, which is the other half of the ask: the hint is suppressed by a menu,
// not killed by one. A fix that simply stopped opening the tooltip would fail here.
tooltip(mount()).p.onOpenChange(true);
report.rehovered = openOf(tooltip(mount()));

console.log(JSON.stringify(report));
