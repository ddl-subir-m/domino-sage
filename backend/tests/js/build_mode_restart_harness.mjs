// Drives the real store.js through init() against a Builder that has just restarted, and reports
// which Build mode the composer ends up on and what it posted to get there.
//
// The server keeps the mode in memory only, so a restart answers `auto` whatever the person had
// picked. The browser's preference record is what survived, so this seeds it and reads the result.
//
// Input on stdin: `{ "saved": { "<projectId>": "<mode>" } | null, "server": "<mode>",
//                    "pick": "<mode>" | null }`. `pick` clicks the picker after boot.
import fs from 'node:fs';
import vm from 'node:vm';
import { unrefTimeout } from './sandbox_timeout.mjs';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { saved, server, pick } = JSON.parse(fs.readFileSync(0, 'utf8'));

let serverMode = server;
const project = () => ({
  id: 'p-acme-risk', name: 'Acme Risk Review', untitled: false, workspace: '/mnt/code',
  attached: [], scratch: [],
  model: { mode: serverMode, selected_mode: serverMode, phase: 'plan', picked_model: null,
           catalog: { plan: 'a/p', implement: 'a/i', ask: 'a/a' } },
});

let stored = saved ? JSON.stringify({ u1: { buildModes: saved } }) : null;
const posted = [];
const json = (body) => ({
  ok: true, status: 200, headers: { get: () => 'application/json' },
  json: async () => body, text: async () => JSON.stringify(body),
});

const quiet = () => {};
const sandbox = {
  console, JSON, Math, Date, process, Set, Map, Promise, Array, Object, String, Number, Boolean,
  RegExp, Error, TextEncoder, TextDecoder, URL, URLSearchParams, setTimeout: unrefTimeout, clearTimeout,
  setInterval, clearInterval, Blob, ArrayBuffer, Uint8Array,
  localStorage: {
    getItem: (k) => (k === 'sw.prefs' ? stored : null),
    setItem: (k, v) => { if (k === 'sw.prefs') stored = v; },
    removeItem() {},
  },
  requestAnimationFrame: (fn) => fn(),
  document: { addEventListener() {}, querySelector: () => null, body: {}, documentElement: { style: { setProperty() {} } } },
  React: { createElement: (t, p, ...c) => ({ t, p, c }), useState: () => [null, () => {}],
           useEffect: () => {}, useRef: () => ({ current: null }), Fragment: 'Fragment' },
  antd: {
    message: { success: quiet, error: quiet, info: quiet, warning: quiet },
    notification: { open: quiet, error: quiet, warning: quiet, info: quiet },
    Modal: { confirm: quiet, info: quiet, error: quiet },
  },
  fetch: async (url, opts = {}) => {
    const path = String(url).split('?')[0].replace(/^\.\/api/, '');
    if (path === '/project/model' && opts.method === 'POST') {
      const body = JSON.parse(opts.body);
      posted.push(body);
      if (body.mode) serverMode = body.mode;
      return json({ model: project().model });
    }
    if (path === '/project') return json(project());
    if (path === '/projects') return json({ items: [], provisioning: true });
    if (path === '/me') return json({ id: 'u1', name: 'Dana Reed' });
    if (path === '/threads') return json([]);
    if (path === '/members') return json({ members: [], directory: [] });
    if (path === '/assets') return json({ assets: [] });
    return json({});
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

for (const f of ['util.js', 'prefs.js', 'api.js', 'store.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

await SW.store.init();
if (pick) await SW.store.setBuildMode(pick);
// The restore is not awaited by init(), so let its POST land before reading.
await new Promise((r) => setTimeout(r, 20));

console.log(JSON.stringify({
  buildMode: SW.store.get().buildMode,
  serverMode,
  posted: posted.filter((b) => 'mode' in b).map((b) => b.mode),
  saved: ((JSON.parse(stored || '{}').u1) || {}).buildModes || null,
}));
process.exit(0);
