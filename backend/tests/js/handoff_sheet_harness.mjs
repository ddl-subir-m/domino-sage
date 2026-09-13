// Renders the real handoff sheet for each preference set, and reports the file sections.
//
// The bug is a consent claim made by the drawn sheet: a file that always crosses was listed beside
// files controlled by preferences. Reading the branch is not enough, because the claim changes with
// the saved answers. This harness renders both states the way the browser does.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));

let cells = [];
let cursor = 0;
let prefs = {};

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, setTimeout, clearTimeout,
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
          title: 'Desk exposure',
          artifacts: [{ path: 'examples/conv_1/by-desk.table.json' },
                      { path: 'examples/conv_1/by-book.table.json' }],
          apps: [],
        },
      }),
      set() {},
      confirmHandoff() { return Promise.resolve({ ok: true }); },
    },
    prefs: { get: (name) => prefs[name] },
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

const byClass = (tree, cls) => [...walk(tree)].filter((n) =>
  n.p && String(n.p.className || '').split(' ').includes(cls));

function render(c) {
  prefs = {
    handoffResources: c.resources,
    handoffArtifacts: c.artifacts,
    handoffTranscript: c.transcript,
  };
  cells = [];
  cursor = 0;
  const tree = sandbox.SW.HandoffSheet();
  return {
    text: strings(tree).join(' '),
    sections: byClass(tree, 'sw-handoff-files').map((section) => ({
      text: strings(section).join(' '),
      rows: byClass(section, 'sw-handoff-file').map((row) => strings(row).join(' ')),
    })),
  };
}

console.log(JSON.stringify(cases.map(render)));
