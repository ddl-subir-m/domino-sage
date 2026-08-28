// Drives the real prefs.js against a fake localStorage and reports what each step answered.
// Reading the source cannot show the two things this file is for: that a choice is still there
// after the page is loaded again, and that a second viewer on the same origin reads their own
// answer rather than the first viewer's.
//
// `reload` re-evaluates prefs.js against the SAME backing storage, which is what a browser does
// on F5 and what an ordinary unit test cannot fake by calling get() twice.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const steps = JSON.parse(fs.readFileSync(0, 'utf8'));

// One backing map for the whole run, so it outlives each reload the way a browser's does.
const backing = new Map();
const localStorage = {
  getItem: (k) => (backing.has(k) ? backing.get(k) : null),
  setItem: (k, v) => backing.set(k, String(v)),
  removeItem: (k) => backing.delete(k),
};

let viewer = null;
const sandbox = {
  console, JSON, Object, String, Array, Error, localStorage,
  // prefs.js asks the store who is looking. The store is not under test here.
  SW: { store: { get: () => ({ me: viewer }) } },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

function load() {
  vm.runInContext(fs.readFileSync(ROOT + 'prefs.js', 'utf8'), sandbox, { filename: 'prefs.js' });
}
load();

const out = [];
for (const step of steps) {
  if (step.viewer !== undefined) viewer = step.viewer ? { id: step.viewer } : null;
  if (step.op === 'get') out.push(sandbox.SW.prefs.get(step.name));
  else if (step.op === 'set') out.push(sandbox.SW.prefs.set(step.name, step.value));
  else if (step.op === 'reload') { sandbox.SW.prefs = undefined; load(); out.push(null); }
  else if (step.op === 'seed') { backing.set(step.key, step.raw); out.push(null); }
  else if (step.op === 'dump') out.push(backing.get(step.key) ?? null);
  else throw new Error(`unknown op ${step.op}`);
}
console.log(JSON.stringify(out));
