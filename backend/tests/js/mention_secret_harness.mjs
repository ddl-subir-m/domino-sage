// A Project secret in the composer's @ menu (#643), and `{env:NAME}` drawn as a chip.
//
// Same approach as `mention_skill_harness.mjs`: `createElement` returns a plain object and
// `useState` is real per mount, so typing "@<query>" through the composer's own handler opens the
// menu. `fetch` records every request, so "picking a secret attaches nothing" is proved by what was
// NOT sent.
//
// Input on stdin: `{ "mode": "chat"|"build", "query": "<after the @>", "available": bool,
//                    "markdown": "<text to draw as a sent message>" }`.
// Output: `{ rows, inserted, posts, field, mirror, message }`.
import fs from 'node:fs';
import vm from 'node:vm';
import { unrefTimeout } from './sandbox_timeout.mjs';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { mode, query, available, markdown } = JSON.parse(fs.readFileSync(0, 'utf8'));

const posts = [];
const json = (body) => ({ ok: true, status: 200, headers: { get: () => 'application/json' },
                          text: async () => JSON.stringify(body), json: async () => body });

let hooks = [];
let cursor = 0;
function hookState(init) {
  const at = cursor;
  cursor += 1;
  if (!(at in hooks)) hooks[at] = typeof init === 'function' ? init() : init;
  return [hooks[at], (next) => {
    hooks[at] = typeof next === 'function' ? next(hooks[at]) : next;
  }];
}

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, TextDecoder,
  setTimeout: unrefTimeout, clearTimeout, setInterval: () => 1, clearInterval: () => {},
  requestAnimationFrame: (fn) => fn(),
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  location: { hash: `#/${mode}`, href: `http://x/#/${mode}` },
  history: { replaceState() {}, pushState() {} },
  addEventListener() {}, removeEventListener() {},
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    useState: hookState, useEffect: () => {}, useMemo: (fn) => fn(), useCallback: (fn) => fn,
    useRef: () => ({ current: null }), Fragment: 'Fragment',
  },
  antd: {
    Input: Object.assign(function Input() {}, { TextArea: 'Input.TextArea' }),
    Button: 'Button', Dropdown: 'Dropdown', Tag: 'Tag', Tooltip: 'Tooltip', Space: 'Space',
    Drawer: 'Drawer', Skeleton: 'Skeleton', Checkbox: 'Checkbox', Alert: 'Alert',
    Modal: { confirm: () => ({ update() {}, destroy() {} }) },
    message: { success() {}, error() {}, info() {}, warning() {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async (url, init = {}) => {
    if ((init.method || 'GET').toUpperCase() !== 'GET') posts.push(String(url));
    return json({});
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js', 'router.js',
                 'components/resource-tree.js', 'components/resource-panel.js',
                 'components/composer.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

function flatten(node, out = [], depth = 0) {
  if (!node || depth > 60) return out;
  if (Array.isArray(node)) {
    node.forEach((child) => flatten(child, out, depth));
    return out;
  }
  if (typeof node !== 'object' || !node.t) return out;
  out.push(node);
  if (typeof node.t === 'function' && node.t.name !== 'Input') {
    flatten(node.t(Object.assign({}, node.p, { children: node.c })), out, depth + 1);
  }
  flatten(node.c, out, depth + 1);
  return out;
}
// In document order, so a chip's braces read where they sit.
const words = (node) => {
  if (node === null || node === undefined || node === false) return '';
  if (typeof node === 'string') return node;
  if (Array.isArray(node)) return node.map(words).join('');
  return node.c ? words(node.c) : '';
};

SW.store.set({
  scope: { id: 'proj', name: 'Demo Project' },
  thread: { id: 'conv_1', title: 'sales', artifacts: [] }, messages: [],
  activeApp: mode === 'build' ? { id: 'app_a', name: 'Sales' } : null,
  resourceGroups: { dataset: [{ id: 'dataset:openings', name: 'openings', kind: 'dataset' }] },
  extensions: { items: [] },
  secrets: { available, reason: available ? null : 'Not in a Domino Project.',
             secrets: [{ name: 'OPENAI_API_KEY', note: 'OpenAI key' }, { name: 'CRM_TOKEN', note: '' }] },
});

const props = { showMode: mode === 'build', onSend: () => {} };
const render = () => { cursor = 0; return SW.Composer(props); };
render();
const box = flatten(render()).find((n) => n.t === 'Input.TextArea');
const value = `Call it with @${query}`;
box.p.onChange({ target: { value, selectionStart: value.length }, nativeEvent: {} });
const items = flatten(render()).filter((n) => String(n.p.className || '').startsWith('sw-mention-item'));
const nameOf = (row) => flatten(row).filter((n) => n.p && n.p.className === 'sw-mention-name')
  .flatMap((n) => (n.c || []).flat(Infinity)).join('');
const rows = items.map((row) => ({ name: nameOf(row) }));
const secret = items.find((row) => nameOf(row) === 'OPENAI_API_KEY');
if (secret) await secret.p.onClick();
for (let i = 0; i < 20; i += 1) await new Promise((r) => setTimeout(r, 0));
const drawn = flatten(render());
const inserted = String(drawn.find((n) => n.t === 'Input.TextArea').p.value || '');
const field = drawn.find((n) => String(n.p.className || '').startsWith('sw-composer-field'));
const mirror = drawn.find((n) => n.p.className === 'sw-composer-mirror');
const chip = (nodes) => nodes.filter((n) => String(n.p.className || '').startsWith('sw-secret-ref ')
  || n.p.className === 'sw-secret-ref');
const out = {
  rows, inserted, posts,
  field: field && field.p.className,
  // What the mirror says, braces and all, and what of it the chip draws as the name.
  mirror: mirror ? { text: words(mirror), chips: chip(flatten(mirror)).map((c) => (c.c || [])[1]) } : null,
};
if (markdown !== undefined) {
  const nodes = flatten(SW.util.markdown(markdown));
  out.message = { chips: chip(nodes).map((c) => words(c)), text: words(SW.util.markdown(markdown)),
                  code: nodes.filter((n) => n.t === 'code').map((n) => words(n)) };
}
console.log(JSON.stringify(out));
