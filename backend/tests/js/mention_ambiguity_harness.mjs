// One name, two files: what the @ menu can be narrowed to, what each row SAYS it is, what the
// picker inserts, and what the turn carries (ADR-0030).
//
// The fixture is the shape ADR-0029 made routine rather than rare — a recursive attach of a
// partitioned folder, so `data.csv` stands twice under two date partitions and `summary.csv`
// stands once. Every claim in the test is read off one arrival, because the four surfaces are the
// same defect seen four times: a query that cannot reach the file, a row that cannot be told from
// its twin, a token that names both, and a turn that silently carries neither.
//
// Input on stdin: `{ "prompts": ["<a Build turn's text>", ...] }`, sent in order.
//
// Nothing is mounted. `createElement` returns a plain object, so calling Composer gives back the
// tree it would draw — but `useState` here is REAL, per mount, because the @ menu only exists
// after a keystroke has opened it and a no-op setter would leave every run looking closed.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { prompts } = JSON.parse(fs.readFileSync(0, 'utf8'));

const APP = { id: 'app_a', name: 'Sales trends', selected: true };

// The app's Attachments, in the shape the server writes them. `file` is the path INSIDE the
// Dataset and `path` is the workspace-relative one `_resolve_mentions` keys on — the two agree on
// the basename and on nothing else, which is exactly the pair this ADR is about.
const ATTACHED = [
  { path: 'public/data/sales/raw/2025/data.csv', file: 'raw/2025/data.csv',
    dataset: 'sales', dataset_id: 'as_sales', size: 20, source: 'dataset' },
  { path: 'public/data/sales/raw/2026/data.csv', file: 'raw/2026/data.csv',
    dataset: 'sales', dataset_id: 'as_sales', size: 21, source: 'dataset' },
  // The control. Its basename is unique among the Attachments, so it must keep the plain
  // `@summary.csv` a person already knows — the fallback is for a collision, not for every file.
  { path: 'public/data/sales/summary.csv', file: 'summary.csv',
    dataset: 'sales', dataset_id: 'as_sales', size: 12, source: 'dataset' },
  // A name with a space in it. `mentionToken` collapses whitespace, so what stands in the box is
  // `@q1_notes.md` — a word neither the file nor the row is called.
  { path: 'public/data/sales/q1 notes.md', file: 'q1 notes.md',
    dataset: 'sales', dataset_id: 'as_sales', size: 5, source: 'dataset' },
];

// A Project Upload whose basename collides with an Attachment ACROSS scopes. Uniqueness is
// computed against the app's own Attachment list (ADR-0030), so this one is untouched: it keeps
// `@notes.csv`, and so does the Attachment it collides with — the crossing case (#147) is a
// different question with a different answer, and this row is here so a widening cannot reach it
// by accident.
const SCRATCH = [{ path: '.sage/scratch/notes.csv', name: 'notes.csv', size: 9 }];

// A Dataset in the Project, so the working set is not files alone and the @ menu has a row above
// them to be ordered against.
const MEMBERS = [{ id: 'dataset:sales', name: 'Sales', kind: 'dataset' }];

// Every Build turn's request body, in order.
const sent = [];

const json = (body, status = 200) => ({
  ok: status < 400,
  status,
  headers: { get: () => 'application/json' },
  text: async () => JSON.stringify(body),
  json: async () => body,
});

// A stream that ends immediately: this harness asks what the turn CARRIES, and the transcript it
// draws afterwards belongs to the transcript's own tests.
const stream = () => ({
  ok: true,
  status: 200,
  headers: { get: () => 'text/event-stream' },
  body: { getReader: () => ({ read: async () => ({ done: true, value: undefined }) }) },
});

function serve(url, init) {
  const path = String(url).replace(/^\.\/api/, '');
  const method = ((init && init.method) || 'GET').toUpperCase();
  if (path === '/project/build/stream' && method === 'POST') {
    sent.push(JSON.parse((init && init.body) || '{}'));
    return stream();
  }
  if (path === '/project') return json({ attached: ATTACHED, scratch: SCRATCH });
  if (path === '/project/resources') return json({ items: MEMBERS });
  if (path === '/apps') return json({ items: [APP] });
  if (path === '/bindings') return json({ bindings: [] });
  if (path === '/members') return json({ members: [], directory: [] });
  if (path.match(/^\/threads\/[^/]+\/context$/)) {
    if (method !== 'POST') return json({ items: [] });
    const body = JSON.parse((init && init.body) || '{}');
    return json({ id: `ctx_${Math.random()}`, ...body, resourceName: body.name });
  }
  if (path === '/threads') return json([]);
  return json({});
}

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, TextDecoder,
  setTimeout, clearTimeout, setInterval: () => 1, clearInterval: () => {},
  requestAnimationFrame: (fn) => fn(),
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  location: { hash: '#/build', href: 'http://x/#/build' },
  history: { replaceState() {}, pushState() {} },
  addEventListener() {}, removeEventListener() {},
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    useState: (init) => hookState(init),
    useEffect: () => {},
    useMemo: (fn) => fn(),
    useCallback: (fn) => fn,
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

// Real hooks, by call order, per mount: the @ menu exists only while `mention` holds a value, and
// only `changeText` puts one there.
let hooks = [];
let cursor = 0;
function hookState(init) {
  const value = () => (typeof init === 'function' ? init() : init);
  const at = cursor;
  cursor += 1;
  if (!(at in hooks)) hooks[at] = value();
  return [hooks[at], (next) => {
    hooks[at] = typeof next === 'function' ? next(hooks[at]) : next;
  }];
}

sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const file of ['util.js', 'api.js', 'store.js', 'prefs.js', 'router.js',
                    'components/resource-tree.js', 'components/resource-panel.js',
                    'components/composer.js']) {
  vm.runInContext(fs.readFileSync(ROOT + file, 'utf8'), sandbox, { filename: file });
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
  if (typeof node.t === 'function' && node.t.name !== 'Input') {
    flatten(node.t(Object.assign({}, node.p, { children: node.c })), out, depth + 1);
  }
  flatten(node.c, out, depth + 1);
  return out;
}

const BUILD = { showMode: true, onSend: (v) => SW.store.sendBuildPrompt(v) };
const render = () => { cursor = 0; return SW.Composer(BUILD); };

const textOf = (node) => flatten(node)
  .filter((n) => n.p && n.p.className === 'sw-mention-name')
  .flatMap((n) => (n.c || []).flat(Infinity))
  .join('');

const captionsOf = (node) => flatten(node)
  .filter((n) => n.p && (n.p.className === 'sw-caption' || n.p.className === 'sw-incontext-tag'))
  .map((n) => (n.c || []).flat(Infinity).join(''));

const titleOf = (node) => {
  const name = flatten(node).find((n) => n.p && n.p.className === 'sw-mention-name');
  return (name && name.p.title) || '';
};

// Type "@<query>" into the box through the composer's OWN handler, so the query, the caret rule
// and the picker's open condition are all the real ones.
function open(query) {
  hooks = [];
  render();
  const box = flatten(render()).find((n) => n.t === 'Input.TextArea');
  const value = `@${query}`;
  box.p.onChange({ target: { value, selectionStart: value.length }, nativeEvent: {} });
  return flatten(render()).filter((n) => String(n.p.className || '').startsWith('sw-mention-item'));
}

const menuFor = (query) => open(query).map((row) => ({
  name: textOf(row), captions: captionsOf(row), title: titleOf(row),
}));

// What the picker actually WRITES into the box for one row — read off the textarea afterwards, so
// this is the token a person would then be looking at, not a second derivation of it.
async function insertedBy(query, index) {
  const rows = open(query);
  await rows[index].p.onClick();
  await settle();
  const box = flatten(render()).find((n) => n.t === 'Input.TextArea');
  return String(box.p.value || '').trim();
}

await SW.store.setScope({ id: 'proj', name: 'Demo Project' }, { silent: true });
SW.store.set({ thread: { id: 'conv_1', title: 'sales', artifacts: [] }, messages: [] });
await SW.store.loadApps();
await settle();

const report = {
  // The query half. Typing the partition, or the folder path above it, has to reach the one file
  // it names — and the basename still reaches both.
  menuPartition: menuFor('2026'),
  menuFolderPath: menuFor('raw/2026'),
  menuBasename: menuFor('data.csv'),
  menuUnique: menuFor('summary'),
  // A query that reaches a row with NO path at all (the Dataset) beside rows that have one, so the
  // widened matcher and the folder caption are both asked what they do with a Resource.
  menuMixed: menuFor('sales'),
  // The mount prefix every Attachment shares. Searching it would match every row in the app and
  // fill the menu's eight slots with the one thing they all have in common.
  menuPrefix: menuFor('public'),
  menuPrefixData: menuFor('data'),
  // The token half, read off the box the picker wrote into.
  inserted2025: await insertedBy('2025', 0),
  inserted2026: await insertedBy('2026', 0),
  insertedUnique: await insertedBy('summary', 0),
  insertedUpload: await insertedBy('notes.csv', 0),
  insertedSpaced: await insertedBy('q1', 0),
};

// The turns. Each prompt goes through the store's own send, so the body is built by
// `collectTurnRefs` rather than by this harness.
for (const prompt of prompts || []) {
  SW.store.set({ buildRunning: false });
  await SW.store.sendBuildPrompt(prompt);
  await settle();
}
report.sent = sent.map((body) => ({ prompt: body.prompt, mentions: body.mentions }));

// The same drift running BACKWARDS. `@2026/data.csv` is in the box; then the sibling partition is
// detached (or the selected Built App changes, which is allowed mid-turn) and the collision that
// earned the qualified token is gone. `mentionToken` would answer `@data.csv` for the file that is
// left, so a matcher reading only today's answer finds nothing and the mention carries nothing —
// with no refusal and no warning, which is the failure this whole area exists to end.
sent.length = 0;
SW.store.set({ appAttachments: ATTACHED.filter((a) => a.path !== 'public/data/sales/raw/2025/data.csv') });
SW.store.set({ buildRunning: false });
await SW.store.sendBuildPrompt('chart the trend from @2026/data.csv');
await settle();
report.sentAfterDetach = sent.map((body) => ({ prompt: body.prompt, mentions: body.mentions }));

console.log(JSON.stringify(report));
