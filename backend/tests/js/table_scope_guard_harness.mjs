// The Build composer, with a table PINNED on the Project's Data Source row while the selected
// app's Binding is scoped to a DIFFERENT table of the same store.
//
// Three questions in one run, because they are the three that disagreed. The @ menu offers the
// pinned tables (`pinRow`); the turn honours the app's Scope; and the warning between them read
// neither, so a mention picked out of that menu went out carrying a table the turn would drop and
// nothing anywhere said so.
//
// Input on stdin: `{ "prompt": "<the Build turn's text>" }`.
//
// Nothing is mounted. `createElement` returns a plain object, so calling the Composer gives the
// tree it would draw — but `useState` is REAL, per mount, because the @ menu only exists after a
// keystroke has opened it.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
// `unscoped` drops the Scope off the app's Binding, which is the ordinary state of a store bound
// from the header: that picker binds in one argument and leaves the Scope as a second act (#142).
const { prompt, unscoped } = JSON.parse(fs.readFileSync(0, 'utf8'));

const APP = { id: 'app_a', name: 'Usage dashboard', selected: true };

// One store in the working set, with two tables pinned on it. Pins are a PROJECT record
// (`pin_project_resource`) and know nothing about any app's Scope, which is the whole gap.
const MEMBERS = [{
  id: 'data_source:ds-dwh', name: 'Snowflake-Data-Warehouse', kind: 'datasource',
  bindingKey: ['data_source', 'ds-dwh'],
  pins: [
    { database: 'DWH', schema: 'MARTS', table: 'FCT_USAGE_DAILY' },
    { database: 'DWH', schema: 'MARTS', table: 'DIM_ACCOUNT' },
  ],
}];

// The app reads ONE of them. The manifest entry is handed through by `_labelled_bindings`, so the
// levels are on the row exactly as they are here.
const BINDINGS = [{
  kind: 'data_source', id: 'ds-dwh', name: 'Snowflake-Data-Warehouse',
  display_name: 'Snowflake-Data-Warehouse',
  ...(unscoped ? {} : { database: 'DWH', schema: 'MARTS', table: 'FCT_USAGE_DAILY' }),
}];

const sent = [];
// Every Scope ladder the guard's button opened, by the Binding it named. The act must open a door
// and stop: widening a Scope has a shape, and a card must not spend its one click guessing which.
const scopeOpened = [];

const json = (body, status = 200) => ({
  ok: status < 400, status,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});
const stream = () => ({
  ok: true, status: 200,
  headers: { get: () => 'text/event-stream' },
  body: { getReader: () => ({ read: async () => ({ done: true, value: undefined }) }) },
});

function serve(url, init) {
  const path = String(url).replace(/^\.\/api/, '');
  const method = ((init && init.method) || 'GET').toUpperCase();
  if (path === '/project/build/stream' && method === 'POST') {
    sent.push(JSON.parse((init && init.body) || '{}'));
    return stream();
  }
  if (path === '/project') return json({ attached: [], scratch: [] });
  if (path === '/project/resources') return json({ items: MEMBERS });
  if (path === '/apps') return json({ items: [APP] });
  if (path === '/bindings') return json({ bindings: BINDINGS });
  if (path === '/members') return json({ members: [], directory: [] });
  if (path.match(/^\/threads\/[^/]+\/context$/)) {
    if (method !== 'POST') return json({ items: [] });
    const body = JSON.parse((init && init.body) || '{}');
    return json({ id: 'ctx_1', ...body, resourceName: body.name });
  }
  if (path === '/threads') return json([]);
  return json({});
}

let stateful = false;
let hooks = [];
let cursor = 0;
function hookState(init) {
  const value = () => (typeof init === 'function' ? init() : init);
  if (!stateful) return [value(), () => {}];
  const at = cursor;
  cursor += 1;
  if (!(at in hooks)) hooks[at] = value();
  return [hooks[at], (next) => { hooks[at] = typeof next === 'function' ? next(hooks[at]) : next; }];
}

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, TextDecoder,
  setTimeout, clearTimeout, setInterval: () => 1, clearInterval: () => {},
  requestAnimationFrame: (fn) => fn(),
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  location: { hash: '#/build' },
  history: { replaceState() {} },
  addEventListener() {}, removeEventListener() {},
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    useState: (init) => hookState(init),
    useEffect: () => {}, useMemo: (fn) => fn(), useRef: () => ({ current: null }),
    Fragment: 'Fragment',
  },
  antd: {
    Input: Object.assign(function Input() {}, { TextArea: 'Input.TextArea' }),
    Button: 'Button', Dropdown: 'Dropdown', Tag: 'Tag', Tooltip: 'Tooltip', Space: 'Space',
    Drawer: 'Drawer', Skeleton: 'Skeleton', Checkbox: 'Checkbox', Alert: 'Alert',
    Modal: { confirm: () => ({ update() {}, destroy() {} }) },
    message: { success() {}, error() {}, info() {}, warning() {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async (url, init) => { await new Promise((r) => setTimeout(r, 0)); return serve(url, init); },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js', 'router.js',
                 'components/resource-tree.js', 'components/resource-panel.js',
                 'components/composer.js', 'modes/builder.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;
const settle = async () => {
  for (let i = 0; i < 60; i += 1) await new Promise((r) => setTimeout(r, 0));
};

// The ladder, watched rather than walked: what is under test is WHICH door the button opens.
const realScopePick = SW.store.openScopePick;
SW.store.openScopePick = (binding) => {
  scopeOpened.push(`${binding.kind}:${binding.id}`);
  return realScopePick.call(SW.store, binding);
};

function flatten(node, out = [], depth = 0) {
  if (!node || depth > 60) return out;
  if (Array.isArray(node)) { node.forEach((c) => flatten(c, out, depth)); return out; }
  if (typeof node !== 'object' || !node.t) return out;
  out.push(node);
  if (typeof node.t === 'function' && node.t.name !== 'Input') {
    flatten(node.t(Object.assign({}, node.p, { children: node.c })), out, depth + 1);
  }
  flatten(node.c, out, depth + 1);
  return out;
}
const textOf = (n) => ((n.p || {}).children || n.c || []).flat(Infinity).join('');

await SW.store.setScope({ id: 'proj', name: 'Demo Project' }, { silent: true });
SW.store.set({ thread: { id: 'conv_1', title: 'Usage', artifacts: [] }, messages: [] });
await SW.store.loadApps();
await settle();

const BUILD = {
  onSend: (v) => SW.store.sendBuildPrompt(v),
  showMode: true, compact: true, disabled: false, placeholder: 'Describe a change…',
};
const render = () => { cursor = 0; return SW.Composer(BUILD); };

// The @ menu, opened the way a person opens it: by typing into the box.
function menuFor(query) {
  stateful = true; hooks = []; render();
  const box = flatten(render()).find((n) => n.t === 'Input.TextArea');
  const value = `@${query}`;
  box.p.onChange({ target: { value, selectionStart: value.length }, nativeEvent: {} });
  const rows = flatten(render())
    .filter((n) => String(n.p.className || '').startsWith('sw-mention-item'));
  stateful = false;
  return rows.map((row) => flatten(row)
    .filter((n) => n.p && n.p.className === 'sw-mention-name')
    .flatMap((n) => (n.c || []).flat(Infinity)).join(''));
}

// The warning under the box, with the prompt already in it. Mounted through the Composer so the
// line and the buttons are the ones a reader would actually see.
function guard(text) {
  stateful = true; hooks = []; render();
  const box = flatten(render()).find((n) => n.t === 'Input.TextArea');
  box.p.onChange({ target: { value: text, selectionStart: text.length }, nativeEvent: {} });
  const tree = flatten(render());
  stateful = false;
  const line = tree.find((n) => String(n.p.className || '') === 'sw-mention-guard-text');
  return {
    line: line ? textOf(line) : '',
    buttons: tree.filter((n) => n.t === 'Button' && n.p.onClick && n.p.loading !== undefined)
      .map((n) => ({ label: textOf(n), click: n.p.onClick })),
  };
}

const report = {
  // What the menu offers. Both, which is the state the rest of this is about.
  menu: menuFor('DIM').concat(menuFor('FCT')),
  guard: (() => { const g = guard(prompt); return { line: g.line, labels: g.buttons.map((b) => b.label) }; })(),
};

// The button, pressed. It opens the ladder on the Binding and sends nothing.
const pressed = guard(prompt);
for (const button of pressed.buttons) await button.click();
await settle();
report.scopeOpened = scopeOpened;
report.sentAfterClick = sent.length;

// And what the turn carries if the warning is read and ignored — the mention is still sent, so the
// server's own sentence is the backstop. The warning never blocks (#136).
await SW.store.sendBuildPrompt(prompt);
await settle();
report.sent = sent.map((b) => ({ mentions: b.mentions, resources: b.resources }));

console.log(JSON.stringify(report));
