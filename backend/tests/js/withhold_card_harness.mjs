// Renders SW.MessageBlock for the guardrail search's card, with a recording createElement, so the
// tests read the tree a browser would build.
//
// The copy is the thing worth pinning. Everything this design decided a person ever SEES is in
// these strings: which thing was matched, whether their own words are involved, what survives, and
// how long the withhold lasts. A source assertion cannot see any of it, and a wrong word here is
// the difference between "Sage stopped sending your file" and "Sage changed your data".
//
// The REAL store is loaded, not a stub. The card's button reads `store.withholdRerunPrompt` to
// decide whether it may promise the conversation carries on (#311), and a fake answer to that
// would certify the card against a rule nobody runs. Only the two ACTS are stubbed — which one a
// button calls matters as much as its label, and neither may reach the network from here.
//
// Input on stdin: `{ block }`, plus optional `messages` / `buildMessages` transcripts. They
// default to a conversation with a question in it, which is the only state the send paths can
// produce; a test that wants the state they cannot produce says so.
import fs from 'node:fs';
import vm from 'node:vm';
import { unrefTimeout } from './sandbox_timeout.mjs';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const spec = JSON.parse(fs.readFileSync(0, 'utf8'));

const clicked = [];

function node(type, props, ...children) {
  const flat = [];
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    flat.push(child);
  }
  if (typeof type === 'function') return type(props || {});
  return {
    tag: String(type),
    className: (props && props.className) || '',
    onClick: props && props.onClick,
    kind: (props && props.type) || '',
    children: flat,
  };
}

const sandbox = {
  console, JSON, Object, String, Array, Error, Set, Map, Date, Math, Number, Boolean, Promise,
  RegExp, Infinity, encodeURIComponent, decodeURIComponent, parseInt, parseFloat, isNaN,
  setTimeout: unrefTimeout, clearTimeout, setInterval: () => 1, clearInterval: () => {},
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  location: { hash: '' },
  addEventListener() {}, removeEventListener() {},
  // Nothing here may reach the network. The card only renders, and the one act that would post is
  // stubbed below, so a request arriving at all is a bug worth failing on rather than serving.
  fetch: async (url) => { throw new Error(`the card asked for ${url}`); },
  React: {
    createElement: node,
    useState: (v) => [v, () => {}],
    useEffect: () => {},
    useMemo: (fn) => fn(),
    useRef: () => ({ current: null }),
    Fragment: 'Fragment',
  },
  antd: {
    Button: 'Button', Table: 'Table', Tooltip: 'Tooltip', Tag: 'Tag', Space: 'Space',
    Spin: 'Spin', Input: Object.assign(function Input() {}, { TextArea: 'Input.TextArea' }),
    Modal: Object.assign(function Modal() {}, { confirm() {}, error() {} }),
    message: { success() {}, error() {}, info() {}, warning() {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
// `index.html`'s order, not a convenient one. The point of loading the real modules is that the
// card is certified against what the browser runs, and an order the browser never uses is a
// second thing this file would be certifying instead.
const LOADED = ['theme.js', 'util.js', 'api.js', 'store.js', 'prefs.js',
                'components/message-blocks.js'];
for (const f of LOADED) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

// A question on both surfaces, because the card names the one it is drawn on and the store reads
// that transcript and no other. This is what a real conversation looks like at the moment a turn
// is refused: both send paths refuse text that trims to nothing, so a user row always carries one.
const ASKED = [{ role: 'user', blocks: [{ type: 'text', value: 'chart the weekly panel spend' }] }];
SW.store.set({
  messages: spec.messages || ASKED,
  buildMessages: spec.buildMessages || ASKED,
});

// Recording stubs, applied over the real store rather than in place of it. The primary act writes
// a row that changes what every later turn sends, and the other only hides a card.
SW.store.withholdContent = (block) => clicked.push(
  `withhold:${block.surface}:${(block.carriers || []).map((c) => c.key).join(',')}`);
SW.store.dismissWithholdCard = () => clicked.push('dismiss');

const rendered = SW.MessageBlock({ block: spec.block });

function walk(n, out) {
  if (n === null || n === undefined) return out;
  if (typeof n === 'string' || typeof n === 'number') {
    if (out.length) out[out.length - 1].text += String(n);
    return out;
  }
  out.push({ tag: n.tag, className: n.className, kind: n.kind, text: '', hasClick: !!n.onClick });
  const at = out.length - 1;
  for (const child of n.children) walk(child, out);
  if (n.onClick) { n.onClick(); out[at].act = clicked[clicked.length - 1]; }
  return out;
}

console.log(JSON.stringify({ nodes: walk(rendered, []), clicked }));
