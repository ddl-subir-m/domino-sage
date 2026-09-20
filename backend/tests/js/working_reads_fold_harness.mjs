// A turn's STEPS, folded, and — the larger half — never read until somebody opens the fold
// (ADR-0063, #452).
//
// Its own harness because the claim spans both files and neither alone can make it.
// `artifact_hydration_harness.mjs` stops at the store, so it can see that a folded row was not
// fetched but not that the control which fetches it exists; a component harness with no store has
// nothing to fetch. So this one loads both, drives the real `openThread`, renders the real
// transcript through `SW.Message`, and clicks the real control.
//
// The hooks are real ones rather than the stub the other transcript harnesses use, for the reason
// `tool_card_detail_harness.mjs` gives: this claim is about a SEQUENCE — closed, opened, read,
// drawn — and a stub whose setter does nothing can only ever show the first frame of it.
//
// Input on stdin: `{ thread, files, open }`. `files` maps a path to
// `{ content?, body?, fail? }`, where `fail` is "network" or "status". `open` clicks the fold.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const spec = JSON.parse(fs.readFileSync(0, 'utf8'));
const FILES = spec.files || {};

// Every file read, in order. The whole point of the fold is that this list stays short until
// somebody asks, so it is recorded across both phases and reported with a mark where the click was.
const requests = [];
// Hooks keyed by the component's POSITION in the tree, not by a flat call counter. Opening the
// fold inserts components in the middle of the walk, and a counter would shift every slot after
// them — the answer's own card would start reading the fold's state. A path is stable under that
// because a sibling's subtree growing does not move the sibling.
const hooks = new Map();
let current = null;
let cursor = 0;
let effects = [];

const json = (body, status = 200) => ({
  ok: status < 400, status, statusText: status < 400 ? 'OK' : 'Server Error',
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

// A scripted Chat turn over SSE. The live reducer is a SECOND reader of an Artifact list and it
// does not share a line with the history one, so a claim proved on reload proves nothing about
// the turn a person is actually watching — ADR-0062 was bitten by exactly that gap.
function stream() {
  const body = (spec.live || []).map((f) => `data: ${JSON.stringify(f)}\n\n`).join('');
  let sent = false;
  return {
    ok: true,
    body: {
      getReader: () => ({
        read: async () => (sent
          ? { done: true }
          : (sent = true, { done: false, value: new TextEncoder().encode(body) })),
      }),
    },
  };
}

async function serve(url) {
  const path = String(url).replace(/^\.\/api/, '');
  let m;
  if (path.includes('/chat/stream')) return stream();
  if (path.match(/^\/threads\/([^/]+)\/context$/)) return json({ items: [] });
  if ((m = path.match(/^\/threads\/([^/]+)$/))) {
    return json(spec.thread && spec.thread.id === m[1] ? spec.thread : { id: m[1], history: [] });
  }
  if ((m = path.match(/^\/project\/file\?path=(.+)$/))) {
    const filePath = decodeURIComponent(m[1]);
    requests.push(filePath);
    const file = FILES[filePath] || {};
    if (file.fail === 'network') throw new Error(`cannot reach ${filePath}`);
    if (file.fail === 'status') return json({ error: 'the file is gone' }, 500);
    return json({ content: file.content ?? JSON.stringify(file.body ?? {}) });
  }
  return json({});
}

const ICONS = ['CopyOutlined', 'RightOutlined', 'DownOutlined', 'PushpinOutlined', 'ReloadOutlined',
  'ExportOutlined', 'DownloadOutlined', 'ThunderboltOutlined', 'EyeOutlined'];
const backing = new Map();
const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, TextEncoder, TextDecoder, URL, URLSearchParams, Infinity,
  isFinite, setTimeout, clearTimeout, setInterval, clearInterval,
  encodeURIComponent, decodeURIComponent,
  requestAnimationFrame: (fn) => fn(),
  fetch: (url) => serve(url),
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
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    Fragment: 'Fragment',
    // A REAL ref, kept beside this component's state. The usual stub hands back a fresh object
    // every render, which is the one thing a ref must never do — a component using one to
    // remember "a read is already in flight" would forget it on the very next frame, and a test
    // driven against that stub would report a defect the product does not have.
    useRef: (init) => {
      if (!hooks.has(current)) hooks.set(current, []);
      const bucket = hooks.get(current);
      const i = cursor++;
      if (!(i in bucket)) bucket[i] = { current: init === undefined ? null : init };
      return bucket[i];
    },
    useMemo: (fn) => fn(),
    // Real state, kept across renders by call order, so a setter firing from a resolved promise
    // shows up in the next frame instead of vanishing.
    useState: (init) => {
      if (!hooks.has(current)) hooks.set(current, []);
      const bucket = hooks.get(current);
      const i = cursor++;
      if (!(i in bucket)) bucket[i] = typeof init === 'function' ? init() : init;
      return [bucket[i], (v) => { bucket[i] = v; }];
    },
    // Deps are not compared. The component guards its own read, and that guard is the thing under
    // test: a harness re-implementing React's comparison would be testing the re-implementation.
    useEffect: (fn) => { effects.push(fn); },
  },
  antd: {
    Button: 'Button', Table: 'Table', Tooltip: 'Tooltip', Tag: 'Tag', Space: 'Space', Spin: 'Spin',
    Dropdown: 'Dropdown', Drawer: 'Drawer', Checkbox: 'Checkbox', Radio: 'Radio',
    Modal: { confirm() {} },
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
SW.store.set({ me: { id: 'u1' } });

// A shallow renderer: a component element is CALLED and its output walked, so a card that only
// exists three levels below `SW.Message` is still on the list. `tool_card_detail_harness.mjs` calls
// one component by hand; this transcript has a fold inside a dispatcher inside a message, and the
// claim is about what a person ends up looking at.
//
// A component that throws is recorded as having thrown rather than taking the run down: the
// population here is "what the transcript drew", and a card that broke is a finding, not a crash.
function render(node, path, out) {
  if (node === null || node === undefined || node === false || node === true) return out;
  if (Array.isArray(node)) {
    node.forEach((n, i) => render(n, `${path}.${i}`, out));
    return out;
  }
  if (typeof node !== 'object') { out.push({ text: String(node) }); return out; }
  const p = node.p || {};
  const isComponent = typeof node.t === 'function';
  out.push({ el: isComponent ? node.t.name : node.t, className: p.className, onClick: p.onClick,
             title: p.block && p.block.title });
  if (isComponent) {
    current = path;
    cursor = 0;
    let tree = null;
    try {
      tree = node.t(p);
    } catch (err) {
      out.push({ el: `THREW:${node.t.name}`, text: String(err && err.message) });
      return out;
    }
    return render(tree, `${path}/r`, out);
  }
  render(node.c, `${path}.c`, out);
  if (p.children) render(p.children, `${path}.p`, out);
  return out;
}

if (spec.live) {
  // No `openThread`: this is a turn sent into an empty transcript, which is what the reducer sees.
  // `seedArtifacts` is what this session already holds from EARLIER turns — the rows
  // `state.thread.artifacts` accumulates as a Conversation runs. A live turn that rewrites one of
  // those paths has to be read against them, so they are seeded rather than assumed empty.
  SW.store.set({ thread: { id: spec.thread.id, artifacts: spec.seedArtifacts || [] },
                 messages: [], scope: { id: 'p', name: 'P' } });
  await SW.store.sendMessage('which customers use model monitoring');
} else {
  await SW.store.openThread(spec.thread.id);
}
const messages = SW.store.get().messages || [];
const blocks = messages.flatMap((m) => m.blocks || []);
// Everything the store asked for while building the transcript. A folded row must not be in it.
const beforeOpening = requests.slice();

// The assistant turn the Artifacts landed on, rendered the way the transcript renders it.
const message = messages.find((m) => (m.blocks || []).some((b) => b.type === 'working_reads_fold'))
  || messages.find((m) => m.role === 'assistant');

// One frame: render the turn, run whatever effects it armed, and let the promises they started
// take their turns. Several ticks rather than one, because the fold's read goes through the same
// pooled builder the transcript uses and that is a chain of awaits, not a single resolution.
// Render the turn and run whatever effects it armed, WITHOUT letting anything settle. This is the
// frame a person is looking at between two fast clicks, and it is the only way to press the
// control twice before a read lands: a handler read off a stale render still closes over the old
// state, so pressing it again re-sets the same value rather than toggling.
function paint() {
  effects = [];
  const out = [];
  if (message) render({ t: SW.Message, p: { message } }, 'msg', out);
  effects.forEach((fn) => fn());
  return out;
}

// One frame, then let the promises it started take their turns. Several ticks rather than one,
// because the fold's read goes through the same pooled builder the transcript uses and that is a
// chain of awaits, not a single resolution.
async function frame() {
  const out = paint();
  for (let i = 0; i < 4; i += 1) await new Promise((r) => setTimeout(r, 0));
  return out;
}

// A FIXED number of frames, not `while (dirty)`. A rendered turn holds components that re-arm
// their own state every frame — the answer's own `TableBlock` is one — so "nothing moved" is not a
// state this tree ever reaches, and looping on it hangs forever. The sequence under test is three
// frames long (open, read, arrive) and six is slack.
const FRAMES = 6;
async function settle() {
  let out = await frame();
  for (let i = 1; i < FRAMES; i += 1) out = await frame();
  return out;
}

let nodes = await settle();
// The control a person presses, found by its words rather than by position — a fold whose face
// stopped offering a way in would fail here rather than quietly testing a closed card.
const toggle = () => nodes.find((n) => n.el === 'Button' && n.onClick
  && nodes[nodes.indexOf(n) + 1] && /the steps$/.test(nodes[nodes.indexOf(n) + 1].text || ''));
const opener = toggle();
if (spec.open) {
  if (!opener) throw new Error('the fold offers nothing to open');
  opener.onClick();
  // `flutter` is the impatient double-click: opened, shut and opened again before the first read
  // can land. `paint` between the presses and never `frame`, for two reasons that pull the same
  // way — a handler must be read off a FRESH render or it closes over the old `open` and re-sets
  // it instead of toggling, and settling would let the read finish, which is the very thing that
  // makes the question unaskable.
  if (spec.flutter) {
    nodes = paint();
    const shut = toggle();
    if (!shut) throw new Error('the open fold offers nothing to shut');
    shut.onClick();
    nodes = paint();
    const again = toggle();
    if (!again) throw new Error('the shut fold offers nothing to open');
    again.onClick();
  }
  nodes = await settle();
}

const words = nodes.filter((n) => n.text).map((n) => n.text).join(' ');
console.log(JSON.stringify({
  // What the store built, in transcript order, so the fold's position can be read off it.
  blocks: blocks.map((b) => ({ type: b.type, path: b.path || b.src || null,
                               count: typeof b.count === 'number' ? b.count : null,
                               items: (b.items || []).map((a) => a.path) })),
  beforeOpening,
  requests,
  words,
  opens: !!opener,
  // What the viewer's data-access preference does to each kind of card the fold can hold, asked of
  // the REAL table with the preference OFF. The fold renders its rows straight through
  // `SW.MessageBlock` rather than through `pushBlock`, so it is a second renderer that never
  // consults `HIDDEN_BY_DATA_ACCESS` — this is what makes the day one of those rows flips to hide
  // a red here instead of a silent leak behind one face.
  foldKinds: await (async () => {
    SW.prefs.set('dataAccessShown', false);
    const kinds = spec.kinds || [];
    const built = await SW.hydrateArtifacts(kinds);
    return built.map((b) => ({ type: b.type, hides: SW.store.hidesForDataAccess(b) }));
  })(),
  // Every dispatcher element the rendered turn actually put on screen, with the table titles, so
  // "the cards are behind the fold" is read off the tree rather than off the store.
  drawn: nodes.filter((n) => n.el === 'MessageBlock').length,
  tables: nodes.filter((n) => n.el === 'TableBlock').map((n) => n.title || null),
  files: nodes.filter((n) => n.el === 'FileCard').length,
}));
