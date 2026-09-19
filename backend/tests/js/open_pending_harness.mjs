// What is on screen WHILE a conversation opens, and whether a row that lost its open can be
// clicked back (#455).
//
// Sibling of open_race_harness, and it cannot be folded into that one. That harness is store-only
// and asks where the store SETTLES; both faults here live in the interval before it settles and in
// the wiring between the router and the mode, so neither is visible without mounting the mode and
// the rail and running the effect that fires the open.
//
// So this harness runs React's hooks for real — cells per component, deps compared between passes,
// effects run when they change. A stubbed `useEffect: () => {}` would make every assertion here
// vacuous: the defect IS a dependency array that did not change, and a harness that never runs an
// effect cannot tell a re-run from a skipped one.
//
// `location.hash` is a real setter that dispatches `hashchange` for the same reason. `SW.router.go`
// takes two different paths depending on whether the hash already holds what it is being sent, and
// the click this ticket is about is the one that takes the second — a plain string property would
// have quietly tested only the first.
//
// Input on stdin: a list of acts. Every act reports the store AND the screen, because a marker that
// is set and not drawn fixes nothing.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const acts = JSON.parse(fs.readFileSync(0, 'utf8'));

// --- the server ------------------------------------------------------------
// Three conversations with their own turns, so a view holding one while the route names another
// shows up as the wrong words rather than as a missing id. `conv_gone` is not here: asking for it
// 404s, which is the failure path `modes/chat.js` catches and routes away from.
const THREADS = {
  conv_a: { id: 'conv_a', title: 'Amortisation', history: [{ type: 'user', text: 'a turn' }],
            artifacts: [], touched: [] },
  // `planId` so the dock's plan bar is REACHABLE. Without one, asserting that the bar is absent
  // while another Conversation arrives would pass over a bar that is never drawn at all.
  conv_b: { id: 'conv_b', title: 'Bookings', history: [{ type: 'user', text: 'b turn' }],
            artifacts: [], touched: [], planId: 'plan_b' },
  conv_x: { id: 'conv_x', title: 'Cross-sell', history: [{ type: 'user', text: 'x turn' }],
            artifacts: [], touched: [] },
  // In ANOTHER Project. Opening it is the slowest open there is, because `openThread` awaits
  // `adoptThreadScope`, which calls `setScope`, which reloads the Project's data and thread
  // list before the remaining leg of the open runs. That is the path a clear placed in the
  // scope switch takes the marker off, mid-open.
  conv_far: { id: 'conv_far', title: 'Far side', projectId: 'proj2',
              history: [{ type: 'user', text: 'far turn' }], artifacts: [], touched: [] },
};

// The rail's own rows, which are not the conversations themselves: `conv_gone` is listed and not
// served, which is how a click reaches a 404.
const RAIL_ROWS = [
  ...Object.values(THREADS).map((t) => ({
    id: t.id, title: t.title, updatedAt: '2026-09-18T10:00:00Z', touched: [],
  })),
  { id: 'conv_gone', title: 'Deleted elsewhere', updatedAt: '2026-09-18T10:00:00Z', touched: [] },
];

// How long each conversation's own GET takes. The interval this whole ticket is about.
let latency = {};

const json = (body, status = 200) => ({
  ok: status < 400, status, statusText: status === 404 ? 'Not Found' : 'OK',
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

// Every conversation read the client made, in order. A count rather than a flag, because "the
// second click on a row that is already loading costs nothing" is a claim about round trips and
// nothing drawn on screen can tell a re-fetch from a no-op.
const threadReads = [];

function serve(url) {
  const path = String(url).replace(/^\.\/api/, '').split('?')[0];
  let m;
  if ((m = path.match(/^\/threads\/([^/]+)$/))) threadReads.push(m[1]);
  if ((m = path.match(/^\/threads\/([^/]+)\/context$/))) return json({ items: [] });
  if ((m = path.match(/^\/threads\/([^/]+)$/))) {
    return THREADS[m[1]] ? json(THREADS[m[1]]) : json({ detail: 'No such conversation' }, 404);
  }
  // A bare array, as `list_threads` returns one — `api.threads` does not unwrap `.items` the
  // way `api.apps` does. `setScope` calls `loadThreadList`, so a cross-Project open REPLACES
  // this list mid-flight; the wrong shape here turned the rail's `threads` into an object.
  if (path === '/threads') return json(RAIL_ROWS);
  if (path.startsWith('/plans/')) return json({ id: 'plan_b', title: 'A plan', steps: [] });
  if (path.startsWith('/apps')) return json({ items: [] });
  return json({});
}

// `:context` is a separate key from the conversation's own read, and it is what opens a window
// between the single write and the end of `openThread` — the attachments read is awaited after
// the view is painted. A marker cleared a line late rather than IN the write is only visible
// inside that window, so without this the test for it would pass on a fix that does not work.
function delayFor(url) {
  // The thread LIST, which is not a thread. `setScope` awaits `loadThreadList` at the end, so
  // this key is what holds a cross-Project open open long enough to look at it from INSIDE
  // `adoptThreadScope` — the window a clear placed in the scope switch takes the marker off.
  if (/\/threads(\?|$)/.test(String(url))) return latency['threads:list'] || 0;
  const m = String(url).match(/\/threads\/([^/?]+)(\/context)?/);
  if (!m) return 0;
  return (m[2] ? latency[`${m[1]}:context`] : latency[m[1]]) || 0;
}

// --- a browser that dispatches ---------------------------------------------
const domListeners = {};
const location = {
  search: '', pathname: '/', href: 'http://localhost/',
  _hash: '#/chat',
  get hash() { return this._hash; },
  set hash(value) {
    this._hash = value;
    (domListeners.hashchange || []).forEach((fn) => fn());
  },
};

// --- hooks that actually hold ----------------------------------------------
// One cell list per component instance, addressed by where it sits in the tree. Deps are compared
// against the previous pass and the effect is run when they differ, which is the rule the defect
// broke. `useState` re-renders nothing on its own: the harness re-renders between acts, which is
// what a store notify does in the app.
const cells = new Map();
let currentCell = null;
let hookIndex = 0;
const pendingEffects = [];

function cellFor(path) {
  if (!cells.has(path)) cells.set(path, { hooks: [] });
  return cells.get(path);
}

const React = {
  createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
  Fragment: 'Fragment',
  useState(init) {
    const cell = currentCell;
    const i = hookIndex++;
    if (!('value' in (cell.hooks[i] || {}))) {
      cell.hooks[i] = { value: typeof init === 'function' ? init() : init };
    }
    return [cell.hooks[i].value, (next) => {
      cell.hooks[i].value = typeof next === 'function' ? next(cell.hooks[i].value) : next;
    }];
  },
  useRef(init) {
    const cell = currentCell;
    const i = hookIndex++;
    if (!cell.hooks[i]) cell.hooks[i] = { current: init };
    return cell.hooks[i];
  },
  useEffect(fn, deps) {
    const cell = currentCell;
    const i = hookIndex++;
    const slot = cell.hooks[i] || (cell.hooks[i] = { deps: undefined, mounted: false });
    const changed = !slot.mounted || deps === undefined ||
      deps.length !== (slot.deps || []).length ||
      deps.some((d, n) => !Object.is(d, slot.deps[n]));
    slot.deps = deps;
    slot.mounted = true;
    // Queued rather than run inline: an effect that opens a conversation must not write the store
    // half way through the pass that is reading it.
    if (changed) pendingEffects.push(fn);
  },
};

// --- the sandbox ------------------------------------------------------------
const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, TextEncoder, TextDecoder, URL, URLSearchParams,
  encodeURIComponent, decodeURIComponent,
  setTimeout, clearTimeout, setInterval, clearInterval,
  requestAnimationFrame: (fn) => fn(),
  localStorage: (() => {
    const backing = new Map();
    return {
      getItem: (k) => (backing.has(k) ? backing.get(k) : null),
      setItem: (k, v) => backing.set(k, String(v)),
      removeItem: (k) => backing.delete(k),
    };
  })(),
  location,
  // `SW.router.replace` is the door out of a failed open, so the failure path cannot be reached
  // without it. It writes the hash without dispatching, exactly as the browser does, and the
  // router calls `handleChange` itself afterwards.
  history: { replaceState: (_s, _t, next) => { location._hash = next; } },
  document: {
    title: '', documentElement: { style: { setProperty: () => {} } },
    addEventListener: () => {}, removeEventListener: () => {},
    querySelector: () => null, getElementById: () => ({}), body: {},
  },
  addEventListener: (ev, fn) => { (domListeners[ev] = domListeners[ev] || []).push(fn); },
  removeEventListener: () => {},
  React,
  antd: {
    Input: Object.assign(function Input() { return null; }, { TextArea: 'Input.TextArea' }),
    Button: 'Button', Dropdown: 'Dropdown', Tag: 'Tag', Tooltip: 'Tooltip', Space: 'Space',
    Skeleton: 'Skeleton', Modal: { confirm() {} },
    message: { success() {}, error() {}, info() {}, warning() {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  EventSource: function () {},
  fetch: async (url) => {
    await new Promise((r) => setTimeout(r, delayFor(url)));
    return serve(url);
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'api.js', 'prefs.js', 'router.js', 'store.js',
                 'components/conversation-list.js', 'modes/chat.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

// --- rendering --------------------------------------------------------------
// Function components are INVOKED. A walk that only reads the element tree never enters them, so
// every assertion about what a row says would be an assertion about a prop nobody drew.
function render(node, path) {
  if (!node || typeof node !== 'object') return node;
  if (Array.isArray(node)) return node.map((n, i) => render(n, `${path}[${i}]`));
  if (typeof node.t === 'function') {
    const name = node.t.name || 'anon';
    const key = (node.p && node.p.key) !== undefined ? node.p.key : '';
    const own = `${path}/${name}#${key}`;
    const previousCell = currentCell;
    const previousIndex = hookIndex;
    currentCell = cellFor(own);
    hookIndex = 0;
    let out;
    try {
      out = node.t(node.p || {});
    } finally {
      currentCell = previousCell;
      hookIndex = previousIndex;
    }
    return { t: node.t, p: node.p || {}, c: [render(out, own)] };
  }
  return { t: node.t, p: node.p || {}, c: (node.c || []).map((n, i) => render(n, `${path}[${i}]`)) };
}

let tree = null;

function pass() {
  // The Rail closes itself when a row is clicked (#150), and a closed rail draws no rows at all.
  // Forced open before every pass so the next act has something to click — the fold is not what
  // this harness is about.
  SW.store.set({ railHidden: false });
  // Render, run whatever the render queued, render what that wrote. The second pass is not a
  // nicety: the effect is what STARTS the open, so a harness that stopped after the first one
  // would report the screen from the instant before the click was acted on and call it the
  // screen during the open. Repeated, because an effect may write state that queues another.
  for (let i = 0; i < 5; i += 1) {
    const route = SW.router.get();
    tree = render({ t: SW.ChatMode, p: { threadId: route.mode === 'chat' ? route.a : null }, c: [] },
                  'root');
    const queued = pendingEffects.splice(0, pendingEffects.length);
    if (!queued.length) return;
    queued.forEach((fn) => fn());
  }
  throw new Error('effects did not settle');
}

function walk(node, out = []) {
  if (!node || typeof node !== 'object') return out;
  if (Array.isArray(node)) { node.forEach((n) => walk(n, out)); return out; }
  out.push(node);
  (node.c || []).forEach((n) => walk(n, out));
  return out;
}

// Every string drawn underneath a node, which is how the row's own words are read rather than the
// prop that was passed in to produce them.
function text(node) {
  return walk(node).filter((n) => typeof n === 'string').join(' ');
}

function words(node, out = []) {
  if (node === null || node === undefined || node === false) return out;
  if (typeof node === 'string') { out.push(node); return out; }
  if (Array.isArray(node)) { node.forEach((n) => words(n, out)); return out; }
  if (typeof node === 'object') (node.c || []).forEach((n) => words(n, out));
  return out;
}

function snapshot() {
  const state = SW.store.get();
  const nodes = walk(tree);
  const rows = nodes.filter((n) => n.t === SW.ConversationRow).map((n) => {
    const drawn = (n.c || [])[0];
    const root = drawn && drawn.p ? drawn.p : {};
    return {
      id: n.p.thread.id,
      // From the CLASS the row drew, not from the prop it was handed: the class is what decides
      // whether anything is on screen.
      active: /\bis-active\b/.test(root.className || ''),
      opening: /\bis-opening\b/.test(root.className || ''),
      busy: root['aria-busy'] || null,
      says: words(drawn).join(' '),
    };
  });
  const inner = nodes.find((n) => n.p && typeof n.p.className === 'string' &&
    n.p.className.includes('sw-messages-inner'));
  return {
    // Counted, not inferred from the wrapper's class. Keyed on the class alone, deleting both
    // `h(Skeleton, …)` calls left `pane: 'skeleton'` and `drawnTurns: []` exactly as before, so
    // every assertion about the wait stayed green over a blank pane.
    skeletons: nodes.filter((n) => n.t === 'Skeleton').length,
    // The composer dock has to outlive the open — `TurnBar` inside it is where Stop lives, and a
    // project-wide turn can be running while another Conversation is opened.
    dock: nodes.some((n) => n.p && n.p.className === 'sw-composer-dock'),
    composerDisabled: (nodes.find((n) => n.p && 'onSend' in n.p && 'placeholder' in n.p)
      || { p: {} }).p.disabled ?? null,
    planbar: nodes.some((n) => n.p && n.p.className === 'sw-chat-planbar'),
    hash: location.hash,
    thread: state.thread ? state.thread.id : null,
    openingThreadId: state.openingThreadId,
    rows,
    // What the centre pane is drawing. `skeleton` is the fix; `turns` while a different
    // conversation is arriving is the bug this ticket opened on.
    pane: inner && /sw-messages-opening/.test(inner.p.className) ? 'skeleton'
      : nodes.some((n) => n.p && n.p.message) ? 'turns'
      : 'landing',
    // The turns actually on screen, so "the previous conversation is still there" is a fact about
    // words rather than about a flag.
    drawnTurns: nodes.filter((n) => n.p && n.p.message)
      .map((n) => (n.p.message.blocks || []).map((b) => b.value || '').join('')).filter(Boolean),
    threadReads: threadReads.slice(),
  };
}

async function settle(ms = 150) {
  await new Promise((r) => setTimeout(r, ms));
  for (let i = 0; i < 60; i += 1) await new Promise((r) => setTimeout(r, 0));
}

function clickRow(id) {
  const row = walk(tree).find((n) => n.t === SW.ConversationRow && n.p.thread.id === id);
  if (!row) throw new Error(`no row for ${id}`);
  const drawn = (row.c || [])[0];
  if (!drawn || !drawn.p || !drawn.p.onClick) throw new Error(`row ${id} draws no click`);
  drawn.p.onClick();
}

// --- the run ----------------------------------------------------------------
SW.store.set({
  me: { id: 'u1', name: 'Dana Reed' },
  scope: { id: 'proj', name: 'Demo Project' },
  projects: [{ id: 'proj', name: 'Demo Project' }, { id: 'proj2', name: 'Other Project' }],
  threads: RAIL_ROWS,
  apps: [],
  railHidden: false,
});

pass();

const report = [];
for (const act of acts) {
  latency = act.latency || latency;
  if (act.act === 'click') {
    // The real handler on the real row: navigate, then let the effect fire the open. Not
    // `openThread` directly — the navigation is half of what is under test.
    clickRow(act.thread);
    // A pass with nothing awaited in between, so the report is the screen DURING the open.
    pass();
  } else if (act.act === 'supersede') {
    // A non-route caller — `store.js` has five — bumping the generation out from under an open
    // that the route asked for. This is how a view gets stranded on a conversation the hash does
    // not name, which is the state the second click has to be able to leave.
    SW.store.openThread(act.thread).catch(() => {});
    pass();
  } else if (act.act === 'filter') {
    // The Rail narrowed to an app, as Build's header narrows it. No conversation here has touched
    // anything, so the filter draws only the one being stood in — which is how a marker comes to
    // name a Conversation this Rail has no row for.
    SW.store.set({ railAppFilter: act.app });
    pass();
  } else if (act.act === 'settle') {
    await settle(act.ms);
    pass();
  } else {
    throw new Error(`unknown act ${JSON.stringify(act)}`);
  }
  report.push({ act: `${act.act}${act.thread ? ` ${act.thread}` : ''}`, ...snapshot() });
}

console.log(JSON.stringify(report));
process.exit(0);
