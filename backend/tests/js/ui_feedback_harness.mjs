// Drives the real components down paths that fail, and reports what the person would have seen.
//
// Four fixes share one shape: the UI updates first and the call that backs it can reject, so a
// swallowed rejection leaves the screen asserting something that never happened. The fifth — the
// plan card's Preview — is the same disagreement without a failure: the card rendered the original
// plan while the store held the edit.
//
// `useState` here is a real cell, not the read-once stub the context harness uses, because these
// assertions are about what a SECOND render shows after a handler has run.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { scenario } = JSON.parse(fs.readFileSync(0, 'utf8'));

const CONVERSATION = 'conv_1';
const SEED = { id: 'ctx_seed', kind: 'file', name: 'sales.csv', path: 'sales.csv',
               addedBy: 'user', resourceId: 'file:sales.csv' };
let context = [SEED];
// Which paths answer with a failure in this scenario. The fixes are about what happens then.
const FAILS = {
  uploadFailures: /\/project\/upload/,
  contextAddFailure: /\/threads\/[^/]+\/context$/,
  detachFailure: /\/threads\/[^/]+\/context\//,
};
const failing = FAILS[scenario];

const json = (body, status = 200) => ({
  ok: status < 400, status,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

function serve(url, options = {}) {
  const method = (options.method || 'GET').toUpperCase();
  const path = String(url).replace(/^\.\/api/, '');
  if (failing && failing.test(path) && method !== 'GET') {
    return json({ error: 'the server said no' }, 500);
  }
  let m;
  if ((m = path.match(/^\/threads\/([^/]+)\/context$/))) {
    if (method === 'GET') return json({ items: context });
    if (method === 'POST') {
      const row = { ...JSON.parse(options.body), id: `ctx_${context.length + 1}` };
      context.push(row);
      return json(row);
    }
  }
  if ((m = path.match(/^\/threads\/[^/]+\/context\/([^/]+)$/)) && method === 'DELETE') {
    context = context.filter((i) => i.id !== decodeURIComponent(m[1]));
    return { ok: true, status: 204, headers: { get: () => '' },
             json: async () => null, text: async () => '' };
  }
  return json({});
}

// --- the hooks the components actually use ----------------------------------
// One cell per useState call, in call order, kept across renders. Mounting a different component
// clears them, which is what a real unmount does.
let cells = [];
let cursor = 0;
const reported = [];

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, setTimeout, clearTimeout, setInterval, clearInterval,
  requestAnimationFrame: (fn) => fn(),
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  React: {
    createElement: (t, p, ...c) => ({ t, p, c }),
    useState: (init) => {
      const i = cursor;
      cursor += 1;
      if (!(i in cells)) cells[i] = typeof init === 'function' ? init() : init;
      return [cells[i], (v) => { cells[i] = typeof v === 'function' ? v(cells[i]) : v; }];
    },
    useEffect: () => {}, useRef: () => ({ current: null }), Fragment: 'Fragment',
  },
  antd: {
    Input: Object.assign(function Input() {}, { TextArea: 'Input.TextArea' }),
    Button: 'Button', Dropdown: 'Dropdown', Tag: 'Tag', Tooltip: 'Tooltip', Space: 'Space',
    Modal: Object.assign(function Modal() {}, { confirm() {} }),
    Checkbox: Object.assign(function Checkbox() {}, { Group: 'Checkbox.Group' }),
    Radio: Object.assign(function Radio() {}, { Group: 'Radio.Group', Button: 'Radio.Button' }),
    Select: 'Select', Alert: 'Alert',
    // What the person would have seen. An empty list is the defect these tests exist for.
    message: {
      success() {}, info() {}, warning() {},
      error: (m) => reported.push(String(m)),
    },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async (url, options) => {
    await new Promise((r) => setTimeout(r, 0));
    return serve(url, options);
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'api.js', 'store.js', 'components/composer.js',
                 'components/message-blocks.js', 'components/handoff.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

function* walk(node) {
  if (!node || typeof node !== 'object') return;
  if (Array.isArray(node)) { for (const c of node) yield* walk(c); return; }
  yield node;
  yield* walk(node.c);
}
const find = (tree, pred) => [...walk(tree)].find(pred);
const strings = (node) => [...walk(node)].flatMap((n) => (n.c || []).flat(Infinity))
  .filter((c) => typeof c === 'string');
const labelled = (tree, label) =>
  find(tree, (n) => n.t === 'Button' && (n.c || []).flat(Infinity).includes(label));

const mount = (fn, props) => { cursor = 0; return fn(props); };
const remount = () => { cells = []; };

async function settle() {
  for (let i = 0; i < 60; i += 1) await new Promise((r) => setTimeout(r, 0));
}

SW.store.set({
  thread: { id: CONVERSATION, title: 'A conversation', artifacts: [] },
  scope: { id: 'proj', name: 'Demo Project' },
  messages: [], resourceGroups: {},
});
await SW.store.reloadAttachments();
await settle();

const COMPOSER = { onSend() {}, placeholder: 'Describe a change…', disabled: false };
let out = {};

if (scenario === 'planPreview') {
  // Edit the plan, then click the button labelled "Preview" and read what it drew. The card must
  // show the edit; showing `block.plan` is a person watching their own typing disappear.
  remount();
  const block = { type: 'build_plan', plan: 'THE ORIGINAL PLAN', pending: true, planId: 'p1' };
  const outer = SW.MessageBlock({ block });
  const Card = outer.t;
  let tree = mount(Card, outer.p);
  labelled(tree, 'Edit plan').p.onClick();
  tree = mount(Card, outer.p);
  const editor = find(tree, (n) => n.t === 'Input.TextArea' && n.p && n.p.autoSize);
  editor.p.onChange({ target: { value: 'THE EDITED PLAN' } });
  tree = mount(Card, outer.p);
  labelled(tree, 'Preview').p.onClick();
  tree = mount(Card, outer.p);
  const shown = find(tree, (n) => n.p && n.p.className === 'sw-plan-card-problem sw-plan-md');
  out = { preview: strings(shown).join(' ') };
} else if (scenario === 'uploadFailures') {
  // Two files, both rejected. Two messages is the loop carrying on past the first failure; one is
  // the bare `for` abandoning the rest of the drop.
  const target = find(mount(SW.Composer, COMPOSER), (n) => n.p && n.p.onDrop);
  target.p.onDrop({
    preventDefault() {},
    dataTransfer: { getData: () => '', files: [{ name: 'first.csv' }, { name: 'second.csv' }] },
  });
  await settle();
  out = { reported };
} else if (scenario === 'contextAddFailure') {
  // The panel drops a resource and the POST fails. The chip never appears, so silence would send a
  // prompt naming a file that is not attached.
  const resource = { id: 'file:margins.csv', name: 'margins.csv', kind: 'file', path: 'margins.csv' };
  SW.store.get().resourceIndex[resource.id] = resource;
  const target = find(mount(SW.Composer, COMPOSER), (n) => n.p && n.p.onDrop);
  target.p.onDrop({
    preventDefault() {},
    dataTransfer: { getData: (k) => (k === 'text/sw-resource' ? resource.id : ''), files: [] },
  });
  await settle();
  out = { reported, chips: [...walk(mount(SW.Composer, COMPOSER))]
    .filter((n) => n.p && n.p.className === 'sw-chip').length };
} else if (scenario === 'detachFailure') {
  // The chip's own close button, with the DELETE failing. The chip stays, so silence reads as a
  // dead button.
  const chip = find(mount(SW.Composer, COMPOSER), (n) => n.p && n.p.className === 'sw-chip');
  chip.p.onClose({ preventDefault() {} });
  await settle();
  out = { reported };
} else if (scenario === 'graduationFailure') {
  // The save rejects. Without a catch the modal sits open saying nothing and Enter retries into
  // the same silence, so the message is the whole fix.
  SW.store.saveToProject = async () => { throw new Error('the server said no'); };
  SW.store.set({ graduationOpen: true });
  remount();
  const tree = mount(SW.GraduationModal, {});
  const field = find(tree, (n) => n.p && n.p.onPressEnter);
  await field.p.onPressEnter();
  await settle();
  out = { reported, stillOpen: !!SW.store.get().graduationOpen };
} else {
  throw new Error(`unknown scenario ${scenario}`);
}
console.log(JSON.stringify(out));
