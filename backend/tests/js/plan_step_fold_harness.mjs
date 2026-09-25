// What a plan's build steps look like to the person deciding whether to approve them (#542).
//
// Every step carries `Files` and `Don't touch` because a phased build executes it in a session
// that never read the plan. Those two fields are addressed to a machine, and they were the bulk
// of what a non-technical reader saw. They are folded now, and the three modes below are the
// three questions that go with that:
//
//   `util`  — `SW.util.planMarkdown` alone, so one spelling per run. The parser is lenient about
//             separators and field synonyms (`plan_steps._FIELD` / `_CANON`), and a spelling the
//             executor accepts but the fold does not is a `Files` line still on the screen.
//   `page`  — the real `SW.PlanDoc`, which is where the Plan section is read on its own page.
//   `card`  — the real `SW.MessageBlock`, which is the approve card in the transcript. It draws
//             the WHOLE plan document rather than one section, so it is a separate render and
//             not a second call of the first one.
//   `edit`  — the same card with "Edit plan" pressed. Nothing is stored differently, so the box
//             a person types in must still hold the file line for line, folded fields included.
//
// The tree is partitioned by walking it once and remembering whether the walk is inside a fold,
// rather than by reading the markdown back: a fold that renders its `<summary>` and drops its
// body, or one that renders nothing at all, both read as "folded" to any check that only asks
// whether the text left the visible half.
//
// Input on stdin: `{ "mode": "util"|"page"|"card"|"edit", "plan": "<markdown>" }`.
import fs from 'node:fs';
import vm from 'node:vm';
import { unrefTimeout } from './sandbox_timeout.mjs';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { mode, plan: PLAN_MD } = JSON.parse(fs.readFileSync(0, 'utf8'));

const PLAN = {
  id: '001',
  title: 'A desk exposure dashboard.',
  version: 1,
  status: 'draft',
  author: 'u-me',
  updatedAt: '2026-08-28T10:00:00Z',
  summary: '',
  sections: { plan: PLAN_MD },
  comments: [],
  approvals: [],
  reviewers: [],
  archived: false,
};

let cells = [];
let cursor = 0;
let effects = [];

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array,
  setTimeout: unrefTimeout, clearTimeout, setInterval, clearInterval,
  requestAnimationFrame: (fn) => fn(),
  localStorage: (() => {
    const backing = new Map();
    return {
      getItem: (k) => (backing.has(k) ? backing.get(k) : null),
      setItem: (k, v) => backing.set(k, String(v)),
      removeItem: (k) => backing.delete(k),
    };
  })(),
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  // `router.js` reads the hash at load. Chat, not Build: Build's sheet offers a raw-markdown view
  // of plan.md beside the preview, and the raw file is not what this is about.
  location: { hash: '#/chat', search: '', pathname: '/', href: 'http://localhost/' },
  addEventListener() {}, removeEventListener() {},
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    useState: (init) => {
      const i = cursor;
      cursor += 1;
      if (!(i in cells)) cells[i] = typeof init === 'function' ? init() : init;
      return [cells[i], (v) => { cells[i] = typeof v === 'function' ? v(cells[i]) : v; }];
    },
    useEffect: (fn) => { effects.push(fn); },
    useRef: () => ({ current: null }),
    Fragment: 'Fragment',
  },
  antd: {
    Input: Object.assign(function Input() {}, { TextArea: 'Input.TextArea' }),
    Button: 'Button', Dropdown: 'Dropdown', Tag: 'Tag', Tooltip: 'Tooltip', Space: 'Space',
    Modal: Object.assign(function Modal() {}, { confirm() {} }),
    Checkbox: Object.assign(function Checkbox() {}, { Group: 'Checkbox.Group' }),
    Radio: Object.assign(function Radio() {}, { Group: 'Radio.Group', Button: 'Radio.Button' }),
    Select: 'Select', Alert: 'Alert', Avatar: 'Avatar', Divider: 'Divider',
    Skeleton: 'Skeleton', Segmented: 'Segmented',
    message: { success() {}, info() {}, warning() {}, error() {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async (url) => {
    await new Promise((r) => setTimeout(r, 0));
    const body = String(url).endsWith(`/plans/${PLAN.id}`) ? PLAN : {};
    return {
      ok: true, status: 200,
      headers: { get: () => 'application/json' },
      json: async () => body,
      text: async () => JSON.stringify(body),
    };
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

// `util.js` alone for the `util` mode; the rest is what `plan.js` and `message-blocks.js` need to
// be the real components rather than a copy of them.
const files = mode === 'util'
  ? ['util.js']
  : ['util.js', 'api.js', 'store.js', 'prefs.js', 'router.js',
     'components/plan.js', 'components/message-blocks.js'];
for (const f of files) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

// Walks once, carrying whether it is inside a fold. `summaries` is taken out of the fold's own
// body: the label is the promise the fold makes, not part of what it hides.
const visible = [];
const hidden = [];
const summaries = [];
const opens = [];

function collect(node, inFold) {
  if (node === null || node === undefined || node === false || node === true) return;
  if (Array.isArray(node)) { node.forEach((c) => collect(c, inFold)); return; }
  if (typeof node === 'string' || typeof node === 'number') {
    (inFold ? hidden : visible).push(String(node));
    return;
  }
  if (typeof node !== 'object') return;
  const isFold = node.t === 'details' && node.p && node.p.className === 'sw-plan-step-files';
  if (isFold) {
    summaries.push(
      (node.c || []).flat(Infinity)
        .filter((c) => c && c.t === 'summary')
        .flatMap((c) => (c.c || []).flat(Infinity))
        .filter((c) => typeof c === 'string')
        .join('')
    );
    opens.push(Object.hasOwn(node.p, 'open'));
    (node.c || []).flat(Infinity)
      .filter((c) => !(c && c.t === 'summary'))
      .forEach((c) => collect(c, true));
    return;
  }
  collect(node.c, inFold);
}

function* walk(node) {
  if (!node || typeof node !== 'object') return;
  if (Array.isArray(node)) { for (const c of node) yield* walk(c); return; }
  yield node;
  yield* walk(node.c);
}
const labelled = (tree, label) =>
  [...walk(tree)].filter((n) => n.t === 'Button' && (n.c || []).flat(Infinity).includes(label))[0];

async function settle() {
  for (let i = 0; i < 40; i += 1) await new Promise((r) => setTimeout(r, 0));
}
async function mount(component, props) {
  cursor = 0;
  effects = [];
  const tree = component(props);
  effects.forEach((fn) => fn());
  await settle();
  return tree;
}

let tree;
let textarea = null;
if (mode === 'util') {
  tree = SW.util.planMarkdown(PLAN_MD);
} else if (mode === 'page') {
  SW.store.set({
    me: { id: 'u-me' },
    userIndex: { 'u-me': { id: 'u-me', name: 'Me' } },
    activeApp: { id: 'app_a' },
  });
  const props = { planId: PLAN.id, variant: 'page', onClose() {} };
  // First mount fetches, second renders what came back.
  await mount(SW.PlanDoc, props);
  tree = await mount(SW.PlanDoc, props);
} else {
  SW.store.set({ buildRunning: false, activePlanId: null });
  const block = { type: 'build_plan', plan: PLAN_MD, planId: PLAN.id, pending: true };
  // `MessageBlock` uses no state of its own; it picks the card and hands it the block. The card
  // is the component with the hooks, so it is the one that gets mounted.
  const picked = SW.MessageBlock({ block });
  tree = await mount(picked.t, picked.p);
  if (mode === 'edit') {
    labelled(tree, 'Edit plan').p.onClick();
    tree = await mount(picked.t, picked.p);
    const box = [...walk(tree)].find((n) => n.t === 'Input.TextArea');
    textarea = box ? box.p.value : null;
  }
}

collect(tree, false);
console.log(JSON.stringify({
  folds: summaries.length,
  summaries,
  opens,
  visible: visible.join(' '),
  hidden: hidden.join(' '),
  textarea,
}));
