// Drives the real store, the real chip and the real drawer against a fake /api/health, and reports
// what a person would have seen (ADR-0027).
//
// Reading the source cannot answer any of the five questions here. Whether the chip is absent, what
// the toast said, which group a Problem landed in and whether anything went grey are all facts
// about the tree the browser builds, and three of them are facts about a SEQUENCE — the toast fires
// once per Problem per session, so the second Preflight is the interesting one.
//
// Input on stdin: a list of steps, each `{ "problems": [...] }` — what /api/health answers for that
// Preflight. `store.refreshProblems()` is called once per step, which is one Preflight.
//
// Nothing is mounted. `createElement` is stubbed to a plain object and every function component on
// the way down is called, so a Select's `disabled` and a Tooltip's `title` are settled here the way
// they would be on screen; mounting would test antd instead.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const steps = JSON.parse(fs.readFileSync(0, 'utf8'));

let answer = { problems: [] };
let healthCalls = 0;
const said = [];

const json = (body) => ({
  ok: true, status: 200,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, encodeURIComponent, decodeURIComponent, URLSearchParams,
  setTimeout, clearTimeout, setInterval: () => 1, clearInterval: () => {},
  requestAnimationFrame: (fn) => fn(),
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  location: { search: '', pathname: '/', href: 'http://localhost/', hash: '#/chat' },
  addEventListener() {}, removeEventListener() {},
  document: {
    title: '', addEventListener() {}, removeEventListener() {},
    querySelector: () => null, getElementById: () => null, body: {},
    documentElement: { style: { setProperty() {} } },
  },
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
    useEffect: () => {},
    useMemo: (fn) => fn(),
    useCallback: (fn) => fn,
    useRef: () => ({ current: null }),
    Fragment: 'Fragment',
  },
  // Every antd component stands in for itself by name, so a control the shell reaches for is never
  // the reason a prop goes unread. `message` is real: a toast is the thing under test.
  antd: new Proxy({
    Modal: { confirm: () => ({ update() {}, destroy() {} }) },
    message: {
      info: (t) => said.push(String(t)), success: (t) => said.push(String(t)),
      error: (t) => said.push(String(t)), warning: (t) => said.push(String(t)),
    },
  }, {
    get: (target, name) => (name in target ? target[name] : String(name)),
    has: () => true,
  }),
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  EventSource: function () {},
  fetch: async (url) => {
    const path = String(url).split('?')[0].replace(/^\.\/api/, '');
    if (path === '/health') { healthCalls += 1; return json(answer); }
    if (path === './healthz') return json({ ok: true, open_weight_models: [] });
    return json({});
  },
  SW: {},
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
// The page's own order (index.html): platform-error before problems, because every Problem is
// drawn through that block.
for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js',
                 'components/platform-error.js', 'components/problems.js',
                 'components/shell.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

// Call every function component on the way down, so a word or a prop that only exists inside
// TopNav or inside PlatformError is still read.
function render(node, depth = 0) {
  if (!node || typeof node !== 'object') return node;
  if (Array.isArray(node)) return node.map((child) => render(child, depth));
  if (typeof node.t === 'function') {
    if (depth > 8) return node;
    try {
      return render(node.t(Object.assign({}, node.p, { children: node.c })), depth + 1);
    } catch (e) {
      return { t: node.t.name, p: node.p, c: [] };
    }
  }
  return { t: node.t, p: node.p, c: (node.c || []).map((child) => render(child, depth + 1)) };
}

function* walk(node) {
  if (!node || typeof node !== 'object') return;
  if (Array.isArray(node)) { for (const c of node) yield* walk(c); return; }
  yield node;
  yield* walk(node.c);
}
const all = (tree, pred) => [...walk(tree)].filter(pred);
const strings = (node) => [...walk(node)]
  .flatMap((n) => (n.c || []).flat(Infinity).filter((c) => typeof c === 'string'));

const settle = async () => { for (let i = 0; i < 20; i += 1) await new Promise((r) => setTimeout(r, 0)); };

SW.store.set({ me: { id: 'u1', name: 'Dana Reed' }, scope: { id: 'p', name: 'P' } });

// The chip, read off the tree the shell actually builds rather than off ProblemChip directly: where
// it sits is half the decision, so the harness has to be able to see the row it sits in.
function chipOf(shell) {
  const found = all(shell, (n) => n.p && String(n.p.className || '').includes('sw-problem-chip'));
  if (!found.length) return null;
  const button = found[0];
  // Its tooltip is the parent, so it is found by looking for the one holding this button.
  const tip = all(shell, (n) => n.t === 'Tooltip'
    && [...walk(n.c)].some((c) => c === button));
  return {
    ariaLabel: button.p['aria-label'],
    className: button.p.className,
    tooltip: tip.length ? tip[0].p.title : null,
    // Which row it landed in, and where in that row. Row 1 is `sw-topnav`, and ADR-0027 puts this
    // with the account controls rather than under Row 2's project scope.
    row: all(shell, (n) => n.p && n.p.className === 'sw-topnav'
      && [...walk(n.c)].some((c) => c === button)).length ? 'topnav' : 'subnav',
    // What it sits after in that row, so "with the account controls" is checked rather than assumed.
    rightOf: (() => {
      const row = all(shell, (n) => n.p && n.p.className === 'sw-topnav')[0];
      if (!row) return null;
      const flat = (row.c || []).flat(Infinity).filter(Boolean);
      const at = flat.indexOf(button);
      // The chip is wrapped in its Tooltip, so find the wrapper's index instead.
      const wrapper = flat.findIndex((n) => n && typeof n === 'object'
        && [...walk(n)].some((c) => c === button));
      const before = flat.slice(0, at >= 0 ? at : wrapper)
        .filter((n) => n && typeof n === 'object');
      const last = before[before.length - 1];
      return last && last.p ? (last.p.className || last.t) : null;
    })(),
  };
}

// Everything that would be greyed out, anywhere in the shell. `disabled` on a control and
// `aria-disabled` on anything that is not one, because ADR-0027's rule is that NOTHING gates on a
// Problem and a rule about one attribute would be a rule about one component library.
const disabledIn = (tree) => all(tree, (n) => n.p
  && (n.p.disabled === true || n.p['aria-disabled'] === true || n.p['aria-disabled'] === 'true'))
  .map((n) => n.p['aria-label'] || n.p.id || n.p.className || String(n.t));

const report = [];
for (const step of steps) {
  answer = { problems: step.problems || [] };
  said.length = 0;
  await SW.store.refreshProblems();
  await settle();

  const shell = render(SW.Shell({ mode: 'chat', route: { mode: 'chat' }, children: null }));
  SW.store.openProblems(true);
  const drawer = render(SW.ProblemsDrawer());

  report.push({
    step: JSON.stringify(step),
    healthCalls,
    chip: chipOf(shell),
    // Every sentence a toast said during THIS Preflight. Empty is the answer that matters most:
    // the second sighting of the same Problem must say nothing.
    toasts: said.slice(),
    disabled: disabledIn(shell),
    // Every string Row 1 drew. The drawer is mounted inside the shell, so the WHOLE tree holds a
    // Problem's sentences by design — the row is the scope where they must not appear, because a
    // chip that carried its own text would be the content ADR-0011 keeps out of a corner.
    // Props as well as children: a tooltip and an `aria-label` are sentences a person reads, and
    // both of them live in a prop rather than in the tree under it.
    topnavWords: all(shell, (n) => n.p && n.p.className === 'sw-topnav')
      .flatMap((row) => strings(row).concat(
        all(row, (n) => n.p).flatMap((n) => [n.p.title, n.p['aria-label']])
      ))
      .filter((w) => typeof w === 'string'),
    drawer: {
      open: all(drawer, (n) => n.t === 'Drawer').map((d) => d.p.open)[0],
      title: all(drawer, (n) => n.t === 'Drawer').map((d) => d.p.title)[0],
      // Group headings in the order they are drawn, and what landed under each.
      groups: all(drawer, (n) => n.p && n.p.className === 'sw-problems-group').map((g) => ({
        title: all(g, (n) => n.p && n.p.className === 'sw-problems-group-title')
          .flatMap((t) => (t.c || []).flat(Infinity).filter((c) => typeof c === 'string')).join(''),
        // Sage's own sentences, which are outside the quotation.
        said: all(g, (n) => n.p && String(n.p.className || '').includes('sw-platform-error-say'))
          .flatMap((n) => (n.c || []).flat(Infinity).filter((c) => typeof c === 'string')),
        // The platform's own words, which are inside it.
        quoted: all(g, (n) => n.p && n.p.className === 'sw-passthrough')
          .flatMap((n) => strings(n)),
      })),
      empty: all(drawer, (n) => n.p && n.p.className === 'sw-problems-empty')
        .flatMap((n) => (n.c || []).flat(Infinity).filter((c) => typeof c === 'string')),
    },
  });
  SW.store.openProblems(false);
}
console.log(JSON.stringify(report));
