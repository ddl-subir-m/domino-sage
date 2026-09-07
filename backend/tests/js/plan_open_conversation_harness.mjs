// Presses the two doors into a Conversation that are not the rail's own row, and reports every
// surface each press moved (#198).
//
// A Conversation opened in Build is two answers, not one: which transcript, and which Built App the
// preview beside it holds. `SW.openConversation` is where both are given, and the two doors here
// wrote the route by hand instead — so the transcript moved and the app did not, and Build sat
// paired with the app you came from. That mismatch has no shape in a rendered tree, which is why
// what is read back here is the CALLS a click made rather than what it drew.
//
// The two doors are driven through their real components, because the bug was in what each one
// called and a harness that called `openConversation` itself would prove nothing about either:
//   plan — the plan sheet's "Open conversation", which knows the app from the plan document.
//   chip — a Resource row's "Open <conversation>", which knows no app at all.
//
// `openThread` is never reached: it is a server round trip, and every claim here is about what
// happens on the click, before one could land. `selectApp` is stubbed for the same reason — the
// real one reloads the whole of Build behind a fetch — so what it recorded is the answer Build
// would have been given.
//
// Input on stdin: `{ "door": "plan" | "chip", "mode": "build" | "chat", "app": "<id>" | "" }`.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { door, mode, app = '' } = JSON.parse(fs.readFileSync(0, 'utf8'));

// The Built App Build already has in the preview. Every case here opens a Conversation belonging
// to a DIFFERENT app, which is the shape the bug needs: same app and there is nothing to move.
const OPEN_APP = 'app_open';
// The app the plan stands in, and so the app of the conversation that produced it.
const OTHER_APP = 'app_other';
const ORIGIN = 'thr_origin';

const PLAN = {
  id: '001', title: 'A desk exposure dashboard.', version: 1, status: 'draft',
  author: 'u-me', updatedAt: '2026-08-28T10:00:00Z', summary: '', sections: {},
  comments: [], approvals: [], reviewers: [],
  originThreadId: ORIGIN, originLive: true, appId: app, archived: false,
};

// The Resource behind the chip door. `liveness: 'missing'` and a holder are what put the
// "Open <conversation>" item on the menu at all — the item only exists for a Resource that Domino
// no longer holds and that a Conversation still uses, which is the row that offers a way to go
// read why. `membershipParent` is the second gate on the same list.
const RESOURCE = {
  id: 'res_1', name: 'q3_desks.csv', kind: 'file', source: 'project',
  liveness: 'missing', membershipParent: 'grp_files',
  usedBy: [], heldBy: [{ threadId: ORIGIN, title: 'Sales trends' }],
};

// What each click asked for. Three separate lists rather than one log, because the failure was
// never that a call was missing everywhere — the route was always written. It was that ONE of the
// three moved and the others did not.
const routed = [];
const selected = [];

let cells = [];
let cursor = 0;
let effects = [];

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, setTimeout, clearTimeout, setInterval, clearInterval,
  requestAnimationFrame: (fn) => fn(),
  // `prefs.js` rides along with the store, and the rail collapse this click performs reads it.
  // In-memory: nothing here is a claim about what survives a reload.
  localStorage: (() => {
    const backing = new Map();
    return {
      getItem: (k) => (backing.has(k) ? backing.get(k) : null),
      setItem: (k, v) => backing.set(k, String(v)),
      removeItem: (k) => backing.delete(k),
    };
  })(),
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
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
    const body = path.endsWith(`/plans/${PLAN.id}`) ? PLAN : {};
    return {
      ok: true, status: 200,
      headers: { get: () => 'application/json' },
      json: async () => body,
      text: async () => JSON.stringify(body),
    };
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
// `conversation-list.js` for `SW.openConversation` — the door itself, real, since the whole claim
// is that both components reach it. `router.js` for the route grammar it writes through.
for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js', 'router.js',
                 'components/conversation-list.js', 'components/plan.js',
                 'components/resource-panel.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

SW.router = { go: (path) => routed.push(path), get: () => ({ mode, a: null, b: null, query: {} }) };
// The one stub inside the door. The real call fetches and then reloads the whole of Build, which
// is a round trip and so cannot be part of a claim about the click itself.
SW.store.selectApp = async (id) => { selected.push(typeof id === 'string' ? id : id && id.id); };

SW.store.set({
  scope: { id: 'proj', name: 'Demo Project' },
  userIndex: { 'u-me': { id: 'u-me', name: 'Me' } },
  me: { id: 'u-me' },
  members: [],
  datasetTargets: [],
  // Build as it stands when either door is pressed: one app in the preview, one conversation open,
  // and — for the plan door — the sheet over the preview that reads the plan.
  activeApp: { id: OPEN_APP },
  thread: { id: 'thr_here', title: 'What is on screen' },
  planViewerId: PLAN.id,
  railHidden: false,
});

function* walk(node) {
  if (!node || typeof node !== 'object') return;
  if (Array.isArray(node)) { for (const c of node) yield* walk(c); return; }
  yield node;
  yield* walk(node.c);
}
const all = (tree, pred) => [...walk(tree)].filter(pred);
const labelled = (tree, label) =>
  all(tree, (n) => n.t === 'Button' && (n.c || []).flat(Infinity).includes(label))[0];

async function settle() { for (let i = 0; i < 40; i += 1) await new Promise((r) => setTimeout(r, 0)); }

async function mount(component, props) {
  cursor = 0;
  effects = [];
  const tree = component(props);
  effects.forEach((fn) => fn());
  await settle();
  return tree;
}

// What the press found, and the press itself. Kept apart so a door that drew nothing to press is
// reported as that, rather than as a click whose calls were all empty.
let offered = null;
let press = null;

if (door === 'plan') {
  // `variant: 'side'` and `planViewerId` set together: the sheet is the only place a plan is read
  // from beside a conversation, and those two are how the app has it on screen.
  const props = { planId: PLAN.id, variant: 'side', onClose() {} };
  // First mount fetches, second renders what came back.
  await mount(SW.PlanDoc, props);
  const tree = await mount(SW.PlanDoc, props);
  const button = labelled(tree, 'Open conversation');
  offered = Boolean(button);
  press = button && button.p.onClick;
} else {
  const tree = await mount(SW.ResourceRow, {
    resource: RESOURCE,
    app: { id: OPEN_APP, name: 'Sales trends' },
    onOpen() {},
  });
  const dropdown = all(tree, (n) => n.t === 'Dropdown' && n.p && n.p.menu)[0];
  const menu = dropdown && dropdown.p.menu;
  const item = menu && (menu.items || []).find((i) => i.key === `open-chat:${ORIGIN}`);
  offered = Boolean(item);
  press = item && (() => menu.onClick({ key: item.key }));
}

if (press) press();
// Read with no await between: the claim is that the switch is whole on the click. `openThread`
// clears the plan sheet too, at the far end of a round trip that is not stubbed in here — so a
// sheet still open at this line is the one a person keeps staring at while the app changes behind
// it.
const onClick = {
  routed: [...routed],
  selected: [...selected],
  planViewerId: SW.store.get().planViewerId,
  railHidden: SW.store.get().railHidden,
};
await settle();

console.log(JSON.stringify({ offered, onClick }));
