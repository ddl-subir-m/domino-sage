// Which control the top bar draws for the product, given a pack (#115).
//
// A switcher with one item is not a switcher — it offers a choice that does not exist — so the
// claim here is about WHICH CONTROL EXISTS, a dropdown trigger or a plain label, and about the
// labels inside it. None of that can be read off the source: the list that decides it arrives from
// GET /api/brand, after the shell has already painted.
//
// Nothing is mounted. `createElement` is stubbed to a plain object, so calling the component
// returns tree data; mounting would test antd rather than the branch under test.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const spec = JSON.parse(fs.readFileSync(0, 'utf8'));

// Every toast, in order. Clicking a peer says something and does nothing else, so the sentence is
// the only place that decision lands.
const said = [];

const sandbox = {
  console, JSON, Object, String, Array, Error, Map, Set, Promise, Date, Math, Number, Boolean,
  RegExp, encodeURIComponent, decodeURIComponent, setTimeout, clearTimeout, setInterval,
  clearInterval, URLSearchParams,
  fetch: () => Promise.reject(new Error('the harness makes no requests')),
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  document: {
    title: '',
    documentElement: { style: { setProperty: () => {} } },
    addEventListener: () => {},
    removeEventListener: () => {},
  },
  location: { search: '', pathname: '/', href: 'http://localhost/', hash: '' },
  addEventListener: () => {},
  removeEventListener: () => {},
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
    useEffect: () => {},
    useMemo: (fn) => fn(),
    useRef: () => ({ current: null }),
    Fragment: 'Fragment',
  },
  antd: {
    Tooltip: 'Tooltip', Dropdown: 'Dropdown', Button: 'Button', Space: 'Space', Tag: 'Tag',
    Modal: {},
    message: {
      info: (t) => said.push(String(t)), success: (t) => said.push(String(t)),
      error: (t) => said.push(String(t)), warning: (t) => said.push(String(t)),
    },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  EventSource: function () {},
  SW: {},
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
// `util.js` before the shell, because a tooltip's shortcut label is written in Mac notation and
// translated to the reader's own keys on the way out (SW.util.shortcut).
for (const f of ['util.js', 'store.js', 'components/shell.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

// No pack means GET /api/brand has not answered yet, which is a state a person can see.
if (spec.pack) SW.store.set({ brand: spec.pack });

// TopNav is private to the shell's module, so it is reached the way the Workbench reaches it:
// through SW.Shell, whose first child it is.
const shell = SW.Shell({ mode: 'chat', route: { mode: 'chat' }, children: null });
const nav = shell.c.find((n) => n && typeof n.t === 'function' && n.t.name === 'TopNav');
const tree = nav.t(nav.p);

// Every element, each remembering what drew it — the parent is what says whether the product
// control is wrapped in a switcher or standing on its own.
const nodes = [];
(function walk(node, parent) {
  if (!node || typeof node !== 'object') return;
  if (Array.isArray(node)) return node.forEach((child) => walk(child, parent));
  nodes.push({ node, parent });
  (node.c || []).forEach((child) => walk(child, node));
})(tree, null);

const hit = nodes.find(({ node }) => node.p && node.p.className === 'sw-topnav-product');
const menu = hit && hit.parent && hit.parent.t === 'Dropdown' ? hit.parent.p.menu : null;

if (spec.click && menu) menu.onClick({ key: spec.click });

console.log(JSON.stringify({
  control: hit ? hit.node.t : null,
  label: hit ? (hit.node.c.find((child) => typeof child === 'string') || null) : null,
  switcher: !!menu,
  items: menu ? menu.items.map((item) => item.label) : null,
  said,
}));
