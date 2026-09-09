// What a chart Artifact card SAYS, given the block `blocksForArtifacts` pushes for a PNG.
//
// Sibling of `table_empty_card_harness.mjs`. The screenshot was a broken-image icon over the alt
// text; `ImageBlock` now has a floor for that, and this is the claim about which sentences land
// on screen. `failed: true` fires the img `onError` the browser would.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { block, failed } = JSON.parse(fs.readFileSync(0, 'utf8'));

const states = [];
let hook = 0;
const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, Infinity, encodeURIComponent, decodeURIComponent,
  setTimeout, clearTimeout, setInterval: () => 1, clearInterval: () => {},
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  location: { hash: '' },
  addEventListener() {}, removeEventListener() {},
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    useState: (init) => {
      const i = hook++;
      if (states[i] === undefined) states[i] = typeof init === 'function' ? init() : init;
      return [states[i], (v) => { states[i] = typeof v === 'function' ? v(states[i]) : v; }];
    },
    useEffect: () => {},
    useMemo: (fn) => fn(),
    useRef: () => ({ current: null }),
    Fragment: 'Fragment',
  },
  antd: {
    Input: Object.assign(function Input() {}, { TextArea: 'Input.TextArea' }),
    Button: 'Button', Table: 'Table', Tooltip: 'Tooltip', Tag: 'Tag', Space: 'Space',
    Modal: Object.assign(function Modal() {}, { confirm() {} }),
    message: { success() {}, error() {}, info() {}, warning() {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async () => ({ ok: true, status: 200, headers: { get: () => 'application/json' },
                        json: async () => ({}), text: async () => '' }),
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'api.js', 'prefs.js', 'store.js', 'components/message-blocks.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

function* walk(node) {
  if (!node || typeof node !== 'object') return;
  if (Array.isArray(node)) { for (const c of node) yield* walk(c); return; }
  yield node;
  yield* walk(node.c);
}

const routed = SW.MessageBlock({ block });
hook = 0;
let tree = routed && typeof routed.t === 'function' ? routed.t(routed.p) : routed;
if (failed) {
  const img = [...walk(tree)].find((n) => n.t === 'img');
  if (img && img.p && img.p.onError) img.p.onError();
  hook = 0;
  tree = routed.t(routed.p);
}
const nodes = [...walk(tree)];
const saidBy = (cls) => nodes.filter((n) => (n.p || {}).className === cls)
  .map((n) => (n.c || []).flat(Infinity).filter((c) => typeof c === 'string').join(''))
  .filter(Boolean);
const img = nodes.find((n) => n.t === 'img');

console.log(JSON.stringify({
  title: saidBy('sw-block-title')[0] || null,
  said: saidBy('sw-block-sub'),
  links: nodes.filter((n) => n.t === 'a').map((n) => (n.p || {}).href).filter(Boolean),
  img: img ? (img.p || {}).src || null : null,
}));
