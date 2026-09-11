// What a person on BUILD is left looking at after they click the guardrail card.
//
// Sibling of `chat_withhold_card_harness.mjs`, and the twin that was missing when this shipped.
// That one replays a Chat transcript; the branch it covers decides what a block IS. This one runs
// the whole click: a live refused turn arrives over SSE, the person presses the button, and the
// question is what CHANGES on screen. Nothing in the Chat harness could have caught the defect —
// `store.withholdContent` filtered `state.messages`, which Build does not draw, so the door was
// called, the row was written, and the transcript came back identical. The buttons looked dead.
//
// It goes through `sendBuildPrompt` and `readSSE` rather than `loadBuild` alone because LIVENESS is
// half the bug: `rememberLiveCard` keeps a `withhold-found` key for the life of the conversation,
// so a card answered once stays answerable until something forgets it.
//
// The fake server behaves like the real one in the single way that matters here: the withhold door
// APPENDS a `recall-withheld` row to the transcript, so the re-read after the click sees what the
// orchestrator would have written.
//
// Input on stdin: `{history, events, act}` — the transcript the turn starts from, the frames the
// refused turn sends, and which button to press ("withhold", "dismiss", or nothing).
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { history = [], events = [], act = '' } = JSON.parse(fs.readFileSync(0, 'utf8'));

const PROMPT = 'chart weekly panel spend';

const posted = [];
let served = history.slice();

const json = (body) => ({
  ok: true, status: 200,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

function sseResponse(frames) {
  const enc = new TextEncoder();
  const chunks = frames.map((ev) => enc.encode(`data: ${JSON.stringify(ev)}\n\n`));
  let i = 0;
  return {
    ok: true, status: 200,
    headers: { get: () => 'text/event-stream' },
    body: {
      getReader: () => ({
        read: async () => (i < chunks.length
          ? { done: false, value: chunks[i++] }
          : { done: true, value: undefined }),
      }),
    },
  };
}

function serve(url, opts) {
  const path = String(url).replace(/^\.\/api/, '');
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  if (opts && opts.method === 'POST') posted.push({ path, body });
  if (path === '/project/recall/withhold') {
    // What the orchestrator does: one row, fingerprints and names, never the refused text.
    served = served.concat([{ type: 'recall-withheld', keys: body.keys, labels: body.labels }]);
    return json({ type: 'recall-withheld', keys: body.keys, labels: body.labels });
  }
  if (path.includes('health')) return json({ problems: [] });
  if (path.startsWith('/project/build/stream')) return sseResponse(events);
  if (path.startsWith('/project/history')) return json({ history: served });
  if (path.startsWith('/apps')) return json({ items: [] });
  if (path.startsWith('/bindings')) return json({ bindings: [] });
  return json({});
}

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, TextEncoder, TextDecoder,
  setTimeout, clearTimeout, setInterval, clearInterval,
  encodeURIComponent, decodeURIComponent, URLSearchParams,
  requestAnimationFrame: (fn) => fn(),
  localStorage: (() => {
    const backing = new Map();
    return {
      getItem: (k) => (backing.has(k) ? backing.get(k) : null),
      setItem: (k, v) => backing.set(k, String(v)),
      removeItem: (k) => backing.delete(k),
    };
  })(),
  location: { search: '', pathname: '/', href: 'http://localhost/', hash: '#/build' },
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  addEventListener() {}, removeEventListener() {},
  React: {
    createElement: (t, p, ...c) => ({ t, p, c }),
    useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
    useEffect: () => {}, useRef: () => ({ current: null }), Fragment: 'Fragment',
  },
  antd: { message: { success() {}, info() {}, warning() {}, error() {} } },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async (url, opts) => {
    await new Promise((r) => setTimeout(r, 0));
    return serve(url, opts);
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

async function settle() {
  for (let i = 0; i < 60; i += 1) await new Promise((r) => setTimeout(r, 0));
}

SW.store.set({
  thread: { id: 'conv_1', title: 'The desk talk', artifacts: [] },
  scope: { id: 'proj', name: 'Demo Project' },
  threads: [],
  activeApp: { id: 'app_a' },
});

await SW.store.loadBuild();
await settle();

// The refused turn itself, live — which is what marks the card answerable.
await SW.store.sendBuildPrompt(PROMPT);
await settle();

// What the orchestrator persists for that turn, so the re-read after a click sees a reload's
// transcript rather than an empty one. All four of these are in `_PERSISTED_EVENTS`, and the card
// surviving the reload is the whole reason the live key has to be forgotten by hand.
served = served.concat([{ type: 'user', text: PROMPT }], events);

const blocks = () => SW.store.get().buildMessages.flatMap((m) => m.blocks || []);
const cards = (bs) => bs.filter((b) => b.type === 'withhold').map((b) => ({
  searching: !!b.searching,
  live: !!b.live,
  labels: (b.carriers || []).map((c) => c.label),
  surviving: b.surviving,
  surface: b.surface,
}));

const before = blocks();
posted.length = 0;

if (act === 'withhold') {
  const card = before.find((b) => b.type === 'withhold');
  await SW.store.withholdContent(card);
} else if (act === 'dismiss') {
  SW.store.dismissWithholdCard(before.find((b) => b.type === 'withhold'));
  // And then the poll tick. Build rebuilds its whole transcript from `buildHistory` every two
  // seconds, so a dismissal that only filters the drawn list lasts until the next one — which is
  // the defect `dismissBuildRecallOffer` was written to fix for the rung above this.
  await SW.store.loadBuild();
}
await settle();

const after = blocks();
console.log(JSON.stringify({
  before: { types: before.map((b) => b.type), cards: cards(before) },
  after: {
    types: after.map((b) => b.type),
    cards: cards(after),
    withheld: after.filter((b) => b.type === 'recall_withheld')
      .map((b) => ({ labels: b.labels })),
    prompts: after.filter((b) => b.type === 'text').map((b) => b.value),
  },
  posted,
}));
