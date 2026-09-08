// What the profile menu on the top right offers, and where Log out actually goes.
//
// The menu is built inside TopNav, which is private to the shell, so the claim cannot be read off
// the source: a stub that toasted "open the platform" and a click that leaves for `/logout` look
// the same in the file. The tree is what a person gets, and the navigation is what makes Log out
// real rather than a sentence.
//
// Nothing is mounted. `createElement` is stubbed to a plain object, so calling the component
// returns tree data; mounting would test antd rather than the branch under test.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const spec = JSON.parse(fs.readFileSync(0, 'utf8'));

const said = [];
const gone = [];
const location = {
  search: '',
  pathname: '/',
  href: spec.href || 'https://apps.example.tech/apps/workbench/',
  hash: '',
  hostname: spec.hostname || 'apps.example.tech',
  protocol: spec.protocol || 'https:',
  assign(url) { gone.push(String(url)); },
};

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
  location,
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
for (const f of ['util.js', 'store.js', 'components/shell.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

const shell = SW.Shell({ mode: 'chat', route: { mode: 'chat' }, children: null });
const nav = shell.c.find((n) => n && typeof n.t === 'function' && n.t.name === 'TopNav');
const tree = nav.t(nav.p);

const nodes = [];
(function walk(node, parent) {
  if (!node || typeof node !== 'object') return;
  if (Array.isArray(node)) return node.forEach((child) => walk(child, parent));
  nodes.push({ node, parent });
  (node.c || []).forEach((child) => walk(child, node));
})(tree, null);

const hit = nodes.find(({ node }) => node.p && node.p.className === 'sw-topnav-user');
const menu = hit && hit.parent && hit.parent.t === 'Dropdown' ? hit.parent.p.menu : null;

if (spec.click && menu) menu.onClick({ key: spec.click });

console.log(JSON.stringify({
  labels: menu ? menu.items.filter((item) => item.type !== 'divider').map((item) => item.label) : [],
  keys: menu ? menu.items.filter((item) => item.type !== 'divider').map((item) => item.key) : [],
  gone,
  settingsOpen: Boolean(SW.store.get().settingsOpen),
  said,
}));
