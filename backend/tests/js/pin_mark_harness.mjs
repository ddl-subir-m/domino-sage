// What a Data Source's table rows draw once a leaf among them is pinned (#468).
//
// Two claims live here and only a rendered tree can hold both. The first is that a pinned leaf
// wears a mark and an unpinned one does not, which is a question about WHICH ROW carries WHICH
// ELEMENT — a source assertion can see the mark exists and not that it lands on the right row. The
// second is that pinning MOVES NOTHING: the leaf order is identical with the pin and without it.
// That claim is about a list, it has no string in it at all, and it is the one nothing else in the
// suite is positioned to ask. Rendering both from the same fixture is what makes them comparable.
//
// `DataSourceCascade` is driven directly rather than through the panel, unlike
// `data_source_scope_harness.mjs`. That file asks what the PANEL hands the cascade, so it has to go
// through the panel to ask it. Everything here is downstream of one prop — `resource.pins`, whose
// route in from membership `groupsFromMembership` already owns — and the questions are about what
// the tree does with it.
//
// The cascade opens at the table stage, because `default_database` and `default_schema` are both
// answered: a Data Source whose first two levels Domino has already decided draws its leaves on the
// first paint. That is a real shape (`ds_2` in the scope harness), not a shortcut around the walk.
//
// Nothing is mounted. Hooks are real, per instance, because the tree fetches from an effect and
// only says anything once the answer lands.
//
// Input on stdin: a list of steps, each `{ pins: [...], query?, pin?: "<TABLE>" }`. `pins` is the
// membership row's own list, in the shape `_normalize_pin` stores it. `pin` names a table whose Pin
// control the step clicks, which is how "the control is wired to the row it sits on" is asked.
//
// Output: one report per step — the leaves in the order the tree drew them, the mark and the acts
// on each, and whatever the click posted.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const steps = JSON.parse(fs.readFileSync(0, 'utf8'));
let step = steps[0];

// Deliberately NOT in alphabetical order, and the table every step pins is the MIDDLE one. Both
// halves are load-bearing and both were got wrong once here: a fixture that arrived sorted cannot
// tell a tree that sorts from one that leaves the listing alone, and a pin on the first row floats
// to a position it already holds — so "pinned rows first" reorders nothing and an order assertion
// built on it passes against the very change it exists to refuse.
const TABLES = ['DIM_ACCOUNT', 'MIXPANEL__EVENT', 'FCT_USAGE_DAILY'];

// --- a React with working hooks --------------------------------------------
const slotsOf = new Map();
let counts = null;
let current = null;
let cursor = 0;
let dirty = false;
const pending = [];

function callComponent(fn, props) {
  const name = fn.name || 'anonymous';
  const n = counts.get(name) || 0;
  counts.set(name, n + 1);
  const id = `${name}#${n}`;
  if (!slotsOf.has(id)) slotsOf.set(id, []);
  const wasId = current;
  const wasCursor = cursor;
  current = id;
  cursor = 0;
  try {
    return fn(props);
  } finally {
    current = wasId;
    cursor = wasCursor;
  }
}

const React = {
  createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
  Fragment: 'Fragment',
  useState: (init) => {
    const slots = slotsOf.get(current);
    const i = cursor++;
    if (!(i in slots)) slots[i] = typeof init === 'function' ? init() : init;
    return [slots[i], (next) => {
      const value = typeof next === 'function' ? next(slots[i]) : next;
      if (!Object.is(value, slots[i])) {
        slots[i] = value;
        dirty = true;
      }
    }];
  },
  useEffect: (fn, deps) => {
    pending.push({ slots: slotsOf.get(current), i: cursor++, fn, deps });
  },
  useRef: (init) => {
    const slots = slotsOf.get(current);
    const i = cursor++;
    if (!(i in slots)) slots[i] = { current: init === undefined ? null : init };
    return slots[i];
  },
};

function runEffects() {
  for (const e of pending.splice(0)) {
    const prev = e.slots[e.i];
    const same = prev && Array.isArray(e.deps) && Array.isArray(prev.deps)
      && prev.deps.length === e.deps.length
      && prev.deps.every((d, k) => Object.is(d, e.deps[k]));
    if (same) continue;
    if (prev && typeof prev.off === 'function') prev.off();
    const off = e.fn();
    e.slots[e.i] = { deps: e.deps, off: typeof off === 'function' ? off : null };
  }
}

// --- the browser -----------------------------------------------------------
const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Infinity, setTimeout, clearTimeout, setInterval: () => 0, clearInterval: () => {},
  requestAnimationFrame: (fn) => fn(),
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  location: { hash: '' },
  history: { replaceState() {} },
  addEventListener() {},
  removeEventListener() {},
  React,
  antd: {
    Input: Object.assign(function Input() { return null; }, { TextArea: 'Input.TextArea' }),
    Button: 'Button', Dropdown: 'Dropdown', Tag: 'Tag', Tooltip: 'Tooltip', Space: 'Space',
    Spin: 'Spin', Empty: 'Empty', Skeleton: 'Skeleton', Alert: 'Alert', Checkbox: 'Checkbox',
    Drawer: 'Drawer',
    Modal: { confirm: (cfg) => ({ update: (n) => Object.assign(cfg, n), destroy: () => {} }) },
    message: { success() {}, error() {}, info() {}, warning() {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  // The one read the table stage makes.
  fetch: async (url) => {
    const path = String(url).replace(/^\.\/api/, '');
    if (!/^\/data-sources\/[^/?]+\/tables/.test(path)) {
      return { ok: true, status: 200, statusText: 'OK',
               headers: { get: () => 'application/json' }, json: async () => ({}) };
    }
    return { ok: true, status: 200, statusText: 'OK',
             headers: { get: () => 'application/json' },
             json: async () => ({ items: TABLES }) };
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

for (const f of ['util.js', 'api.js', 'prefs.js', 'store.js', 'router.js',
                 'components/platform-error.js', 'components/resource-tree.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

// The two store actions the row's controls call, replaced so a click is recorded rather than
// followed. What they do afterwards — post, refresh the working set — belongs to the membership
// tests; what this file asks is which leaf a control was carrying when it was pressed.
const posted = [];
SW.store.pinLeaf = async (parent, pin) => { posted.push({ act: 'pin', parent: parent.id, pin }); };
SW.store.unpinLeaf = async (parent, pin) => { posted.push({ act: 'unpin', parent: parent.id, pin }); };

// --- reading the tree ------------------------------------------------------
const SKIP = new Set(['Input', 'anonymous']);
const named = new Map();
for (const [k, v] of Object.entries(SW)) if (typeof v === 'function') named.set(v, k);

function tag(node) {
  if (typeof node.t === 'string') return node.t;
  return named.get(node.t) || node.t.name || 'anonymous';
}

function walk(node, out = [], depth = 0) {
  if (node === null || node === undefined || node === false || node === true || depth > 80) return out;
  if (Array.isArray(node)) { node.forEach((n) => walk(n, out, depth)); return out; }
  if (typeof node === 'string' || typeof node === 'number') {
    out.push({ text: String(node) });
    return out;
  }
  if (typeof node !== 'object' || !node.t) return out;

  const props = node.p || {};
  const entry = {
    el: tag(node),
    className: props.className || '',
    // Both halves of the mark's promise, side by side: the ticket asks a hover and a screen reader
    // to say the same sentence, and a report that carried one of them could not see them diverge.
    title: typeof props.title === 'string' ? props.title : '',
    label: props['aria-label'] || '',
    // Whether this element does anything when pressed. The mark's whole claim is that it does not,
    // and "no `onClick`" is a fact about the element rather than about its words.
    clickable: typeof props.onClick === 'function',
  };
  if (typeof props.onClick === 'function') entry.onClick = props.onClick;
  const direct = (Array.isArray(node.c) ? node.c : [node.c]).filter(
    (child) => typeof child === 'string' || typeof child === 'number'
  );
  if (direct.length) entry.texts = direct.map(String);
  out.push(entry);

  if (typeof node.t === 'function' && !SKIP.has(tag(node))) {
    walk(callComponent(node.t, Object.assign({}, props, { children: node.c })), out, depth + 1);
  }
  walk(node.c, out, depth + 1);
  return out;
}

const settle = () => new Promise((res) => setTimeout(res, 0));

function resourceFor(s) {
  return {
    id: 'data_source:ds_1',
    name: 'Snowflake-Data-Warehouse',
    kind: 'datasource',
    bindingKey: ['data_source', 'ds_1'],
    levels: ['database', 'schema', 'table'],
    default_database: 'DWH',
    default_schema: 'MARTS',
    pins: s.pins || [],
  };
}

async function paint(s) {
  let nodes = [];
  for (let i = 0; i < 15; i += 1) {
    counts = new Map();
    dirty = false;
    nodes = walk(callComponent(SW.DataSourceCascade, {
      resource: resourceFor(s), query: s.query || '', variant: 'rail',
    }));
    runEffects();
    await settle();
    if (!dirty) break;
  }
  return nodes;
}

// One record per leaf row, in the order the tree drew them. The grouping is what makes the mark
// answerable: a flat list of every element on screen can say a pin mark was rendered and cannot say
// which name it was rendered beside.
function leavesOf(nodes) {
  const out = [];
  let leaf = null;
  for (const n of nodes) {
    const cls = String(n.className || '');
    if (cls === 'sw-tree-leaf') {
      leaf = { name: '', mark: null, acts: [] };
      out.push(leaf);
      continue;
    }
    if (!leaf) continue;
    if (cls === 'sw-tree-leaf-pin') {
      leaf.mark = { title: n.title, label: n.label, clickable: n.clickable };
      continue;
    }
    if (cls === 'sw-tree-leaf-name') {
      leaf.name = (nodes[nodes.indexOf(n) + 1] || {}).text || '';
      continue;
    }
    if (n.el === 'Tooltip') { leaf.acts.push({ tip: n.title, ink: '', clickable: false }); continue; }
    if (n.el === 'Button') {
      const ink = (n.texts || []).join('');
      const last = leaf.acts[leaf.acts.length - 1];
      if (last && !last.ink) { last.ink = ink; last.clickable = n.clickable; }
      else leaf.acts.push({ tip: '', ink, clickable: n.clickable });
    }
  }
  return out;
}

const report = [];
for (let i = 0; i < steps.length; i += 1) {
  step = steps[i];
  // Hooks cleared between steps: each one is a fresh look at the same Data Source with a different
  // pin list, not a walk carried over. That is the comparison the order claim needs.
  slotsOf.clear();
  posted.length = 0;
  const nodes = await paint(step);
  const leaves = leavesOf(nodes);

  if (step.pin) {
    const at = leaves.findIndex((l) => l.name === step.pin);
    if (at < 0) throw new Error(`no leaf named ${step.pin} on screen`);
    const act = leaves[at].acts.find((a) => a.ink === 'Pin' || a.ink === 'Unpin');
    if (!act) throw new Error(`${step.pin} offered neither Pin nor Unpin`);
    // The handler off the walk rather than off the record above, which keeps only what it is safe
    // to serialise.
    const buttons = nodes.filter((n) => n.el === 'Button' && (n.texts || []).some(
      (t) => t === 'Pin' || t === 'Unpin'));
    buttons[at].onClick();
    await settle();
  }

  report.push({ leaves, posted: posted.slice() });
}
console.log(JSON.stringify(report));
