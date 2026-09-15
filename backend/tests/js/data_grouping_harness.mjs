// How the two data surfaces group data-like things, and what a row says about itself (ADR-0054).
//
// Three labels stood over two things: the rail drew `Data` over `Datasets` and `Data Sources`, and
// Browse Domino offered all three as peers with the first count the sum of the next two. One
// section now, and one type per row — File volume, Data connection — in both surfaces.
//
// None of that is greppable. The subhead a Project sees depended on which kinds hold rows, a row's
// meta line is composed at draw time, and the sidebar's nesting is a class on a button. So both
// components are drawn and read off the tree.
//
// Input on stdin: `{ "act": "panel-both" | "panel-one-type" | "catalog" }`.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { act } = JSON.parse(fs.readFileSync(0, 'utf8'));

// A Dataset and a Data Source, plus a model, so the Data section is drawn beside a group that is
// not it. `panel-one-type` drops the Data Source: that is the case the old naming rule got wrong,
// where the same rows read `Data` here and `Data / File volume` in the Project next door.
const GROUPS = {
  dataset: [{ id: 'dataset:d1', name: 'Sales rows', kind: 'dataset' }],
  datasource: [{ id: 'data_source:s1', name: 'Warehouse', kind: 'datasource' }],
  model_llm: [{ id: 'llm_alias:m1', name: 'Risk scorer', kind: 'model_llm', alias: 'risk-scorer' }],
};
const groupsFor = (which) => (which === 'panel-one-type'
  ? { dataset: GROUPS.dataset, model_llm: GROUPS.model_llm }
  : GROUPS);

// What Browse Domino holds when it opens. The same two kinds, so the sidebar's counts can be
// checked against the rows they stand for.
const LISTING = {
  errors: {},
  groups: {
    dataset: GROUPS.dataset,
    datasource: GROUPS.datasource,
    model_llm: GROUPS.model_llm,
    model_predictive: [],
  },
};

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
                 'components/resource-tree.js', 'components/resource-panel.js',
                 'components/resource-catalog.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

function flatten(node, out = [], depth = 0) {
  if (!node || depth > 60) return out;
  if (Array.isArray(node)) {
    node.forEach((child) => flatten(child, out, depth));
    return out;
  }
  if (typeof node !== 'object' || !node.t) return out;
  out.push(node);
  if (typeof node.t === 'function' && node.t.name !== 'Input' && node.t.name !== 'Modal') {
    flatten(node.t(Object.assign({}, node.p, { children: node.c })), out, depth + 1);
  }
  flatten(node.c, out, depth + 1);
  return out;
}

const text = (node) => (node.c || []).flat(Infinity).filter((c) => typeof c === 'string').join('');
const cls = (node) => String((node.p || {}).className || '');
const labelIn = (node) => text(flatten(node).find((d) => cls(d) === 'sw-group-label') || {});

const report = {};

if (act === 'catalog') {
  SW.store.set({
    scope: { id: 'p1', name: 'quick-start' },
    catalogOpen: true,
    catalogKind: null,
    resourceListing: LISTING,
    resourceListingScope: 'p1',
    resourceGroups: {},
  });
  cursor = 0;
  const nodes = flatten(SW.ResourceCatalog());
  // One entry per sidebar row: what it is called, whether it is nested under the row above, and
  // the count beside it. The nesting is the claim — as peers, `Data` and its two shapes were three
  // rows whose first number was the sum of the other two.
  report.side = nodes
    .filter((n) => n.t === 'button' && cls(n).includes('sw-cat-side-btn'))
    .map((n) => {
      const spans = flatten(n).filter((d) => d.t === 'span');
      const count = spans.find((d) => cls(d) === 'sw-cat-side-count');
      return {
        label: text(spans.find((d) => !cls(d))) || '',
        isChild: cls(n).includes('is-child'),
        count: count ? count.c.flat(Infinity)[0] : null,
      };
    });
  // The meta line under each row's name, word by word, so the Domino noun and the reach can be
  // told apart from the origin that follows them.
  report.rows = nodes
    .filter((n) => cls(n) === 'sw-cat-row')
    .map((n) => {
      const drawn = flatten(n);
      const meta = drawn.find((d) => cls(d) === 'sw-cat-meta');
      return {
        name: text(drawn.find((d) => cls(d) === 'sw-cat-name') || {}),
        meta: meta ? flatten(meta).filter((d) => d.t === 'span' && !cls(d)).map(text) : [],
      };
    });
} else {
  SW.store.set({
    resourceGroups: groupsFor(act),
    resourcesLoading: false,
    resourceErrors: {},
  });
  cursor = 0;
  const nodes = flatten(SW.ResourcePanel());
  report.heads = nodes.filter((n) => cls(n) === 'sw-res-group-label').map(labelIn);
  report.subheads = nodes.filter((n) => cls(n) === 'sw-res-subgroup').map(labelIn);
  // Every string a row draws for itself — its name line and whatever subtitle it carries. The
  // claim is that none of them restates the type the subhead above already said, so this has to be
  // the row's whole text rather than one class that happens to be empty in this fixture.
  report.rowText = nodes
    .filter((n) => cls(n) === 'sw-res-main')
    .map((n) => flatten(n).map(text).filter(Boolean).join(' | '));
  // What each row draws in its icon slot: a component (the drawn icon) or a string (the emoji).
  report.icons = [];
  let name = null;
  for (const node of nodes) {
    if (cls(node) === 'sw-res-icon') {
      const glyph = (node.c || []).flat(Infinity)[0];
      report.icons.push({
        kind: typeof glyph === 'string' ? 'emoji' : 'node',
        // `icons` is a Proxy answering its own key, so a drawn icon reports the name it was
        // asked for and the assertion can name the icon rather than counting them.
        glyph: typeof glyph === 'string' ? glyph : String((glyph || {}).t),
      });
    }
  }
  // Pair each icon with the row name that follows it, so an assertion can say WHICH row drew what.
  const names = nodes.filter((n) => cls(n) === 'sw-res-name').map(text);
  report.icons = report.icons.map((icon, i) => ({ ...icon, name: names[i] }));
  void name;
}

console.log(JSON.stringify(report));
