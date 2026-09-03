// Which way the side panel's two toggles actually move, given the tab it is open on (#150 follow-up).
//
// Reading the source cannot answer this. The control's caption and the control's effect are written
// in different files: `components/shell.js` draws "Hide the side panel" and `store.js` decides what
// the click does, and `toggleDock` only closes when its argument is the tab already open. So with
// the panel open on Activity, a control captioned "Hide" and a shortcut the help drawer calls a
// toggle both switched tab instead — and only firing the real handler against the real store shows
// the caption and the effect disagreeing.
//
// Input on stdin: `{ "dockTab": null | "resources" | "activity" }` — the panel's state before the
// press. `activity` is the case that was broken; the other two are the ones that always worked and
// have to keep working.
//
// Both doors are driven, because they are separately wired and #150 fixed a third one and left
// these two behind:
//
//   subnav    The chevron in the sub bar. Reached the way the Workbench reaches it, through
//             SW.Shell, and its tooltip is read out too — the caption is half the claim.
//   fold      The dock's own Hide button, which only exists while the panel is open. It was the
//             one #150 fixed, so it is the control here: it must not have moved.
//   shortcut  ⌘/, which lives in app.js and registers on the window. Its listener is captured by
//             letting the effect run, then handed a keydown the way a browser would.
//
// Nothing is mounted. `createElement` is stubbed to plain objects, so calling a component returns
// tree data; mounting would test antd rather than the wiring under test.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { dockTab } = JSON.parse(fs.readFileSync(0, 'utf8'));

// Every preference write, in order. The panel's open/closed state is remembered per viewer (#150),
// so a toggle that moved the screen without recording it is a bug this would otherwise miss.
const wrote = [];
const listeners = {};

const sandbox = {
  console, JSON, Object, String, Array, Error, Map, Set, Promise, Date, Math, Number, Boolean,
  RegExp, encodeURIComponent, decodeURIComponent, setTimeout, clearTimeout, setInterval,
  clearInterval, URLSearchParams, TextEncoder, TextDecoder, URL,
  fetch: () => Promise.reject(new Error('the harness makes no requests')),
  localStorage: {
    getItem: () => null,
    setItem: (k, v) => wrote.push(JSON.parse(v)),
    removeItem: () => {},
  },
  document: {
    title: '',
    documentElement: { style: { setProperty: () => {} } },
    addEventListener: () => {},
    removeEventListener: () => {},
    getElementById: () => ({}),
  },
  location: { search: '', pathname: '/', href: 'http://localhost/', hash: '' },
  addEventListener: (type, fn) => { listeners[type] = fn; },
  removeEventListener: () => {},
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
    // Run, rather than skip: the keydown handler under test is registered inside one, and a
    // stubbed-away effect is a shortcut that exists in the source and not in the harness.
    useEffect: (fn) => { fn(); },
    useMemo: (fn) => fn(),
    useRef: () => ({ current: null }),
    Fragment: 'Fragment',
  },
  ReactDOM: { createRoot: () => ({ render: () => {} }) },
  antd: {
    Tooltip: 'Tooltip', Dropdown: 'Dropdown', Button: 'Button', Space: 'Space', Tag: 'Tag',
    ConfigProvider: 'ConfigProvider', App: 'AntApp', Result: 'Result', Spin: 'Spin',
    Modal: {},
    message: { info: () => {}, success: () => {}, error: () => {}, warning: () => {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  EventSource: function () {},
  SW: {},
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'prefs.js', 'theme.js', 'router.js', 'store.js', 'components/shell.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

// app.js boots the whole Workbench on load. The boot read is not what this asks about, and it wants
// eight endpoints; stubbed to nothing so `Root` gets as far as registering the shortcut.
SW.store.init = async () => {};

// `Root` is private to app.js and is handed to `render` as tree data. Calling it is what the
// renderer would do, and running its effects is what registers the ⌘/ listener on the window.
const rendered = [];
sandbox.ReactDOM.createRoot = () => ({ render: (el) => rendered.push(el) });
vm.runInContext(fs.readFileSync(ROOT + 'app.js', 'utf8'), sandbox, { filename: 'app.js' });
rendered[0].t(rendered[0].p);

// SubNav and Dock are private to the shell's module, so both are reached the way the Workbench
// reaches them: through SW.Shell, whose children they are.
function part(name) {
  const shell = SW.Shell({ mode: 'chat', route: { mode: 'chat' }, children: null });
  const node = walk(shell).find(({ node: n }) => typeof n.t === 'function' && n.t.name === name);
  return node ? node.node.t(node.node.p) : null;
}

function walk(tree) {
  const nodes = [];
  (function step(node, parent) {
    if (!node || typeof node !== 'object') return;
    if (Array.isArray(node)) return node.forEach((child) => step(child, parent));
    nodes.push({ node, parent });
    (node.c || []).forEach((child) => step(child, node));
  })(tree, null);
  return nodes;
}

const byLabel = (tree, label) =>
  walk(tree).find(({ node }) => node.p && node.p['aria-label'] === label);

// One press per door, each from the same starting state, so the three answers are comparable.
function press(door) {
  SW.store.set({ dockTab, panelFilter: 'dataset' });
  wrote.length = 0;

  let caption = null;
  if (door === 'shortcut') {
    listeners.keydown({ metaKey: true, key: '/', target: { tagName: 'BODY' }, preventDefault() {} });
  } else {
    // The fold button is only drawn while the panel is open, so a closed start has none to press.
    const hit = door === 'subnav'
      ? byLabel(part('SubNav'), 'Toggle side panel')
      : byLabel(part('Dock'), 'Hide panel');
    if (!hit) return { absent: true };
    // The caption is half the claim: this bug was a control that said one thing and did another.
    caption = hit.parent && hit.parent.t === 'Tooltip' ? hit.parent.p.title : null;
    hit.node.p.onClick();
  }

  const after = SW.store.get();
  return {
    caption,
    // The whole claim: what the panel is showing once the press has landed.
    dockTab: after.dockTab,
    // A filter is a question about a list nobody is looking at once the panel shuts.
    panelFilter: after.panelFilter,
    // And the answer is remembered, which is what makes it survive a reload (#150). Copied, because
    // the next press clears the live array and every row is serialised together at the end.
    wrote: wrote.slice(),
  };
}

console.log(JSON.stringify({
  subnav: press('subnav'),
  fold: press('fold'),
  shortcut: press('shortcut'),
}));
