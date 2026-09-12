// Who made an Attachment, read off the surface that owns the app's list (#262, ADR-0048).
//
// The App dependencies modal is where an Attachment is drawn since ADR-0035, so it is where the
// provenance line ADR-0035 removed comes back. The claim is per row and per record: an entry that
// names a person, an entry that names the assistant, and the entry every existing workspace
// already holds — which names nobody and must say nothing rather than guessing "you".
//
// Nothing is mounted. `createElement` returns a plain object, and `flatten` CALLS function
// components so the rows they return are on the walk — the panel's claims are settled on the first
// pass, so the no-op setter is enough here and the composer's real-hook regime is not needed.
//
// No stdin. The fixture is the three entries below, and the pack's assistant name is a sentinel no
// real pack carries: a row printing our own default would pass a test that asserted "Sage".
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;

const APP = { id: 'app_a', name: 'Desk margins', selected: true };

// Three manifest entries, in the shape the server writes them.
//
// `older.csv` is deliberately short of both new fields rather than carrying them as null: that is
// what is actually on disk in every workspace written before #262, and the row has to read it as
// "not known" rather than as a caller who declined to answer.
const ATTACHED = [
  { path: 'public/data/desks/mine.csv', file: 'mine.csv', dataset: 'desks',
    dataset_id: 'as_desks', size: 12, source: 'dataset',
    added_by: 'user', conversation_id: 'conv_1' },
  { path: 'public/data/desks/theirs.csv', file: 'theirs.csv', dataset: 'desks',
    dataset_id: 'as_desks', size: 12, source: 'dataset',
    added_by: 'sage', conversation_id: 'conv_1' },
  { path: 'public/data/desks/older.csv', file: 'older.csv', dataset: 'desks',
    dataset_id: 'as_desks', size: 12, source: 'dataset' },
];

const json = (body, status = 200) => ({
  ok: status < 400,
  status,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

function serve(url) {
  const path = String(url).replace(/^\.\/api/, '');
  if (path === '/project') return json({ attached: ATTACHED, scratch: [] });
  if (path === '/project/resources') return json({ items: [] });
  if (path === '/apps') return json({ items: [APP] });
  if (path === '/bindings') return json({ bindings: [] });
  if (path === '/members') return json({ members: [], directory: [] });
  return json({}, 404);
}

const sandbox = {
  console,
  JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp, Error,
  setTimeout, clearTimeout,
  setInterval: () => 1,
  clearInterval: () => {},
  requestAnimationFrame: (fn) => fn(),
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  // Build. An Attachment is a Built App's record and Chat draws no app section at all.
  location: { hash: '#/build' },
  history: { replaceState() {} },
  addEventListener() {},
  removeEventListener() {},
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
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
    Modal: Object.assign(function Modal() {}, { confirm: () => ({ update() {}, destroy() {} }) }),
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
                 'components/resource-tree.js', 'components/resource-panel.js',
                 'components/composer.js', 'modes/builder.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

async function settle() {
  for (let i = 0; i < 60; i += 1) await new Promise((r) => setTimeout(r, 0));
}

function flatten(node, out = [], depth = 0) {
  if (!node || depth > 60) return out;
  if (Array.isArray(node)) {
    node.forEach((child) => flatten(child, out, depth));
    return out;
  }
  if (typeof node !== 'object' || !node.t) return out;
  out.push(node);
  if (typeof node.t === 'function' && node.t.name !== 'Input' && node.t.name !== 'Modal') {
    flatten(node.t(Object.assign({}, node.p, { children: node.c })), out, depth + 1);
  }
  flatten(node.c, out, depth + 1);
  return out;
}

function textOf(node) {
  return ((node.p || {}).children || node.c || []).flat(Infinity).join('');
}

// Every row under "Files it carries", by name, with the provenance line drawn beneath it. Read in
// one walk so a line can only be reported against the row it actually sits in.
function files() {
  const rows = [];
  let under = null;
  for (const node of flatten(SW.AppDependenciesModal())) {
    const cls = String((node.p || {}).className || '');
    if (cls.includes('sw-app-group')) {
      under = textOf(node).startsWith('Files it carries') ? [] : null;
      continue;
    }
    if (!under) continue;
    if (cls === 'sw-appdeps-name') {
      rows.push({ name: textOf(node), by: '' });
      continue;
    }
    if (cls === 'sw-appdeps-by' && rows.length) rows[rows.length - 1].by = textOf(node);
  }
  return rows;
}

await SW.store.setScope({ id: 'proj', name: 'Demo Project' }, { silent: true });
// A pack no real deployment carries, so the assistant's row cannot pass by printing our default.
SW.store.set({ brand: { assistantName: 'ZZ Helper' } });
await SW.store.loadApps();
await settle();

process.stdout.write(JSON.stringify({ files: files() }) + '\n');
