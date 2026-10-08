// What a template's `runQuery` hands an app for one query answer, run from the template's own file.
//
// Input on stdin: `{ "template": "fastapi-antd" | "react-vite", "body": <the server's JSON answer>,
// "reads": [<keys read off every record>] }`. "Symbol.iterator" reads that symbol.
// Output: `{ "result": <the value runQuery resolved to>, "reports": [<reportRuntimeError messages>],
// "values": [<per record, each read key to the value it got; undefined and functions left out>],
// "keys": [<per record, Object.keys>] }`.
//
// With `"steps"` instead of `"body"`, it drives `useQuery` through a minimal hooks runtime (#681):
// `answers` are the server's replies in request order (`{ "body": ..., "status": 200 }`), each held
// until a `"settle"` step. Steps: `{ "mount": [name, params, opts] }`, `{ "props": [...] }`,
// `"settle"`, `"tick"` (lets settled promises run, releases nothing), `"refresh"`, `"unmount"`. Output: `{ "snapshots": [...], "requests": [...] }`.
//
// #699 adds: `"hook"` picks what the steps drive — `"useQuery"` (the default), `"useDebouncedValue"`,
// `"example"` (the stack's screen example's `useOrdersView`) or `"exampleScreen"` (the FastAPI
// example's `OrdersScreen`, rendered to a plain element tree). `"byQuery"` answers a request by its
// query name when `answers` has no entry for its position. `"abortRejects": false` keeps an aborted
// request held, so a test can answer it after its abort. More steps: `{ "resolve": i }` releases
// request i alone, `{ "reject": [i, message] }` fails it with a TypeError, `{ "advance": ms }` moves
// fake time, `{ "call": [name, ...args] }` calls a function the hook returned. Output adds
// `"timers"`, the number of fake timers still pending at the end.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';

const TEMPLATE = new URL('../../../template/', import.meta.url).pathname;
const { template, body, reads = [], steps, answers = [], hook = 'useQuery', byQuery = {},
        abortRejects = true } = JSON.parse(fs.readFileSync(0, 'utf8'));
const reports = [];

// Just enough of React's hook contract: state, refs, memo, and effects that run after a render
// and clean up when their deps change or the component unmounts.
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
    // An element is plain data here: only the screen's own hooks run, never a child component's.
    createElement(type, props, ...children) {
      return { type: typeof type === 'string' ? type : (type.displayName || type.name), props: props || {},
               children: children.flat() };
    },
  };
  let output;
  function render() {
    do {
      dirty = false;
      rendering = true;
      index = 0;
      pending = [];
      output = mounted.component(...mounted.props);
      const effects = pending;
      for (const run of effects) run();
      rendering = false;
    } while (dirty);
  }
  return {
    React,
    mount(component, props) { slots.length = 0; mounted = { component, props }; render(); return output; },
    props(props) { mounted.props = props; render(); return output; },
    current() { return output; },
    unmount() {
      for (const slot of slots) if (slot && slot.cleanup) slot.cleanup();
      mounted = null;
    },
  };
}

// Ant Design, as names: `antd.Table` renders as an element whose type is "antd.Table".
const stub = (name) => new Proxy(function () {}, {
  get: (_, key) => (key === 'displayName' ? name : typeof key === 'string' ? stub(`${name}.${key}`) : undefined),
});

// Fake time. Nothing here waits on a real timer.
let now = 0;
let nextTimer = 1;
const timers = new Map();
const fakeSetTimeout = (fn, ms = 0) => { const id = nextTimer++; timers.set(id, { at: now + ms, fn }); return id; };
const fakeClearTimeout = (id) => { timers.delete(id); };

// Replies held until a "settle" step, so a test sees the state while a request is in flight.
const requests = [];
const held = [];
async function controlledFetch(url, init) {
  const query = decodeURIComponent(url.split('/').pop());
  const request = { query, params: JSON.parse(init.body).params, aborted: false };
  const index = requests.push(request) - 1;
  const answer = answers[index] || byQuery[query] || { body: { columns: [], rows: [] } };
  return new Promise((resolve, reject) => {
    init.signal?.addEventListener('abort', () => {
      request.aborted = true;
      if (abortRejects) reject(Object.assign(new Error('aborted'), { name: 'AbortError' }));
    });
    held.push({
      index,
      resolve: () => resolve({ ok: (answer.status || 200) < 400, status: answer.status || 200,
                               json: async () => answer.body }),
      reject: (message) => reject(new TypeError(message)),
    });
  });
}

const runtime = steps ? hooksRuntime() : null;
const fetch = steps ? controlledFetch : async () => ({ ok: true, status: 200, json: async () => body });

async function load() {
  if (template === 'fastapi-antd') {
    const sandbox = { fetch, JSON, Array, Object, String, Number, Boolean, Error, Map, Promise, Math,
                      AbortController, encodeURIComponent, React: runtime?.React,
                      setTimeout: fakeSetTimeout, clearTimeout: fakeClearTimeout, antd: stub('antd') };
    sandbox.window = sandbox;
    sandbox.sage = { url: (p) => p, reportRuntimeError: (message) => reports.push(message) };
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync(TEMPLATE + 'fastapi-antd/static/sage/appQuery.js', 'utf8'), sandbox);
    if (hook === 'example' || hook === 'exampleScreen') {
      vm.runInContext(fs.readFileSync(TEMPLATE + 'fastapi-antd/static/examples/OrdersScreen.js', 'utf8'),
                      sandbox);
      return { example: sandbox.app.useOrdersView, exampleScreen: sandbox.app.OrdersScreen };
    }
    return sandbox.sage;
  }
  // The module imports `appBase` from a file the dev server resolves; it is "" in the preview.
  const source = fs.readFileSync(TEMPLATE + 'react-vite/src/appQuery.ts', 'utf8')
    .replace(/^import \{ appBase \} from "\.\/appBase";$/m, 'const appBase = "";')
    .replace(/^import \{ reportRuntimeError \} from "\.\/reportRuntimeError";$/m,
             'const reportRuntimeError = (message) => globalThis.reports.push(message);')
    .replace(/^import \{ ([\w, ]+) \} from "react";$/m, 'const { $1 } = globalThis.React;');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'app-query-'));
  const file = path.join(dir, 'appQuery.ts');
  fs.writeFileSync(file, source);
  globalThis.fetch = fetch;
  globalThis.reports = reports;
  globalThis.React = runtime ? runtime.React : {};
  try {
    const helpers = { ...(await import(file)) };
    if (hook === 'example') {
      const example = path.join(dir, 'useOrdersView.ts');
      fs.writeFileSync(example, fs.readFileSync(TEMPLATE + 'react-vite/src/examples/useOrdersView.ts', 'utf8')
        .replace(/ from "\.\.\/appQuery";$/gm, ' from "./appQuery.ts";')
        .replace(/^import \{ ([\w, ]+) \} from "react";$/m, 'const { $1 } = globalThis.React;'));
      helpers.example = (await import(example)).useOrdersView;
    }
    return helpers;
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

const helpers = await load();

if (steps) {
  // After loading, so only the code under test sees fake time.
  globalThis.setTimeout = fakeSetTimeout;
  globalThis.clearTimeout = fakeClearTimeout;
  const query = (state) => ({
    status: state.status, error: state.error, refreshing: state.refreshing,
    records: state.data ? JSON.parse(JSON.stringify(state.data.records)) : null,
    query: state.data ? state.data.dataUsed.query : null,
  });
  // A hook's answer as plain data: a query state by its fields, functions left out.
  const plain = (value) => {
    if (value === null || typeof value !== 'object') return value;
    if ('status' in value && 'refresh' in value) return query(value);
    if (Array.isArray(value)) return value.map(plain);
    return Object.fromEntries(Object.entries(value).filter(([, v]) => typeof v !== 'function')
      .map(([k, v]) => [k, plain(v)]));
  };
  const snapshot = (state) => (hook === 'useQuery' ? query(state) : plain(state));
  const snapshots = [];
  const tick = async () => { for (let i = 0; i < 10; i++) await new Promise((r) => setImmediate(r)); };
  const release = async (index, how, message) => {
    const at = held.findIndex((h) => h.index === index);
    if (at === -1) throw new Error(`request ${index} is not held`);
    held.splice(at, 1)[0][how](message);
    await tick();
  };
  const settle = async () => {
    while (held.length) held.shift().resolve();
    await tick();
  };
  const advance = async (ms) => {
    const end = now + ms;
    for (;;) {
      const due = [...timers].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
      if (!due) break;
      timers.delete(due[0]);
      now = due[1].at;
      due[1].fn();
      await tick();
    }
    now = end;
  };
  const driven = helpers[hook];
  for (const step of steps) {
    if (step.mount) runtime.mount(driven, step.mount);
    else if (step.props) runtime.props(step.props);
    else if (step === 'settle') await settle();
    else if (step === 'tick') await tick();
    else if (step === 'refresh') runtime.current().refresh();
    else if (step.resolve !== undefined) await release(step.resolve, 'resolve');
    else if (step.reject) await release(step.reject[0], 'reject', step.reject[1]);
    else if (step.advance !== undefined) await advance(step.advance);
    else if (step.call) { const [name, ...args] = step.call; runtime.current()[name](...args); await tick(); }
    else if (step === 'unmount') { runtime.unmount(); await settle(); continue; }
    snapshots.push(snapshot(runtime.current()));
  }
  process.stdout.write(JSON.stringify({ snapshots, requests, timers: timers.size }));
} else {
  const result = await helpers.runQuery('q');
  const values = [];
  for (const record of result.records) {
    const read = {};
    for (const key of reads) {
      const value = record[key === 'Symbol.iterator' ? Symbol.iterator : key];
      if (typeof value !== 'function' && value !== undefined) read[key] = value;
    }
    values.push(read);
  }
  const keys = result.records.map((record) => Object.keys(record));
  const serialized = JSON.parse(JSON.stringify(result));
  process.stdout.write(JSON.stringify({ result: serialized, reports, values, keys }));
}
