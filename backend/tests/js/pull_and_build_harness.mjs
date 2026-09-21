// #347: "Pull and build this" must not start a build when the merge worked but the push did not.
// This drives the real store method and stubs only the API edge that talks to the server.
import fs from 'node:fs';
import vm from 'node:vm';
import { unrefTimeout } from './sandbox_timeout.mjs';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { syncResult } = JSON.parse(fs.readFileSync(0, 'utf8'));

const calls = [];

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array,
  setTimeout: unrefTimeout, clearTimeout, setInterval, clearInterval,
  encodeURIComponent, decodeURIComponent, URLSearchParams,
  requestAnimationFrame: (fn) => fn(),
  localStorage: (() => {
    const backing = new Map();
    return {
      getItem: (k) => (backing.has(k) ? backing.get(k) : null),
      setItem: (k, v) => backing.set(k, String(v)),
      removeItem: (k) => backing.delete(k),
    };
  })(),
  location: { search: '', pathname: '/', href: 'http://localhost/', hash: '#/build' },
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  addEventListener() {}, removeEventListener() {},
  React: {
    createElement: (t, p, ...c) => ({ t, p, c }),
    useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
    useEffect: () => {}, useRef: () => ({ current: null }), Fragment: 'Fragment',
  },
  antd: { message: { success() {}, info() {}, warning() {}, error() {} } },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async () => { throw new Error('the harness uses stubbed store APIs'); },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

SW.store.set({
  thread: { id: 'conv_1', title: 'The desk talk', artifacts: [] },
  scope: { id: 'proj', name: 'Demo Project' },
  threads: [],
  activeApp: { id: 'app_a' },
});

SW.api.syncProject = async () => {
  calls.push('syncProject');
  return syncResult;
};
SW.store.loadApps = async () => { calls.push('loadApps'); };
SW.store.loadBuild = async () => { calls.push('loadBuild'); };
SW.store.sendBuildPrompt = async () => { calls.push('sendBuildPrompt'); };

let thrown = '';
try {
  await SW.store.pullAndBuild('build the chart');
} catch (err) {
  thrown = String((err && err.message) || err);
}

console.log(JSON.stringify({ calls, thrown }));
