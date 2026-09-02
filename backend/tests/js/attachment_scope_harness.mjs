// One file, one scope: what the working set holds, what the @ menu offers, and what a Build turn
// carries — all read off ONE real `/project` answer holding an Upload and an Attachment (#148).
//
// The three questions have to be asked together. Dropping the `public/data/…` row from the
// Project's `file` group is a two-line edit; the risk is entirely in what else was reading that
// group. `collectTurnRefs` was, which is why the row could not simply go: with the row gone and
// nothing put in its place, every @mention of the app's own data file silently becomes a plain word
// again. A test that only counted rows in the panel would pass on exactly that bug.
//
// Input on stdin: `{ "prompt": "<the Build turn's text>" }`.
//
// Nothing is mounted. `createElement` returns a plain object, so calling the Composer gives the
// tree it would draw — but `useState` here is REAL, per mount, because the @ menu only exists
// after a keystroke has opened it and a no-op setter would leave every run looking at a closed one.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { prompt } = JSON.parse(fs.readFileSync(0, 'utf8'));

const APP = { id: 'app_a', name: 'Desk margins', selected: true };

// The two lists `/project` answers with, in the shape the server writes them.
//
// `notes.csv` is an Upload: Chat's own bytes at the Project root, outside every app
// (`_SCRATCH_PREFIX`). `margins.csv` is an Attachment: a symlink under `public/data/` that the app
// records and a build can read. They are deliberately different files, so "which scope listed it"
// is answerable — and `margins.csv` also stands as `.sage/scratch/margins.csv`, because a crossing
// (#147) keeps the scratch copy and that is the state where a Project row and an app row are one
// file under two names.
const SCRATCH = [
  { path: '.sage/scratch/notes.csv', name: 'notes.csv', size: 11 },
  { path: '.sage/scratch/margins.csv', name: 'margins.csv', size: 12 },
];
// `AGENTS.md` is a Dataset file whose NAME collides with Sage's own guardrail file, so
// `isHiddenFromExplorer` hides it wherever a person picks. The app records it and lists it; the @
// menu and the turn do not offer it. It is here because that filter used to live on the Project's
// `file` group, which is the group this issue emptied — the one row whose behaviour could have
// changed by accident on the way.
const ATTACHED = [
  { path: 'public/data/desks/margins.csv', file: 'margins.csv',
    dataset: 'desks', dataset_id: 'as_desks', size: 12, source: 'upload' },
  { path: 'public/data/desks/AGENTS.md', file: 'AGENTS.md',
    dataset: 'desks', dataset_id: 'as_desks', size: 3, source: 'dataset' },
];

// A Dataset in the Project, so the working set is not files alone and the @ menu has a row above
// them to be ordered against.
const MEMBERS = [{ id: 'dataset:desks', name: 'Desk margins', kind: 'dataset' }];

// Every Build turn's request body, in order. This is the assertion the issue asks for: the path a
// mention CARRIES, rather than the presence of a row somewhere.
const sent = [];
// Every chip the @ menu's own click wrote, by the path it carried. An Upload and the Attachment it
// crossed into share a basename, so this is what says WHICH of the two rows a menu offered.
const picked = [];

const json = (body, status = 200) => ({
  ok: status < 400, status,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

// An empty event stream. The turn is over before it starts: what is under test is the body that
// went out, and every event after it belongs to the transcript's own tests.
const stream = () => ({
  ok: true, status: 200,
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
    picked.push(body.path || null);
    return json({ id: `ctx_${picked.length}`, ...body, resourceName: body.name });
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
  // Build. The panel asks the router which mode it is in, and every claim here is Build's: an
  // Attachment is a Built App's record and Chat draws no app section at all.
  location: { hash: '#/build' },
  history: { replaceState() {} },
  addEventListener() {}, removeEventListener() {},
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    useState: (init) => hookState(init),
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

// Two hook regimes, because the two surfaces here need different ones.
//
// The panel takes the usual one: a constant and a no-op setter, since every claim about it — which
// section a row is under, what the head counts — is settled on the first pass.
//
// The composer cannot. Its @ menu exists only while `mention` holds a value and only `changeText`
// puts one there, so its hooks are REAL, by call order, per mount: a setter a handler writes has to
// be visible to the next render of that same mount. Kept off by default so the panel's nested
// components, which are walked in one pass, cannot land on each other's slots.
let stateful = false;
let hooks = [];
let cursor = 0;
function hookState(init) {
  const value = () => (typeof init === 'function' ? init() : init);
  if (!stateful) return [value(), () => {}];
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

function* walk(node) {
  if (!node || typeof node !== 'object') return;
  if (Array.isArray(node)) {
    for (const child of node) yield* walk(child);
    return;
  }
  yield node;
  yield* walk(node.c);
}

// Every node, with function components CALLED so the rows they return are on the walk. antd's own
// components are stubs and are stepped over — calling `Input` returns nothing and is not what any
// claim here is about.
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

// The panel's rows, each under the section head it was drawn beneath, and each head's own count.
// A row is only an answer to "how many scopes list this file" if the section it sits under is part
// of the reading, so the two are collected in one walk.
// The Files drawer starts shut, so `openFiles` presses its toggle and reads the panel again — the
// list under it is half of "the Project lists Uploads only" and a count alone does not say which
// rows were counted. The press needs real hooks, which is why they are switchable above.
//
// The press is CHECKED rather than trusted: hook slots go by call order, so a `useState` added to
// or moved within `ResourcePanel` would send the write to a neighbouring slot and leave the drawer
// shut. Unchecked, that reads as an empty list — a wrong answer. Checked, it is a loud failure.
function panel({ openFiles = false } = {}) {
  if (openFiles) {
    stateful = true;
    hooks = [];
    cursor = 0;
    const toggle = flatten(SW.ResourcePanel()).find(
      (n) => String((n.p || {}).className || '') === 'sw-drawer-head'
    );
    toggle.p.onClick();
    cursor = 0;
    const open = flatten(SW.ResourcePanel()).some(
      (n) => String((n.p || {}).className || '').includes('sw-drawer is-open')
    );
    if (!open) throw new Error('the Files drawer did not open — check which hook slot holds filesOpen');
    cursor = 0;
  }
  const sections = [];
  let head = null;
  for (const node of flatten(SW.ResourcePanel())) {
    const cls = String((node.p || {}).className || '');
    if (cls === 'sw-panel-section-title') {
      head = { title: ((node.p || {}).children || node.c || []).flat(Infinity).join(''), count: null, rows: [] };
      sections.push(head);
      continue;
    }
    // The count element is drawn immediately after its own title, so it belongs to the last head.
    if (cls === 'sw-panel-section-count' && head) {
      head.count = (node.c || []).flat(Infinity).join('');
      continue;
    }
    // The Files drawer is a head of its own shape: a toggle with its own count beside it, outside
    // any `sw-panel-section-title`. It is the one this issue narrows, so it is read as a section.
    if (cls === 'sw-drawer-count') {
      sections.push({ title: 'Files', count: (node.c || []).flat(Infinity).join(''), rows: [] });
      head = sections[sections.length - 1];
      continue;
    }
    if (cls === 'sw-res-name' && head) head.rows.push((node.c || []).flat(Infinity).join(''));
  }
  stateful = false;
  return sections;
}

// Build's own mount, prop for prop as modes/builder.js writes it.
const BUILD = { onSend: (v) => SW.store.sendBuildPrompt(v), showMode: true, compact: true,
                disabled: false, placeholder: 'Describe a change, or ask about this app…' };
const render = () => { cursor = 0; return SW.Composer(BUILD); };

// The @ menu, opened the way a person opens it: type into the box. `onChange` is the composer's
// own handler, so the query, the caret rule and the picker's open condition are all the real ones.
function menuFor(query) {
  stateful = true;
  hooks = [];
  render();
  const box = flatten(render()).find((n) => n.t === 'Input.TextArea');
  const value = `@${query}`;
  box.p.onChange({ target: { value, selectionStart: value.length }, nativeEvent: {} });
  const rows = flatten(render()).filter((n) => String(n.p.className || '').startsWith('sw-mention-item'));
  stateful = false;
  return rows.map((row) => {
    const label = flatten(row)
      .filter((n) => n.p && n.p.className === 'sw-mention-name')
      .flatMap((n) => (n.c || []).flat(Infinity))
      .join('');
    // The words are not enough on their own: an Upload and the Attachment it crossed into share a
    // basename, so what tells the two rows apart is the path behind the click.
    return { name: label, pick: row.p.onClick };
  });
}

// Arriving in the Project is what reads its working set, so the scope is MOVED rather than set:
// `setScope` returns early on the scope it is already on, and a hand-written `resourceGroups` would
// be the harness asserting its own fixture back.
await SW.store.setScope({ id: 'proj', name: 'Demo Project' }, { silent: true });
SW.store.set({ thread: { id: 'conv_1', title: 'The desk talk', artifacts: [] }, messages: [] });
await SW.store.loadApps();
await settle();

const state = SW.store.get();
const report = {
  // What the Project's working set holds. `source` is what the panel reads to label an Upload
  // Chat-only, so it is reported rather than inferred from the path.
  files: (state.resourceGroups.file || []).map((r) => ({ id: r.id, path: r.path, source: r.source || null })),
  // What the app records, which is the other scope and the one that keeps the Attachment.
  appAttachments: (state.appAttachments || []).map((a) => a.path),
  // Every section the panel drew, with its count and its rows, which is where "once per scope" is
  // either true or false.
  panel: panel({ openFiles: true }),
  // Every row the @ menu offers, by name and by the query that found it.
  menu: menuFor('margins').map((r) => r.name),
  menuNotes: menuFor('notes').map((r) => r.name),
  menuAgents: menuFor('AGENTS').map((r) => r.name),
};

// The turn, sent through the store's own send so the body is built by `collectTurnRefs`.
await SW.store.sendBuildPrompt(prompt);
await settle();
// What went out on the wire. The claim about the mention is this list, by path.
report.sent = sent.map((body) => ({ prompt: body.prompt, mentions: body.mentions,
                                    resources: body.resources }));
// The compose-time warning, which must stay quiet about a file the app already holds and speak up
// about the Upload that has not crossed (#136).
report.warned = SW.store.unusableMentions(prompt).map((e) => ({ kind: e.kind, id: e.id, app: e.app }));

// Last, because picking writes a chip and a chip reorders every menu after it: the paths behind the
// rows that say "margins.csv", in the order the menu offered them.
for (const row of menuFor('margins')) {
  if (row.name !== 'margins.csv') continue;
  await row.pick();
  await settle();
}
report.picked = picked;

console.log(JSON.stringify(report));
