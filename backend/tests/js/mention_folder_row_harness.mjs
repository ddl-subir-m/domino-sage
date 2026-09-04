// Above the threshold, the folder is the row (ADR-0030): what the @ menu draws after a folder
// attach, what a row says it stands for, what a pick inserts, and what the turn then carries.
//
// The fixture is a partitioned Dataset the way ADR-0029 made routine — two full partitions and one
// holding a single file — arriving the way the server sends it, with `menu_folder` already decided.
// Nothing here re-derives that grouping: the point of the ticket is that the menu is handed the
// answer, so a harness that grouped for itself would test the wrong thing.
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

const file = (path, menuFolder, count) => ({
  path,
  file: path.replace(/^public\/data\/[^/]+\//, ''),
  dataset: path.split('/')[2],
  dataset_id: `as_${path.split('/')[2]}`,
  size: 20,
  source: 'dataset',
  // Both decided server-side, off the one grouping the `AGENTS.md` block uses. The count is the
  // FOLDER's, not the query's: the row stands for the folder and picking it carries the folder.
  menu_folder: menuFolder,
  menu_folder_count: count,
});

// Twelve files in each of two partitions and one in a third: 25 attachments, well over the
// server's threshold, so every one of them arrives carrying the folder its row collapses into.
const PART = (year, n) => Array.from({ length: n }, (_, i) =>
  file(`public/data/sales/raw/${year}/part-${i}.csv`, `public/data/sales/raw/${year}`, n));
const ATTACHED = [
  ...PART('2024', 12),
  ...PART('2025', 12),
  // The group of one. The block keeps a file line for a folder holding one file, and so does the
  // menu — naming the file describes it exactly as well, and better.
  file('public/data/sales/raw/2026/only.csv', 'public/data/sales/raw/2026', 1),
];

// A second Dataset partitioned by year, so two folder rows collide on their basename the way two
// `data.csv` do. This is the whole defect ADR-0030 is about, arriving at the row that replaced
// them: two rows reading `2024` that insert two different tokens.
const COLLIDING = [
  ...ATTACHED,
  ...Array.from({ length: 12 }, (_, i) => file(
    `public/data/costs/raw/2024/part-${i}.csv`, 'public/data/costs/raw/2024', 12)),
];

// The same app under the threshold: the server sends no folder at all, and the menu goes on
// showing files. Swapped in after the first reads, so both states are read off one arrival.
const SMALL = [
  file('public/data/sales/a.csv', '', 0),
  file('public/data/sales/b.csv', '', 0),
];

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
  if (path === '/project') return json({ attached: ATTACHED, scratch: [] });
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
for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js', 'router.js',
                 'components/resource-tree.js', 'components/resource-panel.js',
                 'components/composer.js']) {
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
  if (typeof node.t === 'function' && node.t.name !== 'Input') {
    flatten(node.t(Object.assign({}, node.p, { children: node.c })), out, depth + 1);
  }
  flatten(node.c, out, depth + 1);
  return out;
}

const BUILD = { showMode: true, onSend: (v) => SW.store.sendBuildPrompt(v) };
// Chat's composer, told apart from Build's the way every other rule in this file tells them
// apart: one flag, and it is the flag that decides whether a folder mention can be honoured.
const CHAT = { showMode: false, onSend: () => {} };
let mode = BUILD;
const render = () => { cursor = 0; return SW.Composer(mode); };

const textOf = (node) => flatten(node)
  .filter((n) => n.p && n.p.className === 'sw-mention-name')
  .flatMap((n) => (n.c || []).flat(Infinity))
  .join('');

const captionsOf = (node) => flatten(node)
  .filter((n) => n.p && (n.p.className === 'sw-caption' || n.p.className === 'sw-incontext-tag'))
  .map((n) => (n.c || []).flat(Infinity).join(''));

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
  name: textOf(row), captions: captionsOf(row),
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
  // The whole list, as the menu draws it with nothing typed but the "@". Twenty-five files that
  // cannot all be shown become three rows that can.
  menuOpen: menuFor('raw'),
  // Typing the partition reaches its folder; typing a file name reaches the file. Both are the
  // same rule applied to what the query matched, which is what makes the second one possible.
  menuPartition: menuFor('2024/part'),
  menuOneFile: menuFor('2024/part-3'),
  // A query that matches PART of a folder. The row still stands for the folder — that is what the
  // pick carries — so it must not report the size of the match.
  menuNarrowed: menuFor('2024/part-1'),
  insertedFolder: await insertedBy('raw', 0),
};

// Two folders, one basename. Read before the turns, so the app is back to ATTACHED for those.
SW.store.set({ appAttachments: COLLIDING });
report.menuColliding = menuFor('raw/2024');
report.insertedColliding = await insertedBy('raw/2024', 0);
SW.store.set({ appAttachments: ATTACHED });

// The turns. Each prompt goes through the store's own send, so the body is built by
// `collectTurnRefs` rather than by this harness.
for (const prompt of prompts || []) {
  SW.store.set({ buildRunning: false });
  await SW.store.sendBuildPrompt(prompt);
  await settle();
}
report.sent = sent.map((body) => ({ prompt: body.prompt, mentions: body.mentions }));

// Chat, on the same app. Its menu is the one it always drew.
mode = CHAT;
report.menuChat = menuFor('2024/part');

// The app below the threshold, where the server sends no folder and nothing collapses.
mode = BUILD;
SW.store.set({ appAttachments: SMALL });
report.menuSmallApp = menuFor('.csv');

console.log(JSON.stringify(report));
