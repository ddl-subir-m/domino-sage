// The Project's own skills in the resources panel (ADR-0071, #620).
//
// Same approach as `resource_group_add_harness.mjs`: `createElement` is stubbed to a plain object,
// so calling the panel returns tree data, and hooks are real per mount. `fetch` records every
// request and answers from `reply`, so a switch or a removal is proved by what it SENT.
//
// Input on stdin: `{ act, hash, thread, app, items, builtinSkills, builtinSections, replaces }`.
//   drawn       the skill rows, the Add menu's keys, and whether the dialog is open
//   press-door  press the Skills group's `+` and report whether the dialog opened
//   open-skill  press the first skill row, then report the drawer's title, its buttons, its
//               Replaces options, and — once `replaces` is chosen — what that sent
//   update      `SW.store.updateSkillFromSource(first skill)`
//   toggle      switch the first skill off
//   remove      confirm the first skill's Remove
//   add-git     `SW.store.addSkills(url, replaces)`
//   add-file    `SW.store.addSkills(file, replaces)`
//   preview-md  `SW.store.previewSkills([two .md files])`
//   add-pick    `SW.store.addSkills(zip, '', [two folders])`
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
    const body = String(url).endsWith('/threads') && options.method === 'POST'
      ? { id: 't-new' }
      : { items: input.items || [], builtinSkills: input.builtinSkills || [],
          builtinSections: input.builtinSections || [] };
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
                 'components/project-skills.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

SW.store.set({
  resourceGroups: {}, resourcesLoading: false, resourceErrors: {},
  thread: input.thread ? { id: input.thread } : null,
  activeApp: input.app ? { id: input.app, name: input.app } : null,
  extensions: { items: input.items || [], builtinSkills: input.builtinSkills || [],
                builtinSections: input.builtinSections || [] },
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

function skillRows(nodes) {
  return nodes.filter((n) => cls(n).includes('sw-skill-row')).map((row) => {
    const drawn = flatten(row);
    const sw = drawn.find((d) => d.t === 'Switch');
    return {
      name: text(drawn.find((d) => cls(d) === 'sw-res-name')),
      subtitle: text(drawn.find((d) => cls(d) === 'sw-res-sub')),
      checked: sw.p.checked,
      disabled: sw.p.disabled,
      label: sw.p['aria-label'],
      tip: (drawn.find((d) => d.t === 'Tooltip' && d.c.includes(sw)) || {}).p?.title,
      sw,
      more: drawn.find((d) => d.t === 'Dropdown'),
      open: drawn.find((d) => d.t === 'button' && cls(d) === 'sw-res-open'),
    };
  });
}

const report = {};
const settle = () => new Promise((r) => setTimeout(r, 0));

if (act === 'drawn' || act === 'press-door') {
  let nodes = panel();
  if (act === 'press-door') {
    const head = nodes.filter((n) => cls(n) === 'sw-res-group-label')
      .find((n) => text(n).startsWith('Skills'));
    flatten(head).find((d) => cls(d) === 'sw-res-group-add').p.onClick();
    nodes = panel();
  }
  report.rows = skillRows(nodes).map(({ sw, more, open, ...row }) => row);
  report.menuKeys = (nodes.find((n) => n.t === 'Dropdown' && (n.p.menu || {}).items
    && n.p.menu.items.some((i) => i.key === 'browse')).p.menu.items).map((i) => i.key);
  report.dialogOpen = !!nodes.find((n) => n.t === SW.AddSkillModal);
} else if (act === 'open-skill') {
  skillRows(panel())[0].open.p.onClick();
  const drawer = panel().find((n) => n.t === SW.SkillDrawer);
  report.drawerOpen = !!drawer;
  const inner = flatten(SW.SkillDrawer(drawer.p));
  const shell = inner.find((d) => d.t === 'Drawer');
  report.title = shell.p.title;
  report.buttons = flatten(shell.p.extra).filter((d) => d.t === 'Button').map((d) => text(d));
  const select = inner.find((d) => d.t === 'Select');
  report.replacesOptions = select.p.options.map((o) => o.value);
  report.replacesCaption = text(inner.filter((d) => d.t === 'p' && cls(d) === 'sw-caption').pop());
  if (input.replaces !== undefined) {
    select.p.onChange(input.replaces);
    await settle(); await settle();
    report.calls = calls;
  }
} else if (act === 'update') {
  await SW.store.updateSkillFromSource(input.items[0]);
  report.calls = calls;
  report.toasts = toasts;
} else if (act === 'toggle') {
  skillRows(panel())[0].sw.p.onChange(false);
  await settle(); await settle();
  report.calls = calls;
} else if (act === 'remove') {
  skillRows(panel())[0].more.p.menu.onClick({ key: 'remove' });
  report.confirmTitle = confirms[0] && confirms[0].title;
  await confirms[0].onOk();
  report.calls = calls;
} else if (act === 'add-git') {
  await SW.store.addSkills('https://example.com/skills.git', 'data-table');
  report.calls = calls;
  report.toasts = toasts;
} else if (act === 'add-dataset') {
  await SW.store.addSkills({ dataset: 'dataset:ds1', path: 'skills/house' }, '');
  report.calls = calls;
} else if (act === 'candidates') {
  report.candidates = SW.skillCandidates(input.files);
} else if (act === 'add-file') {
  const file = new Blob(['---\nname: x\n---\n']);
  file.name = 'house.zip';
  await SW.store.addSkills(file, '');
  report.calls = calls;
} else if (act === 'preview-md') {
  const named = (body, name) => Object.assign(new Blob([body]), { name });
  await SW.store.previewSkills([named('---\nname: house\n---\n', 'house.md'),
                                named('Teal.', 'colors.md')]);
  report.calls = calls;
} else if (act === 'add-pick') {
  const file = new Blob(['zip']);
  file.name = 'pack.zip';
  await SW.store.addSkills(file, '', ['skills/a', 'skills/b']);
  report.calls = calls;
}

console.log(JSON.stringify(report));
