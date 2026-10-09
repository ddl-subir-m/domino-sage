// What `SW.util.markdown` makes of the URLs a model writes.
//
// Input on stdin: `{ "text": "<markdown>" }`.
//
// Output: every anchor as `{ href, target, rel, text }`, plus the plain text left around them.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/', import.meta.url).pathname;
const { text } = JSON.parse(fs.readFileSync(0, 'utf8'));

const sandbox = {
  console, JSON, Math, Date, Set, Map, Array, Object, String, Number, Boolean, RegExp, Error,
  React: { createElement: (t, p, ...c) => ({ t, p: p || {}, c }), Fragment: 'Fragment' },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.self = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(ROOT + 'js/util.js', 'utf8'), sandbox, { filename: 'util.js' });

const flat = (node) => {
  if (node === null || node === undefined || node === false || node === true) return '';
  if (Array.isArray(node)) return node.map(flat).join('');
  if (typeof node === 'string') return node;
  return flat(node.c);
};

const links = [];
const plain = [];
function collect(node) {
  if (node === null || node === undefined || node === false || node === true) return;
  if (Array.isArray(node)) { node.forEach(collect); return; }
  if (typeof node === 'string') { plain.push(node); return; }
  if (node.t === 'a') {
    links.push({ href: node.p.href, target: node.p.target, rel: node.p.rel, text: flat(node.c) });
    return;
  }
  collect(node.c);
}
collect(sandbox.SW.util.markdown(text));
console.log(JSON.stringify({ links, plain: plain.join('') }));
