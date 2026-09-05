// Mounts the real plan page and reports the ways back it offers.
//
// A plan has two ends to link back to and they are independent (#54): the Conversation that
// produced it, and the Built App it stands in. A plan may carry either, both, or neither, so the
// page is driven here once per shape and what it drew is what gets asserted.
//
// `useEffect` runs for real, unlike the sibling feedback harness: the page has no plan at all
// until its load effect has run, so a no-op stub would only ever report a skeleton.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
// `variant` and `mode` are not free of each other in the real app — the page is only ever mounted
// on `#/plan/<id>`, and the sheet only from inside Chat or Build — so the caller passes the pair
// and the tests only ever pass one the app can produce. `thread` is the conversation already open
// behind the sheet, which the routes have to carry rather than drop.
const { plan, mode, variant = 'page', thread = null } = JSON.parse(fs.readFileSync(0, 'utf8'));

// What the archive control asked the server for, in the shape the route takes it (#167). Stubbed
// rather than served over the fetch below, because the claim is the call: putting a plan away is a
// write with a refusal behind it, and a page that sent `archived` the wrong way round would look
// identical in a rendered tree.
const archived = [];

const json = (body) => ({
  ok: true, status: 200,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

// Where SW.router.go was sent. The back-links are routes, so this IS what the person gets.
const routed = [];

// What the workspace holds for this plan. Only Build's Markdown tab ever asks for it.
const RAW = { path: '.sage/plans/001/v1.md', content: '# A desk exposure dashboard.\n\n## Steps\n' };

let cells = [];
let cursor = 0;
let effects = [];

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, setTimeout, clearTimeout, setInterval, clearInterval,
  requestAnimationFrame: (fn) => fn(),
  // `openThread` asks the viewer which conversation view they are in (#56), so the real prefs.js
  // comes along with the store. An in-memory backing map: this harness is not about what persists.
  localStorage: (() => {
    const backing = new Map();
    return {
      getItem: (k) => (backing.has(k) ? backing.get(k) : null),
      setItem: (k, v) => backing.set(k, String(v)),
      removeItem: (k) => backing.delete(k),
    };
  })(),
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  // Enough of a window for `router.js` to load. Its `SW.router` is replaced below; what this file
  // wants from the file is `SW.appRoute`, which sits beside it.
  location: { hash: '' },
  history: { replaceState() {} },
  addEventListener() {},
  removeEventListener() {},
  React: {
    createElement: (t, p, ...c) => ({ t, p, c }),
    useState: (init) => {
      const i = cursor;
      cursor += 1;
      if (!(i in cells)) cells[i] = typeof init === 'function' ? init() : init;
      return [cells[i], (v) => { cells[i] = typeof v === 'function' ? v(cells[i]) : v; }];
    },
    useEffect: (fn) => { effects.push(fn); },
    useRef: () => ({ current: null }),
    Fragment: 'Fragment',
  },
  antd: {
    Input: Object.assign(function Input() {}, { TextArea: 'Input.TextArea' }),
    Button: 'Button', Dropdown: 'Dropdown', Tag: 'Tag', Tooltip: 'Tooltip', Space: 'Space',
    Modal: Object.assign(function Modal() {}, { confirm() {} }),
    Checkbox: Object.assign(function Checkbox() {}, { Group: 'Checkbox.Group' }),
    Radio: Object.assign(function Radio() {}, { Group: 'Radio.Group', Button: 'Radio.Button' }),
    Select: 'Select', Alert: 'Alert', Avatar: 'Avatar', Divider: 'Divider',
    Skeleton: 'Skeleton', Segmented: 'Segmented',
    message: { success() {}, info() {}, warning() {}, error() {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async (url) => {
    await new Promise((r) => setTimeout(r, 0));
    const path = String(url);
    // The document, and the file it is stored as. They are two reads because they are two things:
    // the sections the sheet renders, and the raw markdown behind the Markdown tab.
    if (path.endsWith(`/plans/${plan.id}/markdown`)) return json(RAW);
    return json(path.endsWith(`/plans/${plan.id}`) ? plan : {});
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
// `router.js` is here for `SW.appRoute`, which used to live in the Build rail and moved out with
// it (#82). The two route grammars a plan's back links are built from now sit one file apart.
for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js', 'components/conversation-list.js',
                 'router.js', 'components/plan.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

// The rail's route grammar is real (conversation-list.js above); only the hash it would be written
// into is stubbed, so what a click asks for is readable.
SW.router = { go: (path) => routed.push(path), get: () => ({ mode, a: null, b: null, query: {} }) };
SW.api.archivePlan = async (id, next) => { archived.push(`archive ${id} archived=${!!next}`); };
// The archive control reloads the panel's list on the way out, and the panel is not mounted here.
SW.store.reloadProjectPlan = () => Promise.resolve();

function* walk(node) {
  if (!node || typeof node !== 'object') return;
  if (Array.isArray(node)) { for (const c of node) yield* walk(c); return; }
  yield node;
  yield* walk(node.c);
}
const all = (tree, pred) => [...walk(tree)].filter(pred);
const labelled = (tree, label) =>
  all(tree, (n) => n.t === 'Button' && (n.c || []).flat(Infinity).includes(label))[0];
const tooltips = (tree) =>
  all(tree, (n) => n.t === 'Tooltip' && n.p && typeof n.p.title === 'string').map((n) => n.p.title);
const textOf = (node) =>
  [...walk(node)].flatMap((n) => (n.c || []).flat(Infinity)).filter((c) => typeof c === 'string').join('\n');

async function settle() {
  for (let i = 0; i < 40; i += 1) await new Promise((r) => setTimeout(r, 0));
}

async function mount(props) {
  cursor = 0;
  effects = [];
  const tree = SW.PlanDoc(props);
  effects.forEach((fn) => fn());
  await settle();
  return tree;
}

SW.store.set({
  scope: { id: 'proj', name: 'Demo Project' },
  userIndex: { 'u-me': { id: 'u-me', name: 'Me' } },
  me: { id: 'u-me' },
  members: [],
  // The conversation and the app Build already has open. Both route grammars read them, and the
  // point of going through those grammars is that a click does not silently drop either.
  thread,
  activeApp: { id: 'app_open' },
});

const props = { planId: plan.id, variant, onClose() {} };
// First mount fetches, second renders what came back.
await mount(props);
const tree = await mount(props);

const conversation = labelled(tree, 'Open conversation');
const builder = labelled(tree, 'Open in Builder');
const build = labelled(tree, 'Build this');
// One control, two words, and which word it wears is the whole of what it says about the document.
const archive = labelled(tree, 'Archive');
const unarchive = labelled(tree, 'Unarchive');
[conversation, builder, build, archive, unarchive].forEach((b) => b && b.p.onClick && b.p.onClick());
await settle();

// The raw file behind the document, reached the way a person reaches it: press the toggle, then
// read what the next render draws. Only Build's sheet offers the toggle at all, so `raw` staying
// null is the report that the file is out of reach from here.
const views = all(tree, (n) => n.t === 'Segmented')[0];
let raw = null;
if (views) {
  views.p.onChange('Markdown');
  // First mount fetches the file, second renders it.
  await mount(props);
  const after = await mount(props);
  const file = all(after, (n) => n.t === 'code')[0];
  const body = all(after, (n) => n.t === 'pre')[0];
  raw = { path: file ? textOf(file) : null, text: body ? textOf(body) : null };
}

console.log(JSON.stringify({
  offers: [conversation && 'conversation', builder && 'builder', build && 'build'].filter(Boolean),
  // Reported beside `offers` rather than inside it: `offers` is the list of ways BACK out of this
  // plan, and putting one away is not one of them.
  archiveLabel: (archive && 'Archive') || (unarchive && 'Unarchive') || null,
  routed,
  buildDisabled: Boolean(build && build.p.disabled),
  tooltips: tooltips(tree),
  views: views ? views.p.options : null,
  raw,
  archived,
}));
