// Renders SW.MessageBlock for the two blocks a refused Conversation draws (ADR-0022), with a
// recording createElement, so the tests read the tree the browser would build.
//
// The copy is the thing worth pinning. Everything this design decided that a person ever SEES is
// in these strings: which rung they are on, what clearing costs them, what survives it, and that
// the transcript is not the thing being emptied. A source assertion cannot see any of it, and a
// wrong word here rebuilds the dead end the whole ticket exists to remove.
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
  React: { createElement: node, useState: (v) => [v, () => {}] },
  antd: {
    Button: 'Button', Table: 'Table', Tooltip: 'Tooltip', Tag: 'Tag', Space: 'Space',
    Input: { TextArea: 'Input.TextArea' },
    message: { success() {}, error() {}, info() {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  SW: {
    brand: { text: (s) => s },
    // Recording stubs: the two buttons must call DIFFERENT acts, and the destructive one must
    // carry the scope. A card that offered a complete clear and asked for a seeded one would read
    // correctly and do the wrong thing.
    store: {
      clearRecall: (scope) => clicked.push(`clear:${scope}`),
      dismissRecallOffer: () => clicked.push('dismiss'),
      draftHandoffPlan: () => clicked.push('plan'),
      dismissPlanSuggestion: () => clicked.push('dismiss-plan'),
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
  // Fire the handler so the test can assert which act a button is wired to, not merely that it
  // has one.
  if (n.onClick) { out[at].fired = clicked.length; n.onClick(); out[at].act = clicked[clicked.length - 1]; }
  return out;
}

console.log(JSON.stringify({ nodes: walk(rendered, []), clicked }));
