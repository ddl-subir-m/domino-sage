// The Project's Secrets and MCP servers groups in the resources panel (#643).
//
// Same approach as `project_skills_harness.mjs`: `createElement` is stubbed to a plain object, so
// calling the panel returns tree data, and hooks are real per mount. `fetch` records every request
// and answers the #641 and #642 contracts from `secrets` and `servers`, so every act is proved by
// what it SENT.
//
// Input on stdin: `{ act, secrets, servers, value }`.
//   drawn           the group heads, their captions, the secret and server rows, the Add menu keys
//   add-secret      press the Secrets door, fill Name, Value (`value`) and Note, press Save; report
//                   what was sent and where `value` can still be found afterwards
//   replace-secret  the first secret's Replace value, then Save with `value`
//   note-secret     the first secret's Edit note, then Save with a new note
//   remove-secret   confirm the first secret's Remove
//   toggle-mcp      switch the first server off
//   reread-mcp      the first server's Read its tools again
//   remove-mcp      confirm the first server's Remove
//   add-mcp         Add MCP server from the Add menu, a header from the secret `CRM_TOKEN`, Add
//   add-domino-mcp  Add MCP server, an Authorization header typed, then Domino-hosted, another
//                   header, Add
//   mcp-settles     load the servers as the panel does on mount, where `serverReads[i]` is what
//                   the i-th read of `/project/mcp` answers (the last one repeats), then look at
//                   each virtual moment in `at` — the row subtitles and how many reads went out;
//                   with `again`, load them once more and look `again` milliseconds later
import fs from 'node:fs';
import vm from 'node:vm';
import { fakeClock } from './sandbox_timeout.mjs';

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
const answer = (url, method, body) => {
  const path = url.replace(/^\.\/api/, '').split('?')[0];
  if (path === '/project/secrets') return input.secrets;
  if (path.startsWith('/project/secrets/')) {
    return method === 'DELETE' ? { ok: true }
      : { name: decodeURIComponent(path.split('/').pop()), note: (body && body.note) || '' };
  }
  if (path === '/project/mcp' && method === 'GET' && input.serverReads) {
    const reads = calls.filter((c) => c.url.endsWith('/project/mcp') && c.method === 'GET').length;
    return { servers: input.serverReads[Math.min(reads, input.serverReads.length) - 1] };
  }
  if (path === '/project/mcp' && method === 'GET') return { servers: input.servers || [] };
  if (path === '/project/mcp') return { ...body, enabled: true, tools: [], status: 'pending', warning: null };
  return {};
};
const clock = fakeClock();
const sandbox = {
  console, JSON, Object, String, Array, Error, Map, Set, Promise, Date, Math, Number, Boolean,
  RegExp, encodeURIComponent, decodeURIComponent, setTimeout: clock.setTimeout,
  clearTimeout: clock.clearTimeout,
  setInterval: () => 1, clearInterval: () => {}, requestAnimationFrame: (fn) => fn(),
  URLSearchParams, TextEncoder, TextDecoder, URL, Blob,
  fetch: async (url, options = {}) => {
    const method = options.method || 'GET';
    const body = typeof options.body === 'string' ? JSON.parse(options.body) : null;
    calls.push({ url: String(url), method, body });
    const reply = answer(String(url), method, body);
    return { ok: true, status: 200, statusText: 'OK',
             headers: { get: () => 'application/json' },
             json: async () => reply, text: async () => JSON.stringify(reply) };
  },
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  document: {
    title: '', documentElement: { style: { setProperty: () => {} } },
    addEventListener: () => {}, removeEventListener: () => {},
    getElementById: () => ({}), querySelector: () => null, body: {},
  },
  location: { search: '', pathname: '/', href: 'http://localhost/', hash: '#/chat' },
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
    Input: Object.assign(function Input() {}, { TextArea: 'Input.TextArea', Password: 'Input.Password' }),
    Button: 'Button', Dropdown: 'Dropdown', Tag: 'Tag', Tooltip: 'Tooltip', Space: 'Space',
    Drawer: 'Drawer', Skeleton: 'Skeleton', Empty: 'Empty', Checkbox: 'Checkbox', Alert: 'Alert',
    Switch: 'Switch', Select: 'Select', Segmented: 'Segmented',
    Modal: Object.assign(function Modal() {}, { confirm: (o) => confirms.push(o), info: () => {} }),
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
                 'components/project-skills.js', 'components/project-secrets.js',
                 'components/project-mcp-servers.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

SW.store.set({
  resourceGroups: {}, resourcesLoading: false, resourceErrors: {}, thread: { id: 't1' },
  secrets: input.secrets, mcpServers: input.servers || [],
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
const labelled = (nodes, label) => nodes.find((n) => n.p && n.p['aria-label'] === label);
const settle = async () => { for (let i = 0; i < 10; i += 1) await new Promise((r) => setTimeout(r, 0)); };

function rowsOf(nodes, rowClass) {
  return nodes.filter((n) => cls(n).includes(rowClass)).map((row) => {
    const drawn = flatten(row);
    const sw = drawn.find((d) => d.t === 'Switch');
    return {
      name: text(drawn.find((d) => cls(d) === 'sw-res-name')),
      subtitle: text(drawn.find((d) => cls(d).startsWith('sw-res-sub'))),
      checked: sw ? sw.p.checked : null,
      sw,
      more: drawn.find((d) => d.t === 'Dropdown'),
    };
  });
}
const plain = ({ sw, more, ...row }) => row;
const modal = (nodes) => nodes.find((n) => n.t === sandbox.antd.Modal);
const type = (label, value) => labelled(panel(), label).p.onChange({ target: { value } });
// Everything a person or a later read could still find the value in once the dialog is done.
const leftovers = (value) => {
  const tree = JSON.stringify(panel().map((n) => n.p), (k, v) => (typeof v === 'function' ? undefined : v));
  return { state: JSON.stringify(SW.store.get()).includes(value), tree: tree.includes(value),
           toasts: toasts.some((t) => String(t).includes(value)),
           hooks: JSON.stringify(hooks).includes(value) };
};

const report = {};

if (act === 'drawn') {
  const nodes = panel();
  report.heads = nodes.filter((n) => cls(n) === 'sw-res-group-label').map((head) => ({
    label: text(flatten(head).find((d) => cls(d) === 'sw-group-label')),
    hasAdd: !!flatten(head).find((d) => cls(d) === 'sw-res-group-add'),
  }));
  report.captions = nodes.filter((n) => cls(n) === 'sw-group-caption' || cls(n) === 'sw-group-note')
    .map((n) => ({ cls: cls(n), text: text(n) }));
  report.secrets = rowsOf(nodes, 'sw-secret-row').map(plain);
  report.servers = rowsOf(nodes, 'sw-mcp-row').map(plain);
  report.menuKeys = nodes.find((n) => n.t === 'Dropdown' && (n.p.menu || {}).items
    && n.p.menu.items.some((i) => i.key === 'browse')).p.menu.items.map((i) => i.key);
} else if (act === 'add-secret') {
  const head = panel().filter((n) => cls(n) === 'sw-res-group-label')
    .find((n) => text(n).startsWith('Secrets'));
  flatten(head).find((d) => cls(d) === 'sw-res-group-add').p.onClick();
  report.title = modal(panel()).p.title;
  type('Name', 'OPENAI_API_KEY');
  type('Value', input.value);
  type('Note', 'OpenAI key; use with api.openai.com');
  const field = panel().find((n) => n.t === 'Input.Password');
  report.valueField = { password: !!field, toggle: field.p.visibilityToggle };
  await modal(panel()).p.onOk();
  await settle();
  report.calls = calls;
  report.dialogOpen = !!modal(panel());
  report.leftovers = leftovers(input.value);
} else if (act === 'replace-secret' || act === 'note-secret') {
  rowsOf(panel(), 'sw-secret-row')[0].more.p.menu.onClick({ key: act === 'replace-secret' ? 'replace' : 'note' });
  const nodes = panel();
  report.title = modal(nodes).p.title;
  report.hasName = !!labelled(nodes, 'Name');
  const field = labelled(nodes, act === 'replace-secret' ? 'Value' : 'Note');
  report.prefilled = field.p.value;
  type(act === 'replace-secret' ? 'Value' : 'Note', act === 'replace-secret' ? input.value : 'Rotated monthly.');
  await modal(panel()).p.onOk();
  await settle();
  report.calls = calls;
  if (input.value) report.leftovers = leftovers(input.value);
} else if (act === 'remove-secret') {
  rowsOf(panel(), 'sw-secret-row')[0].more.p.menu.onClick({ key: 'remove' });
  report.confirmTitle = confirms[0] && confirms[0].title;
  await confirms[0].onOk();
  report.calls = calls;
} else if (act === 'toggle-mcp') {
  rowsOf(panel(), 'sw-mcp-row')[0].sw.p.onChange(false);
  await settle();
  report.calls = calls;
} else if (act === 'reread-mcp') {
  const more = rowsOf(panel(), 'sw-mcp-row')[0].more;
  report.menuKeys = more.p.menu.items.map((i) => i.key || i.label);
  more.p.menu.onClick({ key: 'reread' });
  await settle();
  report.calls = calls;
} else if (act === 'remove-mcp') {
  rowsOf(panel(), 'sw-mcp-row')[0].more.p.menu.onClick({ key: 'remove' });
  report.confirmTitle = confirms[0] && confirms[0].title;
  await confirms[0].onOk();
  report.calls = calls;
} else if (act === 'add-mcp') {
  panel().find((n) => n.t === 'Dropdown' && (n.p.menu || {}).items
    && n.p.menu.items.some((i) => i.key === 'browse')).p.menu.onClick({ key: 'mcp' });
  report.title = modal(panel()).p.title;
  type('Name', 'crm');
  type('URL', 'https://crm.example.com/mcp');
  type('Header name', 'Authorization');
  type('Header value', 'Bearer ');
  const pick = panel().find((n) => n.t === 'Select' && cls(n) === 'sw-mcp-secret-pick');
  report.secretOptions = pick.p.options.map((o) => o.value);
  pick.p.onChange('CRM_TOKEN');
  report.headerValue = labelled(panel(), 'Header value').p.value;
  await modal(panel()).p.onOk();
  await settle();
  report.calls = calls;
  report.toasts = toasts;
} else if (act === 'add-domino-mcp') {
  panel().find((n) => n.t === 'Dropdown' && (n.p.menu || {}).items
    && n.p.menu.items.some((i) => i.key === 'browse')).p.menu.onClick({ key: 'mcp' });
  const kinds = () => panel().find((n) => n.t === 'Segmented' && cls(n) === 'sw-mcp-kind');
  const note = () => panel().find((n) => cls(n).includes('sw-mcp-domino-note'));
  const headerNames = () => panel().filter((n) => n.p['aria-label'] === 'Header name').map((n) => n.p.value);
  report.kinds = kinds().p.options.map((o) => o.label);
  report.before = { kind: kinds().p.value, note: !!note() };
  type('Header name', 'Authorization');
  type('Header value', 'Bearer {env:CRM_TOKEN}');
  kinds().p.onChange('domino');
  report.after = { kind: kinds().p.value, note: note() && text(note()), headerNames: headerNames() };
  type('Name', 'crm');
  type('URL', 'https://apps.domino.example.com/crm/mcp');
  panel().find((n) => n.t === 'Button' && text(n) === 'Add a header').p.onClick();
  type('Header name', 'X-Region');
  type('Header value', 'eu');
  await modal(panel()).p.onOk();
  await settle();
  report.calls = calls;
} else if (act === 'mcp-settles') {
  const look = (t) => ({
    at: t,
    subtitles: rowsOf(panel(), 'sw-mcp-row').map((r) => r.subtitle),
    reads: calls.filter((c) => c.url.endsWith('/project/mcp') && c.method === 'GET').length,
  });
  await SW.store.loadMcpServers();
  report.looks = [look(0)];
  let elapsed = 0;
  for (const t of input.at) {
    await clock.advance(t - elapsed);
    elapsed = t;
    report.looks.push(look(t));
  }
  if (input.again) {
    await SW.store.loadMcpServers();
    await clock.advance(input.again);
    report.looks.push(look('again'));
  }
}

console.log(JSON.stringify(report));
