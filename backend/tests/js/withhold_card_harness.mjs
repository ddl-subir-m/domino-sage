// Renders SW.MessageBlock for the guardrail search's card, with a recording createElement, so the
// tests read the tree a browser would build.
//
// The copy is the thing worth pinning. Everything this design decided a person ever SEES is in
// these strings: which thing was matched, whether their own words are involved, what survives, and
// how long the withhold lasts. A source assertion cannot see any of it, and a wrong word here is
// the difference between "Sage stopped sending your file" and "Sage changed your data".
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const spec = JSON.parse(fs.readFileSync(0, 'utf8'));

const clicked = [];

function node(type, props, ...children) {
  const flat = [];
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    flat.push(child);
  }
  if (typeof type === 'function') return type(props || {});
  return {
    tag: String(type),
    className: (props && props.className) || '',
    onClick: props && props.onClick,
    kind: (props && props.type) || '',
    children: flat,
  };
}

const sandbox = {
  console, JSON, Object, String, Array, Error, Set, Map, Date, Math, Number, Boolean,
  encodeURIComponent, decodeURIComponent, parseInt, parseFloat, isNaN,
  React: { createElement: node, useState: (v) => [v, () => {}], Fragment: 'Fragment' },
  antd: {
    Button: 'Button', Table: 'Table', Tooltip: 'Tooltip', Tag: 'Tag', Space: 'Space',
    Spin: 'Spin', Input: { TextArea: 'Input.TextArea' },
    message: { success() {}, error() {}, info() {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  SW: {
    brand: { text: (s) => s },
    util: { useBusyAct: () => ['', (key, fn) => () => fn()] },
    // Recording stubs. Which act a button calls matters as much as its label: the primary one
    // writes a row that changes what every later turn sends, and the other only hides a card.
    store: {
      withholdContent: (block) => clicked.push(
        `withhold:${block.surface}:${(block.carriers || []).map((c) => c.key).join(',')}`),
      dismissWithholdCard: () => clicked.push('dismiss'),
      get: () => ({ threads: [], touched: [] }),
    },
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(
  fs.readFileSync(ROOT + 'components/message-blocks.js', 'utf8'),
  sandbox,
  { filename: 'message-blocks.js' },
);

const rendered = sandbox.SW.MessageBlock({ block: spec.block });

function walk(n, out) {
  if (n === null || n === undefined) return out;
  if (typeof n === 'string' || typeof n === 'number') {
    if (out.length) out[out.length - 1].text += String(n);
    return out;
  }
  out.push({ tag: n.tag, className: n.className, kind: n.kind, text: '', hasClick: !!n.onClick });
  const at = out.length - 1;
  for (const child of n.children) walk(child, out);
  if (n.onClick) { n.onClick(); out[at].act = clicked[clicked.length - 1]; }
  return out;
}

console.log(JSON.stringify({ nodes: walk(rendered, []), clicked }));
