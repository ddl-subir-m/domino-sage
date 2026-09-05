// What Build's empty state says, given an app and a plan (#172).
//
// Nothing is mounted and no child component is called. `createElement` is stubbed to a plain object,
// and the greeting is built by `BuildMode` itself — so the whole claim of this ticket, which is
// WHICH SENTENCES ARE ON SCREEN, is settled in the tree the mode returns. Calling the children would
// only draw the rail and the composer, neither of which says anything about a plan.
//
// `useEffect` is a no-op: every effect in the mode is a read, and a harness that ran them would be
// asserting on what a stubbed `fetch` answered rather than on the state the caller set up.
//
// Input on stdin: `{ "app": {...} | null, "plan": {...} | null, "running": bool, "turn": {...} }`
// — the rail row the server sends for the selected app, `/api/project/plan`'s answer, both
// verbatim, whether the Project's lock is held at all (a fact about the Project, not about this
// tab), and the turn holding it. `turn` is null for a lock held by something with no name, which
// is a state the store really reaches between two queued turns. `wedged` is the third state: the
// server reports a wedged turn as NOT running, so the lock is held and `running` is false.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { app = null, plan = null, running = false, turn = null, wedged = false } =
  JSON.parse(fs.readFileSync(0, 'utf8'));

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, Infinity,
  setTimeout, clearTimeout, setInterval: () => 1, clearInterval: () => {},
  requestAnimationFrame: (fn) => fn(),
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  location: { hash: '' },
  history: { replaceState() {} },
  addEventListener() {}, removeEventListener() {},
  open() {},
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
    Modal: Object.assign(function Modal() {}, { confirm() {} }),
    message: { success() {}, error() {}, info() {}, warning() {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async () => ({ ok: true, status: 200, headers: { get: () => 'application/json' },
                        json: async () => ({}), text: async () => '' }),
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js', 'router.js',
                 'components/conversation-list.js', 'components/resource-panel.js',
                 'components/build-history.js', 'modes/builder.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

SW.store.set({
  scope: { id: 'proj', name: 'Demo Project' },
  thread: { id: 'thr_1', title: 'A conversation', artifacts: [], history: [] },
  apps: app ? [{ ...app, selected: true }] : [],
  activeApp: app,
  buildMessages: [],
  buildTranscript: [],
  buildTyping: '',
  buildRunning: running,
  runningTurn: turn,
  turnWedged: wedged,
  projectPlan: plan,
});

// The tree as data. Function components are stepped OVER rather than called: the greeting is the
// mode's own markup, and the children draw the rail and the composer.
function* walk(node) {
  if (!node || typeof node !== 'object') return;
  if (Array.isArray(node)) { for (const c of node) yield* walk(c); return; }
  yield node;
  yield* walk(node.c);
}
const byClass = (node, cls) => [...walk(node)].filter((n) => (n.p || {}).className === cls);
// The strings directly under one block, joined — a sentence split across two children is still one
// sentence to read.
const said = (node) => (node ? (node.c || []).flat(Infinity).filter((c) => typeof c === 'string')
  .join('') : null);

// One block's title and detail. Read off the block's OWN descendants, so a note nested inside the
// greeting answers for itself rather than for the greeting around it.
function lines(node) {
  return {
    title: said(byClass(node, 'sw-empty-title')[0] || null),
    detail: said(byClass(node, 'sw-empty-detail')[0] || null),
  };
}

const tree = SW.BuildMode({ conversationId: 'thr_1', appId: app ? app.id : null });
const greeting = byClass(tree, 'sw-build-greeting')[0] || null;
const notes = greeting ? byClass(greeting, 'sw-build-resume-note') : [];

console.log(JSON.stringify({
  shown: Boolean(greeting),
  // The greeting's own two lines, taken from the children it holds directly: the notes below sit
  // inside it, and a deep read would hand back the first note's title instead.
  greeting: greeting ? lines({ c: (greeting.c || []).flat(Infinity)
    .filter((n) => n && typeof n === 'object'
            && String((n.p || {}).className || '').startsWith('sw-empty-')) }) : null,
  notes: notes.map(lines),
}));
