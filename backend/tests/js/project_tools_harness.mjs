// The Project's own custom tools in the resources panel (ADR-0071, #622).
//
// The same approach as `project_skills_harness.mjs`: `createElement` is stubbed to a plain object,
// so calling the panel returns tree data, and hooks are real per mount. `fetch` records every
// request, so a switch, a read-only change or a removal is proved by what it SENT.
//
// Input on stdin: `{ act, hash, thread, app, items }`.
//   drawn       the tool rows, the Add menu's keys, and whether the dialog is open
//   press-door  press the Tools group's `+`, then report the dialog's read-only checkbox
//   toggle      switch the first tool off
//   read-only   flip the first tool's read-only flag from its menu
//   remove      confirm the first tool's Remove
//   add-file    `SW.store.addTools(file, true)` for a file named `input.filename`
//   add-git     `SW.store.addTools({ url, path }, true)`
//   dialog-py   the dialog with a .py chosen: the read-only checkbox
import fs from 'node:fs';
import vm from 'node:vm';
import { unrefTimeout } from './sandbox_timeout.mjs';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const { act } = input;

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

const calls = [];
const confirms = [];
const toasts = [];
const sandbox = {
  console, JSON, Object, String, Array, Error, Map, Set, Promise, Date, Math, Number, Boolean,
  RegExp, encodeURIComponent, decodeURIComponent, setTimeout: unrefTimeout, clearTimeout,
  setInterval: () => 1, clearInterval: () => {}, requestAnimationFrame: (fn) => fn(),
  URLSearchParams, TextEncoder, TextDecoder, URL, Blob,
  fetch: async (url, options = {}) => {
    calls.push({ url: String(url), method: options.method || 'GET',
                 body: typeof options.body === 'string' ? JSON.parse(options.body)
                   : (options.body instanceof Blob ? `<blob ${options.body.size}>` : null) });
    const body = { items: input.items || [], builtinSkills: [] };
    return { ok: true, status: 200, statusText: 'OK',
             headers: { get: () => 'application/json' },
             json: async () => body, text: async () => JSON.stringify(body) };
  },
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  document: {
    title: '', documentElement: { style: { setProperty: () => {} } },
    addEventListener: () => {}, removeEventListener: () => {},
    getElementById: () => ({}), querySelector: () => null, body: {},
  },
  location: { search: '', pathname: '/', href: 'http://localhost/', hash: input.hash || '#/chat' },
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
    Switch: 'Switch', Select: 'Select', Segmented: 'Segmented',
    Modal: Object.assign(function Modal() {}, { confirm: (o) => confirms.push(o), info: () => {} }),
    message: { info: () => {}, success: (t) => toasts.push(t), error: (t) => toasts.push(t),
               warning: () => {} },
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
                 'components/project-skills.js', 'components/project-tools.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

SW.store.set({
  resourceGroups: {}, resourcesLoading: false, resourceErrors: {},
  thread: input.thread ? { id: input.thread } : null,
  activeApp: input.app ? { id: input.app, name: input.app } : null,
  extensions: { items: input.items || [], builtinSkills: [] },
});

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
const text = (node) => flatten(node).flatMap((n) => n.c || []).flat(Infinity)
  .filter((c) => typeof c === 'string').join('');
const panel = () => { cursor = 0; return flatten(SW.ResourcePanel()); };
const cls = (n) => String((n.p || {}).className || '');

function toolRows(nodes) {
  return nodes.filter((n) => cls(n).includes('sw-tool-row')).map((row) => {
    const drawn = flatten(row);
    const sw = drawn.find((d) => d.t === 'Switch');
    const more = drawn.find((d) => d.t === 'Dropdown');
    return {
      name: text(drawn.find((d) => cls(d) === 'sw-res-name')),
      subtitle: text(drawn.find((d) => cls(d) === 'sw-res-sub')),
      checked: sw.p.checked,
      disabled: sw.p.disabled,
      label: sw.p['aria-label'],
      menu: more.p.menu.items.map((i) => i.label),
      sw,
      more,
    };
  });
}

// `alone` draws the dialog with hooks of its own; otherwise it continues after the panel's.
function dialog(props, alone) {
  if (alone) cursor = 0;
  const inner = flatten(SW.AddToolModal(props));
  const box = inner.find((d) => d.t === 'Checkbox');
  return { inner, box: box && { checked: box.p.checked, disabled: box.p.disabled } };
}

const report = {};
const settle = () => new Promise((r) => setTimeout(r, 0));

if (act === 'drawn' || act === 'press-door') {
  let nodes = panel();
  if (act === 'press-door') {
    const head = nodes.filter((n) => cls(n) === 'sw-res-group-label')
      .find((n) => text(n).startsWith('Tools'));
    flatten(head).find((d) => cls(d) === 'sw-res-group-add').p.onClick();
    nodes = panel();
  }
  report.rows = toolRows(nodes).map(({ sw, more, ...row }) => row);
  report.heads = nodes.filter((n) => cls(n) === 'sw-res-group-label').map(text);
  report.menuKeys = (nodes.find((n) => n.t === 'Dropdown' && (n.p.menu || {}).items
    && n.p.menu.items.some((i) => i.key === 'browse')).p.menu.items).map((i) => i.key);
  const modal = nodes.find((n) => n.t === SW.AddToolModal);
  report.dialogOpen = !!modal;
  report.skillDialogOpen = !!nodes.find((n) => n.t === SW.AddSkillModal);
  if (modal) report.box = dialog(modal.p).box;
} else if (act === 'toggle') {
  toolRows(panel())[0].sw.p.onChange(false);
  await settle(); await settle();
  report.calls = calls;
} else if (act === 'read-only') {
  toolRows(panel())[0].more.p.menu.onClick({ key: 'readOnly' });
  await settle(); await settle();
  report.calls = calls;
} else if (act === 'remove') {
  toolRows(panel())[0].more.p.menu.onClick({ key: 'remove' });
  report.confirmTitle = confirms[0] && confirms[0].title;
  await confirms[0].onOk();
  report.calls = calls;
} else if (act === 'add-file') {
  const file = new Blob(['x']);
  file.name = input.filename;
  await SW.store.addTools(file, true);
  report.calls = calls;
  report.toasts = toasts;
} else if (act === 'add-git') {
  await SW.store.addTools({ url: 'https://example.com/tools.git', path: 'tools' }, true);
  report.calls = calls;
} else if (act === 'dialog-py') {
  const props = { open: true, onClose: () => {} };
  // Pick a .py through the hidden input, then draw again with the state that set.
  const first = dialog(props, true).inner.find((d) => d.t === 'input');
  const file = new Blob(['x']);
  file.name = 'adder.py';
  first.p.onChange({ target: { files: [file], value: 'adder.py' } });
  report.box = dialog(props, true).box;
}

console.log(JSON.stringify(report));
