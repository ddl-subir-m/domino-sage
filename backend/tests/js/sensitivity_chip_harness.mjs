// Drives the real SW.util.lockedLabel against the lock states the composer can be in.
//
// Reading the source cannot show the thing this is for. The chip is built from a picked model and
// a lock that arrives separately, and the defect this guards against (found in live QA, 2026-09-09)
// was the two disagreeing: the chip kept naming `gpt-5.4` after the lock had moved the session to
// `opus`, with the correction living only in a notice that has a "Got it" on it.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const spec = JSON.parse(fs.readFileSync(0, 'utf8'));

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
vm.runInContext(fs.readFileSync(ROOT + 'util.js', 'utf8'), sandbox, { filename: 'util.js' });

const out = (spec.cases || []).map((c) => ({
  label: sandbox.SW.util.lockedLabel(c.sensitivity, c.picked),
  approved: sandbox.SW.util.isApproved(c.sensitivity, c.picked),
  locked: sandbox.SW.util.isLocked(c.sensitivity),
}));
console.log(JSON.stringify(out));
