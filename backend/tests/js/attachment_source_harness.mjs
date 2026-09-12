// Which Dataset an Attachment came from, in the words the refusal uses (#266, ADR-0011).
//
// The App dependencies modal is the only surface that draws an Attachment, so it is the
// destination half of the property #263 pinned on the pointer: a Dataset renamed after the attach
// goes on being SERVED from its old slug, and a row reading the entry's recorded name — or its
// path — spells that Dataset a second way. The fixture renames one after the attach for exactly
// that reason.
//
// The second entry records no `dataset_id`, which is what `_rehydrate_attached`'s symlink scan
// writes for a workspace older than the manifest. Its recorded `dataset` is a slug path, not a
// name, so the row has nothing it can honestly print and must print nothing.
//
// `rows` is reported beside `files` because `attachmentRow` is shared with the @ menu and the
// turn: those two read `name`, `path` and the folder fields, and this says they came through
// unchanged — including that the row carries no Dataset name of its own, which is what keeps a
// store read out of a loop those two walk on every keystroke.
//
// Nothing is mounted. `createElement` returns a plain object and `flatten` CALLS the function
// components, so the rows they return are on the walk.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;

const APP = { id: 'app_a', name: 'Desk margins', selected: true };

// The Project's Datasets as the working set holds them TODAY. `ds-3` was called `sales 2026` when
// the file below was attached, which is the name the entry still records and the slug the file is
// still served from.
const MEMBERS = [{ id: 'dataset:ds-3', name: 'Sales 2026 EMEA', kind: 'dataset' }];

// Two manifest entries, in the shape the server writes them.
//
// `q2.csv` is deliberately short of `dataset_id` rather than carrying it as null: that is what the
// symlink scan leaves on disk, and its `dataset` is the served folder (`sales_2026/raw`), which is
// a slug the client does not own the rule for.
const ATTACHED = [
  { path: 'public/data/sales_2026/raw/q3.csv', file: 'q3.csv', dataset: 'sales 2026',
    dataset_id: 'ds-3', size: 12, source: 'dataset',
    added_by: 'user', conversation_id: 'conv_1' },
  { path: 'public/data/sales_2026/raw/q2.csv', file: 'q2.csv', dataset: 'sales_2026/raw',
    size: 12, source: 'dataset' },
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
  if (path === '/project/resources') return json({ items: MEMBERS });
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

// Every row under "Files it carries", by name, with the quiet line drawn beneath it. Read in one
// walk so a line can only be reported against the row it actually sits in.
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

process.stdout.write(JSON.stringify({
  files: files(),
  rows: SW.util.attachmentRows(ATTACHED),
}) + '\n');
