import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const messages = JSON.parse(fs.readFileSync(0, 'utf8'));
const h = (t, p, ...c) => ({ t, p: p || {}, c });
const sandbox = {
  window: {},
  React: {
    createElement: h,
    useState: (value) => [value, () => {}],
    useEffect: () => {},
    Fragment: 'Fragment',
  },
  antd: new Proxy({}, { get: (_, name) => String(name) }),
  icons: new Proxy({}, { get: (_, name) => String(name) }),
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(ROOT + 'components/plan.js', 'utf8'), sandbox);

const tree = sandbox.SW.OriginalRequest({ messages });
function* walk(node) {
  if (!node || typeof node !== 'object') return;
  if (Array.isArray(node)) { for (const child of node) yield* walk(child); return; }
  yield node;
  yield* walk(node.c);
}
const nodes = [...walk(tree)];
console.log(JSON.stringify({
  tag: tree.t,
  open: Object.hasOwn(tree.p, 'open'),
  words: nodes.flatMap((node) => node.c || []).flat(Infinity)
    .filter((value) => typeof value === 'string'),
  inputs: nodes.filter((node) => String(node.t).includes('Input')).length,
}));
