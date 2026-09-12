// What a removal RECEIPT says about the Dataset the file stays in (#271, ADR-0011).
//
// The sentence is a promise — it tells somebody their bytes are safe somewhere — so unlike the row
// #266 fixed it cannot fall silent and leave "the file stays in ." on screen. Three entries drive
// the three answers it has:
//
//   q3.csv   a Dataset the Project holds, renamed after the attach. The receipt must say the word
//            the Project uses TODAY, which is the word the row and the refusal use.
//   old.csv  a recorded `dataset_id` that names no Dataset this client can see. No current name
//            exists, so the sentence written for that case is drawn instead — never the recorded
//            one, which is the attach-time half, and never a blank slot.
//   q2.csv   no `dataset_id` at all, which is what `_rehydrate_attached`'s symlink scan leaves.
//            Its recorded `dataset` is a served slug path, and the sentence for it predates #271.
//
// Read off the drawn modal rather than out of the store, because the store field is not the
// promise — the sentence a reader sees is. Both the row's line and the receipt are walked in one
// pass so the two surfaces' spellings can be compared over the same fixture.
//
// Nothing is mounted. `createElement` returns a plain object and `flatten` CALLS the function
// components, so the rows they return are on the walk.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;

const APP = { id: 'app_a', name: 'Desk margins', selected: true };

// The Project's Datasets as the working set holds them TODAY. `ds-3` was called `sales 2026` when
// q3.csv was attached, which is the name that entry still records and the slug the file is still
// served from. `ds-gone` is deliberately absent.
const MEMBERS = [{ id: 'dataset:ds-3', name: 'Sales 2026 EMEA', kind: 'dataset' }];

const ATTACHED = [
  { path: 'public/data/sales_2026/raw/q3.csv', file: 'q3.csv', dataset: 'sales 2026',
    dataset_id: 'ds-3', size: 12, source: 'dataset', added_by: 'user' },
  { path: 'public/data/archive/old.csv', file: 'old.csv', dataset: 'Cold archive',
    dataset_id: 'ds-gone', size: 12, source: 'dataset', added_by: 'user' },
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
  // No manifest back, no leaked copies and nothing left behind: the receipt under test is the
  // Dataset half of the sentence, and the copy clauses would only pad it.
  if (path === '/project/files/detach') {
    return json({ removed_copies: [], kept_copies: [], refs: [] });
  }
  return json({}, 404);
}

const sandbox = {
  console,
  JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp, Error,
  Blob,
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

// The one sentence the modal draws after a removal, as the reader gets it.
function receipt() {
  for (const node of flatten(SW.AppDependenciesModal())) {
    if (String((node.p || {}).className || '') === 'sw-appdeps-notice-text') return textOf(node);
  }
  return '';
}

// The quiet line under each "Files it carries" row, by file name — #266's surface, read off the
// same fixture so the two spellings can be held against each other.
function lines() {
  const rows = {};
  let under = null;
  let last = null;
  for (const node of flatten(SW.AppDependenciesModal())) {
    const cls = String((node.p || {}).className || '');
    if (cls.includes('sw-app-group')) {
      under = textOf(node).startsWith('Files it carries') ? true : null;
      continue;
    }
    if (!under) continue;
    if (cls === 'sw-appdeps-name') {
      last = textOf(node);
      rows[last] = '';
      continue;
    }
    if (cls === 'sw-appdeps-by' && last) rows[last] = textOf(node);
  }
  return rows;
}

await SW.store.setScope({ id: 'proj', name: 'Demo Project' }, { silent: true });
// A pack no real deployment carries, so a sentence cannot pass by printing our own default noun.
SW.store.set({ brand: { nouns: { dataset: { singular: 'Data Box', plural: 'Data Boxes' } } } });
await SW.store.loadApps();
await settle();

const before = lines();
const receipts = {};
for (const entry of ATTACHED) {
  await SW.store.removeAttachmentFromApp(entry);
  await settle();
  receipts[entry.file] = receipt();
  SW.store.dismissAppRemoval();
}

process.stdout.write(JSON.stringify({ receipts, lines: before }) + '\n');
