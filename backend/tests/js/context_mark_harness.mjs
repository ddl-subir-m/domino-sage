// What mark the resources panel draws beside a row, and which of the acts earned it (#410).
//
// The reported bug was one surface promising what another refuses: the panel ticked a row that was
// in the Session context and drew a `+` on everything else, while `_delegated_aliases` counted a
// Binding as reaching the conversation just as a chip does. So a model bound through **Use in app**
// was callable on the turn and the row still offered to add it.
//
// THE FIXTURE IS NOT WRITTEN HERE. `members` and `context` arrive on stdin, and the Python wrapper
// puts the REAL `list_project_resources()` items and the REAL `/threads/<id>/context` body in them,
// taken off an orchestrator that performed the act for real. That is deliberate: `boundHere` is a
// server-computed field, and a harness inventing it would prove the client reads a boolean it was
// handed and say nothing about whether the boolean is ever true. See
// `test_the_panel_and_the_conversation_agree_on_what_it_can_call.py`, which owns the other side.
//
// Input on stdin: `{ "members": [...], "context": {...}, "mode": "chat" | "build" }`.
// Output on stdout: one row per panel row — the mark drawn, the words behind it, and whether the
// add button is still offered.
import fs from 'node:fs';
import vm from 'node:vm';
import { unrefTimeout } from './sandbox_timeout.mjs';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { members, context, mode = 'chat', unlisted: gone = [] } =
  JSON.parse(fs.readFileSync(0, 'utf8'));

// Ids the platform listing is told to LEAVE OUT, so a caller can ask what the row does once Domino
// stops holding it. Opt-in and named, because the default has to be the opposite: an accidentally
// unlisted row reads as a verdict about the mark when it is a verdict about the fixture.
const dropped = new Set(gone);

const THREAD = 'conv_1';

// The platform listing, derived from the working set so that every row reads as live. Liveness is a
// real subtraction the panel makes (#161, ADR-0034) and it is NOT the axis here: a row the listing
// does not name is drawn as missing, which would hide the mark this harness exists to read. So each
// kind is answered with exactly what membership holds, and the whole question is left to
// `working_set_liveness_harness.mjs`, which owns it.
const bare = (id) => String(id).split(':').slice(1).join(':') || String(id);

// ONE MEMBERSHIP KIND HAS TWO SPELLINGS AND THE FIXTURE HAS TO KNOW BOTH. A row added through the
// panel is written `model_llm`; the row `_join_project_on_bind` writes when an app binds an Alias
// is written `llm_alias`. Matching one spelling left the other out of the listing below, so the
// row came back `liveness: 'missing'` — and because the mark used to ignore liveness, the test
// passed anyway. It only surfaced once the mark started checking. So this normalises, and the
// guard at the bottom of this block fails loudly rather than letting a fixture hole read as a
// verdict again.
const GROUP = {
  model_llm: 'llm', llm_alias: 'llm', llmalias: 'llm',
  dataset: 'dataset',
  datasource: 'datasource', data_source: 'datasource',
  model_predictive: 'modelapi', model_api: 'modelapi',
};
const kinds = (group) => members.filter(
  (m) => GROUP[String(m.kind || '')] === group && !dropped.has(m.id));
const RESOURCES = {
  llm_aliases: kinds('llm').map((m) => ({
    id: bare(m.id), name: m.alias || bare(m.id), display_name: m.name,
  })),
  data_sources: kinds('datasource').map((m) => ({ id: bare(m.id), name: m.name })),
  model_apis: kinds('modelapi').map((m) => ({ id: bare(m.id), name: m.name })),
  assets: kinds('dataset').map((m) => ({ id: bare(m.id), name: m.name })),
  errors: {},
};

const unlisted = members.filter((m) => !GROUP[String(m.kind || '')]);
if (unlisted.length) {
  // Not a silent skip: an unrecognised kind would be served no listing row, the panel would draw
  // it as gone from Domino, and every claim about its mark would be a claim about a missing row.
  console.error(`context_mark_harness: no listing leg for kind(s) `
    + `${[...new Set(unlisted.map((m) => m.kind))].join(', ')} — add it to GROUP`);
  process.exit(2);
}

function serve(url) {
  const path = String(url).replace(/^\.\/api/, '').split('?')[0];
  if (path === '/project/resources') return { items: members };
  if (path.match(/^\/threads\/[^/]+\/context$/)) return context;
  // Before the bare-thread route below it, which would otherwise swallow the context read.
  if (path.match(/^\/threads\/[^/]+$/)) {
    return { id: THREAD, title: 'The desk talk', history: [], artifacts: [], touched: [] };
  }
  if (path === '/resources') return RESOURCES;
  if (path === '/assets') return { assets: RESOURCES.assets };
  if (path === '/project') return { attached: [], scratch: [] };
  if (path === '/apps') return { items: [] };
  if (path === '/bindings') return { bindings: [] };
  if (path === '/threads') return { threads: [{ id: THREAD, title: 'The desk talk' }] };
  if (path === '/members') {
    return { members: [], directory: [], ownerId: '', self: '', connected: true };
  }
  return {};
}

let hooks = [];
let cursor = 0;
function hookState(init) {
  const at = cursor;
  cursor += 1;
  if (!(at in hooks)) hooks[at] = typeof init === 'function' ? init() : init;
  return [hooks[at], (next) => { hooks[at] = typeof next === 'function' ? next(hooks[at]) : next; }];
}

const sandbox = {
  console, JSON, Object, String, Array, Error, Map, Set, Promise, Date, Math, Number, Boolean,
  RegExp, encodeURIComponent, decodeURIComponent, setTimeout: unrefTimeout, clearTimeout,
  setInterval: () => 1, clearInterval: () => {}, requestAnimationFrame: (fn) => fn(),
  URLSearchParams, TextEncoder, TextDecoder, URL, Blob, ArrayBuffer, Uint8Array,
  fetch: async (url) => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    const body = serve(url);
    return {
      ok: true, status: 200, statusText: 'OK',
      headers: { get: () => 'application/json' },
      json: async () => body,
      text: async () => JSON.stringify(body),
    };
  },
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  location: { href: `http://localhost/#/${mode}`, hash: `#/${mode}` },
  history: { replaceState: () => {}, pushState: () => {} },
  addEventListener: () => {},
  removeEventListener: () => {},
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    useState: hookState,
    useEffect: () => {},
    useMemo: (fn) => fn(),
    useCallback: (fn) => fn,
    useRef: () => ({ current: null }),
    Fragment: 'Fragment',
  },
  antd: {
    Input: Object.assign(function Input() {}, { TextArea: 'Input.TextArea' }),
    Button: 'Button', Dropdown: 'Dropdown', Tag: 'Tag', Tooltip: 'Tooltip', Space: 'Space',
    Drawer: 'Drawer', Skeleton: 'Skeleton', Empty: 'Empty', Checkbox: 'Checkbox', Alert: 'Alert',
    Modal: Object.assign(function Modal() {}, { confirm: () => {}, info: () => {} }),
    message: { info: () => {}, success: () => {}, error: () => {}, warning: () => {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  EventSource: function () {},
  SW: {},
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

for (const file of ['util.js', 'prefs.js', 'router.js', 'store.js', 'api.js',
                    'components/resource-tree.js', 'components/resource-panel.js',
                    'components/conversation-list.js', 'components/composer.js']) {
  vm.runInContext(fs.readFileSync(ROOT + file, 'utf8'), sandbox, { filename: file });
}
const SW = sandbox.SW;
SW.router.go = () => {};

const settle = async () => {
  for (let i = 0; i < 60; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

// Function components are CALLED rather than left as nodes. The `createElement` stub above returns
// a plain object, so `h(MyComponent, props)` would otherwise sit in the tree with its body never
// run and every className this harness looks for absent — a walk over that tree finds nothing and
// reads exactly like a row that drew nothing.
function flatten(node, out = [], depth = 0) {
  if (!node || depth > 60) return out;
  if (Array.isArray(node)) {
    node.forEach((child) => flatten(child, out, depth + 1));
    return out;
  }
  if (!node.t) return out;
  out.push(node);
  if (typeof node.t === 'function') {
    flatten(node.t(Object.assign({}, node.p, { children: node.c })), out, depth + 1);
  }
  flatten(node.c, out, depth + 1);
  return out;
}

await SW.store.setScope({ id: 'proj', name: 'Demo Project' }, { silent: true });
// Through `openThread` rather than by assigning `state.thread`: the chips are read by
// `refreshAttachments`, which only that path calls. Setting the thread by hand leaves
// `state.attachments` empty, and every row then draws the `+` — the panel would look exactly as it
// did before the fix while proving nothing about it.
await SW.store.openThread(THREAD);
await settle();

const drawn = flatten(SW.ResourcePanel());

// One entry per row the panel drew. The mark is read off the rendered tree and not off the store,
// because what is on screen is the thing the report is about: `sw-res-ctx` is the single slot the
// row keeps for this, and which of its three shapes landed there is the whole answer.
const rows = drawn
  .filter((n) => typeof n.t === 'function' && n.t.name === 'ResourceRow')
  .map((n) => {
    const sub = flatten(n);
    const slot = sub.find((d) => String((d.p || {}).className || '').includes('sw-res-ctx'));
    const cls = slot ? String(slot.p.className) : '';
    // The Tooltip wrapping the slot carries the sentence behind the mark, and the two states have
    // to name different ones: a Binding has no chip above the message box to send a reader to.
    const tip = sub.find((d) => d.t === 'Tooltip' && flatten(d).some((k) => k === slot));
    return {
      id: (n.p.resource || {}).id || null,
      name: (n.p.resource || {}).name || '',
      // What the row was told, so a disagreement can be read as the panel's or the server's.
      inContext: !!n.p.inContext,
      callableHere: !!n.p.callableHere,
      boundHere: !!(n.p.resource || {}).boundHere,
      liveness: (n.p.resource || {}).liveness || null,
      // and what it drew: `mark` for the tick, `add` for the `+`, `spacer` for neither.
      slot: cls.includes('sw-res-ctx-add') ? 'add' : cls.includes('is-spacer') ? 'spacer' : 'mark',
      aria: slot ? (slot.p['aria-label'] || null) : null,
      title: tip ? (tip.p.title || null) : null,
      offersAdd: typeof n.p.onAddToContext === 'function',
    };
  });

console.log(JSON.stringify({ rows }));
