// What the DESTROY door calls the Dataset it is about to delete bytes out of (#273, ADR-0011).
//
// Two strings, on two surfaces, about one Dataset: the menu item `Delete from {dataset}` in
// `modes/builder.js` and the confirm `Delete {file} from {dataset}?` in `store.js`. Both read the
// entry's recorded `dataset`, which is the name the Dataset had at ATTACH time, so a rename on the
// platform leaves this door — the one with no undo — naming something the Project no longer calls
// anything. Two entries drive the two answers it has:
//
//   margins.csv  a Dataset the Project holds, renamed after the attach. Both strings must say the
//                word the Project uses TODAY.
//   old.csv      a recorded `dataset_id` that names no Dataset this client can see. Both strings
//                fall back to the recorded name, plainly — this door takes no third sentence the
//                way #271's receipt did, because the two cases that forced one there cannot
//                arrive at a door only `isSageUpload` opens.
//
// Read off the drawn menu and the confirm's own config rather than out of the store, because the
// strings under test are the ones a reader sees. Both surfaces are walked over one fixture so
// their two spellings can be held against each other.
//
// Nothing is mounted. `createElement` returns a plain object and `flatten` CALLS the function
// components, so the rows they return are on the walk. `Modal.confirm` is captured rather than
// answered: the door is under test, not the deletion behind it.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;

const APP = { id: 'app_a', name: 'Desk margins', selected: true };

// The Project's Datasets as the working set holds them TODAY. `ds-3` was called `sales 2026` when
// margins.csv was uploaded, which is the name that entry still records. `ds-gone` is deliberately
// absent, which is the only way `datasetNameNow` can answer nothing.
const MEMBERS = [{ id: 'dataset:ds-3', name: 'Sales 2026 EMEA', kind: 'dataset' }];

// Both uploads carry `source: 'upload'`, which is one of the two things that open this door
// (`isSageUpload`). q3.csv is a genuine pre-existing Dataset file, on the walk so the door being
// read is provably the upload's and not whichever row came last — and it carries the
// `dataset_rel_path` every real writer sets (`_dataset_entry`), so it is kept out by the one
// condition a real entry is kept out by rather than by a field the fixture simply omitted.
const ATTACHED = [
  { path: 'public/data/sales_2026/uploads/margins.csv', file: 'margins.csv',
    dataset: 'sales 2026', dataset_id: 'ds-3', dataset_rel_path: 'uploads/margins.csv',
    size: 12, source: 'upload', added_by: 'user' },
  { path: 'public/data/archive/uploads/old.csv', file: 'old.csv', dataset: 'Cold archive',
    dataset_id: 'ds-gone', dataset_rel_path: 'uploads/old.csv',
    size: 12, source: 'upload', added_by: 'user' },
  { path: 'public/data/sales_2026/raw/q3.csv', file: 'q3.csv', dataset: 'sales 2026',
    dataset_id: 'ds-3', dataset_rel_path: 'raw/q3.csv',
    size: 12, source: 'dataset', added_by: 'user' },
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

// Every confirm this run opened, in order, unanswered until the walk asks for one.
const confirms = [];

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, setTimeout, clearTimeout,
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
    Modal: Object.assign(function Modal() {}, {
      confirm: (cfg) => {
        confirms.push(cfg);
        return { update() {}, destroy() {} };
      },
    }),
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

// The menu each row carries, by the path it is keyed on. A row is reported even when it offers no
// delete item, so "the door is absent" and "the row was never drawn" are different answers here.
function menus() {
  const found = {};
  let at = null;
  for (const node of flatten(SW.AppDependenciesModal())) {
    const props = node.p || {};
    if (String(props.className || '') === 'sw-appdeps-row') {
      at = String(props.key || '');
      found[at] = { drawn: true, labels: [] };
      continue;
    }
    // The FIRST Dropdown after a row is that row's. The footer's Add door is a Dropdown too, and
    // reading on would hand the last row somebody else's menu — which looks exactly like a row
    // whose delete item went missing.
    if (node.t === 'Dropdown' && at) {
      found[at].labels = ((props.menu || {}).items || []).map((i) => String(i.label));
      at = null;
    }
  }
  return found;
}

await SW.store.setScope({ id: 'proj', name: 'Demo Project' }, { silent: true });
await SW.store.loadApps();
await settle();

const rows = menus();

// The confirm, per file. Answered with Cancel: what it SAYS is the door, and taking the other
// branch would be a test of the deletion behind it.
const titles = {};
for (const entry of ATTACHED) {
  const before = confirms.length;
  const answered = SW.store.deleteAttachmentFromApp(entry);
  const cfg = confirms.length > before ? confirms[confirms.length - 1] : null;
  titles[entry.file] = cfg ? { asked: true, title: String(cfg.title) } : { asked: false };
  if (cfg) cfg.onCancel();
  await answered;
}

process.stdout.write(JSON.stringify({ rows, titles }) + '\n');
