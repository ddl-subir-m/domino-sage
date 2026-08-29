// Two Build tabs against ONE server selection (#100).
//
// Every other harness in here runs one store, which is the right shape for a defect inside one.
// This one is not: the thing being asserted is what TWO tabs do to each other through the server,
// and one store cannot show that — `selectApp` writes the per-Project selection, and a tab only
// learns that the selection moved by re-reading `/apps`. So a tab here is its own vm context with
// its own `store.js`, `router.js` and `location`, and the server — the fixtures and `selected`
// below — is the only thing they share. That is exactly the two-tab arrangement.
//
// The React shim is real about the one thing this ticket turns on: EFFECT DEPENDENCIES. A shim
// that ran every effect on every render could not tell "seeds once" from "re-asserts forever",
// because those two differ only in their dependency list. So effects run when their deps change
// and not otherwise, and `useRef` survives a re-render — the seed effect keeps its "I wrote this
// URL myself" note in one.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const steps = JSON.parse(fs.readFileSync(0, 'utf8'));

// --- the server ------------------------------------------------------------
// Four apps, because the ping-pong needs two tabs naming two different ones and the follow needs a
// third nobody's URL names — a selection that moves to an app already in a URL cannot tell being
// followed from being re-asserted.
const APPS = [
  { id: 'app_a', name: 'Desk dashboard', built: true },
  { id: 'app_b', name: 'P&L report', built: true },
  { id: 'app_c', name: 'Rate curve viewer', built: true },
  { id: 'app_d', name: 'Risk monitor', built: true },
];

const THREADS = {
  thr_many: { id: 'thr_many', title: 'Desks', artifacts: [], history: [], touched: [] },
  // A Conversation with a confirmed handoff, which is what `resolveConversationApp` reads. Its app
  // is NOT the one the server has selected, so a bare link resolving is a selection that moved.
  thr_bound: {
    id: 'thr_bound', title: 'Bound elsewhere', artifacts: [], history: [], touched: [],
    handoff: { status: 'bound', appId: 'app_c' },
  },
};

// The one piece of state the two tabs share: which app the Project has selected. Moved by a
// `POST /apps/<id>/select` from either tab, and read back by both.
let selected = 'app_a';
// Every request either tab made, in order, tagged with the tab that made it. The ping-pong is a
// claim about WRITES, so the ledger is the assertion and the screen is not.
const calls = [];

const json = (body, status = 200) => ({
  ok: status < 400, status,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

function route(path, init) {
  let m;
  if ((m = path.match(/^\/apps\/([^/?]+)\/select$/))) {
    selected = m[1];
    return json({});
  }
  if (path === '/apps') {
    return json({ items: APPS.map((a) => ({ ...a, selected: a.id === selected })), selected });
  }
  if (path === '/bindings') return json({ bindings: [] });
  if (path === '/project') return json({ attached: [] });
  if (path.match(/^\/threads\/([^/]+)\/conversation$/)) return json({ history: [] });
  if (path.match(/^\/threads\/([^/]+)\/context$/)) return json({ items: [] });
  if ((m = path.match(/^\/threads\/([^/?]+)$/))) {
    return json(THREADS[m[1]] || { id: m[1], history: [], touched: [] });
  }
  if (path === '/threads') return json(Object.values(THREADS));
  return json({});
}

// --- a tab -----------------------------------------------------------------
// One vm context: one store, one router, one address bar. Everything a browser tab owns alone.
function makeTab(name, hash) {
  const listeners = {};
  let current = hash;
  // Recorded rather than run. The 30-second poll is what a step FIRES by hand — leaving a real
  // interval behind would hold node open long after the assertions were done.
  const timers = [];

  // Hook cells by call order, the way React keys them. One counter for every hook, so a `useRef`
  // and a `useEffect` cannot swap places between renders.
  const cells = [];
  let cursor = 0;
  const pending = [];
  const cleanups = [];

  const sandbox = {
    console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
    Error, Blob, ArrayBuffer, Uint8Array, Infinity,
    setTimeout: (fn, ms) => (ms >= 5000 ? -1 : setTimeout(fn, ms)),
    clearTimeout,
    setInterval: (fn, ms) => { timers.push({ ms, fn }); return timers.length; },
    clearInterval: () => {},
    requestAnimationFrame: (fn) => fn(),
    localStorage: (() => {
      const backing = new Map();
      return {
        getItem: (k) => (backing.has(k) ? backing.get(k) : null),
        setItem: (k, v) => backing.set(k, String(v)),
        removeItem: (k) => backing.delete(k),
      };
    })(),
    document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
    // A real address bar, in the one way that matters here: assigning `hash` fires `hashchange`,
    // and `replaceState` changes the URL WITHOUT firing it. `SW.router.replace` leans on the
    // second half — it re-reads the route itself — and a stub that fired the event would hide a
    // double render rather than reveal it.
    location: {
      get hash() { return current; },
      set hash(v) {
        current = String(v).startsWith('#') ? String(v) : `#${v}`;
        (listeners.hashchange || []).forEach((fn) => fn());
      },
    },
    history: { replaceState(_state, _title, url) { current = String(url); } },
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    removeEventListener() {},
    React: {
      createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
      useState: (init) => {
        const i = cursor++;
        if (!cells[i]) cells[i] = { v: typeof init === 'function' ? init() : init };
        const cell = cells[i];
        return [cell.v, (next) => { cell.v = typeof next === 'function' ? next(cell.v) : next; }];
      },
      useRef: (init) => {
        const i = cursor++;
        if (!cells[i]) cells[i] = { ref: { current: init } };
        return cells[i].ref;
      },
      useEffect: (fn, deps) => {
        const i = cursor++;
        const prev = cells[i];
        const changed =
          !prev || !prev.deps || !deps
          || deps.length !== prev.deps.length
          || deps.some((d, n) => !Object.is(d, prev.deps[n]));
        cells[i] = { deps: deps ? deps.slice() : null };
        if (changed) pending.push(fn);
      },
      Fragment: 'Fragment',
    },
    antd: {
      Input: Object.assign(function Input() {}, { TextArea: 'Input.TextArea' }),
      Button: 'Button', Dropdown: 'Dropdown', Tag: 'Tag', Tooltip: 'Tooltip', Space: 'Space',
      Checkbox: 'Checkbox', Modal: { confirm() {} },
      message: { success() {}, error() {}, info() {}, warning() {} },
    },
    icons: new Proxy({}, { get: (_, key) => String(key) }),
    fetch: async (url, init) => {
      const path = String(url).replace(/^\.\/api/, '');
      calls.push(`${name} ${(init && init.method) || 'GET'} ${path}`);
      return route(path, init);
    },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js', 'router.js',
                   'components/conversation-list.js', 'components/resource-panel.js',
                   'modes/builder.js']) {
    vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
  }
  const SW = sandbox.SW;
  SW.Composer = function Composer() { return null; };
  SW.Message = function Message() { return null; };
  SW.TypingIndicator = function TypingIndicator() { return null; };
  SW.PlanSheet = function PlanSheet() { return null; };

  let dirty = true;
  SW.store.subscribe(() => { dirty = true; });
  SW.router.subscribe(() => { dirty = true; });

  let renders = 0;
  function render() {
    renders += 1;
    cursor = 0;
    pending.length = 0;
    const at = SW.router.get();
    // Exactly what `app.js` hands BuildMode, so the props are the route's rather than a step's.
    SW.BuildMode({ conversationId: at.a, appId: at.query.app || null });
    const queue = pending.slice();
    pending.length = 0;
    for (const fn of queue) {
      try {
        const off = fn();
        if (typeof off === 'function') cleanups.push(off);
      } catch (err) { /* a store read this step is not about */ }
    }
  }

  // Renders until nothing more changes, which is what "steady state" means. Bounded rather than
  // trusted: a tab that re-asserted its URL over the server would go round for ever, and the cap
  // turns that into a number the report carries instead of a hung test.
  async function settle(rounds = 40) {
    let used = 0;
    for (let i = 0; i < rounds; i += 1) {
      await new Promise((r) => setTimeout(r, 0));
      await new Promise((r) => setTimeout(r, 0));
      if (!dirty) break;
      dirty = false;
      used += 1;
      render();
    }
    return used;
  }

  return {
    name,
    SW,
    settle,
    // The 30-second tick, fired by hand. This is the only way a tab hears that the OTHER tab moved
    // the selection: `/apps` simply starts answering differently.
    async poll() {
      timers.filter((t) => t.ms === 30000).forEach((t) => t.fn());
      return settle();
    },
    go(path) { SW.router.go(path); },
    view() {
      const state = SW.store.get();
      return {
        tab: name,
        hash: current,
        app: (state.activeApp || {}).id || null,
        name: (state.activeApp || {}).name || null,
        thread: state.thread ? state.thread.id : null,
        renders,
        pollers: timers.filter((t) => t.ms === 30000).length,
      };
    },
    unmount() { cleanups.forEach((fn) => { try { fn(); } catch (err) { /* nothing */ } }); },
  };
}

const writes = (from = 0) => calls.slice(from).filter((c) => / POST \/apps\/[^/]+\/select$/.test(c));

// --- the run ---------------------------------------------------------------
const report = [];
for (const step of steps) {
  selected = step.selected || 'app_a';
  calls.length = 0;

  // Two tabs, two `?app=` values, one Project. Each seeds, then both poll — which is the shape the
  // ticket describes: whatever the second tab wrote, the first one's poll sees as drift.
  if (step.tabs) {
    const tabs = step.tabs.map((t, i) => makeTab(`t${i + 1}`, t));
    for (const tab of tabs) await tab.settle();
    const seeded = { selected, writes: writes(), views: tabs.map((t) => t.view()) };
    // Three ticks each, alternating. One tick could not tell a settled pair from a pair that had
    // not swapped back yet; the ping-pong takes a full round trip to show.
    const mark = calls.length;
    const rounds = [];
    for (let i = 0; i < (step.ticks || 3); i += 1) {
      for (const tab of tabs) await tab.poll();
      rounds.push({ selected, views: tabs.map((t) => t.view()) });
    }
    tabs.forEach((t) => t.unmount());
    report.push({
      step: `tabs ${step.tabs.join(' + ')}`,
      seeded,
      rounds,
      // The whole claim, in one number: what the ticks WROTE. A settled pair writes nothing.
      tickWrites: writes(mark),
      selected,
      views: tabs.map((t) => t.view()),
    });
    continue;
  }

  // One tab, and a selection moved out from under it by somebody else. `selected` changes with no
  // request, because that is what another tab's write looks like from here.
  if (step.moveTo) {
    const tab = makeTab('t1', step.at);
    await tab.settle();
    const before = tab.view();
    const mark = calls.length;
    selected = step.moveTo;
    await tab.poll();
    // A second tick, because "followed, not reverted" is a claim about what happens NEXT.
    await tab.poll();
    const after = tab.view();
    tab.unmount();
    report.push({
      step: `moveTo ${step.moveTo}`, before, after,
      tickWrites: writes(mark), calls: calls.slice(mark),
    });
    continue;
  }

  // The picker's path, which writes the ROUTE. The seed effect is what turns that into a
  // selection, so a seed that stopped listening to the URL would break the control.
  // Several actions against ONE tab, which every step above cannot do: each of them mounts its own
  // and throws it away, so a ref that survives a render — and the guard against a followed rewrite
  // asking for its own app back is one — is fresh for each. This is where a stale one would show.
  if (step.sequence) {
    const tab = makeTab('t1', step.at);
    await tab.settle();
    const acts = [];
    for (const act of step.sequence) {
      const mark = calls.length;
      if (act.moveTo) {
        selected = act.moveTo;
        await tab.poll();
        await tab.poll();
      } else {
        tab.go(act.pick);
        await tab.settle();
      }
      acts.push({ act: act.moveTo ? `moveTo ${act.moveTo}` : `pick ${act.pick}`,
                  writes: writes(mark), view: tab.view(), selected });
    }
    tab.unmount();
    report.push({ step: 'sequence', acts });
    continue;
  }

  if (step.pick) {
    const tab = makeTab('t1', step.at);
    await tab.settle();
    const before = tab.view();
    const mark = calls.length;
    tab.go(step.pick);
    await tab.settle();
    const after = tab.view();
    tab.unmount();
    report.push({ step: `pick ${step.pick}`, before, after, writes: writes(mark), selected });
    continue;
  }

  // A link naming NO app, which is the effect below the seed: it resolves the Conversation's bound
  // app once. Nothing here may pin that answer into the URL — the next person to open the link has
  // to get the resolution too.
  if (step.bare) {
    const tab = makeTab('t1', step.bare);
    await tab.settle();
    const settled = tab.view();
    const mark = calls.length;
    await tab.poll();
    await tab.poll();
    const after = tab.view();
    tab.unmount();
    report.push({
      step: `bare ${step.bare}`, settled, after,
      writes: writes(), tickWrites: writes(mark), selected,
    });
    continue;
  }

  throw new Error(`unknown step ${JSON.stringify(step)}`);
}
console.log(JSON.stringify(report));
