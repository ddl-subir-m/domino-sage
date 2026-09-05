// What the working set's Plans group does with a plan somebody put away (#167).
//
// Three claims can only be seen by drawing the group: that an archived document is not listed, that
// the head still counts it and offers the way back to it, and that it is never drawn as the live
// plan while it is hidden. None of them can be grepped out of the source — the row's `live` flag
// and the group's filter are two expressions that have to agree about one document.
//
// `resource_group_add_harness` is the prior art. Nothing is mounted: `createElement` is stubbed to
// a plain object, so calling the component returns tree data. Hooks are real per mount, because the
// toggle IS a hook — pressing it and drawing again is the whole of what this asks about.
//
// Input on stdin: `{ "act": "drawn" | "press" | "press-while-collapsed" | "press-and-back" }`.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { act } = JSON.parse(fs.readFileSync(0, 'utf8'));

// Two plans, one of them put away, and the archived one is ALSO the Conversation's current plan.
// That pairing is the interaction worth pinning: the filter that hides an archived document from an
// app's plan pin deliberately does not reach `_thread_plan_id`, so a document really can come back
// archived and live at once.
const PLANS = [
  { id: '002', title: 'A desk exposure dashboard.', status: 'approved', appId: 'app_a',
    archived: true },
  { id: '001', title: 'A consumption dashboard.', status: 'draft', appId: 'app_a',
    archived: false },
];

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
  location: { search: '', pathname: '/', href: 'http://localhost/#/chat', hash: '#/chat' },
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

// Chat, so the live mark is read off `activePlanId` — the plan the Conversation produced — rather
// than off the app's plan.md. That is the reading an archived document can still answer.
SW.store.set({
  resourceGroups: {}, resourcesLoading: false, resourceErrors: {},
  plans: PLANS, activePlanId: '002', apps: [{ id: 'app_a', name: 'Desk exposure' }],
});

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

const head = (nodes) => {
  const label = nodes.find((n) => (n.p || {}).className === 'sw-res-group-label');
  if (!label) return null;
  const drawn = flatten(label);
  const toggle = drawn.find((d) => (d.p || {}).className === 'sw-res-group-toggle');
  const archived = drawn.find((d) => (d.p || {}).className === 'sw-res-group-archived');
  return {
    // The caret, so a test can fold the group before it presses the way in.
    caret: toggle || null,
    collapsed: toggle ? (toggle.p || {})['aria-expanded'] === false : null,
    label: toggle ? text(flatten(toggle).find((d) => (d.p || {}).className === 'sw-group-label')) : '',
    archivedLabel: archived ? text(archived) : null,
    archivedIsButton: !!archived && archived.t === 'button',
    archivedPressed: archived ? (archived.p || {})['aria-pressed'] : null,
    node: archived || null,
  };
};

// One entry per plan row: what it is called, what its subtitle says, and whether the panel drew it
// as the live one. `SW.ResourceRow` is a real component, so this reads the rendered row.
const planRows = (nodes) => nodes
  .filter((n) => typeof (n.p || {}).className === 'string'
    && n.p.className.startsWith('sw-res-row'))
  .map((n) => {
    const drawn = flatten(n);
    const name = drawn.find((d) => (d.p || {}).className === 'sw-res-name');
    const sub = drawn.find((d) => (d.p || {}).className === 'sw-res-sub');
    return {
      name: name ? text(name) : '',
      subtitle: sub ? text(sub) : '',
      live: n.p.className.includes('is-live'),
    };
  });

// What each row's overflow offers, and the handler behind it. The Dropdown carries its menu as a
// prop rather than as children, so this reads the prop: that the plan row supplies its own menu at
// all is the claim, and the source cannot be grepped for a value reaching a component.
const planMenus = (nodes) => nodes
  .filter((n) => typeof (n.p || {}).className === 'string'
    && n.p.className.startsWith('sw-res-row'))
  .map((n) => {
    const drawn = flatten(n);
    const name = drawn.find((d) => (d.p || {}).className === 'sw-res-name');
    const dropdown = drawn.find((d) => d.t === 'Dropdown');
    const menu = dropdown ? (dropdown.p || {}).menu || {} : null;
    return {
      name: name ? text(name) : '',
      items: menu ? (menu.items || []).map((i) => i.key) : null,
      onClick: menu ? menu.onClick : null,
    };
  });

const report = {};

if (act === 'menu') {
  // A named plan and an unnamed one. The rail draws "Untitled plan" for the second so the row has
  // something to say, and the rename box must not prefill that: it is a label this list chose, not
  // a name anybody gave the document.
  SW.store.set({
    resourceGroups: {}, resourcesLoading: false, resourceErrors: {},
    plans: [
      { id: '001', title: 'A consumption dashboard.', status: 'draft', appId: 'app_a',
        archived: false },
      { id: '003', title: '', status: 'draft', appId: '', archived: false },
    ],
    activePlanId: '001', apps: [{ id: 'app_a', name: 'Desk exposure' }],
  });
  const menus = planMenus(panel());
  report.menus = menus.map(({ name, items }) => ({ name, items }));

  // Press Rename on the unnamed one. Modal.confirm is stubbed to keep the config, because what the
  // box opens WITH is the half of this that a list of item keys cannot show.
  let opened = null;
  sandbox.antd.Modal.confirm = (config) => { opened = config; };
  const unnamed = menus.find((m) => m.name === 'Untitled plan');
  unnamed.onClick({ key: 'rename', domEvent: { stopPropagation: () => {} } });
  report.opened = opened && {
    title: opened.title,
    okText: opened.okText,
    defaultValue: (opened.content.p || {}).defaultValue,
  };
} else if (act === 'drawn') {
  const nodes = panel();
  report.head = head(nodes);
  report.rows = planRows(nodes);
} else if (act === 'press-and-back') {
  // In, then out. The un-collapse belongs to the way IN only: folding the group again on the way
  // out would take the live plans with it, which nobody asked to hide.
  head(panel()).node.p.onClick();
  head(panel()).node.p.onClick();
  const after = panel();
  report.head = head(after);
  report.rows = planRows(after);
} else if (act === 'press-while-collapsed') {
  // Fold the group first, then press the way in. The rows live under the caret, so a toggle that
  // only flipped its own label here would report a press with nothing to show for it.
  head(panel()).caret.p.onClick();
  head(panel()).node.p.onClick();
  const after = panel();
  report.head = head(after);
  report.rows = planRows(after);
} else {
  // Press the way in, then draw again. The second draw is the claim: the toggle is a hook, and a
  // control that reports itself pressed while the list below it has not changed is the failure.
  const before = panel();
  head(before).node.p.onClick();
  const after = panel();
  report.head = head(after);
  report.rows = planRows(after);
  report.rowsBefore = planRows(before);
}

console.log(JSON.stringify(report));
