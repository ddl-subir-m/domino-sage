// #646: a model a gateway administrator adds must reach an open tab's pickers without a reload.
// Drives the real store and `SW.api` and reports which platform listing reads left the browser.
//
// Input on stdin: `{ "act": ... }`
//   cold        open the Chat model menu before any listing was read
//   open        open the Chat model menu once the tab holds a listing
//   open-twice  open it twice in a row
//   visible     the tab comes back into view
//   hidden      the tab goes out of view
//   drawer      open the Build model drawer
import fs from 'node:fs';
import vm from 'node:vm';
import { unrefTimeout } from './sandbox_timeout.mjs';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { act } = JSON.parse(fs.readFileSync(0, 'utf8'));

// The listing the tab read at its scope load: one model. The gateway now answers with two.
const HELD = {
  errors: {},
  groups: {
    dataset: [], datasource: [], model_predictive: [],
    model_llm: [{ id: 'llm_alias:m1', name: 'sonnet', alias: 'sonnet', kind: 'model_llm' }],
  },
};
const FRESH_RESOURCES = {
  data_sources: [],
  model_apis: [],
  llm_aliases: [
    { id: 'm1', name: 'sonnet', display_name: 'sonnet' },
    { id: 'm2', name: 'new-model', display_name: 'new-model' },
  ],
};

const listingReads = [];
const docListeners = {};
const document = {
  title: '', visibilityState: 'visible', documentElement: { style: { setProperty() {} } },
  addEventListener: (type, fn) => { (docListeners[type] = docListeners[type] || []).push(fn); },
  removeEventListener() {}, querySelector: () => null, getElementById: () => ({}), body: {},
};

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, TextEncoder, TextDecoder, URL,
  setTimeout: unrefTimeout, clearTimeout, setInterval, clearInterval,
  encodeURIComponent, decodeURIComponent, URLSearchParams,
  requestAnimationFrame: (fn) => fn(),
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  location: { search: '', pathname: '/', href: 'http://localhost/', hash: '#/chat' },
  document,
  addEventListener() {}, removeEventListener() {},
  React: {
    createElement: (t, p, ...c) => ({ t, p, c }),
    useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
    useEffect: () => {}, useRef: () => ({ current: null }), Fragment: 'Fragment',
  },
  antd: { message: { success() {}, info() {}, warning() {}, error() {} } },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: (url) => {
    // `/api/project/resources` is membership and ends in the same word; it is not the listing.
    if (url.endsWith('/api/resources') || url.endsWith('/api/assets')) listingReads.push(url);
    const body = url.endsWith('/api/resources') ? FRESH_RESOURCES
      : url.endsWith('/api/assets') ? { assets: [] } : {};
    return Promise.resolve({
      ok: true, status: 200, statusText: 'OK',
      headers: { get: () => 'application/json' }, json: () => Promise.resolve(body),
    });
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

SW.store.set({
  scope: { id: 'proj', name: 'Demo Project' },
  resourceListing: act === 'cold' ? null : HELD,
  resourceListingScope: 'proj',
  resourceGroups: {},
  gatewayAliases: act === 'cold' ? [] : HELD.groups.model_llm,
});
SW.store.loadAssignments = async () => {};

// Until the held listing is replaced, or long enough that a read nobody started would have landed.
// Polled rather than a fixed count of ticks, so a slow machine cannot read as "nothing was read".
async function settle() {
  const start = Date.now();
  while (SW.store.get().resourceListing === HELD || SW.store.get().resourceListing === null) {
    if (Date.now() - start > 200) return;
    await new Promise((r) => setTimeout(r, 1));
  }
  await new Promise((r) => setTimeout(r, 0));
}
const fire = (state) => {
  document.visibilityState = state;
  (docListeners.visibilitychange || []).forEach((fn) => fn());
};

if (act === 'cold' || act === 'open') SW.store.refreshModelList();
if (act === 'open-twice') { SW.store.refreshModelList(); SW.store.refreshModelList(); }
if (act === 'visible') fire('visible');
if (act === 'hidden') fire('hidden');
if (act === 'drawer') SW.store.openAssignments(true);
await settle();
if (act === 'drawer') SW.store.openAssignments(false);

console.log(JSON.stringify({
  resourceReads: listingReads.filter((u) => u.endsWith('/api/resources')).length,
  aliases: (SW.store.get().gatewayAliases || []).map((a) => a.alias),
}));
