// Opens Conversations under each conversation view and reports what Chat (#56) and Build (#57)
// would draw.
//
// Nothing is mounted. Almost everything this ticket decides on the client is a decision about
// MESSAGE STATE — whether the two halves merged, in what order, whether a build run folded into one
// row and what that row's face is built from — and all of it is settled before React is asked to
// draw anything. Mounting would test antd.
//
// The `card` step is the one exception, and it is not an exception to that rule: `createElement` is
// stubbed to a plain object, so calling `AppChange` returns a tree of data. Three things the card
// alone decides — published or not, whether it keeps the way through, whether it says `in the
// preview` — have nowhere else to be asked.
//
// The fetch log is part of the report, because one criterion is about cost rather than content: an
// app card reads publish state live, and a merged transcript with many cards must still cost one
// read of the rail's list, not one per card.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const steps = JSON.parse(fs.readFileSync(0, 'utf8'));

// --- the server ------------------------------------------------------------
// Two Built Apps, two publish states, and Conversations that exercise the four shapes the merged
// read has to survive: both halves, Build only, two apps in one Conversation, and nothing at all.
const APPS = [
  { id: 'app_a', name: 'Desk dashboard', built: true, published: true,
    publishedAt: '2026-01-02T09:00:00Z', building: false, behind: false },
  { id: 'app_b', name: 'P&L report', built: true, published: false,
    publishedAt: '', building: false, behind: false },
  // Published before the stamp existed — which is every published app in every Project on the
  // release that added one, and any app whose stamp has not reached this Builder's clone yet.
  { id: 'app_old', name: 'Risk monitor', built: true, published: true,
    publishedAt: '', building: false, behind: false },
];

// The Chat half as `/threads/<id>` returns it — which is all the split view ever reads.
const THREADS = {
  thr_both: { id: 'thr_both', title: 'Desks', artifacts: [], touched: [],
              history: [{ type: 'user', text: 'which desks lost money?', at: '2026-01-01T09:00:00Z' },
                        { type: 'user', text: 'thanks', at: '2026-01-01T11:00:00Z' }] },
  thr_build_only: { id: 'thr_build_only', title: 'Straight into Build', artifacts: [], touched: [],
                    history: [] },
  thr_two_apps: { id: 'thr_two_apps', title: 'Two apps', artifacts: [], touched: [], history: [] },
  thr_lead_ins: { id: 'thr_lead_ins', title: 'Two Lead-ins', artifacts: [], touched: [], history: [] },
  thr_split_gap: { id: 'thr_split_gap', title: 'A gap in two', artifacts: [], touched: [], history: [] },
  thr_orphan_answer: { id: 'thr_orphan_answer', title: 'An answer alone', artifacts: [], touched: [], history: [] },
  thr_three_apps: { id: 'thr_three_apps', title: 'Three apps', artifacts: [], touched: [], history: [] },
  thr_empty: { id: 'thr_empty', title: 'New chat', artifacts: [], touched: [], history: [] },
  // The ordinary way a Conversation reaches Build: a confirmed handoff, which writes the plan card
  // and its `done` into the Build log with no user row in front of them.
  thr_handoff: { id: 'thr_handoff', title: 'Handed off', artifacts: [], touched: [],
                 history: [{ type: 'user', text: 'build me a dashboard',
                             at: '2026-01-01T09:00:00Z' }] },
  // The merged read fails. The Chat half came with the thread and is still good.
  thr_broken: { id: 'thr_broken', title: 'Merge is down', artifacts: [], touched: [],
                history: [{ type: 'user', text: 'which desks lost money?',
                            at: '2026-01-01T09:00:00Z' }] },
  // A live handoff offer. Chat draws it; Build must not, because it offers a way over to Build.
  thr_offer: { id: 'thr_offer', title: 'Offered', artifacts: [], touched: [],
               handoff: { status: 'suggested' },
               history: [{ type: 'user', text: 'chart this', at: '2026-01-01T09:00:00Z' }] },
  // The two rules for a link with no `?app=` disagree here, which is the point of it: the handoff
  // bound app_a, and the newest build turn is app_b's. ADR-0009 says the bound entry wins.
  thr_bound: { id: 'thr_bound', title: 'Bound then wandered', artifacts: [], touched: [],
               handoff: { status: 'bound', appId: 'app_a' },
               history: [{ type: 'user', text: 'build me a dashboard',
                           at: '2026-01-01T09:00:00Z' }] },
  // A Project upgraded from before per-app logs: its build rows carry no app at all.
  thr_legacy: { id: 'thr_legacy', title: 'Adopted', artifacts: [], touched: [], history: [] },
};

// The merged read as the control API returns it: ordered, and every row labelled with its half.
const CONVERSATIONS = {
  thr_both: [
    { half: 'chat', type: 'user', text: 'which desks lost money?', at: '2026-01-01T09:00:00Z' },
    { half: 'build', type: 'user', text: 'add a date filter', app: 'app_a', at: '2026-01-01T10:00:00Z' },
    { half: 'build', type: 'agent', kind: 'tool', tool: 'edit', detail: 'src/App.tsx',
      app: 'app_a', at: '2026-01-01T10:00:01Z' },
    { half: 'build', type: 'app_change', appId: 'app_a', name: 'Desk dashboard',
      app: 'app_a', at: '2026-01-01T10:00:02Z' },
    { half: 'build', type: 'done', ok: true, decision: 'typecheck clean', app: 'app_a',
      at: '2026-01-01T10:00:03Z' },
    { half: 'chat', type: 'user', text: 'thanks', at: '2026-01-01T11:00:00Z' },
  ],
  thr_build_only: [
    { half: 'build', type: 'user', text: 'build me a dashboard', app: 'app_a', at: '2026-01-01T10:00:00Z' },
    { half: 'build', type: 'app_change', appId: 'app_a', name: 'Desk dashboard as it was then',
      app: 'app_a', at: '2026-01-01T10:00:01Z' },
    { half: 'build', type: 'done', ok: true, decision: 'typecheck clean', app: 'app_a',
      at: '2026-01-01T10:00:02Z' },
  ],
  // One Conversation, two Built Apps, in the order the runs happened. This is the shape a merge
  // built on the selected app's log alone cannot show.
  thr_two_apps: [
    { half: 'build', type: 'user', text: 'build the dashboard', app: 'app_a', at: '2026-01-01T10:00:00Z' },
    { half: 'build', type: 'app_change', appId: 'app_a', name: 'Desk dashboard', app: 'app_a',
      at: '2026-01-01T10:00:01Z' },
    { half: 'build', type: 'done', ok: true, decision: 'typecheck clean', app: 'app_a',
      at: '2026-01-01T10:00:02Z' },
    { half: 'build', type: 'user', text: 'now the P&L report', app: 'app_b', at: '2026-01-01T12:00:00Z' },
    { half: 'build', type: 'app_change', appId: 'app_b', name: 'P&L report', app: 'app_b',
      at: '2026-01-01T12:00:01Z' },
    { half: 'build', type: 'done', ok: true, decision: 'typecheck clean', app: 'app_b',
      at: '2026-01-01T12:00:02Z' },
  ],
  // The shape ADR-0019 is about: one Conversation, two Built Apps, and Chat turns on either side of
  // each handoff. app_a's Lead-in is c1-c3 and app_b's is c4-c5, because the forward reading gives
  // a turn to the app of the NEXT build after it. c6 came after every handoff, so it belongs to
  // neither and shows under both.
  //
  // `c_untimed` sits FIRST on purpose. A merged read sorts an untimed row as "all Chat, then all
  // Build" (ADR-0009), so an implementation that attributed by index would file it under app_a and
  // hide it from app_b — which is the trap, and the opposite of showing a turn nobody can place.
  thr_lead_ins: [
    { half: 'chat', type: 'user', text: 'c_untimed' },
    { half: 'chat', type: 'user', text: 'c1', at: '2026-01-01T09:00:00Z' },
    { half: 'chat', type: 'user', text: 'c2', at: '2026-01-01T09:01:00Z' },
    { half: 'chat', type: 'user', text: 'c3', at: '2026-01-01T09:02:00Z' },
    { half: 'build', type: 'user', text: 'build the dashboard', app: 'app_a', at: '2026-01-01T10:00:00Z' },
    { half: 'build', type: 'app_change', appId: 'app_a', name: 'Desk dashboard', app: 'app_a',
      at: '2026-01-01T10:00:01Z' },
    { half: 'build', type: 'done', ok: true, decision: 'typecheck clean', app: 'app_a',
      at: '2026-01-01T10:00:02Z' },
    // Answers under the questions, so the fold's face has to count TURNS and not rows: a Lead-in
    // of two questions each with an answer under it is two turns, never four.
    { half: 'chat', type: 'user', text: 'c4', at: '2026-01-01T11:00:00Z' },
    { half: 'chat', type: 'agent', kind: 'text', text: 'a4', at: '2026-01-01T11:00:30Z' },
    { half: 'chat', type: 'user', text: 'c5', at: '2026-01-01T11:01:00Z' },
    { half: 'chat', type: 'agent', kind: 'text', text: 'a5', at: '2026-01-01T11:01:30Z' },
    { half: 'build', type: 'user', text: 'now the P&L report', app: 'app_b', at: '2026-01-01T12:00:00Z' },
    { half: 'build', type: 'app_change', appId: 'app_b', name: 'P&L report', app: 'app_b',
      at: '2026-01-01T12:00:01Z' },
    { half: 'build', type: 'done', ok: true, decision: 'typecheck clean', app: 'app_b',
      at: '2026-01-01T12:00:02Z' },
    { half: 'chat', type: 'user', text: 'c6', at: '2026-01-01T13:00:00Z' },
  ],
  // app_a runs TWICE, and both Chat turns between its runs planned app_b. Under app_a the two
  // turns are hidden and the run between them is drawn, so they are two gaps and not one: a single
  // fold would have to sit above a build turn that happened between its own turns.
  thr_split_gap: [
    { half: 'build', type: 'user', text: 'build the dashboard', app: 'app_a', at: '2026-01-01T10:00:00Z' },
    { half: 'build', type: 'done', ok: true, decision: 'typecheck clean', app: 'app_a',
      at: '2026-01-01T10:00:01Z' },
    { half: 'chat', type: 'user', text: 'g1', at: '2026-01-01T11:00:00Z' },
    { half: 'build', type: 'user', text: 'add a date filter', app: 'app_a', at: '2026-01-01T11:30:00Z' },
    { half: 'build', type: 'done', ok: true, decision: 'typecheck clean', app: 'app_a',
      at: '2026-01-01T11:30:01Z' },
    { half: 'chat', type: 'user', text: 'g2', at: '2026-01-01T11:40:00Z' },
    { half: 'build', type: 'user', text: 'now the P&L report', app: 'app_b', at: '2026-01-01T12:00:00Z' },
    { half: 'build', type: 'done', ok: true, decision: 'typecheck clean', app: 'app_b',
      at: '2026-01-01T12:00:02Z' },
  ],
  // A Chat answer with no question of its own on this side of the boundary. It is attributed to
  // app_b like anything else at that moment, but there is no request in it to count, so it is not a
  // Lead-in and must not fold behind a face reading "0 turns".
  thr_orphan_answer: [
    { half: 'build', type: 'user', text: 'build the dashboard', app: 'app_a', at: '2026-01-01T10:00:00Z' },
    { half: 'build', type: 'done', ok: true, decision: 'typecheck clean', app: 'app_a',
      at: '2026-01-01T10:00:01Z' },
    { half: 'chat', type: 'agent', kind: 'text', text: 'the answer nobody asked for',
      at: '2026-01-01T11:00:00Z' },
    { half: 'build', type: 'user', text: 'now the P&L report', app: 'app_b', at: '2026-01-01T12:00:00Z' },
    { half: 'build', type: 'done', ok: true, decision: 'typecheck clean', app: 'app_b',
      at: '2026-01-01T12:00:01Z' },
  ],
  // THREE apps, so app_b's Lead-in folds at the SAME place under two different selected apps —
  // under app_a, and under app_old which has no Lead-in of its own that early. On the fold's own
  // position alone the two rows would share an id, and React would carry an open fold across the
  // switch into a transcript nobody has looked at yet.
  thr_three_apps: [
    { half: 'chat', type: 'user', text: 't1', at: '2026-01-01T09:00:00Z' },
    { half: 'build', type: 'user', text: 'build the dashboard', app: 'app_a', at: '2026-01-01T10:00:00Z' },
    { half: 'chat', type: 'user', text: 't2', at: '2026-01-01T10:30:00Z' },
    { half: 'chat', type: 'user', text: 't3', at: '2026-01-01T10:40:00Z' },
    { half: 'build', type: 'user', text: 'now the P&L report', app: 'app_b', at: '2026-01-01T11:00:00Z' },
    { half: 'build', type: 'user', text: 'now the risk monitor', app: 'app_old', at: '2026-01-01T12:00:00Z' },
  ],
  thr_empty: [],
  // A handoff, then a build, then an app reset. Only the middle one is a run: the other two were
  // written outside any turn, and folding them makes the transcript claim a build prompt did them.
  thr_handoff: [
    { half: 'chat', type: 'user', text: 'build me a dashboard', at: '2026-01-01T09:00:00Z' },
    { half: 'build', type: 'plan-proposed', plan: 'A desk exposure dashboard.', kind: 'plan',
      planId: 'pln_1', steps: 3, app: 'app_a', at: '2026-01-01T09:30:00Z' },
    { half: 'build', type: 'done', ok: true, decision: 'awaiting approval', app: 'app_a',
      at: '2026-01-01T09:30:01Z' },
    { half: 'build', type: 'user', text: 'add a date filter', app: 'app_a', at: '2026-01-01T10:00:00Z' },
    { half: 'build', type: 'app_change', appId: 'app_a', name: 'Desk dashboard', app: 'app_a',
      at: '2026-01-01T10:00:01Z' },
    { half: 'build', type: 'done', ok: true, decision: 'typecheck clean', app: 'app_a',
      at: '2026-01-01T10:00:02Z' },
    { half: 'build', type: 'app-reset', app: 'app_a', at: '2026-01-01T11:00:00Z' },
  ],
  // Never served as a merged read — `BROKEN` answers 500 for this one before it is looked up. It is
  // here so `/project/history` has rows to hand back, which is the whole point of the fallback:
  // Build short of its Chat turns still has its own.
  thr_broken: [
    { half: 'build', type: 'user', text: 'add a date filter', app: 'app_a', at: '2026-01-01T10:00:00Z' },
    { half: 'build', type: 'done', ok: true, decision: 'typecheck clean', app: 'app_a',
      at: '2026-01-01T10:00:01Z' },
  ],
  thr_offer: [
    { half: 'chat', type: 'user', text: 'chart this', at: '2026-01-01T09:00:00Z' },
    // The offer as the Chat log persists it, beside the live one the thread's `handoff` adds. Two
    // shapes, one control, and Build must drop both.
    { half: 'chat', type: 'handoff-suggest', at: '2026-01-01T09:00:01Z' },
    { half: 'build', type: 'user', text: 'add a date filter', app: 'app_a', at: '2026-01-01T10:00:00Z' },
  ],
  thr_bound: [
    { half: 'chat', type: 'user', text: 'build me a dashboard', at: '2026-01-01T09:00:00Z' },
    { half: 'build', type: 'user', text: 'build it', app: 'app_a', at: '2026-01-01T10:00:00Z' },
    { half: 'build', type: 'user', text: 'now the other one', app: 'app_b', at: '2026-01-01T12:00:00Z' },
  ],
  // Adopted rows: tagged with the Conversation, never with an app, because when they were written
  // there was only one app to be in.
  thr_legacy: [
    { half: 'build', type: 'user', text: 'the turn nobody stamped', at: '2026-01-01T10:00:00Z' },
    { half: 'build', type: 'done', ok: true, decision: 'typecheck clean',
      at: '2026-01-01T10:00:01Z' },
  ],
};

// The one conversation whose merged read is broken, so the store's fallback is reachable.
const BROKEN = new Set(['thr_broken']);

const calls = [];
// Reads and writes are logged apart because one criterion is about a write that must NOT happen:
// pressing "New conversation" draws a row, and `GET /threads` alone cannot tell an empty
// conversation being persisted from the rail's list being read.
const writes = [];

// Which app the server has selected. Build reads one app at a time (#57), so this is what decides
// which half of a two-app Conversation its transcript is allowed to show.
let selected = 'app_a';
const json = (body, status = 200) => ({
  ok: status < 400, status,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

function serve(url, options) {
  const path = String(url).replace(/^\.\/api/, '');
  const method = (options && options.method) || 'GET';
  calls.push(path);
  if (method !== 'GET') writes.push(`${method} ${path}`);
  let m;
  if ((m = path.match(/^\/threads\/([^/]+)\/conversation$/))) {
    if (BROKEN.has(m[1])) return json({ error: 'the merge fell over' }, 500);
    return json({ history: CONVERSATIONS[m[1]] || [] });
  }
  if ((m = path.match(/^\/threads\/([^/]+)\/context$/))) return json({ items: [] });
  if ((m = path.match(/^\/threads\/([^/]+)$/))) return json(THREADS[m[1]] || { id: m[1], history: [] });
  // A bare list, the way `GET /api/threads` answers — `state.threads` is an array, and the
  // rail filters it directly.
  if (path === '/threads' && method === 'GET') return json(Object.values(THREADS));
  // The first message minting the Conversation the rail's placeholder was standing in for.
  if (path === '/threads' && method === 'POST') {
    const id = `thr_minted_${Object.keys(THREADS).length}`;
    THREADS[id] = { id, title: 'New chat', artifacts: [], touched: [], history: [] };
    CONVERSATIONS[id] = [];
    return json(THREADS[id]);
  }
  if ((m = path.match(/^\/apps\/([^/]+)\/select$/))) {
    selected = m[1];
    return json({});
  }
  if (path === '/apps') {
    return json({ items: APPS.map((a) => ({ ...a, selected: a.id === selected })), selected });
  }
  // Build's own read, and the only one the split view makes: the SELECTED app's build log for this
  // Conversation. The same rows the merged read labels `build`, minus the label and minus every
  // other app — which is exactly the difference the merged read exists to close.
  if ((m = path.match(/^\/project\/history\?conversation=(.+)$/))) {
    const rows = (CONVERSATIONS[decodeURIComponent(m[1])] || [])
      .filter((row) => row.half === 'build' && (!row.app || row.app === selected))
      .map(({ half, ...row }) => row);
    return json({ history: rows });
  }
  return json({});
}

const backing = new Map();
const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, Infinity, setTimeout, clearTimeout, setInterval, clearInterval,
  requestAnimationFrame: (fn) => fn(),
  localStorage: {
    getItem: (k) => (backing.has(k) ? backing.get(k) : null),
    setItem: (k, v) => backing.set(k, String(v)),
    removeItem: (k) => backing.delete(k),
  },
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  // router.js reads the hash and listens for changes to it. `mode` is half of what decides whether
  // the card says `in the preview`, so the harness has to be able to move it.
  location: { hash: '' },
  history: { replaceState() {} },
  addEventListener() {},
  removeEventListener() {},
  React: {
    createElement: (t, p, ...c) => ({ t, p, c }),
    useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
    useEffect: () => {}, useRef: () => ({ current: null }), Fragment: 'Fragment',
  },
  antd: {
    Input: { TextArea: 'Input.TextArea' }, Button: 'Button', Dropdown: 'Dropdown', Tag: 'Tag',
    Tooltip: 'Tooltip', Space: 'Space', Modal: { confirm() {} },
    message: { success() {}, error() {}, info() {}, warning() {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async (url, options) => serve(url, options),
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js', 'router.js',
                 'components/message-blocks.js', 'components/conversation-list.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

// The app card, called as the function it is. `createElement` is stubbed to a plain object, so this
// walks a tree of data and never mounts anything — which is what lets the three questions the card
// alone answers (published or not, whether it keeps the control, whether it says `in the preview`)
// be asked at all. Everything else in this file stops at the store.
function cardText(block) {
  const AppChange = SW.MessageBlock({ block: { type: 'app_change', ...block } }).t;
  const words = [];
  const walk = (node) => {
    if (node === null || node === undefined || node === false || node === true) return;
    if (typeof node === 'string' || typeof node === 'number') { words.push(String(node)); return; }
    if (Array.isArray(node)) { node.forEach(walk); return; }
    if (node.c) walk(node.c);
  };
  walk(AppChange({ block: { type: 'app_change', ...block } }));
  return words;
}

// The plan card, called the same way and for the same reason: whether a plan folds is decided by
// the view preference and the block, before React is asked to draw anything. Reported as words plus
// the classNames on the way down, because the fold is a claim about BOTH — that the pitch is there
// and that the plan body is not.
function planCardText(block) {
  const PlanCard = SW.MessageBlock({ block: { type: 'build_plan', ...block } }).t;
  const words = [];
  // Tags as well as words, because half of what the fold decides is about controls that carry no
  // text of their own: an antd `Input.TextArea` puts its prompt in a `placeholder` prop, so "the
  // note field is gone" has nothing in `words` to be read off.
  const tags = [];
  const walk = (node) => {
    if (node === null || node === undefined || node === false || node === true) return;
    if (typeof node === 'string' || typeof node === 'number') { words.push(String(node)); return; }
    if (Array.isArray(node)) { node.forEach(walk); return; }
    if (typeof node.t === 'string') tags.push(node.t);
    if (node.c) walk(node.c);
  };
  walk(PlanCard({ block: { type: 'build_plan', ...block } }));
  return { words, tags };
}

// The rail, called as the function it is — same trick as `cardText`, and for the same reason:
// which row the rail says you are looking at is decided before React is asked to draw anything.
// Rows are reported as `className | words`, so "there is a selected row" and "it is the one that
// says New conversation" are one assertion rather than two hopeful ones.
// The Rail starts collapsed since #150, and a collapsed Rail draws two icon buttons instead of the
// list. Every claim here is about a row, so the read opens it first — through `set` rather than
// `toggleRail`, because this is the harness getting at the list and not a person choosing anything.
function railRows(mode) {
  SW.store.set({ railHidden: false });
  const rows = [];
  const walk = (node, row) => {
    if (node === null || node === undefined || node === false || node === true) return;
    if (Array.isArray(node)) { node.forEach((n) => walk(n, row)); return; }
    if (typeof node !== 'object') { if (row) row.words.push(String(node)); return; }
    const className = (node.p && node.p.className) || '';
    // `sw-thread` is the row; `sw-thread-title`, `-meta`, `-more` are inside one.
    if (/(^|\s)sw-thread(\s|$)/.test(className)) {
      row = { className, words: [] };
      rows.push(row);
    }
    // A component, not a tag: call it, the way the rail would have.
    if (typeof node.t === 'function') { walk(node.t(node.p || {}), row); return; }
    walk(node.c, row);
  };
  walk(SW.ConversationRail({ mode }), null);
  return rows.map((r) => `${r.className} | ${r.words.join(' ')}`);
}

// What a block says, short enough to assert on. A build run reports its face and its fold
// separately, because the whole question is which facts are in which.
function describe(block) {
  if (block.type === 'build_run') {
    return {
      run: block.prompt,
      apps: (block.apps || []).map((a) => `${a.appId}:${a.name}`),
      folded: (block.messages || []).map((m) =>
        `${m.role}:${(m.blocks || []).map((b) => b.type).join('+')}`),
    };
  }
  if (block.type === 'lead_in_fold') {
    // `count` is the FACE and `holds` is what opens, reported apart because the whole question is
    // whether the face counts turns or counts rows.
    return {
      fold: block.appName,
      count: block.count,
      holds: (block.messages || []).map((m) =>
        (m.blocks || []).map((b) => (b.type === 'text' ? b.value : b.type)).join('+')),
    };
  }
  if (block.type === 'app_change') return { card: block.appId, name: block.name };
  if (block.type === 'text') return block.value;
  return block.type;
}

function view(messages) {
  return (messages || []).map((m) => ({
    role: m.role,
    blocks: (m.blocks || []).map(describe),
  }));
}

// The invariant every writer of the Build log has to keep: what Build DRAWS is never behind what it
// HOLDS. A writer that set `buildMessages` without recomputing `buildTranscript` breaks it for
// exactly one frame — long enough for an echoed prompt to be missing from the pane until the turn's
// first event happened to recompute it, which is why counting frames catches what a final snapshot
// cannot.
let behindFrames = 0;
SW.store.subscribe(() => {
  const now = SW.store.get();
  const drawn = new Set((now.buildTranscript || []).map((m) => m.id));
  if ((now.buildMessages || []).some((m) => !drawn.has(m.id))) behindFrames += 1;
});

const report = [];
for (const step of steps) {
  if (step.pref) {
    // A viewer, because a preference is keyed by one and `prefs` refuses to write a record it
    // cannot key. Every control that sets this one lives behind a Workbench that has already booted,
    // so a harness with no viewer would be exercising a moment nobody can reach.
    SW.store.set({ me: { id: 'u1', name: 'Dana Reed' } });
    SW.prefs.set('conversationView', step.pref);
    report.push({ step: `pref ${step.pref}`, view: SW.prefs.get('conversationView') });
  } else if (step.open) {
    calls.length = 0;
    await SW.store.openThread(step.open);
    report.push({
      step: `open ${step.open}`,
      messages: view(SW.store.get().messages),
      apps: (SW.store.get().apps || []).map((a) => a.id),
      // Every path this open fetched, so "one read for the transcript" is a claim and not a hope.
      calls: calls.slice(),
    });
  } else if (step.build) {
    // What Build does on arrival: open the Conversation, then load the app. Both, in that order,
    // because Build's transcript is the Conversation's Chat turns and this app's build turns, and
    // neither call knows both halves on its own.
    calls.length = 0;
    await SW.store.openThread(step.build);
    await SW.store.loadBuild();
    const now = SW.store.get();
    report.push({
      step: `build ${step.build}`,
      transcript: view(now.buildTranscript),
      // What React keys the fold rows on. A fold that kept its id across an app switch keeps its
      // open state with it, and the ADR says a fold closes on a switch.
      foldIds: (now.buildTranscript || [])
        .filter((m) => (m.blocks || []).some((b) => b.type === 'lead_in_fold'))
        .map((m) => m.id),
      // The greeting's own question, kept apart from the transcript because it IS a different
      // question: has THIS app got turns in this Conversation? A brand-new app in a Conversation
      // full of talk answers no, and is still right to say so (#74).
      appTurns: (now.buildMessages || []).length,
      app: (now.activeApp && now.activeApp.id) || null,
      calls: calls.slice(),
    });
  } else if (step.echo) {
    // A prompt echoed into the log without going near the network. The turn itself cannot run here
    // — there is no SSE behind this fetch — but the echo is appended before the request, which is
    // the write this step is about: every writer of the log has to leave the transcript in step
    // with it, or Build draws the list as it was one row ago.
    await SW.store.sendBuildPrompt(step.echo).catch(() => {});
    const now = SW.store.get();
    report.push({
      step: `echo ${step.echo}`,
      transcript: view(now.buildTranscript),
      appTurns: (now.buildMessages || []).length,
      behindFrames,
    });
  } else if (step.select) {
    await SW.store.selectApp(step.select);
    report.push({ step: `select ${step.select}`, app: (SW.store.get().activeApp || {}).id || null });
  } else if (step.deselect) {
    // A Project where the server has no app selected, which is what a Project with no Built App in
    // it looks like from here. `loadAppList` reads the selection off the list, so moving the
    // server's answer is the only honest way to leave Build with no app on screen.
    selected = '';
    await SW.store.loadApps({ cascade: false });
    report.push({ step: 'deselect', app: (SW.store.get().activeApp || {}).id || null });
  } else if (step.resolve) {
    // Where a `#/build/<id>` link with no `?app=` lands.
    calls.length = 0;
    const app = await SW.store.resolveConversationApp(step.resolve);
    report.push({ step: `resolve ${step.resolve}`, app: app || null, calls: calls.slice() });
  } else if (step.card) {
    // `mode` and `activeApp` are what decides "in the preview": the card is only ever there when
    // Build is the mode on screen and it is showing this app.
    // Straight onto the stub, then `go` to the same place: nothing here dispatches a hashchange,
    // and `go` re-parses when the hash it is handed is the one already there.
    sandbox.location.hash = step.route || '#/chat';
    SW.router.go(step.route || '#/chat');
    SW.store.set({ activeApp: (SW.store.get().apps || []).find((a) => a.id === step.activeApp) || null });
    report.push({ step: `card ${step.card.appId}`, words: cardText(step.card) });
  } else if (step.planFold) {
    // The other side of the same claim: the CARD is a function of its block, so whether a plan
    // folds is the store's answer, given once. This reports what the store handed down.
    await SW.store.openThread(step.planFold);
    if (step.pane === 'build') await SW.store.loadBuild();
    const plans = [];
    const where = step.pane === 'build'
      ? SW.store.get().buildTranscript
      : SW.store.get().messages;
    for (const message of where || []) {
      for (const block of message.blocks || []) {
        if (block.type === 'build_plan') plans.push(!!block.folded);
      }
    }
    report.push({ step: `planFold ${step.planFold}`, plans });
  } else if (step.planCard) {
    // Same two levers the card reads: the route decides the mode, the pref decides the view.
    sandbox.location.hash = step.route || '#/chat';
    SW.router.go(step.route || '#/chat');
    report.push({ step: 'planCard', ...planCardText(step.planCard) });
  } else if (step.newConversation) {
    // The press, whole: the store action the button runs, then the navigation it runs after it —
    // including the route effect Build fires on arrival, which is the one that used to wipe this.
    // The rail always has its list; "at the top of the list" is not a claim you can check
    // against an empty one.
    await SW.store.reloadThreads();
    writes.length = 0;
    SW.store.newConversation();
    const route = step.route || '#/chat';
    sandbox.location.hash = route;
    SW.router.go(route);
    report.push({
      step: `newConversation ${route}`,
      rows: railRows(route.startsWith('#/build') ? 'build' : 'chat'),
      // Nothing may be written. An empty conversation that survives the press is one the rail
      // has to list forever, and the row above exists precisely so that none is needed.
      writes: writes.slice(),
    });
  } else if (step.firstMessage) {
    // What Chat does when someone types into a conversation that does not exist yet: mint it,
    // then send. Minting is the half the rail cares about — the placeholder was standing in for
    // exactly this Thread, and now there is one.
    writes.length = 0;
    await SW.store.newThread();
    await SW.store.reloadThreads();
    report.push({
      step: 'firstMessage',
      rows: railRows('chat'),
      writes: writes.slice(),
    });
  } else if (step.clearConversation) {
    // `clearConversation` on its own. Three real paths run it with no press behind them —
    // Build's arrival at a conversation-less route (`modes/builder.js`), deleting the open
    // Conversation (`components/conversation-list.js`) and minting an app (`createApp`) — and
    // none of them can be driven here, because the harness stubs `useEffect` to a no-op and
    // mounts nothing. What they have in common is this call, so this is what gets asserted:
    // the step is the contract, not an imitation of any one caller.
    SW.store.clearConversation();
    report.push({ step: 'clearConversation', rows: railRows(step.mode || 'chat') });
  } else if (step.railRows) {
    // The rail as it stands right now, after whatever the steps before this one did to it.
    // `railAppFilter` is store state, so it can be driven; the search box is `useState` inside
    // the component and the stub cannot type into it. They are the same expression in the rail.
    await SW.store.reloadThreads();
    if ('railAppFilter' in step) SW.store.set({ railAppFilter: step.railAppFilter });
    report.push({ step: `railRows ${step.railRows}`, rows: railRows(step.railRows) });
  } else {
    throw new Error(`unknown step ${JSON.stringify(step)}`);
  }
}
console.log(JSON.stringify(report));
