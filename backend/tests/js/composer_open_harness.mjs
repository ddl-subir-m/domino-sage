// Whether the box you type the next question into is open, in both modes (#79).
//
// The store half of "a second question is accepted" has its own tests: `sendBuildPrompt` and
// `sendMessage` no longer return early on a running turn. This is the other half, and it is the
// half a person meets — a store that would take the send is worth nothing behind a composer that
// will not let anybody make it. Those two live in different files and only one of them was moved,
// so nothing but this reads them together.
//
// Input on stdin: `{ "turn": "idle" | "running" | "wedged" }`. Output is every composer either mode
// draws, by the `disabled` it was handed, because the claim is about all of them rather than about
// a particular one: Build's, Chat's, and the Landing composer a new Conversation opens on.
//
// Nothing is mounted. `createElement` is stubbed to a plain object, so calling a component returns
// the tree it would draw — which is where a prop is settled, before React is asked for anything.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { turn } = JSON.parse(fs.readFileSync(0, 'utf8'));

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, setTimeout, clearTimeout,
  setInterval: () => 1, clearInterval: () => {},
  requestAnimationFrame: (fn) => fn(),
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  location: { hash: '' },
  history: { replaceState() {} },
  addEventListener() {}, removeEventListener() {},
  open() {},
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    // No setter and no effects: every `disabled` this file reads is decided by store state on the
    // first pass, so a component that could only reach the answer by re-rendering has not made it.
    useState: (v) => [v, () => {}],
    useEffect: () => {},
    useMemo: (fn) => fn(),
    useRef: () => ({ current: null }),
    Fragment: 'Fragment',
  },
  antd: {
    Input: Object.assign(function Input() {}, { TextArea: 'Input.TextArea' }),
    Button: 'Button', Dropdown: 'Dropdown', Tag: 'Tag', Tooltip: 'Tooltip', Space: 'Space',
    Drawer: 'Drawer', Skeleton: 'Skeleton', Checkbox: 'Checkbox', Alert: 'Alert',
    Modal: { confirm: (cfg) => ({ update: () => Object.assign(cfg, {}), destroy: () => {} }) },
    message: { success() {}, error() {}, info() {}, warning() {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async () => ({ ok: true, status: 200, headers: { get: () => 'application/json' },
                        json: async () => ({}), text: async () => '{}' }),
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js', 'router.js',
                 'modes/builder.js', 'modes/chat.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

// The pieces both modes mount that this file is not about. Stubbed rather than left undefined, so
// an element carrying `disabled` is never mistaken for a missing component — and `Composer` is a
// named function because finding it in the tree is the whole measurement.
SW.Composer = function Composer() { return null; };
SW.Message = function Message() { return null; };
SW.TypingIndicator = function TypingIndicator() { return null; };
SW.ConversationRail = function ConversationRail() { return null; };
SW.PlanSheet = function PlanSheet() { return null; };
SW.BuildHistoryDrawer = function BuildHistoryDrawer() { return null; };

// A turn is running SOMEWHERE in the project, which is the state that used to close the box. Both
// flags, because Chat and Build read their own and one project runs one turn.
const running = turn !== 'idle';
SW.store.set({
  thread: { id: 'conv_1', title: 'The desk talk', artifacts: [], touched: [] },
  scope: { id: 'proj', name: 'Demo Project' },
  activeApp: { id: 'app_a', name: 'Desk dashboard' },
  apps: [{ id: 'app_a', name: 'Desk dashboard' }],
  messages: [{ role: 'user', blocks: [{ type: 'text', value: 'how many rows?' }] }],
  buildMessages: [], buildTranscript: [],
  chatRunning: running,
  buildRunning: running,
  turnWedged: turn === 'wedged',
  starters: { chat: { 'cross-industry': [{ title: 'Chart the sales', prompt: 'chart the sales' }] } },
});

function flatten(node, out = []) {
  if (!node || typeof node !== 'object') return out;
  if (Array.isArray(node)) { node.forEach((n) => flatten(n, out)); return out; }
  out.push(node);
  flatten(node.c, out);
  if (node.p) Object.values(node.p).forEach((v) => flatten(v, out));
  return out;
}

// Every composer the mode draws, not the first one: Chat has two paths into a composer and only
// one of them is on screen at a time, so reading one would leave the other free to disagree.
const composers = (tree) =>
  flatten(tree).filter((n) => n.t === SW.Composer).map((n) => !!n.p.disabled);

const drawn = {
  build: composers(SW.BuildMode({ conversationId: 'conv_1', appId: 'app_a' })),
  chat: composers(SW.ChatMode({ threadId: 'conv_1' })),
};

// The Landing composer is a different element in a different branch, reached only with no
// Conversation open — so the state is actually cleared rather than the branch asked for politely.
// Its starter buttons carry the same `disabled` and are reported beside it: a starter is a send.
SW.store.set({ thread: null, messages: [] });
// `Landing` is local to `chat.js`, so nothing exports it and the stubbed `createElement` will not
// call it — Chat's tree holds the element, and the element has to be run to get the tree below it.
// Found by the component's own name rather than by position, so moving it does not quietly turn
// this into an assertion about nothing.
const landingEl = flatten(SW.ChatMode({ threadId: null }))
  .find((n) => typeof n.t === 'function' && n.t.name === 'Landing');
if (!landingEl) throw new Error('Chat with no Conversation open no longer draws a Landing');
const landing = landingEl.t(landingEl.p);
drawn.landing = composers(landing);
// The starter buttons carry the same `disabled` and are reported beside it: a starter IS a send.
drawn.starters = flatten(landing)
  .filter((n) => n.p && n.p.className === 'sw-starter')
  .map((n) => !!n.p.disabled);
console.log(JSON.stringify(drawn));
