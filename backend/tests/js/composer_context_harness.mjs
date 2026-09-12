// Renders the real Composer twice off one store — once with the props Chat mounts it with, once
// with Build's — and reports the chips each one drew after every step.
//
// Session context belongs to the Conversation, not to the mode and not to the Built App. That is
// true today because both modes mount the same component and it reads one list; nothing tested it.
// Reading the source proves the property for the code as written, which is exactly the guard that
// stops holding the moment someone makes the chips follow the app selector (#82).
//
// The stubs are the smallest set composer.js and store.js touch on this path. React is never
// rendered — the assertions are about the element tree the component returns. `fetch` is a small
// server that files context under a conversation id and knows no other key, so a composer that
// kept its own list, or asked per app, would show up as a row the server never had.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const steps = JSON.parse(fs.readFileSync(0, 'utf8'));

const CONVERSATION = 'conv_1';

// --- the server ------------------------------------------------------------
// One context per conversation. There is deliberately no way to ask for "the context of this app"
// or "of this mode": the route the Workbench calls does not carry either, and neither does this.
const contexts = new Map([[CONVERSATION, [
  { id: 'ctx_seed', kind: 'file', name: 'sales.csv', path: 'sales.csv',
    addedBy: 'user', resourceId: 'file:sales.csv' },
]]]);
let apps = [
  { id: 'app_alpha', name: 'Alpha', selected: true },
  { id: 'app_beta', name: 'Beta', selected: false },
];
let inflight = 0;
let nextId = 0;

const json = (body, status = 200) => ({
  ok: status < 400, status,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

function serve(url, options = {}) {
  const method = (options.method || 'GET').toUpperCase();
  const path = String(url).replace(/^\.\/api/, '');
  const rows = (id) => {
    if (!contexts.has(id)) contexts.set(id, []);
    return contexts.get(id);
  };
  let m;
  if ((m = path.match(/^\/threads\/([^/]+)\/context$/))) {
    if (method === 'GET') return json({ items: rows(m[1]) });
    if (method === 'POST') {
      const posted = JSON.parse(options.body);
      const row = { ...posted, id: `ctx_${++nextId}` };
      // What `add_thread_context` does with `inBuild` on a Dataset file: it runs `attach_file`
      // and records WHICH app took the bytes, so the chip's receipt is a fact on the row rather
      // than something re-derived from whatever is selected later (ADR-0048). Modelled here
      // because the mark is unreadable without it, and because `inBuild` is a membership-only
      // field the server never stores — the client must not be able to reconstruct it.
      if (posted.inBuild && posted.datasetId) {
        const selected = apps.find((a) => a.selected);
        if (selected) row.attachedApp = selected.id;
      }
      delete row.inBuild;
      rows(m[1]).push(row);
      return json(row);
    }
  }
  if ((m = path.match(/^\/threads\/([^/]+)\/context\/([^/]+)$/)) && method === 'DELETE') {
    contexts.set(m[1], rows(m[1]).filter((i) => i.id !== decodeURIComponent(m[2])));
    return { ok: true, status: 204, headers: { get: () => '' },
             json: async () => null, text: async () => '' };
  }
  if (path === '/apps') return json({ items: apps });
  if ((m = path.match(/^\/apps\/([^/]+)\/select$/)) && method === 'POST') {
    const wanted = decodeURIComponent(m[1]);
    apps = apps.map((a) => ({ ...a, selected: a.id === wanted }));
    return json({ ok: true });
  }
  return json({});
}

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, setTimeout, clearTimeout,
  requestAnimationFrame: (fn) => fn(),
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  React: {
    createElement: (t, p, ...c) => ({ t, p, c }),
    // One render per read, so the initial value is the value: nothing here presses a key or
    // opens a menu, and the chips do not come from component state anyway.
    useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
    useEffect: () => {},
    useRef: () => ({ current: null }),
    Fragment: 'Fragment',
  },
  antd: {
    Input: { TextArea: 'Input.TextArea' },
    Button: 'Button', Dropdown: 'Dropdown', Tag: 'Tag', Tooltip: 'Tooltip', Space: 'Space',
    message: { success() {}, error() {}, info() {}, warning() {} },
    Modal: { confirm() {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  // `router.js` is loaded below, so the sandbox owes it the three window surfaces it reads at load:
  // the hash it parses, the listener it registers for `hashchange`, and the history it writes
  // through. `replaceState` puts the value where `parse()` will read it, which is what makes
  // `SW.router.replace` work here — `go` does not, because nothing in a vm fires a hashchange.
  location: { hash: '#/chat' },
  addEventListener() {},
  removeEventListener() {},
  history: { replaceState(_state, _title, next) { sandbox.location.hash = next; } },
  fetch: async (url, options) => {
    inflight += 1;
    try {
      // A tick of latency, so a request in flight is visible to `settle` below rather than
      // finishing inside the call that made it.
      await new Promise((r) => setTimeout(r, 0));
      return serve(url, options);
    } finally {
      inflight -= 1;
    }
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
// `router.js` before `api.js`, which reads `SW.router.get().mode` to decide the `inBuild` flag on
// an add (#74). Missing, that read threw inside the promise `onDrop` hands to `.catch(sayFailed)` —
// so every drop in this harness failed silently and the three tests that drop saw an unchanged row.
for (const f of ['util.js', 'router.js', 'api.js', 'store.js', 'prefs.js',
                 'components/composer.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

// The two mounts, prop for prop as modes/chat.js:217 and modes/builder.js:187 write them.
const CHAT = { onSend() {}, placeholder: 'Ask about your data… use @ to bring in a resource',
               disabled: false };
const BUILD = { onSend() {}, placeholder: 'Describe a change, or ask about this app…',
                disabled: false, showMode: true, compact: true };
const mount = (where) => SW.Composer(where === 'build' ? BUILD : CHAT);

function* walk(node) {
  if (!node || typeof node !== 'object') return;
  if (Array.isArray(node)) {
    for (const child of node) yield* walk(child);
    return;
  }
  yield node;
  yield* walk(node.c);
}
const chipNodes = (tree) => [...walk(tree)].filter((n) => n.p && n.p.className === 'sw-chip');
const chipName = (node) =>
  (node.c || []).flat(Infinity).filter((c) => typeof c === 'string').join('');
const chips = (where) => chipNodes(mount(where)).map(chipName);
// The mark ADR-0048 puts on a chip whose click was an Attachment rather than a Session context
// row. Read off its own class rather than out of the chip's words, because `chipName` takes only
// the direct string children and every other reader here names a chip by that.
const chipMark = (node) => [...walk(node)]
  .filter((n) => n.p && n.p.className === 'sw-chip-scope')
  .map((n) => (n.c || []).flat(Infinity).filter((c) => typeof c === 'string').join(''))
  .join('');
const marks = (where) => chipNodes(mount(where)).map(chipMark);

// Handlers fire and forget; the store writes to the server and reads back. Wait for the traffic to
// stop rather than for a fixed number of ticks.
async function settle() {
  let idle = 0;
  for (let i = 0; i < 500 && idle < 8; i += 1) {
    await new Promise((r) => setTimeout(r, 0));
    idle = inflight === 0 ? idle + 1 : 0;
  }
}

function snapshot() {
  const state = SW.store.get();
  return {
    chat: chips('chat'),
    build: chips('build'),
    chatMarks: marks('chat'),
    buildMarks: marks('build'),
    server: (contexts.get(CONVERSATION) || []).map((i) => i.name),
    activeApp: state.activeApp ? state.activeApp.id : null,
  };
}

SW.store.set({
  thread: { id: CONVERSATION, artifacts: [] },
  scope: { id: 'proj', name: 'Demo Project' },
  messages: [],
});
// Opening a conversation is what loads its context, in either mode.
await SW.store.reloadAttachments();
await SW.store.loadApps();
await settle();

const report = [{ step: 'opened', ...snapshot() }];
for (const step of steps) {
  // The route follows the composer the step drives. There is one router and two mounts here, and
  // `addToConversation` reads the route rather than the mount to decide `inBuild` — so leaving it
  // on chat would have this harness modelling an act in the Build composer while the URL says chat,
  // which is a state the app never reaches.
  const on = (step.drop || step.closeChip || {}).on;
  if (on) SW.router.replace(`#/${on}`);
  if (step.drop) {
    // The composer's own drop target, in the named mode: the resource panel hands it an id and it
    // puts the resource into the conversation's context.
    const { on, resource } = step.drop;
    SW.store.get().resourceIndex[resource.id] = resource;
    const target = [...walk(mount(on))].find((n) => n.p && n.p.onDrop);
    target.p.onDrop({
      preventDefault() {},
      dataTransfer: { getData: (k) => (k === 'text/sw-resource' ? resource.id : ''), files: [] },
    });
  } else if (step.closeChip) {
    // The chip's own close button, in the named mode.
    const { on, name } = step.closeChip;
    const node = chipNodes(mount(on)).find((n) => chipName(n) === name);
    if (!node) throw new Error(`no chip named ${name} in the ${on} composer`);
    node.p.onClose({ preventDefault() {} });
  } else if (step.selectApp) {
    await SW.store.selectApp(step.selectApp);
  } else {
    throw new Error(`unknown step ${JSON.stringify(step)}`);
  }
  await settle();
  report.push({ step: JSON.stringify(step), ...snapshot() });
}
console.log(JSON.stringify(report));
