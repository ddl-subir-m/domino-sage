// What happens to a group note after the leg that raised it stops refusing.
//
// The panel reads the platform on a scope change and when Browse Domino opens, and nowhere else. So
// a gateway that refused one read left "the LLM Gateway answered 400 at /v1/models" standing under
// a group that visibly held models — the rows being the last good answer carried forward — until
// somebody happened to do one of those two things. The claim here is about a read that leaves the
// browser on its own afterwards, which no amount of grepping the source can settle.
//
// Input on stdin: `{ "act": "clears" | "once" }` — whether the platform recovers by the second read
// or goes on refusing.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { act } = JSON.parse(fs.readFileSync(0, 'utf8'));

const REFUSAL = 'The LLM Gateway answered 400 at /v1/models.';
const MODELS = [{ id: 'm1', name: 'risk-scorer', display_name: 'Risk scorer' }];

// The shape `/api/resources` answers a refused leg with: 200, an error keyed on the kind, and no
// rows for it. The other two kinds answered.
const REFUSED = { data_sources: [], llm_aliases: [], model_apis: [], errors: { llm_aliases: REFUSAL } };
const ANSWERED = { data_sources: [], llm_aliases: MODELS, model_apis: [], errors: {} };

let resourceReads = 0;
function answer(url) {
  if (url.endsWith('/api/assets')) return { assets: [], default_dataset_id: null };
  if (url.endsWith('/api/resources')) {
    resourceReads += 1;
    if (act === 'once') return REFUSED;
    return resourceReads === 1 ? REFUSED : ANSWERED;
  }
  return {};
}

const sandbox = {
  console, JSON, Object, String, Array, Error, Map, Set, Promise, Date, Math, Number, Boolean,
  RegExp, encodeURIComponent, decodeURIComponent, setTimeout, clearTimeout, setInterval,
  clearInterval, URLSearchParams, TextEncoder, TextDecoder, URL, Blob, ArrayBuffer, Uint8Array,
  fetch: (url) => Promise.resolve({
    ok: true,
    status: 200,
    statusText: 'OK',
    headers: { get: () => 'application/json' },
    json: () => Promise.resolve(answer(url)),
  }),
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  document: {
    title: '', documentElement: { style: { setProperty: () => {} } },
    addEventListener: () => {}, removeEventListener: () => {}, getElementById: () => ({}),
  },
  location: { search: '', pathname: '/', href: 'http://localhost/', hash: '#/build' },
  addEventListener: () => {}, removeEventListener: () => {},
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
    useEffect: () => {}, useRef: () => ({ current: null }), useMemo: (fn) => fn(),
    Fragment: 'Fragment',
  },
  antd: {
    message: { info: () => {}, success: () => {}, error: () => {}, warning: () => {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  EventSource: function () {},
  SW: {},
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'prefs.js', 'router.js', 'store.js', 'api.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

SW.store.set({ scope: { id: 'p1', name: 'quick-start' }, resourceGroups: {}, resourceErrors: {} });

await SW.store.refreshResourceListing();
const afterRefusal = {
  note: (SW.store.get().resourceErrors || {}).llm_aliases || null,
  reads: resourceReads,
};

// Past the retry window, and then some: the claim is that a read leaves on its own, and a second
// wait of the same length is what tells one retry from a poll.
await new Promise((resolve) => setTimeout(resolve, 5000));
const afterRetry = {
  note: (SW.store.get().resourceErrors || {}).llm_aliases || null,
  models: ((SW.store.get().resourceListing || {}).groups || {}).model_llm || [],
  reads: resourceReads,
};
await new Promise((resolve) => setTimeout(resolve, 4000));
const later = { reads: resourceReads };

console.log(JSON.stringify({ afterRefusal, afterRetry, later }));
