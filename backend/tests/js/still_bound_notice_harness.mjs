// What the 409 from `remove_project_resource` draws when the Resource still has holders.
//
// The payload already names the Built Apps, the files, and the conversation titles. Concatenating
// those into one Modal.info string reprints the titles (the server sentence AND "Held in") and
// turns untitled chats — first message as title — into a wall of @mentions. This harness calls
// the notice as the function it is, against a stubbed `createElement`, so the test reads the
// groups and rows a person would see rather than grepping the source for a join.
//
// Nothing is mounted. Input on stdin: `{ apps, refs, conversations, scopeName }`.
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
  return {
    tag: String(type),
    className: (props && props.className) || '',
    title: (props && props.title) || '',
    children: flat,
  };
}

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
  React: { createElement: node },
  EventSource: function () {},
  SW: {},
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'store.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}

const notice = sandbox.SW.util.stillBoundNotice({
  apps: spec.apps || [],
  refs: spec.refs || [],
  conversations: spec.conversations || [],
  scopeName: spec.scopeName || 'Default',
});

function walk(n, out = []) {
  if (n === null || n === undefined || n === false) return out;
  if (typeof n === 'string' || typeof n === 'number') {
    out.push({ tag: '#text', className: '', title: '', text: String(n) });
    return out;
  }
  if (Array.isArray(n)) {
    n.forEach((child) => walk(child, out));
    return out;
  }
  out.push({
    tag: n.tag,
    className: n.className,
    title: n.title,
    text: (n.children || []).filter((c) => typeof c === 'string').join(''),
  });
  (n.children || []).forEach((child) => walk(child, out));
  return out;
}

console.log(JSON.stringify({
  title: notice.title,
  nodes: walk(notice.content),
}));
