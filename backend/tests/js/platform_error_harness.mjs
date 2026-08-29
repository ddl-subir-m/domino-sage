// Renders SW.PlatformError / SW.PlatformQuote with a recording createElement, so the tests read the
// tree the browser would build rather than the source that builds it.
//
// The thing worth proving is structural: the platform's words land inside their own element,
// unaltered, and Sage's sentence stays outside it. A source assertion cannot see either.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const spec = JSON.parse(fs.readFileSync(0, 'utf8'));

function node(type, props, ...children) {
  const flat = [];
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    flat.push(child);
  }
  // A component: call it, so the tree is what actually renders.
  if (typeof type === 'function') return type(props || {});
  return { tag: type, className: (props && props.className) || '', children: flat };
}

const sandbox = { console, JSON, Object, String, Array, Error, React: { createElement: node }, SW: {} };
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(
  fs.readFileSync(ROOT + 'components/platform-error.js', 'utf8'),
  sandbox,
  { filename: 'platform-error.js' },
);

const rendered = sandbox.SW[spec.component](spec.props || {});

// Flatten to {tag, className, text} so a test can ask "where did this string end up".
function walk(n, out) {
  if (n === null || n === undefined) return out;
  if (typeof n === 'string') {
    if (out.length) out[out.length - 1].text += n;
    return out;
  }
  out.push({ tag: n.tag, className: n.className, text: '' });
  for (const child of n.children) walk(child, out);
  return out;
}

console.log(JSON.stringify(walk(rendered, [])));
