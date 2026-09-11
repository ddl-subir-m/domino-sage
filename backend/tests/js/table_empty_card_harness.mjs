// What a `.table.json` Artifact card SAYS, given the block the store built for it.
//
// `table_artifact_harness.mjs` is the sibling of this file and stops one step short: it settles what
// `columns` and `rows` a wrapper recovers, which is a claim about data. The bug reported against
// this pair was seen on screen first — "2 blank adverse events summary boxes" — so the claim here
// is WHICH SENTENCES ARE ON SCREEN, and no amount of correct block data settles that.
//
// Nothing is mounted. `createElement` is stubbed to a plain object and `TableBlock` builds its own
// markup, so the card is fully decided by the tree `MessageBlock` returns. antd's `Table` is stubbed
// to the bare string 'Table': the empty card must not render one at all, and a card that does is
// reported here as `table: true` rather than inspected — what antd paints inside it is antd's.
//
// Input on stdin: one block, verbatim as `blocksForArtifacts` pushes it.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const block = JSON.parse(fs.readFileSync(0, 'utf8'));
// Every request the card makes. Nothing may be re-read on open or on scroll — only on a press
// (#256) — so the claim is a count and not a shape.
const fetched = [];

const cells = [];
const cursor = { n: 0 };
const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, Infinity, encodeURIComponent, decodeURIComponent,
  setTimeout, clearTimeout, setInterval: () => 1, clearInterval: () => {},
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  location: { hash: '' },
  addEventListener() {}, removeEventListener() {},
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    // A real cell, so a press can be followed: the card sets state and is rendered again, which is
    // the only way to see what a press PUT on screen rather than only that it called something.
    useState: (init) => {
      const i = cursor.n++;
      if (i >= cells.length) cells.push(typeof init === 'function' ? init() : init);
      return [cells[i], (v) => { cells[i] = typeof v === 'function' ? v(cells[i]) : v; }];
    },
    useEffect: () => {},
    useMemo: (fn) => fn(),
    useRef: () => ({ current: null }),
    Fragment: 'Fragment',
  },
  antd: {
    Input: Object.assign(function Input() {}, { TextArea: 'Input.TextArea' }),
    Button: 'Button', Table: 'Table', Tooltip: 'Tooltip', Tag: 'Tag', Space: 'Space',
    Modal: Object.assign(function Modal() {}, { confirm() {} }),
    message: { success() {}, error() {}, info() {}, warning() {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async (url) => {
    fetched.push(String(url));
    return { ok: true, status: 200, headers: { get: () => 'application/json' },
             json: async () => JSON.parse(process.env.READ_AGAIN || '{}'), text: async () => '' };
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'api.js', 'prefs.js', 'store.js', 'components/message-blocks.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

// The Project's CURRENT answer, which is not the same fact as the one in the file and is read from
// the store rather than from the block (ADR-0045). Off unless a test says otherwise, which is the
// state every other test here is written against. An env var rather than a second field on stdin:
// the input to this harness is one block, and its sibling test file depends on that.
if (process.env.KEPT_ROWS === '1') SW.store.set({ keptRows: { on: true, destination: '' } });

function* walk(node) {
  if (!node || typeof node !== 'object') return;
  if (Array.isArray(node)) { for (const c of node) yield* walk(c); return; }
  yield node;
  yield* walk(node.c);
}

// `MessageBlock` only routes: for a table it returns a `TableBlock` ELEMENT, and a stubbed
// `createElement` never calls it. Step through that one hop so the card under test is the card the
// router actually picks, rather than one this harness named for itself.
function render() {
  cursor.n = 0;
  const routed = SW.MessageBlock({ block });
  const tree = routed && typeof routed.t === 'function' ? routed.t(routed.p) : routed;
  return [...walk(tree)];
}

let nodes = render();
// One press, then the card again. `PRESS` names the button by its label, so a test that presses
// something the card does not offer fails loudly rather than quietly proving nothing.
if (process.env.PRESS) {
  const pressed = nodes.find(
    (n) => n.t === 'Button' && (n.c || []).flat(Infinity).includes(process.env.PRESS));
  if (!pressed) { console.error(`no button labelled ${process.env.PRESS}`); process.exit(2); }
  await pressed.p.onClick();
  nodes = render();
}
const saidBy = (cls) => nodes.filter((n) => (n.p || {}).className === cls)
  .map((n) => (n.c || []).flat(Infinity).filter((c) => typeof c === 'string').join(''))
  .filter(Boolean);

// `copyTextFor` is private to the module, and exporting it to be tested would be the test changing
// the thing it tests. The Copy button holds the only reference, so press it: `SW.util.copy` is the
// clipboard here, and what the button hands it is what the person pastes. This is the path that
// produced the `|  |` over `|  |` in the report.
let copied = null;
SW.util.copy = (text) => { copied = text; };
const message = { id: 'msg_1', role: 'assistant', blocks: [block] };
const button = [...walk(SW.Message({ message }))]
  .find((n) => (n.p || {})['aria-label'] === 'Copy message');
if (button) button.p.onClick();

const tableNode = nodes.find((n) => n.t === 'Table');
const tableCols = tableNode ? (tableNode.p || {}).columns || [] : [];
const tableRows = tableNode ? (tableNode.p || {}).dataSource || [] : [];
const first = tableRows[0] || {};

console.log(JSON.stringify({
  title: saidBy('sw-block-title')[0] || null,
  // Every line of prose the card puts on screen, in order. A blank card says none.
  said: saidBy('sw-block-sub'),
  // Where the card sends someone who cannot read the table it failed to draw.
  links: nodes.filter((n) => n.t === 'a').map((n) => (n.p || {}).href).filter(Boolean),
  table: nodes.some((n) => n.t === 'Table'),
  // What antd is asked to paint. A card can be `table: true` and still blank on screen: zero
  // column defs, or `dataIndex` keys that none of the rows have, both draw the grid with no
  // cells. The filename-derived "api usage detail.table" card was that shape — 50 rows, no
  // values — so the claim has to reach into the Table props, not stop at whether one exists.
  headers: tableCols.map((c) => c.title),
  cells: tableCols.map((c) => first[c.dataIndex]),
  copied,
  // What the card offers to press, and everything it asked the server for. A card that re-reads
  // on open would show a request here with no press behind it (#256).
  buttons: nodes.filter((n) => n.t === 'Button')
    .map((n) => (n.c || []).flat(Infinity).filter((c) => typeof c === 'string').join(''))
    .filter(Boolean),
  fetched,
}));
