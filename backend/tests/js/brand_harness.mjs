// Drives the real SW.brand accessor in store.js against a pack the way /api/brand delivers one.
//
// Reading the source cannot show the thing this is for: the accessor resolves a token from
// whatever pack the server sent, and that pack arrives after the shell has already painted. So the
// harness runs both states — before the fetch answers (state.brand's built-in literal) and after
// (store.set) — against the same code the browser runs.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const spec = JSON.parse(fs.readFileSync(0, 'utf8'));

// store.js is the whole Workbench store; it needs just enough of a browser to finish evaluating.
const sandbox = {
  console, JSON, Object, String, Array, Error, Map, Set, Promise, Date, Math, Number, Boolean,
  RegExp, encodeURIComponent, decodeURIComponent, setTimeout, clearTimeout, setInterval,
  clearInterval, URLSearchParams,
  fetch: () => Promise.reject(new Error('the harness makes no requests')),
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  document: {
    title: '',
    documentElement: { style: { setProperty: () => {} } },
    addEventListener: () => {},
  },
  location: { search: '', pathname: '/', href: 'http://localhost/' },
  antd: { message: {}, Modal: {} },
  React: {},
  EventSource: function () {},
  SW: {},
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(ROOT + 'store.js', 'utf8'), sandbox, { filename: 'store.js' });

// No pack means /api/brand has not answered yet, which is a state a person can see.
if (spec.pack) sandbox.SW.store.set({ brand: spec.pack });

const out = [];
for (const call of spec.calls || []) {
  if (call.op === 'text') out.push(sandbox.SW.brand.text(call.template));
  else if (call.op === 'assistant') out.push(sandbox.SW.brand.assistant());
  else if (call.op === 'product') out.push(sandbox.SW.brand.product());
  else if (call.op === 'platform') out.push(sandbox.SW.brand.platform());
  else throw new Error(`unknown op ${call.op}`);
}
console.log(JSON.stringify(out));
