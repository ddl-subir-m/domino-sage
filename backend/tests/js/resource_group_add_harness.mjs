// Where the working set offers a way to add a Resource of a given kind (#164).
//
// The panel's add doors used to be two, and neither was the one a person needed. The head's
// "Add resources" dropdown is kind-blind — it names the acts, not the groups. The per-group
// "Add from Domino" link lived inside the `count === 0` branch, so it was drawn only while the
// group was empty: adding the first Language model to a Project took the door away, and adding
// a second one had nowhere to start from inside the group it belongs to.
//
// The claim is about a control being on screen, and that cannot be greppped out of the source —
// the link's source line does not say which branch it sits in. So the tree is drawn, per group,
// with rows in some groups and none in others, and the doors are counted off it.
//
// Nothing is mounted: `createElement` is stubbed to a plain object, so calling the component
// returns tree data. Hooks are real per mount, because the collapse state is what criterion 4 is
// about — a `+` that also collapsed the group would be a door that shuts behind you.
//
// Input on stdin: `{ "act": "drawn" | "press-filled" | "press-empty" | "press-data" }`.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { act } = JSON.parse(fs.readFileSync(0, 'utf8'));

// A working set holding every shape of group this asks about, in one draw.
//
// `model_predictive` held NO rows when this was written, because the bug was a door that appeared
// only on an empty group and the two states had to sit side by side. A group with nothing in it is
// no longer drawn at all (ADR-0035), so "the empty group keeps its door too" is not a claim about
// anything any more — and the claim that mattered, a group with ROWS still offering the door, is
// asked of three groups here instead of one.
//
// `agent` holds a row for the opposite reason: Agents is a `placeholder` group, and the rule that
// it gets no door was previously only ever exercised on an empty group, which is now indis-
// tinguishable from a group that is absent. With a row it is drawn, and the absence of the door is
// the placeholder rule rather than the group being gone.
const GROUPS = {
  dataset: [{ id: 'dataset:d1', name: 'Sales rows', kind: 'dataset' }],
  datasource: [{ id: 'data_source:s1', name: 'Warehouse', kind: 'datasource' }],
  model_llm: [{ id: 'llm_alias:m1', name: 'Risk scorer', kind: 'model_llm', alias: 'risk-scorer' }],
  model_predictive: [{ id: 'model_api:p1', name: 'Churn risk', kind: 'model_predictive' }],
  agent: [{ id: 'agent:a1', name: 'Desk agent', kind: 'agent' }],
  file: [{ id: 'file:.sage/scratch/notes.csv', name: 'notes.csv', kind: 'file',
           path: '.sage/scratch/notes.csv', source: 'scratch' }],
};

// Real hooks, by call order, per mount. `collapsed` is the one that matters: pressing `+` must
// leave it exactly as it was.
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
  console, JSON, Object, String, Array, Error, Map, Set, Promise, Date, Math, Number, Boolean,
  RegExp, encodeURIComponent, decodeURIComponent, setTimeout, clearTimeout,
  setInterval: () => 1, clearInterval: () => {}, requestAnimationFrame: (fn) => fn(),
  URLSearchParams, TextEncoder, TextDecoder, URL,
  fetch: () => Promise.reject(new Error('the harness makes no requests')),
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  document: {
    title: '', documentElement: { style: { setProperty: () => {} } },
    addEventListener: () => {}, removeEventListener: () => {},
    getElementById: () => ({}), querySelector: () => null, body: {},
  },
  location: { search: '', pathname: '/', href: 'http://localhost/#/build', hash: '#/build' },
  history: { replaceState() {}, pushState() {} },
  addEventListener: () => {}, removeEventListener: () => {},
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    useState: hookState,
    useEffect: () => {}, useMemo: (fn) => fn(), useCallback: (fn) => fn,
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
for (const f of ['util.js', 'prefs.js', 'router.js', 'store.js', 'api.js',
                 'components/resource-tree.js', 'components/resource-panel.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

// The listing has landed — `resourcesLoading` false — because a loading panel draws '…' for every
// count and the empty branch says "Loading this project…", which is neither of the states here.
SW.store.set({ resourceGroups: GROUPS, resourcesLoading: false, resourceErrors: {} });

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

const text = (node) => (node.c || []).flat(Infinity).filter((c) => typeof c === 'string').join('');
const panel = () => { cursor = 0; return flatten(SW.ResourcePanel()); };

// One entry per group head: what it is called, whether it carries an add door, and whether the
// door is a real button rather than a div — a `+` inside a `role="button"` row is both invalid
// markup and unreachable by keyboard.
function groupHeads(nodes) {
  return nodes
    .filter((n) => (n.p || {}).className === 'sw-res-group-label')
    .map((n) => {
      const drawn = flatten(n);
      const toggle = drawn.find((d) => (d.p || {}).className === 'sw-res-group-toggle');
      const add = drawn.find((d) => (d.p || {}).className === 'sw-res-group-add');
      const tip = drawn.find((d) => d.t === 'Tooltip');
      return {
        label: toggle ? text(flatten(toggle).find((d) => (d.p || {}).className === 'sw-group-label')) : '',
        // The row itself must NOT be the control any more; it holds two of them.
        rowIsButton: (n.p || {}).role === 'button' || typeof (n.p || {}).onClick === 'function',
        toggleIsButton: !!toggle && toggle.t === 'button',
        hasAdd: !!add,
        addIsButton: !!add && add.t === 'button',
        addLabel: add ? (add.p || {})['aria-label'] : null,
        addTooltip: tip ? (tip.p || {}).title : null,
      };
    });
}

// Every group's collapse state, so a press can be shown to have left it alone.
const collapseState = () => JSON.stringify(hooks);

const report = {};

if (act === 'drawn') {
  const nodes = panel();
  report.heads = groupHeads(nodes);
  // The empty branch's own link, which went with the branch (ADR-0035). Reported rather than
  // dropped: this fix moved the door to the head, and a second copy reappearing below would be the
  // two doors #164 spent a commit collapsing into one.
  report.emptyLinks = nodes
    .filter((n) => n.t === 'Button' && (n.p || {}).type === 'link')
    .map(text)
    .filter((t) => t.includes('Add from'));
} else {
  // Which group gets pressed. `press-filled` is the bug's own case: a group with rows in it.
  const which = { 'press-filled': 'Language models', 'press-predictive': 'Predictive models', 'press-data': 'Data' }[act];
  const head = panel().filter((n) => (n.p || {}).className === 'sw-res-group-label')
    .map((n) => ({ n, drawn: flatten(n) }))
    .find(({ drawn }) => drawn.some((d) => (d.p || {}).className === 'sw-group-label' && text(d).startsWith(which)));
  const before = collapseState();
  const add = head.drawn.find((d) => (d.p || {}).className === 'sw-res-group-add');
  add.p.onClick();
  const after = SW.store.get();
  report.pressed = which;
  report.catalogOpen = after.catalogOpen;
  // `null` is Everything, which is the only honest filter for a group holding two kinds.
  report.catalogKind = after.catalogKind;
  report.collapseUnchanged = collapseState() === before;
}

console.log(JSON.stringify(report));
