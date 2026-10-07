// Draws the real handoff sheet over a plan whose heading is already an app's name, and reports what
// its Plan line says beside the name field as the person edits the name and picks an app (#685).
//
// stdout is one JSON line.
import fs from 'node:fs';
import vm from 'node:vm';
import { unrefTimeout } from './sandbox_timeout.mjs';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;

let cells = [];
let cursor = 0;

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
        // `title` is the plan's own heading; `appName` is the name the server made fresh for it.
        handoffDraft: {
          title: 'Signal Room', appName: 'Signal Room 3',
          artifacts: [],
          apps: [{ id: 'a1', name: 'Draft app 1' }, { id: 'a2', name: 'Signal Room' }],
        },
      }),
    },
    prefs: { get: () => false },
    util: { relativeTime: () => 'today' },
    brand: { text: (s) => s },
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
const render = () => { cursor = 0; return sandbox.SW.HandoffSheet(); };
const find = (tree, pred) => [...walk(tree)].find((n) => n.p && pred(n));
const planLine = (tree) => {
  const section = find(tree, (n) => n.p.className === 'sw-handoff-section'
    && n.c[0] && n.c[0].c && n.c[0].c[0] === 'Plan');
  return section.c[1];
};
const field = (tree) => find(tree, (n) => n.p['aria-label'] === 'New app name');
const shown = () => {
  const tree = render();
  const f = field(tree);
  return { plan: planLine(tree), field: f ? f.p.value : null };
};

const opened = shown();
field(render()).p.onChange({ target: { value: 'Pipeline Board' } });
const edited = shown();
field(render()).p.onChange({ target: { value: '  ' } });
const cleared = shown();
find(render(), (n) => n.t === sandbox.antd.Radio.Group).p.onChange({ target: { value: 'a1' } });
const existing = shown();

console.log(JSON.stringify({ opened, edited, cleared, existing }));
