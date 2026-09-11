// Whether a New app puts the Rail away (#150 follow-up).
//
// `railHidden` is one value carrying two meanings, and only one of them is on file. `toggleRail`
// is the person's own choice and writes the preference; `collapseRail` and `expandRail` are the UI
// moving the panel on its own behalf and deliberately write nothing. The write-free pair is what
// makes this only visible by running it: the auto-expand has no end of its own, so what closes it
// again is a fact about every OTHER door, spread across three files.
//
// The path this covers crosses both modes. Chat's collapsed head opens the Rail through
// `expandRail` — the pending row is the only thing on screen saying the press worked — and nothing
// closes it again but a click on a row, which somebody who STARTED the conversation never makes.
// One Rail serves both modes (#82), so the open panel crosses into Build intact, and Build is
// where 260px of a list you have finished reading costs the most.
//
// Two ends to that: the New app in Build, and the first message in Chat, which is where the
// auto-expand's own reason runs out (`newThread` — "the flag has done its job"). The second is
// guarded where the first is not, and the guard is the point. A row click may close a Rail
// somebody opened by hand, because it ANSWERS the Rail. Typing answers nothing it asked.
//
// Input on stdin: `{ "act": "chat-plus-then-new-app" | "rail-opened-by-hand" | "chat-plus-then-type"
// | "hand-opened-then-type" }` — how the Rail came to be open, and what was done over it. Every act
// reports the panel AND the preference, because a collapse that took the person's stored choice
// with it would fix this by breaking the rule above it.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { act } = JSON.parse(fs.readFileSync(0, 'utf8'));

const THREADS = [
  { id: 't-old', title: 'Last quarter', updatedAt: '2026-09-01T00:00:00Z', touched: [] },
];

// Real storage, not a stub that forgets: the preference is half of what is asserted, and a write
// that vanished would read exactly like a write that never happened.
const store = {};
const localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
  removeItem: (k) => { delete store[k]; },
};

const sandbox = {
  console, JSON, Object, String, Array, Error, Map, Set, Promise, Date, Math, Number, Boolean,
  RegExp, encodeURIComponent, decodeURIComponent, setTimeout, clearTimeout, setInterval,
  clearInterval, URLSearchParams, TextEncoder, TextDecoder, URL,
  fetch: () => Promise.reject(new Error('the harness makes no requests')),
  localStorage,
  document: {
    title: '', documentElement: { style: { setProperty: () => {} } },
    addEventListener: () => {}, removeEventListener: () => {}, getElementById: () => ({}),
  },
  location: { search: '', pathname: '/', href: 'http://localhost/', hash: '#/chat' },
  addEventListener: () => {}, removeEventListener: () => {},
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
    useEffect: () => {},
    useRef: () => ({ current: null }),
    Fragment: 'Fragment',
  },
  antd: {
    Tooltip: 'Tooltip', Button: 'Button', Input: 'Input', Dropdown: 'Dropdown', Modal: {},
    message: { info: () => {}, success: () => {}, error: () => {}, warning: () => {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  EventSource: function () {},
  SW: {},
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'prefs.js', 'router.js', 'store.js', 'components/conversation-list.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

// Every read after the create answers empty, which is the whole of what the reload needs to be
// here: what is asserted is the panel, and the panel is decided before any of it lands.
SW.api = new Proxy({}, {
  get: (_, name) => {
    if (name === 'createApp') return () => Promise.resolve({ id: 'app-new', name: 'Untitled' });
    // What Chat's first message mints. Untitled, as the server does — it names the Thread from
    // the text afterwards — so the row that replaces the placeholder is a real one.
    if (name === 'createThread') return () => Promise.resolve({ id: 't-new', title: 'Untitled' });
    // The Thread index, re-read at the end of `newThread`. It has to answer with a LIST: on the
    // act that leaves the Rail open — the point of that act — the list is what the Rail then draws,
    // and an empty object reaching `threads.filter` is the harness breaking, not the rule.
    if (name === 'threads') return () => Promise.resolve(THREADS);
    return () => Promise.resolve({});
  },
});

SW.store.set({
  me: { id: 'u1', name: 'Dana Reed' },
  threads: THREADS,
  apps: [],
  activeApp: null,
  thread: null,
  railHidden: true,
  pendingConversation: false,
});

function walk(tree) {
  const nodes = [];
  (function step(node) {
    if (!node || typeof node !== 'object') return;
    if (Array.isArray(node)) return node.forEach(step);
    nodes.push(node);
    (node.c || []).forEach(step);
  })(tree);
  return nodes;
}

const newConversationButton = (tree) =>
  walk(tree).find((n) =>
    (n.p && n.p['aria-label'] === 'New conversation') || (n.c || []).includes('New conversation'));

if (act === 'chat-plus-then-new-app' || act === 'chat-plus-then-type') {
  // Chat's collapsed head, pressed through the handler the Rail actually draws. This is the door
  // that opens the Rail without anyone choosing to, and the door the report came in through.
  newConversationButton(SW.ConversationRail({ mode: 'chat' })).p.onClick();
} else {
  // The other way in: opened by hand, which is the only door that writes the preference. The
  // collapse must reach the panel and leave that record alone.
  SW.store.toggleRail();
}

const openedBefore = !SW.store.get().railHidden;
// What was done over the open Rail. `newThread` is the one door a conversation opens from nothing
// through — Chat's send, Build's first type and an attachment all reach it — so this is the act,
// not the composer above it.
if (act.endsWith('-type')) await SW.store.newThread();
else await SW.store.createApp();
const after = SW.store.get();

console.log(JSON.stringify({
  // Open before the New app, or the test proves nothing about what closed it.
  openedBefore,
  railHidden: after.railHidden,
  // What is on file for this viewer. `undefined` means nothing was ever written.
  storedRailHidden: JSON.parse(store['sw.prefs'] || '{}').u1?.railHidden,
  // The Rail as it would next be drawn: collapsed draws the two icon heads, never the list.
  railDrawsTheList: walk(SW.ConversationRail({ mode: 'build' })).some(
    (n) => n.p && n.p.className === 'sw-rail-search'),
  activeAppId: (after.activeApp || {}).id || null,
  // The conversation that replaced the placeholder, so a Rail closing over nothing is told from
  // one closing because its reason ran out.
  threadId: (after.thread || {}).id || null,
}));
