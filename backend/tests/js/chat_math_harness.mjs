// What `SW.util.markdown` makes of the TeX a model writes, typeset by the real vendored KaTeX.
//
// Input on stdin: `{ "text": "<markdown>", "katex": true|false }`. `katex: false` is the page with
// the bundle missing, which must leave the source as written rather than drop it.
//
// Output: every typeset formula as `{ display, tex }`, where `tex` is read back out of KaTeX's own
// MathML annotation — so it is the source KaTeX actually received, not the one this harness meant
// to send — plus the plain text and the code spans left around them.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/', import.meta.url).pathname;
const { text, katex } = JSON.parse(fs.readFileSync(0, 'utf8'));

const sandbox = {
  console, JSON, Math, Date, Set, Map, Array, Object, String, Number, Boolean, RegExp, Error,
  React: { createElement: (t, p, ...c) => ({ t, p: p || {}, c }), Fragment: 'Fragment' },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.self = sandbox;
vm.createContext(sandbox);
if (katex) {
  vm.runInContext(fs.readFileSync(ROOT + 'vendor/katex/katex.min.js', 'utf8'), sandbox);
}
vm.runInContext(fs.readFileSync(ROOT + 'js/util.js', 'utf8'), sandbox, { filename: 'util.js' });

const math = [];
const plain = [];
const code = [];
const lists = [];
function collect(node) {
  if (node === null || node === undefined || node === false || node === true) return;
  if (Array.isArray(node)) { node.forEach(collect); return; }
  if (typeof node === 'string') { plain.push(node); return; }
  if (node.t === 'ul' || node.t === 'ol') lists.push(node.c.flat(Infinity).length);
  if (node.t === 'code') { code.push(node.c.flat(Infinity).join('')); return; }
  if (node.p && node.p.className === 'sw-math') {
    const html = node.p.dangerouslySetInnerHTML.__html;
    const tex = /<annotation encoding="application\/x-tex">([\s\S]*?)<\/annotation>/.exec(html);
    math.push({
      display: html.includes('katex-display'),
      tex: tex ? tex[1].replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>') : null,
      error: html.includes('katex-error'),
    });
    return;
  }
  collect(node.c);
}
collect(sandbox.SW.util.markdown(text));
console.log(JSON.stringify({ math, plain: plain.join(''), code, lists }));
