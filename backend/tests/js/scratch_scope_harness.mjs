// What an Upload's row SAYS about its scope, read in both modes off one real `/project` answer.
//
// The claim is about the subject of the sentence, not its tone. Chat draws no app rail, so a row
// there that names an app names one the reader cannot go find — and because a Project holds a
// Built App from birth, the app it named was always the `Unnamed Built App` placeholder, for an
// app nobody had built. Build draws that row, placeholder label and all, so there the name is a
// destination rather than a phantom.
//
// Both readings have to come off the SAME panel: the branch is one ternary, and a test that only
// mounted Chat would pass just as well if Build's name had been dropped with it.
//
// Input on stdin: `{ "mode": "chat" | "build" }`.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { mode } = JSON.parse(fs.readFileSync(0, 'utf8'));

// Never built and never renamed, which is the state the bug was reported in: the rail calls this
// row `Unnamed Built App`, and `/apps` is where that label is settled.
const APP = { id: 'app_a', name: 'Unnamed Built App', built: false, selected: true };

// An Upload: Chat's own bytes at the Project root, outside every app (`_SCRATCH_PREFIX`).
const SCRATCH = [{ path: '.sage/scratch/support_tickets.csv', name: 'support_tickets.csv', size: 11 }];

const json = (body, status = 200) => ({
  ok: status < 400,
  status,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

function serve(url) {
  const path = String(url).replace(/^\.\/api/, '').split('?')[0];
  if (path === '/project') return json({ attached: [], scratch: SCRATCH });
  if (path === '/project/resources') return json({ items: [] });
  if (path === '/apps') return json({ items: [APP] });
  if (path === '/bindings') return json({ bindings: [] });
  if (path === '/members') return json({ members: [], directory: [] });
  if (path === '/plans') return json({ items: [] });
  return json({ items: [] });
}

const sandbox = {
  console,
  JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp, Error,
  Blob, ArrayBuffer, Uint8Array, TextDecoder,
  setTimeout, clearTimeout,
  setInterval: () => 1,
  clearInterval: () => {},
  requestAnimationFrame: (fn) => fn(),
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  // The mode under test. The panel asks the router for it, so this is the one line that separates
  // the two runs.
  location: { hash: mode === 'build' ? '#/build' : '#/chat' },
  history: { replaceState() {} },
  addEventListener() {},
  removeEventListener() {},
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    // One pass settles every claim here, so a constant and a no-op setter are enough.
    useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
    useEffect: () => {},
    useMemo: (fn) => fn(),
    useRef: () => ({ current: null }),
    Fragment: 'Fragment',
  },
  antd: {
    Input: Object.assign(function Input() {}, { TextArea: 'Input.TextArea' }),
    Button: 'Button', Dropdown: 'Dropdown', Tag: 'Tag', Tooltip: 'Tooltip', Space: 'Space',
    Drawer: 'Drawer', Skeleton: 'Skeleton', Checkbox: 'Checkbox', Alert: 'Alert',
    Modal: { confirm: () => ({ update() {}, destroy() {} }) },
    message: { success() {}, error() {}, info() {}, warning() {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async (url, init) => {
    await new Promise((r) => setTimeout(r, 0));
    return serve(url, init);
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js', 'router.js',
                 'components/resource-tree.js', 'components/resource-panel.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

async function settle() {
  for (let i = 0; i < 60; i += 1) await new Promise((r) => setTimeout(r, 0));
}

// Every node, with function components CALLED so the rows they return are on the walk. antd's own
// components are stubs and are stepped over.
function flatten(node, out = [], depth = 0) {
  if (!node || depth > 60) return out;
  if (Array.isArray(node)) {
    node.forEach((child) => flatten(child, out, depth));
    return out;
  }
  if (typeof node !== 'object' || !node.t) return out;
  out.push(node);
  if (typeof node.t === 'function' && node.t.name !== 'Input') {
    flatten(node.t(Object.assign({}, node.p, { children: node.c })), out, depth + 1);
  }
  flatten(node.c, out, depth + 1);
  return out;
}

const textOf = (node) => ((node.p || {}).children || node.c || []).flat(Infinity).join('');

// Name and subtitle together, because the subtitle is what is under test and the name is what says
// which row it was under.
function rows() {
  const found = [];
  let name = '';
  for (const node of flatten(SW.ResourcePanel())) {
    const cls = String((node.p || {}).className || '');
    if (cls === 'sw-res-name') name = textOf(node);
    if (cls === 'sw-res-sub' && name) {
      found.push({ name, subtitle: textOf(node) });
      name = '';
    }
  }
  return found;
}

// Arriving in a Project reads the working set, so the scope is MOVED rather than set.
await SW.store.setScope({ id: 'proj', name: 'Demo Project' }, { silent: true });
SW.store.set({ thread: { id: 'conv_1', title: 'The desk talk', artifacts: [] }, messages: [] });
await SW.store.loadApps();
await settle();

console.log(JSON.stringify({ mode, rows: rows() }));
