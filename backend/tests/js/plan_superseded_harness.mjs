// Reloads a Build conversation whose plan a second Conversation superseded, and reports the card.
//
// The whole defect (#59) is what the FIRST conversation sees after the second one plans into the
// same Built App: nothing had changed for it, so its card went on offering "Approve & build" for a
// plan the app had stopped holding. So this drives the real path a person takes to get back there
// — `store.loadBuild()` re-reads that conversation's transcript from the server — rather than
// hand-building the block the card renders. The server row under test is the one
// `_supersede_live_plan` appends.
//
// `useState` is a real cell, like the sibling feedback harness: the card is mounted twice so what
// a second render draws is what gets asserted.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { history, threads = [] } = JSON.parse(fs.readFileSync(0, 'utf8'));

const CONVERSATION = 'conv_first';

const json = (body) => ({
  ok: true, status: 200,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

function serve(url) {
  const path = String(url).replace(/^\.\/api/, '');
  if (path.startsWith('/project/history')) return json({ history });
  if (path.startsWith('/apps')) return json({ items: [] });
  if (path.startsWith('/bindings')) return json({ bindings: [] });
  return json({});
}

let cells = [];
let cursor = 0;
// Where "reopen" actually went. The way back IS the plan it opens, so this is what the person gets.
const opened = [];

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, setTimeout, clearTimeout, setInterval, clearInterval,
  requestAnimationFrame: (fn) => fn(),
  localStorage: (() => {
    const backing = new Map();
    return {
      getItem: (k) => (backing.has(k) ? backing.get(k) : null),
      setItem: (k, v) => backing.set(k, String(v)),
      removeItem: (k) => backing.delete(k),
    };
  })(),
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  React: {
    createElement: (t, p, ...c) => ({ t, p, c }),
    useState: (init) => {
      const i = cursor;
      cursor += 1;
      if (!(i in cells)) cells[i] = typeof init === 'function' ? init() : init;
      return [cells[i], (v) => { cells[i] = typeof v === 'function' ? v(cells[i]) : v; }];
    },
    useEffect: () => {}, useRef: () => ({ current: null }), Fragment: 'Fragment',
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
    return serve(url);
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
// `prefs.js` comes along with the store: since #57, readBuildTranscript asks the viewer which
// conversation view they are in before it reads anything. The default is split, which is the
// path these tests are about — the plan card, not the merged transcript.
for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js', 'components/message-blocks.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

function* walk(node) {
  if (!node || typeof node !== 'object') return;
  if (Array.isArray(node)) { for (const c of node) yield* walk(c); return; }
  yield node;
  yield* walk(node.c);
}
const all = (tree, pred) => [...walk(tree)].filter(pred);
const strings = (node) => [...walk(node)].flatMap((n) => (n.c || []).flat(Infinity))
  .filter((c) => typeof c === 'string');
const buttons = (tree) => all(tree, (n) => n.t === 'Button')
  .map((n) => (n.c || []).flat(Infinity).filter((c) => typeof c === 'string').join(''));
const labelled = (tree, label) =>
  all(tree, (n) => n.t === 'Button' && (n.c || []).flat(Infinity).includes(label))[0];

async function settle() {
  for (let i = 0; i < 40; i += 1) await new Promise((r) => setTimeout(r, 0));
}

SW.store.set({
  thread: { id: CONVERSATION, title: 'The desk talk', artifacts: [] },
  scope: { id: 'proj', name: 'Demo Project' },
  threads,
  activeApp: { id: 'app_a' },
});
SW.store.openPlanArtifact = (planId) => opened.push(planId);

await SW.store.loadBuild();
await settle();

const block = SW.store.get().buildMessages
  .flatMap((m) => m.blocks || [])
  .find((b) => b.type === 'build_plan');

const props = { block };
cursor = 0;
const Card = SW.MessageBlock(props).t;
Card(props);
const tree = Card(props);

const reopen = labelled(tree, 'Reopen this plan');
const newer = labelled(tree, 'Open the newer plan');
[reopen, newer].forEach((b) => b && b.p.onClick && b.p.onClick());

console.log(JSON.stringify({
  pending: Boolean(block && block.pending),
  superseded: (block && block.superseded) || null,
  buttons: buttons(tree),
  text: strings(tree).join(' '),
  opened,
}));
