// What the TWO removal doors say when the copy Chat fetched stays on disk (#249).
//
// Build's Data panel and Chat's chip both remove the same attached file, and until this ticket
// only one of them removed the data. The server half now releases the fetch where it can; these
// are the sentences for where it cannot, which is the half a release alone does not cover: while a
// live Conversation still names the fetch, the bytes stay whichever door is pressed, and a person
// told nothing reads the silence as "the data went with it".
//
// Both surfaces are driven off one sandbox so the two spellings can be held against each other.
// Build's is read off the drawn modal, because the reader's sentence is the promise rather than
// the store field behind it; Chat's is read off `antd.message.info`, which is the whole of what
// that surface says.
//
// The server's answer is stubbed on purpose. What is under test here is the copy, and the Python
// suite beside this file owns whether `kept_fetch` and `heldBy` are true.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const APP = { id: 'app_a', name: 'Desk margins', selected: true };
const THREAD = { id: 'conv_1', title: 'Card transactions' };
const ATTACHED = [
  { path: 'public/data/card_data/transactions.csv', file: 'transactions.csv',
    dataset: 'card_data', dataset_id: 'ds-cards', size: 12, source: 'dataset' },
];
const MEMBERS = [{ id: 'dataset:ds-cards', name: 'card_data', kind: 'dataset' }];

// What the detach route answers, swapped between the runs below. The same vocabulary the chip
// door gets as `heldBy`, which is the point of it: one holder, one word, both surfaces.
let keptFetch = '';
// What the chip route answers. '' is a fetch that went, and the two words are the two things that
// can hold one back — see `_fetch_holder`.
let heldBy = '';

const json = (body, status = 200) => ({
  ok: status < 400, status, headers: { get: () => 'application/json' },
  json: async () => body, text: async () => JSON.stringify(body),
});

function serve(url) {
  const path = String(url).replace(/^\.\/api/, '');
  if (path === '/project') return json({ attached: ATTACHED, scratch: [] });
  if (path === '/project/resources') return json({ items: MEMBERS });
  if (path === '/apps') return json({ items: [APP] });
  if (path === '/bindings') return json({ bindings: [] });
  if (path === '/members') return json({ members: [], directory: [] });
  if (path === '/project/files/detach') {
    return json({ removed_copies: [], kept_copies: [], refs: [], kept_fetch: keptFetch });
  }
  if (path.startsWith('/threads/conv_1/context/')) return json({ ok: true, heldBy });
  return json({}, 404);
}

const said = [];
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
    message: { success() {}, error() {}, info: (t) => said.push(String(t)), warning() {} },
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

await SW.store.setScope({ id: 'proj', name: 'Demo Project' }, { silent: true });
await SW.store.loadApps();
await settle();

// --- Build's door ---------------------------------------------------------------------------
const receipts = {};
for (const holder of ['', 'conversation', 'app']) {
  keptFetch = holder;
  await SW.store.removeAttachmentFromApp(ATTACHED[0]);
  await settle();
  receipts[holder || 'released'] = receipt();
  SW.store.dismissAppRemoval();
}

// --- Chat's door ----------------------------------------------------------------------------
// The chip, as the composer holds it: a Dataset file is not a Binding, so the "still needs it"
// join below it can never fire for this row and the server's answer is the only thing that knows.
const chip = { id: 'ctx_1', kind: 'file', name: 'transactions.csv',
               resourceId: 'dsfile:ds-cards:transactions.csv', resourceName: 'transactions.csv' };
const chips = {};
for (const holder of ['', 'app', 'conversation']) {
  heldBy = holder;
  said.length = 0;
  SW.store.set({ thread: THREAD, attachments: [chip] });
  await SW.store.removeFromConversation(chip);
  await settle();
  chips[holder || 'released'] = said.join(' | ');
}

process.stdout.write(JSON.stringify({ receipts, chips }) + '\n');
