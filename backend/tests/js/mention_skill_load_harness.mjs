// A Project skill in the composer's @ menu on a page nobody seeded (#736).
//
// `mention_skill_harness.mjs` writes `extensions` into the store, so it could not see that nothing
// filled it on a real page load: the only reader was the Project resources panel's mount. This one
// boots through the real `init()`, answers every read from a stub server, and never mounts the
// panel — `useEffect` is a no-op, so no component can fill the list either. Whatever the menu
// offers came off the store's own reads.
//
// Each stage changes one thing a Project's skill list hangs on and types into the composer again:
//   boot     fresh load, Chat, no conversation open
//   project  the Project switches, still with no conversation open
//   thread   a conversation opens
//   mode     the router moves to Build, with an app selected
//   upload   a skill is added
//   enable   a skill is switched on
//
// Output: `{ <stage>: { rows: [name], reads: [query] } }`, `reads` being the extension reads that
// stage sent, as their query strings.
import fs from 'node:fs';
import vm from 'node:vm';
import { unrefTimeout } from './sandbox_timeout.mjs';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;

const skill = (name, enabled = true) => ({ id: `skill:${name}`, kind: 'skill', name, enabled });
const server = {
  project: 'p-signal',
  skills: {
    'p-signal': [skill('revops-conventions')],
    'p-other': [skill('drift-metrics'), skill('meddpicc', false)],
  },
};
let reads = [];

const json = (body) => ({ ok: true, status: 200, headers: { get: () => 'application/json' },
                          text: async () => JSON.stringify(body), json: async () => body });

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

const location = { hash: '#/chat', href: 'http://x/#/chat' };
const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, TextEncoder, TextDecoder, URL, URLSearchParams,
  setTimeout: unrefTimeout, clearTimeout, setInterval: () => 1, clearInterval: () => {},
  requestAnimationFrame: (fn) => fn(),
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {},
              documentElement: { style: { setProperty() {} } } },
  location,
  history: { replaceState() {}, pushState() {} },
  addEventListener() {}, removeEventListener() {},
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    useState: hookState, useEffect: () => {}, useMemo: (fn) => fn(), useCallback: (fn) => fn,
    useRef: () => ({ current: null }), Fragment: 'Fragment',
  },
  antd: {
    Input: Object.assign(function Input() {}, { TextArea: 'Input.TextArea' }),
    Button: 'Button', Dropdown: 'Dropdown', Tag: 'Tag', Tooltip: 'Tooltip', Space: 'Space',
    Drawer: 'Drawer', Skeleton: 'Skeleton', Checkbox: 'Checkbox', Alert: 'Alert',
    Modal: { confirm: () => ({ update() {}, destroy() {} }), info() {} },
    message: { success() {}, error() {}, info() {}, warning() {} },
    notification: { open() {}, error() {}, warning() {}, info() {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async (url, init = {}) => {
    const [path, query] = String(url).replace(/^\.\/api/, '').split('?');
    const method = (init.method || 'GET').toUpperCase();
    if (path === '/project') return json({ id: server.project, name: server.project });
    if (path === '/projects') return json({ items: [], provisioning: false });
    if (path === '/me') return json({ id: 'u1', name: 'Dana Reed' });
    if (path === '/threads') return json([]);
    if (path === '/project/extensions' && method === 'GET') {
      reads.push(query || '');
      return json({ items: server.skills[server.project].map((s) => ({ ...s })) });
    }
    if (path === '/project/extensions/skills' && method === 'POST') {
      server.skills[server.project].push(skill('deal-brief'));
      return json({ items: [skill('deal-brief')] });
    }
    const enabled = path.match(/^\/project\/extensions\/(.+)\/enabled$/);
    if (enabled && method === 'PUT') {
      const id = decodeURIComponent(enabled[1]);
      server.skills[server.project].forEach((s) => { if (s.id === id) s.enabled = JSON.parse(init.body).enabled; });
      return json({});
    }
    return json({});
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'prefs.js', 'api.js', 'store.js', 'router.js',
                 'components/resource-tree.js', 'components/resource-panel.js',
                 'components/composer.js']) {
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
  if (typeof node.t === 'function' && node.t.name !== 'Input') {
    flatten(node.t(Object.assign({}, node.p, { children: node.c })), out, depth + 1);
  }
  flatten(node.c, out, depth + 1);
  return out;
}

const settle = async () => { for (let i = 0; i < 30; i += 1) await new Promise((r) => setTimeout(r, 0)); };

// A fresh composer each time, so one stage's typed text cannot carry into the next.
function offered(query) {
  hooks = [];
  const props = { showMode: SW.router.get().mode === 'build', onSend: () => {} };
  const render = () => { cursor = 0; return SW.Composer(props); };
  render();
  const box = flatten(render()).find((n) => n.t === 'Input.TextArea');
  const value = `@${query}`;
  box.p.onChange({ target: { value, selectionStart: value.length }, nativeEvent: {} });
  return flatten(render())
    .filter((n) => String(n.p.className || '').startsWith('sw-mention-item'))
    .map((row) => flatten(row).filter((n) => n.p && n.p.className === 'sw-mention-name')
      .flatMap((n) => (n.c || []).flat(Infinity)).join(''));
}

const out = {};
async function stage(name, act) {
  reads = [];
  await act();
  await settle();
  out[name] = { rows: [...offered('rev'), ...offered('medd'), ...offered('deal'), ...offered('drift')],
                reads };
}

await stage('boot', () => SW.store.init());
// Before any conversation or app is open, so the Project is the only thing that moved.
await stage('project', () => {
  server.project = 'p-other';
  return SW.store.setScope({ id: 'p-other', name: 'Other' }, { silent: true });
});
await stage('thread', () => SW.store.set({ thread: { id: 'conv_1', title: 'deals', artifacts: [] } }));
await stage('mode', () => {
  SW.store.set({ activeApp: { id: 'app_a', name: 'Pipeline' } });
  location.hash = '#/build';
  SW.router.go('#/build');
});
await stage('upload', () => SW.store.addSkills({ name: 'deal-brief.zip' }));
await stage('enable', () => SW.store.setExtensionEnabled(skill('meddpicc'), true));
out.enable.meddpiccEnabled = SW.store.get().extensions.items
  .find((s) => s.name === 'meddpicc').enabled;

console.log(JSON.stringify(out));
