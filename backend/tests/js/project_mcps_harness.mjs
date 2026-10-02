// The Project's own MCP servers in the resources panel (ADR-0071, #621).
//
// Same approach as `project_skills_harness.mjs`: `createElement` is stubbed to a plain object, so
// calling the panel returns tree data, and `fetch` records every request, so a switch, an override
// or an add is proved by what it SENT.
//
// Input on stdin: `{ act, hash, thread, app, items, form, body }`.
//   drawn       the MCP rows, the first row's menu, the Add menu's keys, which dialog is open
//   press-door  press the MCPs group's `+`, then report which dialog is open
//   toggle      switch the first server off
//   menu        press `key` in the first server's menu
//   body        `SW.mcpBody(form)`, or the error it throws
//   add         `SW.store.addMcp(body)`
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
const toasts = [];
const sandbox = {
  console, JSON, Object, String, Array, Error, Map, Set, Promise, Date, Math, Number, Boolean,
  RegExp, encodeURIComponent, decodeURIComponent, setTimeout: unrefTimeout, clearTimeout,
  setInterval: () => 1, clearInterval: () => {}, requestAnimationFrame: (fn) => fn(),
  URLSearchParams, TextEncoder, TextDecoder, URL, Blob,
  fetch: async (url, options = {}) => {
    calls.push({ url: String(url), method: options.method || 'GET',
                 body: typeof options.body === 'string' ? JSON.parse(options.body) : null });
    const body = { items: input.items || [], builtinSkills: [], item: input.reply || {} };
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
    Modal: Object.assign(function Modal() {}, { confirm: () => {}, info: () => {} }),
    message: { info: () => {}, success: (t) => toasts.push(t), error: (t) => toasts.push(t),
               warning: (t) => toasts.push(t) },
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
                 'components/project-skills.js', 'components/project-mcps.js']) {
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
  if (typeof node.t === 'function' && node.t.name !== 'Input' && node.t.name !== 'Modal'
      && node.t !== SW.AddSkillModal && node.t !== SW.AddMcpModal) {
    flatten(node.t(Object.assign({}, node.p, { children: node.c })), out, depth + 1);
  }
  flatten(node.c, out, depth + 1);
  return out;
}
const text = (node) => flatten(node).flatMap((n) => n.c || []).flat(Infinity)
  .filter((c) => typeof c === 'string').join('');
const panel = () => { cursor = 0; return flatten(SW.ResourcePanel()); };
const cls = (n) => String((n.p || {}).className || '');

function mcpRows(nodes) {
  return nodes.filter((n) => cls(n).includes('sw-mcp-row')).map((row) => {
    const drawn = flatten(row);
    const sw = drawn.find((d) => d.t === 'Switch');
    const sub = drawn.find((d) => cls(d).includes('sw-res-sub'));
    return {
      name: text(drawn.find((d) => cls(d) === 'sw-res-name')),
      subtitle: text(sub),
      failed: cls(sub).includes('is-failed'),
      checked: sw.p.checked,
      disabled: sw.p.disabled,
      label: sw.p['aria-label'],
      sw,
      more: drawn.find((d) => d.t === 'Dropdown'),
    };
  });
}
const menuItems = (items) => (items || []).flatMap((i) => (i.children ? menuItems(i.children)
  : [{ key: i.key, label: i.label }]));

const report = {};
const settle = () => new Promise((r) => setTimeout(r, 0));

if (act === 'drawn' || act === 'press-door') {
  let nodes = panel();
  if (act === 'press-door') {
    const head = nodes.filter((n) => cls(n) === 'sw-res-group-label')
      .find((n) => text(n).startsWith('MCPs'));
    const add = flatten(head).find((d) => cls(d) === 'sw-res-group-add');
    report.doorLabel = add.p['aria-label'];
    add.p.onClick();
    nodes = panel();
  }
  const rows = mcpRows(nodes);
  report.rows = rows.map(({ sw, more, ...row }) => row);
  report.menu = rows.length ? menuItems(rows[0].more.p.menu.items) : [];
  report.menuKeys = (nodes.find((n) => n.t === 'Dropdown' && (n.p.menu || {}).items
    && n.p.menu.items.some((i) => i.key === 'browse')).p.menu.items).map((i) => i.key);
  report.mcpDialogOpen = nodes.some((n) => n.t === SW.AddMcpModal);
  report.skillDialogOpen = nodes.some((n) => n.t === SW.AddSkillModal);
} else if (act === 'toggle') {
  mcpRows(panel())[0].sw.p.onChange(false);
  await settle(); await settle();
  report.calls = calls;
} else if (act === 'menu') {
  mcpRows(panel())[0].more.p.menu.onClick({ key: input.key });
  await settle(); await settle();
  report.calls = calls;
} else if (act === 'body') {
  try {
    report.body = SW.mcpBody(input.form);
  } catch (err) {
    report.error = err.message;
  }
} else if (act === 'add') {
  await SW.store.addMcp(input.body);
  report.calls = calls;
  report.toasts = toasts;
}

console.log(JSON.stringify(report));
