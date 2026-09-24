// What an answer draws for a viewer who has put disclosure away, and what it draws anyway (#448,
// ADR-0062).
//
// Three claims live here because none of them can be read off the source.
//
// The first is the TABLE. `HIDDEN_BY_DATA_ACCESS` gives every `block.type` the dispatcher can draw
// a row, with no default, and the point of that shape is that the population cannot go quietly
// short. So this reads the case labels out of the REAL dispatcher — `SW.MessageBlock.toString()`,
// the running function's own source, which cannot drift from the function that runs — and holds
// them against the table both ways. A new type added to the switch without a row reds here; a row
// for a type the switch no longer has reds here too.
//
// Reading labels out of source can pick up a mention in a comment or a string, so each one is then
// DRIVEN: the dispatcher is called with a block of that type, and `null` back means the label
// reached `default` and was never a case. A label whose component throws on a bare block is still
// a real case — the throw happened inside it — so a throw counts as handled.
//
// The second is what the store's read actually produces, and the third is that the nudge on the
// answer is drawn from what the store stamped. Both need the real store and the real
// `message-blocks.js` in one sandbox: the store can partition correctly and the answer can still
// offer no way back to what it withheld.
//
// Input on stdin:
//   { "thread": {...}, "shown": bool, "toggle": bool }  — seed the preference, walk a Chat
//     transcript through `openThread`, and optionally flip the preference afterwards the way the
//     drawer's checkbox and the answer's nudge both do.
//   { "table": true }                                   — the dispatcher-against-table check.
import fs from 'node:fs';
import vm from 'node:vm';
import { unrefTimeout } from './sandbox_timeout.mjs';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const { thread, shown, toggle, table, view, live, storage } = input;

const json = (body) => ({
  ok: true, status: 200,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

function serve(url) {
  const path = String(url).replace(/^\.\/api/, '');
  if (/^\/threads\/[^/]+\/context$/.test(path)) return json({ items: [] });
  // The merged read, which is the whole of `conversationView: unified`. Served with the SAME
  // history as the split read, because the claim under test is that the preference filters on
  // where a row came FROM and not on which pane draws it — so the two views must be given the
  // same rows, or a difference in what they hide would prove nothing about the rule.
  if (/^\/threads\/[^/]+\/conversation$/.test(path)) return json({ history: thread.history });
  if (/^\/threads\/[^/]+$/.test(path)) return json(thread);
  return json({});
}

// A scripted Chat turn, for the one path no history read reaches: `putDataUsed` called live, out of
// the SSE reducer, pushing straight into `state.messages`. ADR-0062 puts the filter in the three
// history functions and says nothing about this one, so whether a live turn draws what the same
// turn hides after a reload is a question only a live run can answer.
function stream() {
  const body = (live || []).map((f) => `data: ${JSON.stringify(f)}\n\n`).join('');
  let sent = false;
  return { ok: true, body: { getReader: () => ({
    read: async () => (sent ? { done: true }
      : (sent = true, { done: false, value: new TextEncoder().encode(body) })),
  }) } };
}

const ICONS = ['CopyOutlined', 'RightOutlined', 'DownOutlined', 'PushpinOutlined', 'ReloadOutlined',
  'ExportOutlined', 'DownloadOutlined', 'ThunderboltOutlined', 'EyeOutlined'];

const warnings = [];
const backing = new Map();
const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, TextEncoder, TextDecoder, URL, URLSearchParams, Infinity,
  isFinite, encodeURIComponent, decodeURIComponent,
  setTimeout: unrefTimeout, clearTimeout, setInterval, clearInterval,
  requestAnimationFrame: (fn) => fn(),
  fetch: (url) => Promise.resolve(String(url).includes('/chat/stream') ? stream() : serve(url)),
  // `storage: "blocked"` is a browser that will not take a write — private mode, blocked site data,
  // a full quota. prefs.js refuses the write and reports false, and what the session does with
  // that refusal is a claim worth driving: the nudge on the answer is the only way in for somebody
  // in that state, and it used to be inert.
  localStorage: {
    getItem: (k) => (backing.has(k) ? backing.get(k) : null),
    setItem: (k, v) => {
      if (storage === 'blocked') throw new Error('storage is blocked');
      backing.set(k, String(v));
    },
    removeItem: (k) => backing.delete(k),
  },
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  location: { hash: '' },
  history: { replaceState() {} },
  addEventListener() {},
  removeEventListener() {},
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    Fragment: 'Fragment',
    useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
    useEffect: () => {}, useRef: () => ({ current: null }), useMemo: (fn) => fn(),
  },
  antd: {
    Button: 'Button', Table: 'Table', Tooltip: 'Tooltip', Tag: 'Tag', Space: 'Space', Spin: 'Spin',
    Dropdown: 'Dropdown', Drawer: 'Drawer', Checkbox: 'Checkbox', Radio: 'Radio',
    Modal: { confirm() {} },
    Input: Object.assign(function Input() {}, { TextArea: 'Input.TextArea' }),
    // A refused write warns rather than failing silently, and `setDataAccessShown` is one of the
    // callers that does. Collected so a test can tell a warning from a swallowed failure.
    message: {
      success() {}, error() {}, info() {},
      warning: (text) => warnings.push(String(text)),
    },
  },
  icons: Object.fromEntries(ICONS.map((n) => [n, n])),
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const file of ['util.js', 'api.js', 'prefs.js', 'store.js', 'components/message-blocks.js']) {
  vm.runInContext(fs.readFileSync(ROOT + file, 'utf8'), sandbox, { filename: file });
}
const SW = sandbox.SW;

// prefs.js asks the store who is looking, and refuses in both directions when nobody knows — so
// without a viewer every `set` below would report false and every `get` the fallback, and the whole
// run would pass by testing nothing. A real id, set the way boot sets one.
SW.store.set({ me: { id: 'u1' } });

// ---- the table against the real dispatcher --------------------------------------------------

if (table) {
  const source = SW.MessageBlock.toString();
  // ANY quoted label, not `[a-z_]+`. The narrow character class was the bug this check exists to
  // prevent, one layer down: `case 'chartV2':` and `case 'chart_2':` matched nothing, so a block
  // type carrying a digit or a capital was neither counted against the table nor reported missing
  // from it — every field below still read clean. The anchor was bounding the population, not the
  // table, which is the whole thing the table was chosen to avoid.
  const cases = [...source.matchAll(/case\s+'([^']*)':/g)].map((m) => m[1]);
  // The permissive pattern above still cannot see a `case` whose label is not a string literal —
  // `case SOME_CONST:` would parse as nothing. So the labels are cross-checked against a count
  // that needs no pattern at all: every `case` keyword in the dispatcher's own source. A label
  // shape this scanner cannot read now reds here instead of passing quietly.
  //
  // A scanner cannot tell a signal from a quote of it, so this counts what it can and declares the
  // gap rather than asserting it away.
  const keywords = (source.match(/\bcase\s/g) || []).length;
  const rows = SW.store.dataAccessRows();
  // Driven, so a label that is really a comment or a string cannot pad the population. `null` back
  // is the dispatcher's `default`; anything else, a thrown error included, means the label is a
  // case and the block reached it.
  const handled = cases.filter((type) => {
    try {
      return SW.MessageBlock({ block: { type } }) !== null;
    } catch (err) {
      return true;
    }
  });
  // What the preference actually does to a lone block of each type, asked of the REAL table rather
  // than read off it. The table says two types are governed and thirty-four are not, and "it governs
  // nothing else" is the ADR's claim — so the governed set is derived here and the test asserts it
  // equals exactly those two. The other rows were previously pinned by nothing but sharing a
  // constant, and flipping any of them to hide silently cost a viewer their table receipts.
  //
  // Asked with the preference OFF, which is the only state in which a row can hide anything.
  SW.prefs.set('dataAccessShown', false);
  // A plain block of each type, carrying nothing. `data_used` hides here because a card with no
  // shortfall on it is a whole read, and `status` does NOT, because a status hides only when it
  // carries the investigation origin — so the two conditional rows land in different lists, which
  // is what makes each of them checkable.
  const governed = cases.filter((type) => SW.store.hidesForDataAccess({ type }));
  const conditional = {
    investigationLine: SW.store.hidesForDataAccess(
      { type: 'status', fromEvent: 'investigation-state' }),
    aTurnsFailure: SW.store.hidesForDataAccess({ type: 'status', ok: false }),
    aWholeRead: SW.store.hidesForDataAccess({ type: 'data_used', events: [{ coverage: {} }] }),
    aShortRead: SW.store.hidesForDataAccess(
      { type: 'data_used', events: [{ coverage: { failed: 1 } }] }),
  };
  console.log(JSON.stringify({
    cases, handled, rows, keywords, governed, conditional,
    // What a drift in either direction looks like, named rather than left to a length comparison —
    // a test that only counted would red without saying which type moved.
    missingRows: handled.filter((t) => !rows.includes(t)),
    staleRows: rows.filter((t) => !handled.includes(t)),
  }));
  process.exit(0);
}

// ---- what a read produces, and what the answer offers ----------------------------------------

function flatten(node, out = []) {
  if (node === null || node === undefined || node === false || node === true) return out;
  if (Array.isArray(node)) { node.forEach((n) => flatten(n, out)); return out; }
  if (typeof node !== 'object') { out.push({ text: String(node) }); return out; }
  const p = node.p || {};
  out.push({ el: typeof node.t === 'function' ? node.t.name : node.t, className: p.className });
  flatten(node.c, out);
  if (p.children) flatten(p.children, out);
  return out;
}

// The nudge as the transcript reaches it — through `SW.Message`, so a stamp the answer stopped
// reading fails here instead of quietly testing a field nothing draws.
function nudgeOn(message) {
  const nodes = flatten(SW.Message({ message }));
  const at = nodes.findIndex((n) => n.className === 'sw-msg-disclosure');
  if (at < 0) return null;
  const words = nodes.slice(at).filter((n) => n.text).map((n) => n.text);
  return words.find((w) => w.startsWith('Show data access')) || words.join(' ');
}

// How many blocks the answer actually PUT ON SCREEN, counted off the rendered tree.
//
// Not the same question as which blocks the store marked, and the difference is the point. The
// first version of this file reported `types` by applying the component's filter itself, so a
// component that stopped filtering and drew every marked block passed every test — the harness
// was checking its own copy of the reader rather than the reader. This counts dispatcher elements
// in what `SW.Message` returned, so the component has to agree with the store or the number does
// not match.
function drawnOn(message) {
  return flatten(SW.Message({ message })).filter((n) => n.el === 'MessageBlock').length;
}

function report() {
  return (SW.store.get().messages || []).map((m) => ({
    role: m.role,
    // What is DRAWN, which since the marking rewrite is not the same as what is on the message.
    // Taken by the same test the component applies, so a block the store marked and the answer
    // still drew would show up here rather than passing as hidden.
    types: (m.blocks || []).filter((b) => !b.hiddenDisclosure).map((b) => b.type),
    // Everything on the message, drawn or not. This is what tells "hidden" from "destroyed": a
    // card the store lost entirely is absent from BOTH lists, and the first version of this
    // feature lost one — a read whose gateway request failed — while reporting a clean sheet.
    onMessage: (m.blocks || []).map((b) => b.type),
    // Every `status` block's origin, so the one row that is not constant can be checked for what
    // it actually did: hiding the investigation line and leaving a turn's failure on screen are
    // two claims, and `types` alone cannot tell them apart.
    // Drawn statuses only, and for the same reason `types` filters: hiding the investigation line
    // and leaving a turn's failure on screen are two claims, and the unfiltered list cannot tell
    // them apart.
    statuses: (m.blocks || []).filter((b) => b.type === 'status' && !b.hiddenDisclosure)
      .map((b) => b.fromEvent || null),
    hidden: m.disclosureHidden || 0,
    withheld: (m.blocks || []).filter((b) => b.hiddenDisclosure).map((b) => b.type),
    drawn: drawnOn(m),
    nudge: m.role === 'user' ? null : nudgeOn(m),
  }));
}

if (shown !== undefined) SW.prefs.set('dataAccessShown', shown);
if (view !== undefined) SW.prefs.set('conversationView', view);

if (live) {
  // No `openThread`: this is a turn sent into an empty transcript, which is what the reducer sees.
  SW.store.set({ thread: { id: thread.id, artifacts: [] }, messages: [],
                 scope: { id: 'p', name: 'P' } });
  await SW.store.sendMessage('q');
} else {
  await SW.store.openThread(thread.id);
}
const before = report();
// The drawer's checkbox and the answer's nudge are the same writer, so flipping it here is the same
// act either control performs — including the direction the nudge never asks for, which is the one
// a viewer unticking the box in the drawer is owed.
if (toggle !== undefined) SW.store.setDataAccessShown(toggle);

console.log(JSON.stringify({
  before,
  after: toggle === undefined ? null : report(),
  stored: backing.get('sw.prefs') || null,
  warnings,
}));
