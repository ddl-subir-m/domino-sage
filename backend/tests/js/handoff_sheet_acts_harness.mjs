// Drives the real handoff sheet through its acts — name the new app, Open Builder, Cancel, write a
// new plan — and reports what each one asked the store for (#661).
//
// stdout is one JSON line.
import fs from 'node:fs';
import vm from 'node:vm';
import { unrefTimeout } from './sandbox_timeout.mjs';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;

let cells = [];
let cursor = 0;
const calls = [];

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, setTimeout: unrefTimeout, clearTimeout,
  React: {
    createElement: (t, p, ...c) => ({ t, p, c }),
    useState: (init) => {
      const i = cursor;
      cursor += 1;
      if (!(i in cells)) cells[i] = typeof init === 'function' ? init() : init;
      return [cells[i], (v) => { cells[i] = typeof v === 'function' ? v(cells[i]) : v; }];
    },
    useEffect: () => {},
  },
  antd: {
    Input: Object.assign(function Input() {}, { TextArea: 'Input.TextArea' }),
    Button: function Button() {},
    Modal: function Modal() {},
    Radio: Object.assign(function Radio() {}, { Group: function RadioGroup() {} }),
    Space: function Space() {},
    Select: function Select() {},
    Alert: function Alert() {},
    Checkbox: function Checkbox() {},
    message: { error() {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  SW: {
    store: {
      get: () => ({
        handoffOpen: true,
        handoffDraft: {
          title: 'Pipeline Signal Board', appName: 'Pipeline Signal Board',
          artifacts: [], apps: [],
        },
      }),
      set(patch) { calls.push(['set', patch]); },
      confirmHandoff(include, target) {
        calls.push(['confirmHandoff', target]);
        return Promise.resolve({ ok: true });
      },
      cancelHandoffDraft() { calls.push(['cancelHandoffDraft']); },
      draftHandoffPlan(id, opts) {
        calls.push(['draftHandoffPlan', id || null, opts || null]);
        return Promise.resolve(null);
      },
    },
    prefs: { get: () => false },
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(ROOT + 'components/handoff.js', 'utf8'), sandbox,
  { filename: 'components/handoff.js' });

function* walk(node) {
  if (!node || typeof node !== 'object') return;
  if (Array.isArray(node)) { for (const c of node) yield* walk(c); return; }
  yield node;
  yield* walk(node.c);
}
const strings = (node) => [...walk(node)].flatMap((n) => (n.c || []).flat(Infinity))
  .filter((c) => typeof c === 'string');
const render = () => { cursor = 0; return sandbox.SW.HandoffSheet(); };
const find = (tree, pred) => [...walk(tree)].find((n) => n.p && pred(n));

let tree = render();
const nameField = find(tree, (n) => n.p['aria-label'] === 'New app name');
const defaultName = nameField ? nameField.p.value : null;
if (nameField) nameField.p.onChange({ target: { value: 'Signal Room' } });
tree = render();
await find(tree, (n) => n.t === sandbox.antd.Modal).p.onOk();
const redraft = find(render(), (n) => n.t === sandbox.antd.Button
  && strings(n).join(' ').includes('Write a new plan'));
if (redraft) await redraft.p.onClick();
find(render(), (n) => n.t === sandbox.antd.Modal).p.onCancel();

console.log(JSON.stringify({
  defaultName,
  confirmTargets: calls.filter((c) => c[0] === 'confirmHandoff').map((c) => c[1]),
  redrafts: calls.filter((c) => c[0] === 'draftHandoffPlan').map((c) => c[2]),
  cancels: calls.filter((c) => c[0] === 'cancelHandoffDraft').length,
}));
