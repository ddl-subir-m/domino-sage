// How many "Data used" dropdowns a turn draws, and what is inside the one it draws.
//
// #447: `putDataUsed` minted one block per `operation_id`, so a turn that read a table, computed on
// it and analysed text stacked three collapsed `<details>` under one answer. Every event already
// carries the `turn_id` that groups them (`DataUse.record` stamps it), and the browser ignored it.
//
// Both halves of that claim live here because neither settles the other: the store can group
// correctly and the card can still lose every operation but the first. So this loads the real
// store AND the real `message-blocks.js` into one sandbox, walks a seeded history through
// `openThread`, and then renders each `data_used` block it produced through the block dispatcher.
//
// Input on stdin: `{ "thread": { "id": ..., "history": [...] } }` — a Chat transcript, with
// `dataUsed` rows on it exactly as the server persists them.
//
// Or `{ "block": {...} }` to render one hand-made block straight through the dispatcher, without
// the store. That is the only way to reach a block shape `putDataUsed` cannot currently produce —
// an empty `events`, say — which is exactly the shape a defensive guard is written for. A guard
// no test can reach is a written claim, not a guard.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { thread, block: rawBlock } = JSON.parse(fs.readFileSync(0, 'utf8'));

const json = (body) => ({
  ok: true, status: 200,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

function serve(url) {
  const path = String(url).replace(/^\.\/api/, '');
  if (/^\/threads\/[^/]+\/context$/.test(path)) return json({ items: [] });
  if (/^\/threads\/[^/]+$/.test(path)) return json(thread);
  return json({});
}

const ICONS = ['CopyOutlined', 'RightOutlined', 'DownOutlined', 'PushpinOutlined', 'ReloadOutlined',
  'ExportOutlined', 'DownloadOutlined', 'ThunderboltOutlined'];

const backing = new Map();
const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, TextEncoder, TextDecoder, URL, URLSearchParams, Infinity,
  isFinite, encodeURIComponent, decodeURIComponent,
  setTimeout, clearTimeout, setInterval, clearInterval,
  requestAnimationFrame: (fn) => fn(),
  fetch: (url) => Promise.resolve(serve(url)),
  localStorage: {
    getItem: (k) => (backing.has(k) ? backing.get(k) : null),
    setItem: (k, v) => backing.set(k, String(v)),
    removeItem: (k) => backing.delete(k),
  },
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  location: { hash: '' },
  history: { replaceState() {} },
  addEventListener() {},
  removeEventListener() {},
  // `DataUsed` holds no state, so a stub is honest here — the claim is which elements the card
  // returns for a given block, not a sequence of frames.
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    Fragment: 'Fragment',
    useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
    useEffect: () => {}, useRef: () => ({ current: null }), useMemo: (fn) => fn(),
  },
  antd: {
    Button: 'Button', Table: 'Table', Tooltip: 'Tooltip', Tag: 'Tag', Space: 'Space', Spin: 'Spin',
    Dropdown: 'Dropdown', Modal: { confirm() {} },
    Input: Object.assign(function Input() {}, { TextArea: 'Input.TextArea' }),
    message: { success() {}, error() {}, info() {}, warning() {} },
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

// Render one `data_used` block the way the transcript reaches it — through the dispatcher, so a
// block shape that stopped routing to `DataUsed` fails here instead of quietly testing nothing.
function draw(block) {
  const el = SW.MessageBlock({ block });
  if (!el || typeof el.t !== 'function') throw new Error('data_used no longer renders a component');
  const nodes = flatten(el.t(el.p));
  return {
    // One dropdown per card. A card that drew two would be the defect moved rather than fixed.
    details: nodes.filter((n) => n.el === 'details').length,
    sections: nodes.filter((n) => n.className === 'sw-data-used-op').length,
    words: nodes.filter((n) => n.text).map((n) => n.text).join(' '),
  };
}

if (rawBlock) {
  console.log(JSON.stringify({ cards: null, operations: null, rendered: [draw(rawBlock)] }));
  process.exit(0);
}

await SW.store.openThread(thread.id);
const blocks = (SW.store.get().messages || []).flatMap((m) => m.blocks || [])
  .filter((b) => b.type === 'data_used');

console.log(JSON.stringify({
  cards: blocks.length,
  // What each card claims to cover. Keyed off the block, so a grouping that put the right number
  // of cards on screen with the wrong operations under them still fails.
  //
  // No `|| [b.event]` fallback to the pre-#447 shape. Nothing writes that shape any more, and
  // tolerating it here would let a store reverted to one-block-per-operation keep reporting
  // plausible ids — only the card count would red, and it would red with a misleading message.
  // Without the fallback the ids come back `[[null]]` and name the actual breakage.
  operations: blocks.map((b) => (b.events || []).map((e) => e && e.operation_id)),
  rendered: blocks.map(draw),
}));
