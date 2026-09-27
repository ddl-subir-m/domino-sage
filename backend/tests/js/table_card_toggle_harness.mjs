// Drives the real table candidate card through clicks: toggle, toggle, confirm.
//
// The other two table-card harnesses mock `useState` as a function that forgets every update, which
// is right for what they ask and blind to this: a click that toggles a selection is state, and the
// confirm reads that state. So this one keeps hook state between renders, the way React does, and
// re-renders after every click. The store's two confirm doors are replaced with recorders, since
// what is asked here is what the CARD hands them — the store's own half is covered beside this.
//
// stdin is `{ block, clicks }`: the card and the button labels to press, in order. stdout is one
// JSON line.
import fs from 'node:fs';
import vm from 'node:vm';
import { unrefTimeout } from './sandbox_timeout.mjs';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { block, clicks } = JSON.parse(fs.readFileSync(0, 'utf8'));

// Hook state by call order, reset to the first slot before every render — React's own rule.
const hooks = [];
let slot = 0;
const useState = (init) => {
  const i = slot;
  slot += 1;
  if (!(i in hooks)) hooks[i] = typeof init === 'function' ? init() : init;
  return [hooks[i], (v) => { hooks[i] = typeof v === 'function' ? v(hooks[i]) : v; }];
};

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, TextEncoder, TextDecoder, URL, URLSearchParams,
  setTimeout: unrefTimeout, clearTimeout, setInterval, clearInterval,
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  React: {
    createElement: (t, p, ...c) => ({ t, p, c }),
    useState, useEffect: () => {}, useRef: () => ({ current: null }), Fragment: 'Fragment',
  },
  antd: {
    message: { success() {}, info() {}, warning() {}, error() {} },
    Button: 'Button', Table: 'Table', Tooltip: 'Tooltip', Tag: 'Tag', Space: 'Space',
    Input: 'Input', Spin: 'Spin',
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async () => ({ ok: true, status: 200, headers: { get: () => 'application/json' },
    json: async () => ({}), text: async () => '{}' }),
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js', 'components/message-blocks.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

const sent = [];
SW.store.chooseTableAndAsk = async (...args) => { sent.push({ door: 'chat', args }); };
SW.store.chooseTableAndBuild = async (...args) => { sent.push({ door: 'build', args }); };

const text = (node) => {
  if (node === null || node === undefined || node === false) return '';
  if (Array.isArray(node)) return node.map(text).join('');
  if (typeof node !== 'object') return String(node);
  return text(node.c);
};

// Every Button, by label, with its props — rendered fresh so the props read the current state.
function render() {
  slot = 0;
  const buttons = [];
  const walk = (node) => {
    if (node === null || node === undefined || node === false) return;
    if (Array.isArray(node)) { node.forEach(walk); return; }
    if (typeof node !== 'object') return;
    if (typeof node.t === 'function') { walk(node.t({ ...(node.p || {}), children: node.c })); return; }
    if (node.t === 'Button') buttons.push({ label: text(node.c), props: node.p || {} });
    walk(node.c);
  };
  walk(SW.MessageBlock({ block }));
  return buttons;
}

const settle = async () => { for (let i = 0; i < 10; i += 1) await new Promise((r) => setTimeout(r, 0)); };

const confirmOf = (buttons) => buttons.find((b) => String(b.props.className).includes('sw-table-confirm'));
const before = confirmOf(render());
const steps = [];
for (const label of clicks) {
  const buttons = render();
  const target = label === 'confirm' ? confirmOf(buttons) : buttons.find((b) => b.label === label);
  target.props.onClick();
  await settle();
  const after = render();
  steps.push({
    label,
    picked: after.filter((b) => b.props['aria-pressed']).map((b) => b.label),
    confirmDisabled: !!(confirmOf(after) || {}).props.disabled,
    confirmLabel: (confirmOf(after) || {}).label,
    sent: sent.length,
  });
}

fs.writeSync(1, `${JSON.stringify({
  hasConfirm: !!before,
  confirmDisabledBefore: !!(before && before.props.disabled),
  steps,
  sent,
})}\n`);
process.exit(0);
