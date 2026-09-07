// A tool card whose input the read that drew it left out, and what it does about that.
//
// The Build history drawer reads the app's WHOLE log — every conversation, by definition, since it
// names none — and on a real app six bytes in seven of that log are the arguments a tool was called
// with: one `bash` row carries the entire file the agent wrote. So `api.appHistory` asks with
// `detail=off`, and the card goes back for its own row at the fold that reveals it.
//
// Its own harness because no other one renders a card. `build_header_harness` stops at `SW.Message`
// — it reports the drawer, and the turns inside it are elements it never calls — so the fold this
// is about is below everything that harness can see.
//
// The hooks here are real ones rather than the stub that harness uses: this claim is about a
// SEQUENCE (open, read, arrive) and a stub whose setter does nothing can only ever show the first
// frame of it.
//
// Input on stdin: `{ "block": {...}, "open": true, "rowFails": false, "retry": false }`.
// `rowFails` is `true` for a route that never answers, or a NUMBER for one that fails that many
// times and then works — which is the only way to show that the way back leads anywhere.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { block, open = false, rowFails = false, retry = false } = JSON.parse(fs.readFileSync(0, 'utf8'));

const calls = [];
let failuresLeft = rowFails === true ? Infinity : Number(rowFails) || 0;
let hooks = [];
let cursor = 0;
let effects = [];
let dirty = false;

const ICONS = ['CopyOutlined', 'RightOutlined', 'DownOutlined', 'PushpinOutlined', 'ReloadOutlined',
  'ExportOutlined', 'DownloadOutlined', 'ThunderboltOutlined'];

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, TextEncoder, TextDecoder, URL, URLSearchParams, setTimeout, clearTimeout, isFinite,
  encodeURIComponent, decodeURIComponent,
  document: { addEventListener() {}, querySelector: () => null, body: {} },
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    Fragment: 'Fragment',
    useRef: () => ({ current: null }),
    useMemo: (fn) => fn(),
    // Real state, kept across renders by call order — which is what lets a setter that fires from a
    // resolved promise show up in the next frame instead of vanishing.
    useState: (init) => {
      const i = cursor++;
      if (!(i in hooks)) hooks[i] = typeof init === 'function' ? init() : init;
      return [hooks[i], (v) => { hooks[i] = v; dirty = true; }];
    },
    // Deps are not compared. The component guards its own read (`row === null`), and that guard is
    // the thing under test: a harness that re-implemented React's comparison would be testing the
    // re-implementation instead.
    useEffect: (fn) => { effects.push(fn); },
  },
  antd: {
    Button: 'Button', Table: 'Table', Tooltip: 'Tooltip', Tag: 'Tag', Space: 'Space',
    Input: Object.assign(function Input() {}, { TextArea: 'Input.TextArea' }),
    message: { success() {}, error() {}, info() {}, warning() {} },
  },
  icons: Object.fromEntries(ICONS.map((n) => [n, n])),
  fetch: async (url) => {
    const path = String(url).replace(/^\.\/api/, '');
    calls.push(`GET ${path}`);
    if (/^\/project\/history\/row\//.test(path) && failuresLeft > 0) {
      failuresLeft -= 1;
      return { ok: false, status: 500, statusText: 'Server Error',
               headers: { get: () => 'application/json' }, json: async () => ({ error: 'nope' }) };
    }
    if (/^\/project\/history\/row\//.test(path)) {
      return { ok: true, status: 200, headers: { get: () => 'application/json' },
               json: async () => ({ detail: 'cat > src/App.tsx <<EOF\nexport default App\nEOF' }) };
    }
    return { ok: true, status: 200, headers: { get: () => 'application/json' },
             json: async () => ({}) };
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'api.js', 'components/message-blocks.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

function flatten(node, out = []) {
  if (node === null || node === undefined || node === false || node === true) return out;
  if (Array.isArray(node)) { node.forEach((n) => flatten(n, out)); return out; }
  if (typeof node !== 'object') { out.push({ text: String(node) }); return out; }
  const p = node.p || {};
  out.push({ el: typeof node.t === 'function' ? node.t.name : node.t, className: p.className,
             onClick: p.onClick, code: p.code });
  flatten(node.c, out);
  // A child passed as `children` rather than positionally (antd's own convention) still renders.
  if (p.children) flatten(p.children, out);
  return out;
}

// One frame: render the card, run whatever effects it armed, and let every promise they started
// settle. Repeated while a setter has moved something, which is how the arriving read gets drawn.
async function frame() {
  cursor = 0;
  effects = [];
  dirty = false;
  // The card is reached the way the transcript reaches it, through the block dispatcher, so a
  // `sandbox_run` that stopped routing here would fail rather than quietly test nothing.
  const el = SW.MessageBlock({ block });
  if (typeof el.t !== 'function') throw new Error('sandbox_run no longer renders a component');
  const tree = el.t(el.p);
  effects.forEach((fn) => fn());
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  return flatten(tree);
}

// The card starts folded. `open` asks for it opened, which is the click a person makes and the one
// moment that can send it looking for its row.
let nodes = await frame();
if (open) {
  const toggle = nodes.find((n) => String(n.className || '').startsWith('sw-sandbox-toggle'));
  if (!toggle || !toggle.onClick) throw new Error('the card offers nothing to open');
  toggle.onClick();
  nodes = await frame();
  while (dirty) nodes = await frame();
}
if (retry) {
  const again = nodes.find((n) => n.el === 'Button' && n.onClick);
  if (!again) throw new Error('the failed card offers no way back');
  again.onClick();
  nodes = await frame();
  while (dirty) nodes = await frame();
}

const words = nodes.filter((n) => n.text).map((n) => n.text).join(' ');
console.log(JSON.stringify({
  calls,
  words,
  // Whether a chevron is offered at all: a deferred row has to count as detail, or the one card
  // worth a fetch would be the one card nobody can open.
  opens: !!nodes.find((n) => String(n.className || '').startsWith('sw-sandbox-toggle') && n.onClick),
  // What the code fold ended up holding. `null` when no CodeBlock was drawn.
  code: (nodes.find((n) => n.el === 'CodeBlock') || {}).code ?? null,
}));
