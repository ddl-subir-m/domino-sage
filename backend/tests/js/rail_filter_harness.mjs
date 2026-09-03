// Whether the Rail's app filter can narrow a list nobody chose to narrow (#150 follow-up).
//
// The filter is set from Build's header — "the rail follows the pick, so the two halves of the
// screen name the same app" — and the Rail now starts hidden, so it is routinely set while nothing
// is on screen. Two things then go wrong that reading the source will not show, because each is a
// rule written in one file and depended on in another:
//
//   Starting a Conversation. A new Conversation has touched no app, and `conversation-list.js`
//   draws its pending row only when no filter is set. So the row for the Conversation somebody just
//   started is absent, and the Rail says no conversation has changed that app yet — which is the
//   Rail contradicting the button that was just pressed. Three doors start one; only the expanded
//   head cleared the filter, and it did so by accident, through the `collapseRail` after it.
//
//   Opening the Rail. `toggleRail` cleared the filter on close, on the stated grounds that it would
//   otherwise "come back on the next open as a filter nobody could see they had applied". A filter
//   set WHILE the Rail was hidden arrives by that same door and was not cleared.
//
// Input on stdin: `{ "act": "collapsed-plus" | "expanded-plus" | "palette" | "open-rail" }` — what
// somebody does with an app filter already standing. Every act reports the store AND the Rail as
// drawn afterwards, because the filter is only a bug once it takes a row off the screen.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { act } = JSON.parse(fs.readFileSync(0, 'utf8'));

// One conversation that touched the app the filter names, so the list is not empty for reasons
// other than the filter, and the "no conversations have changed it" sentence is reachable only by
// the filter hiding the pending row.
const THREADS = [
  { id: 't-old', title: 'Last quarter', updatedAt: '2026-09-01T00:00:00Z', touched: [] },
];
const APPS = [{ id: 'app-x', name: 'Risk Dashboard' }];

const sandbox = {
  console, JSON, Object, String, Array, Error, Map, Set, Promise, Date, Math, Number, Boolean,
  RegExp, encodeURIComponent, decodeURIComponent, setTimeout, clearTimeout, setInterval,
  clearInterval, URLSearchParams, TextEncoder, TextDecoder, URL,
  fetch: () => Promise.reject(new Error('the harness makes no requests')),
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  document: {
    title: '', documentElement: { style: { setProperty: () => {} } },
    addEventListener: () => {}, removeEventListener: () => {}, getElementById: () => ({}),
  },
  location: { search: '', pathname: '/', href: 'http://localhost/', hash: '#/build' },
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

// The state Build leaves behind: an app picked in the header, so the filter is set, and the Rail
// hidden, which is where it now starts. A viewer, because a preference is keyed by one.
SW.store.set({
  me: { id: 'u1', name: 'Dana Reed' },
  threads: THREADS,
  apps: APPS,
  activeApp: APPS[0],
  railAppFilter: 'app-x',
  railHidden: act !== 'expanded-plus',
  thread: null,
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

// The two heads draw the same act with different controls — the collapsed one is an icon button
// carrying an aria-label, the expanded one an antd Button carrying the words — so both spellings of
// "the New conversation control" are looked for.
const newConversationButton = (tree) =>
  walk(tree).find((n) =>
    (n.p && n.p['aria-label'] === 'New conversation') || (n.c || []).includes('New conversation'));

if (act === 'open-rail') {
  SW.store.toggleRail();
} else if (act === 'palette') {
  // The third door, and the one nothing was clearing for. The palette's own helper is private to
  // its module, so this is the store call it makes plus the navigation that follows it.
  SW.store.newConversation();
} else {
  // The Rail's two heads, pressed through the real handler the Rail draws.
  newConversationButton(SW.ConversationRail({ mode: 'build' })).p.onClick();
}

// The Rail as somebody would see it next. Forced open, because a filter that hides a row is only
// visible once the panel is: the collapsed head draws no list at all.
SW.store.set({ railHidden: false });
const drawn = walk(SW.ConversationRail({ mode: 'build' }));
const after = SW.store.get();

console.log(JSON.stringify({
  railAppFilter: after.railAppFilter,
  pendingConversation: after.pendingConversation,
  // The row for the Conversation that was just started. Absent is the bug.
  pendingRowDrawn: drawn.some((n) => typeof n.t === 'function' && n.t.name === 'PendingConversationRow'),
  // The "Only <app>" chip, which is the filter saying it is there.
  filterChip: drawn.some((n) => n.p && n.p.className === 'sw-rail-filter'),
  // Whatever the Rail says when it has nothing to show, which is where the contradiction landed.
  emptyText: (drawn.find((n) => n.p && n.p.className === 'sw-rail-empty sw-secondary') || {}).c || null,
  // Rows of real history, so a cleared filter is told from an empty list.
  rows: drawn.filter((n) => n.t === SW.ConversationRow).map((n) => n.p.thread.id),
}));
