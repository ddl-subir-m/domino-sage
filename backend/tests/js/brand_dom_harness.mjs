// Every word the shell's own chrome draws, given the pack GET /api/brand returned (#124).
//
// The paranoid pack needs the rendered DOM rather than the source: `SW.brand.text('{gallery}')`
// reads clean in the file and leaks the moment the token is one the pack does not carry. So this
// renders SW.Shell in every mode, calls the function components it finds on the way down, and
// prints every string that ends up in the tree.
//
// Nothing is mounted. `createElement` is stubbed to a plain object, so a component call returns
// tree data; mounting would test antd rather than the words.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const spec = JSON.parse(fs.readFileSync(0, 'utf8'));

// Props that hold an identifier rather than prose. ADR-0014: a path, a route and a class name are
// not renamed by the overlay, so reading them here would report a leak that is not one.
const IDENTIFIER_PROPS = new Set([
  't', 'className', 'key', 'src', 'href', 'id', 'type', 'role', 'path', 'style', 'icon',
  'htmlType', 'placement', 'trigger', 'target', 'rel', 'name', 'mode',
]);

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
    getElementById: () => null,
  },
  location: { search: '', pathname: '/', href: 'http://localhost/', hash: '' },
  addEventListener: () => {},
  removeEventListener: () => {},
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
    useEffect: () => {},
    useMemo: (fn) => fn(),
    useCallback: (fn) => fn,
    useRef: () => ({ current: null }),
    Fragment: 'Fragment',
  },
  // Every antd component stands in for itself by name, so a control the shell reaches for is never
  // the reason a word goes unread. `message` is real, because a toast is a sentence a person reads.
  antd: new Proxy({
    Modal: {},
    message: {
      info: (t) => said.push(String(t)), success: (t) => said.push(String(t)),
      error: (t) => said.push(String(t)), warning: (t) => said.push(String(t)),
    },
  }, {
    get: (target, name) => (name in target ? target[name] : String(name)),
    has: () => true,
  }),
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  EventSource: function () {},
  SW: {},
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['store.js', 'prefs.js', 'components/shell.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

// No pack means GET /api/brand has not answered yet, which is a state a person can see.
if (spec.pack) SW.store.set({ brand: spec.pack });

// Call every function component on the way down, so a word that only exists inside TopNav or a
// mode tab is still read. A component that needs state this harness does not fake is left as the
// node it was: its own words are then unread, which is a gap the coverage list names rather than
// a pass this pretends to.
const unrendered = new Set();
function render(node, depth) {
  if (!node || typeof node !== 'object') return node;
  if (Array.isArray(node)) return node.map((child) => render(child, depth));
  if (typeof node.t === 'function') {
    if (depth > 8) return node;
    try {
      return render(node.t(Object.assign({}, node.p, { children: node.c })), depth + 1);
    } catch (e) {
      unrendered.add(node.t.name || 'anonymous');
      return { t: node.t.name, p: node.p, c: [] };
    }
  }
  return { t: node.t, p: node.p, c: (node.c || []).map((child) => render(child, depth + 1)) };
}

const words = new Set();
function collect(value, key) {
  if (typeof value === 'string') {
    if (!IDENTIFIER_PROPS.has(key)) words.add(value);
    return;
  }
  if (!value || typeof value !== 'object') return;
  if (Array.isArray(value)) return value.forEach((child) => collect(child, key));
  for (const k of Object.keys(value)) {
    if (typeof value[k] === 'function') continue;
    collect(value[k], k);
  }
}

const trees = [];
for (const mode of spec.modes || []) {
  const tree = render(SW.Shell({ mode, route: { mode }, children: null }), 0);
  trees.push(tree);
  collect(tree, null);
}

// Clicking a peer product says a sentence and does nothing else, so the toast is the only place
// that sentence lands. It is driven rather than read, because it is built inside the handler.
(function drive(node) {
  if (!node || typeof node !== 'object') return;
  if (Array.isArray(node)) return node.forEach(drive);
  const menu = node.p && node.p.menu;
  if (menu && Array.isArray(menu.items) && typeof menu.onClick === 'function') {
    for (const item of menu.items) {
      try { menu.onClick({ key: item.key }); } catch (e) { /* not every menu acts here */ }
    }
  }
  (node.c || []).forEach(drive);
})(trees);
collect(said, null);

console.log(JSON.stringify({
  words: [...words],
  title: sandbox.document.title,
  unrendered: [...unrendered],
}));
