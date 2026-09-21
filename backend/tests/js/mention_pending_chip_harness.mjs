// A chip is drawn the moment it is picked, and a second pick during the wait is not a second POST.
//
// `store.attach` used to append the chip when the POST answered. For a table chip that POST took
// as long as the warehouse took to describe the table, and `addToContext`'s only duplicate guard
// reads the chip list — empty until then — so picking the same row again during the wait was a
// second POST, and two chips landed. The placeholder pushed before the POST is both the feedback
// and the guard.
//
// Driven through the real store rather than a rendered tree, because the claim is about which
// requests the store issues and in what order — the same seam `chat_attach_lock_harness` uses.
//
// Input on stdin: `{ "act": "pick-twice" | "pick-fails" | "send-waits" | "remove-pending"
//                   | "undo-not-acked" | "switch-mid-post" | "pick-unknown" }`.

import fs from 'node:fs';
import vm from 'node:vm';
import { unrefTimeout } from './sandbox_timeout.mjs';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { act } = JSON.parse(fs.readFileSync(0, 'utf8'));

const TABLE = {
  id: 'table:ds-dwh:DWH.MARTS.MIXPANEL__EVENT',
  name: 'MIXPANEL__EVENT',
  kind: 'table',
  bindingKey: ['data_source', 'ds-dwh'],
  scope: { database: 'DWH', schema: 'MARTS', table: 'MIXPANEL__EVENT' },
};

// The attach POST is held open until `release()` — the wait the symptom lives in.
let release = null;
const held = new Promise((r) => { release = r; });

const requests = [];
let context = [];
// Snapshots of the chip list, one per request, so the order "chip first, POST second" is
// something the harness measured and not something it assumed.
const chipsAtRequest = [];

function answer(path, init) {
  const method = (init && init.method) || 'GET';
  if (/\/api\/threads\/[^/]+\/context$/.test(path) && method === 'POST') {
    const row = JSON.parse(init.body);
    context.push({ ...row, id: `att_${context.length + 1}` });
    return { id: `att_${context.length}`, name: row.name, addedBy: 'user', resourceId: row.resourceId,
             scope: row.scope };
  }
  if (/\/api\/threads\/[^/]+\/context$/.test(path)) return { items: context };
  if (/\/api\/threads\/[^/]+\/context\/[^/]+$/.test(path) && method === 'DELETE') {
    const id = path.split('/').pop();
    context = context.filter((row) => row.id !== id);
    return { removed: true };
  }
  if (path.endsWith('/api/project/resources/remove') && method === 'POST') return { ok: true };
  if (path.endsWith('/api/threads') && method === 'POST') return { id: 'thr_new' };
  if (path.endsWith('/api/project')) return { scratch: [], attached: [] };
  if (path.endsWith('/api/apps')) return { apps: [] };
  if (path.endsWith('/api/threads')) return { threads: [] };
  return {};
}

// One SSE frame and then the end, so `readSSE` returns and `sendMessage` settles.
function streamBody() {
  const bytes = new TextEncoder().encode('data: {"type":"done"}\n\n');
  let sent = false;
  return {
    getReader: () => ({
      read: () => Promise.resolve(sent ? { done: true } : (sent = true, { done: false, value: bytes })),
    }),
  };
}

const sandbox = {
  console, JSON, Object, String, Array, Error, Map, Set, Promise, Date, Math, Number, Boolean,
  RegExp, encodeURIComponent, decodeURIComponent, URL, URLSearchParams,
  setTimeout: unrefTimeout, clearTimeout, setInterval, clearInterval,
  TextEncoder, TextDecoder, Blob, ArrayBuffer, Uint8Array, FormData,
  fetch: async (url, init) => {
    const path = url.split('?')[0];
    const method = (init && init.method) || 'GET';
    requests.push(`${method} ${path}`);
    chipsAtRequest.push(SW.store.get().attachments.map((a) => ({ id: a.id, pending: !!a.pending })));
    const isAttach = /\/api\/threads\/[^/]+\/context$/.test(path) && method === 'POST';
    if (isAttach) {
      await held;
      if (act === 'pick-fails') throw new Error('the store refused the chip');
    }
    if (path.endsWith('/chat/stream')) {
      return { ok: true, status: 200, statusText: 'OK', headers: { get: () => 'text/event-stream' },
               body: streamBody(), json: () => Promise.resolve({}) };
    }
    const body = answer(path, init);
    return { ok: true, status: 200, statusText: 'OK', headers: { get: () => 'application/json' },
             json: () => Promise.resolve(body) };
  },
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  document: {
    title: '', documentElement: { style: { setProperty: () => {} } },
    addEventListener: () => {}, removeEventListener: () => {}, getElementById: () => ({}),
  },
  location: { search: '', pathname: '/', href: 'http://localhost/', hash: '#/chat' },
  addEventListener: () => {}, removeEventListener: () => {},
  React: { createElement: () => ({}) },
  EventSource: function () {},
  antd: {
    message: { success: () => {}, error: () => {}, info: () => {}, warning: () => {} },
    Modal: { confirm: () => {} },
  },
  SW: {},
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'prefs.js', 'router.js', 'store.js', 'api.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

const tick = async () => { for (let i = 0; i < 20; i += 1) await new Promise((r) => setTimeout(r, 0)); };

SW.store.set({
  scope: { id: 'p1', name: 'quick-start' },
  ready: true,
  thread: { id: 'thr_a' },
  messages: [],
  attachments: [],
  sensitivity: { enabled: false, locked: false, approved: [], datasets: [], group: '', reason: '' },
  resourceIndex: { [TABLE.id]: TABLE },
  resourceGroups: { table: [TABLE] },
});

const chips = () => SW.store.get().attachments.map((a) => ({ id: a.id, pending: !!a.pending,
                                                             name: a.resourceName }));
const out = { act, requests, chipsAtRequest };

if (act === 'pick-twice' || act === 'pick-fails') {
  const first = SW.store.addToContext(TABLE, { quiet: true });
  await tick();
  out.duringWait = chips();
  // The second pick, taken while the first POST is still out. Not settled in between: the whole
  // point is that the server has not answered yet.
  const second = SW.store.addToContext(TABLE, { quiet: true });
  await tick();
  out.postsDuringWait = requests.filter((r) => r.startsWith('POST') && r.endsWith('/context')).length;
  release();
  const settled = await Promise.allSettled([first, second]);
  await tick();
  out.outcomes = settled.map((s) => s.status);
  out.after = chips();
}

if (act === 'send-waits') {
  const pick = SW.store.addToContext(TABLE, { quiet: true });
  await tick();
  const send = SW.store.sendMessage('what is in @MIXPANEL__EVENT', { echo: false });
  await tick();
  // Measured while the attach is still held: the turn must not have gone yet.
  out.turnPostsWhileHeld = requests.filter((r) => r.endsWith('/chat/stream')).length;
  release();
  await Promise.allSettled([pick, send]);
  await tick();
  out.after = chips();
}

if (act === 'remove-pending') {
  const pick = SW.store.addToContext(TABLE, { quiet: true });
  await tick();
  out.duringWait = chips();
  // The undo, taken while the POST is still out: the chip has no server id yet.
  const undo = SW.store.removeResourceFromConversation(TABLE.id);
  await tick();
  out.deletesWhileHeld = requests.filter((r) => r.startsWith('DELETE')).length;
  release();
  await Promise.allSettled([pick, undo]);
  await tick();
  out.after = chips();
  out.serverRows = context.length;
}

if (act === 'undo-not-acked') {
  // A transcript with a line in it, so `acknowledgeAttachment` would draw its receipt.
  SW.store.set({ messages: [{ id: 'u_1', role: 'user', at: '', blocks: [{ type: 'text', value: 'hi' }] }] });
  const pick = SW.store.addToContext(TABLE);   // not quiet: the receipt is the thing measured
  await tick();
  const undo = SW.store.removeResourceFromConversation(TABLE.id);
  await tick();
  release();
  await Promise.allSettled([pick, undo]);
  await tick();
  out.after = chips();
  out.receipts = SW.store.get().messages
    .filter((m) => m.role === 'system')
    .map((m) => (m.blocks || []).map((b) => b.value).join(' '));
}

if (act === 'switch-mid-post') {
  const pick = SW.store.addToContext(TABLE, { quiet: true });
  await tick();
  // Another conversation opened while the POST is still out; its list starts empty.
  SW.store.set({ thread: { id: 'thr_b' }, attachments: [] });
  release();
  await Promise.allSettled([pick]);
  await tick();
  out.after = chips();
}

if (act === 'pick-unknown') {
  // A Resource the panel has not indexed — what a Sage-added row looks like to `attach`.
  const unknown = { ...TABLE, id: 'table:ds-dwh:DWH.MARTS.NOBODY_LISTED' };
  const pick = SW.store.attach(unknown.id, 'sage', 'picked for you.', { silent: true });
  await tick();
  out.duringWait = chips();
  release();
  await Promise.allSettled([pick]);
  await tick();
  out.after = chips();
}

out.posts = requests.filter((r) => r.startsWith('POST') && r.endsWith('/context')).length;
out.deletes = requests.filter((r) => r.startsWith('DELETE'));
console.log(JSON.stringify(out));
