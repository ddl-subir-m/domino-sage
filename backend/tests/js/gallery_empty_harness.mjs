// What an empty Gallery says, given a pack (#113).
//
// The claim is about words a person reads, and ADR-0014 rules a grep over the source out: it
// cannot tell our word from a code comment, and once the word is a `{token}` there is nothing in
// the source to grep for at all. So the tree is drawn and the strings are read off it.
//
// Nothing is mounted. `createElement` is stubbed to a plain object, so calling the component
// returns tree data; mounting would test antd rather than the branch under test.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const spec = JSON.parse(fs.readFileSync(0, 'utf8'));

// GalleryMode holds one piece of state, and it is filled by a fetch this harness does not make —
// `useEffect` is a no-op here, as in every one of these harnesses. So the state is seeded instead,
// which is also how the empty case is reached: an answer that listed nothing.
const seeded = { loading: false, items: [], error: null, provisioning: true };

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
    useState: () => [seeded, () => {}],
    useEffect: () => {},
    useMemo: (fn) => fn(),
    useRef: () => ({ current: null }),
    Fragment: 'Fragment',
  },
  antd: {
    Result: 'Result', Button: 'Button', Spin: 'Spin', Tag: 'Tag',
    Empty: { PRESENTED_IMAGE_SIMPLE: 'PRESENTED_IMAGE_SIMPLE' },
    Modal: {}, message: {},
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  EventSource: function () {},
  SW: {},
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['store.js', 'modes/gallery.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}

// No pack means GET /api/brand has not answered yet, which is a state a person can see.
if (spec.pack) sandbox.SW.store.set({ brand: spec.pack });

// Every string in the tree, props included: Result carries its title and subtitle as props rather
// than as children, and those are the two lines this empty state is made of.
const said = [];
(function walk(node) {
  if (typeof node === 'string') return said.push(node);
  if (!node || typeof node !== 'object') return;
  if (Array.isArray(node)) return node.forEach(walk);
  Object.values(node.p || {}).forEach(walk);
  (node.c || []).forEach(walk);
})(sandbox.SW.GalleryMode({ appId: null }));

console.log(JSON.stringify(said));
