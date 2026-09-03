// What the Dataset tree draws once the listing has landed, complete or cut short (ADR-0029).
//
// Rendered rather than grepped, because the claim is about a sentence that appears BESIDE the
// files — and about the one position a source assertion cannot reach at all: a filter that matches
// nothing returns early, so "the note outlives the filter" is a question about which branch the
// component took, not about which string it holds.
//
// Nothing is mounted. Hooks are real, per instance, because the tree fetches from an effect and
// only says anything after the answer arrives: a no-op setter would leave every run on the spinner.
//
// Input on stdin: a list of steps, each one `/files` answer in the shape the route writes it —
// `{ "files": [...], "truncated": bool, "query": "", "fail": bool }`. A second step is the SAME
// tree asked about a DIFFERENT Dataset, which is the only way to reach the state a walk carries
// over: the component instance survives the change of `resource`, its hooks with it.
//
// Output: one flattened tree per step.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const steps = JSON.parse(fs.readFileSync(0, 'utf8'));
let step = steps[0];

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
  // The one read this tree makes. Everything else it asks for is already in the store.
  fetch: async (url) => {
    const path = String(url).replace(/^\.\/api/, '');
    if (!/^\/project\/assets\/[^/]+\/files$/.test(path)) {
      return { ok: true, status: 200, statusText: 'OK',
               headers: { get: () => 'application/json' }, json: async () => ({}) };
    }
    if (step.fail) {
      return { ok: false, status: 502, statusText: 'Bad Gateway',
               headers: { get: () => 'application/json' },
               json: async () => ({ error: 'Domino answered 502.' }) };
    }
    return {
      ok: true, status: 200, statusText: 'OK',
      headers: { get: () => 'application/json' },
      json: async () => ({ files: step.files || [], truncated: !!step.truncated }),
    };
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

for (const f of ['util.js', 'api.js', 'store.js', 'router.js',
                 'components/platform-error.js', 'components/resource-tree.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

// --- reading the tree ------------------------------------------------------
const SKIP = new Set(['Input', 'anonymous']);
const named = new Map();
for (const [k, v] of Object.entries(SW)) if (typeof v === 'function') named.set(v, k);

function tag(node) {
  if (typeof node.t === 'string') return node.t;
  return named.get(node.t) || node.t.name || 'anonymous';
}

// Flattened to {el, className, text} the way `platform_error_harness.mjs` does it: the questions
// here are "which element holds this sentence" and "is it on screen at all".
function walk(node, out = [], depth = 0) {
  if (node === null || node === undefined || node === false || node === true || depth > 80) return out;
  if (Array.isArray(node)) { node.forEach((n) => walk(n, out, depth)); return out; }
  if (typeof node === 'string' || typeof node === 'number') {
    if (out.length) out[out.length - 1].text += String(node);
    return out;
  }
  if (typeof node !== 'object' || !node.t) return out;

  const props = node.p || {};
  out.push({ el: tag(node), className: props.className || '', text: '' });
  if (typeof node.t === 'function' && !SKIP.has(tag(node))) {
    // `children` is only taken from the call's own children when there are any. A `FolderNode`
    // carries its subtree in a PROP of that name, and overwriting it with an empty argument list
    // makes every folder render as empty — a tree that draws nothing while the data is all there.
    const child = node.c.filter((c) => c !== undefined);
    walk(callComponent(node.t, child.length ? Object.assign({}, props, { children: child }) : props),
         out, depth + 1);
  }
  walk(node.c, out, depth + 1);
  return out;
}

const settle = () => new Promise((res) => setTimeout(res, 0));

const RESOURCE = { id: 'dataset:ds_1', name: 'Revenue', kind: 'dataset', path: '', pins: [] };

async function paint() {
  let nodes = [];
  for (let i = 0; i < 15; i += 1) {
    counts = new Map();
    dirty = false;
    nodes = walk(callComponent(SW.DatasetFileTree, { resource: RESOURCE, query: step.query || '' }));
    runEffects();
    await settle();
    if (!dirty) break;
  }
  return nodes;
}

const painted = [];
for (let i = 0; i < steps.length; i += 1) {
  step = steps[i];
  // A different Dataset for every step after the first. The hooks are NOT cleared between them,
  // which is the point: what a walk leaves behind is what the next Dataset inherits.
  RESOURCE.id = `dataset:ds_${i + 1}`;
  painted.push(await paint());
}
console.log(JSON.stringify(painted));
