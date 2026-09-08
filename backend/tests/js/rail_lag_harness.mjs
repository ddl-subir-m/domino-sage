// What the rail says WHILE a build turn is running, not after it.
//
// Sibling of build_stream_harness, and it asks the one question that one cannot: that harness
// awaits the whole turn and reports what is left on screen, so every read the `finally` makes has
// already happened by the time it looks. The rail's complaint was about the minutes before that.
//
// The server names a Conversation off the first prompt at the TOP of the turn
// (`_name_conversation`, called from `build_stream` before the loop starts), and tells the client
// on the stream. So the question is whether a frame is enough to move the rail, and the only way
// to ask it is to look while the stream is still open.
//
// `/threads` answers with the PLACEHOLDER forever, deliberately. The rail can only learn the real
// name from the frame, so a pass cannot be bought by a round trip the fix is supposed to remove —
// and the fetch log is reported for the same reason.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;

const PLACEHOLDER = 'New conversation';
const NAMED = 'Add a date filter to the chart';

// Every path the client asked for, in order. The report needs it to tell a rail that learned the
// name from the frame from one that went and fetched it.
const fetched = [];

let midTurn = null;
let fetchedAtSnapshot = [];

const snapshot = (SW) => {
  const s = SW.store.get();
  return {
    railRow: (s.threads[0] || {}).title || '',
    openConversation: (s.thread || {}).title || '',
  };
};

const json = (body) => ({
  ok: true, status: 200,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

function sseResponse(frames, onFrame) {
  const enc = new TextEncoder();
  const chunks = frames.map((ev) => enc.encode(`data: ${JSON.stringify(ev)}\n\n`));
  let i = 0;
  return {
    ok: true, status: 200,
    headers: { get: () => 'text/event-stream' },
    body: {
      getReader: () => ({
        read: async () => {
          if (i >= chunks.length) return { done: true, value: undefined };
          const value = chunks[i];
          i += 1;
          // Straight after the naming frame, with the stream still open: give the client a beat to
          // react to it, then look. This is the moment the person is sitting and watching.
          //
          // `i === 3` and not 2: `i` counts chunks TAKEN, and this runs before the one just taken
          // is handed over. At 2 the naming frame is still in this function's hand, so a snapshot
          // there reports a rail that has not been told yet, and no fix can turn it green.
          if (i === 3) {
            for (let n = 0; n < 20; n += 1) await new Promise((r) => setTimeout(r, 0));
            onFrame();
          }
          return { done: false, value };
        },
      }),
    },
  };
}

// The naming frame sits second, where the server puts it: after the queue's own row and before any
// work, because the Conversation is named before the build loop is entered.
const FRAMES = [
  { type: 'pending', ticket: 'tkt_1' },
  { type: 'conversation_named', conversation: 'conv_1', title: NAMED },
  { type: 'status', value: 'Writing src/App.tsx' },
  { type: 'done' },
];

function serve(url) {
  const path = String(url).replace(/^\.\/api/, '');
  fetched.push(path);
  if (path.includes('health')) return json({ problems: [] });
  if (path.startsWith('/project/build/stream')) {
    return sseResponse(FRAMES, () => {
      midTurn = snapshot(sandbox.SW);
      fetchedAtSnapshot = fetched.slice();
    });
  }
  // A bare array, as `list_threads` returns one — `api.threads` does not unwrap `.items` the way
  // `api.apps` does. Still the placeholder, on purpose: see the head of this file.
  if (path.startsWith('/threads')) {
    return json([{ id: 'conv_1', title: PLACEHOLDER, updatedAt: '2026-09-08T10:00:00Z' }]);
  }
  if (path.startsWith('/apps')) return json({ items: [] });
  if (path.startsWith('/project/history')) return json({ history: [] });
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
  fetch: async (url) => {
    await new Promise((r) => setTimeout(r, 0));
    return serve(url);
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
  scope: { id: 'proj', name: 'Demo Project' },
  thread: { id: 'conv_1', title: PLACEHOLDER, artifacts: [] },
  threads: [{ id: 'conv_1', title: PLACEHOLDER, updatedAt: '2026-09-08T10:00:00Z' }],
  apps: [],
  activeApp: { id: 'app_a', name: 'Built App 1' },
});

await SW.store.sendBuildPrompt('Add a date filter to the chart');
await settle();

console.log(JSON.stringify({
  named: NAMED,
  placeholder: PLACEHOLDER,
  midTurn,
  afterTurn: snapshot(SW),
  threadReadsBeforeSnapshot: fetchedAtSnapshot.filter((p) => p.startsWith('/threads')).length,
}));
