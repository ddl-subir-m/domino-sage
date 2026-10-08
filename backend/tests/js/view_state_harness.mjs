// Drives a template's `useViewState` (#701) from the template's own file, in a fake page.
//
// Input on stdin: `{ "template": "fastapi-antd" | "react-vite", "schema": {...}, "location":
// { "pathname", "search", "hash" }, "historyState": <any>, "steps": [...], "keys": <answer> }`.
// A schema field's `"pattern"` is a regular-expression source; it reaches the hook as a RegExp.
// Steps: `"mount"`, `{ "patch": [values, options] }`, `{ "hash": "#..." }` (the person edits the
// address bar: a new history entry, then popstate and hashchange), `"back"`, `"forward"`,
// `"unmount"`. With `"keys"`, the FastAPI keys panel (`static/sage/keys.js`) is loaded first and
// `GET sage/keys` answers with that value.
// Output: `{ "snapshots": [{ "state", "url", "renders" }], "writes": [{ "method", "url", "state" }],
// "listeners", "topReads", "dialogs", "error" }`. `dialogs` counts open keys panels; `topReads`
// counts reads of `window.top`.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';

const TEMPLATE = new URL('../../../template/', import.meta.url).pathname;
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const { template, steps = [], keys } = input;
const schema = Object.fromEntries(Object.entries(input.schema || {}).map(([key, field]) => [
  key, field.pattern === undefined ? field : { ...field, pattern: new RegExp(field.pattern) }]));

// Just enough of React's hook contract (see app_query_records_harness.mjs).
function hooksRuntime() {
  const slots = [];
  let index = 0;
  let pending = [];
  let rendering = false;
  let dirty = false;
  let mounted = null;
  const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
  const React = {
    useState(init) {
      const slot = slots[index++] ||= { value: typeof init === 'function' ? init() : init };
      return [slot.value, (next) => {
        const value = typeof next === 'function' ? next(slot.value) : next;
        if (Object.is(value, slot.value)) return;
        slot.value = value;
        if (rendering) dirty = true; else if (mounted) render();
      }];
    },
    useRef(init) { return slots[index++] ||= { current: init }; },
    useMemo(make, deps) {
      const slot = slots[index++] ||= {};
      if (!same(slot.deps, deps)) { slot.value = make(); slot.deps = deps; }
      return slot.value;
    },
    useCallback(fn, deps) { return React.useMemo(() => fn, deps); },
    useEffect(effect, deps) {
      const slot = slots[index++] ||= { cleanup: null };
      if (same(slot.deps, deps)) return;
      slot.deps = deps;
      pending.push(() => { if (slot.cleanup) slot.cleanup(); slot.cleanup = effect() || null; });
    },
  };
  let output;
  function render() {
    do {
      dirty = false;
      rendering = true;
      index = 0;
      pending = [];
      output = mounted.component();
      const effects = pending;
      for (const run of effects) run();
      rendering = false;
    } while (dirty);
  }
  return {
    React,
    mount(component) { slots.length = 0; mounted = { component }; render(); return output; },
    current() { return output; },
    unmount() {
      for (const slot of slots) if (slot && slot.cleanup) slot.cleanup();
      mounted = null;
    },
  };
}

// A page's location and session history. A History API write changes the entry and fires nothing;
// a navigation the person makes fires popstate, then hashchange when the fragment moved.
function fakePage(start, startState) {
  const parse = (url, state) => {
    const hashAt = url.indexOf('#');
    const hash = hashAt === -1 ? '' : url.slice(hashAt);
    const rest = hashAt === -1 ? url : url.slice(0, hashAt);
    const searchAt = rest.indexOf('?');
    return { pathname: searchAt === -1 ? rest : rest.slice(0, searchAt),
             search: searchAt === -1 ? '' : rest.slice(searchAt), hash: hash === '#' ? '' : hash, state };
  };
  const entries = [{ ...start, state: startState }];
  let at = 0;
  const writes = [];
  const listeners = { hashchange: new Set(), popstate: new Set() };
  const fire = (type) => { for (const fn of [...listeners[type]]) fn({ type }); };
  const location = {
    get pathname() { return entries[at].pathname; },
    get search() { return entries[at].search; },
    get hash() { return entries[at].hash; },
    get href() { const e = entries[at]; return e.pathname + e.search + e.hash; },
  };
  const history = {
    get state() { return entries[at].state; },
    get length() { return entries.length; },
    pushState(state, _title, url) {
      writes.push({ method: 'pushState', url, state });
      entries.splice(at + 1);
      entries.push(parse(url, state));
      at += 1;
    },
    replaceState(state, _title, url) {
      writes.push({ method: 'replaceState', url, state });
      entries[at] = parse(url, state);
    },
  };
  const move = (to) => {
    if (to < 0 || to >= entries.length) throw new Error(`no history entry ${to}`);
    const before = entries[at].hash;
    at = to;
    fire('popstate');
    if (entries[at].hash !== before) fire('hashchange');
  };
  return {
    location, history, writes,
    addEventListener(type, fn) { listeners[type]?.add(fn); },
    removeEventListener(type, fn) { listeners[type]?.delete(fn); },
    listenerCount: () => listeners.hashchange.size + listeners.popstate.size,
    navigate(hash) {
      const before = entries[at].hash;
      entries.splice(at + 1);
      entries.push({ ...entries[at], hash, state: null });
      at += 1;
      fire('popstate');
      if (hash !== before) fire('hashchange');
    },
    back: () => move(at - 1),
    forward: () => move(at + 1),
  };
}

// The few DOM calls the keys panel makes.
function fakeDocument() {
  const body = { children: [], appendChild(node) { this.children.push(node); return node; } };
  const createElement = (tag) => ({
    tag, attrs: {}, style: {}, children: [],
    setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener() {},
    appendChild(child) { this.children.push(child); return child; },
    remove() { body.children = body.children.filter((n) => n !== this); },
  });
  return { readyState: 'complete', body, createElement, createTextNode: (text) => ({ text }),
           addEventListener() {} };
}

const runtime = hooksRuntime();
const page = fakePage({ pathname: '/', search: '', hash: '', ...input.location }, input.historyState ?? null);
const document = fakeDocument();
let topReads = 0;
const win = {
  location: page.location, history: page.history, document, URLSearchParams,
  addEventListener: page.addEventListener, removeEventListener: page.removeEventListener,
  fetch: async () => ({ ok: true, status: 200, json: async () => keys }),
  React: runtime.React, Object, Array, String, Number, Boolean, Error, RegExp, JSON, Promise,
};
Object.defineProperty(win, 'top', { get() { topReads += 1; return win; } });
win.window = win;

const tick = async () => { for (let i = 0; i < 10; i++) await new Promise((r) => setImmediate(r)); };

async function load() {
  if (template === 'fastapi-antd') {
    vm.createContext(win);
    if (keys !== undefined) {
      vm.runInContext(fs.readFileSync(TEMPLATE + 'fastapi-antd/static/sage/keys.js', 'utf8'), win);
      await tick();
    }
    vm.runInContext(fs.readFileSync(TEMPLATE + 'fastapi-antd/static/sage/viewState.js', 'utf8'), win);
    return win.sage.useViewState;
  }
  const source = fs.readFileSync(TEMPLATE + 'react-vite/src/appViewState.ts', 'utf8')
    .replace(/^import \{ ([\w, ]+) \} from "react";$/m, 'const { $1 } = globalThis.React;');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'app-view-state-'));
  const file = path.join(dir, 'appViewState.ts');
  fs.writeFileSync(file, source);
  globalThis.React = runtime.React;
  globalThis.window = win;
  try {
    return (await import(file)).useViewState;
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

const useViewState = await load();
let renders = 0;
let patch = null;
function Shell() {
  renders += 1;
  const [state, patchState] = useViewState(schema);
  patch = patchState;
  return state;
}

const snapshots = [];
let error = null;
for (const step of steps) {
  try {
    if (step === 'mount') runtime.mount(Shell);
    else if (step.patch) patch(...step.patch);
    else if (step.hash !== undefined) page.navigate(step.hash);
    else if (step === 'back') page.back();
    else if (step === 'forward') page.forward();
    else if (step === 'unmount') runtime.unmount();
  } catch (e) {
    error = e.message;
    break;
  }
  await tick();
  snapshots.push({ state: step === 'unmount' ? null : JSON.parse(JSON.stringify(runtime.current())),
                   url: page.location.href, renders });
}
process.stdout.write(JSON.stringify({
  snapshots, writes: page.writes, listeners: page.listenerCount(), topReads, error,
  dialogs: document.body.children.filter((n) => n.attrs.role === 'dialog').length,
}));
