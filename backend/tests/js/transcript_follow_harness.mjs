// Where the Chat or Build scroller sits while a turn runs, for a reader who is following it and for
// one who has scrolled up to read something earlier, and whether Jump to latest is offered.
//
// The mode alone is invoked; the components it draws are left as elements, because the scroll
// effects live in the mode and nothing below it moves the scroller. Hooks hold across passes and
// effects run only when their deps change — the defect was an effect whose deps changed on every
// narration line, so a harness that ran every effect every pass could not tell it from the fix.
//
// The scroller is a plain object handed to the ref the mode drew, and the reader scrolls it by
// setting `scrollTop` and calling the `onScroll` the mode drew, which is what the browser does.
//
// Input on stdin: `{mode: 'chat' | 'build', acts}`. Every act reports where the scroller is
// afterwards and whether the button is drawn.
import fs from 'node:fs';
import vm from 'node:vm';
import { unrefTimeout } from './sandbox_timeout.mjs';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { mode, acts } = JSON.parse(fs.readFileSync(0, 'utf8'));

const hooks = [];
let hookIndex = 0;
const pendingEffects = [];

const React = {
  createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
  Fragment: 'Fragment',
  useState(init) {
    const i = hookIndex++;
    if (!hooks[i]) hooks[i] = { value: typeof init === 'function' ? init() : init };
    return [hooks[i].value, (next) => {
      hooks[i].value = typeof next === 'function' ? next(hooks[i].value) : next;
    }];
  },
  useRef(init) {
    const i = hookIndex++;
    if (!hooks[i]) hooks[i] = { current: init };
    return hooks[i];
  },
  useEffect(fn, deps) {
    const i = hookIndex++;
    const slot = hooks[i] || (hooks[i] = { deps: undefined, mounted: false });
    const changed = !slot.mounted || deps === undefined ||
      deps.length !== (slot.deps || []).length ||
      deps.some((d, n) => !Object.is(d, slot.deps[n]));
    slot.deps = deps;
    slot.mounted = true;
    if (changed) pendingEffects.push(fn);
  },
};

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, TextEncoder, TextDecoder, URL, URLSearchParams,
  encodeURIComponent, decodeURIComponent,
  setTimeout: unrefTimeout, clearTimeout, setInterval, clearInterval,
  requestAnimationFrame: (fn) => fn(),
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  location: { search: '', pathname: '/', href: 'http://localhost/', hash: '#/chat/conv_a' },
  history: { replaceState: () => {} },
  document: {
    title: '', documentElement: { style: { setProperty: () => {} } },
    addEventListener: () => {}, removeEventListener: () => {},
    querySelector: () => null, getElementById: () => ({}), body: {},
  },
  addEventListener: () => {},
  removeEventListener: () => {},
  React,
  antd: {
    Input: Object.assign(function Input() { return null; }, { TextArea: 'Input.TextArea' }),
    Button: 'Button', Dropdown: 'Dropdown', Tag: 'Tag', Tooltip: 'Tooltip', Space: 'Space',
    Skeleton: 'Skeleton', Modal: { confirm() {} },
    message: { success() {}, error() {}, info() {}, warning() {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  EventSource: function () {},
  fetch: async () => ({ ok: true, status: 200, headers: { get: () => 'application/json' },
                        json: async () => ({}), text: async () => '{}' }),
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'api.js', 'prefs.js', 'router.js', 'store.js',
                 'components/follow-latest.js', 'modes/chat.js', 'modes/builder.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

function walk(node, out = []) {
  if (!node || typeof node !== 'object') return out;
  if (Array.isArray(node)) { node.forEach((n) => walk(n, out)); return out; }
  out.push(node);
  (node.c || []).forEach((n) => walk(n, out));
  return out;
}

// What the browser would be holding: a box of fixed height over content that grows, which clamps
// `scrollTop` the way the browser does — `el.scrollTop = el.scrollHeight` lands on the bottom.
const el = {
  scrollHeight: 2000, clientHeight: 500, _top: 1500,
  get scrollTop() { return this._top; },
  set scrollTop(v) { this._top = Math.max(0, Math.min(v, this.scrollHeight - this.clientHeight)); },
};
let drawn = null;
let jumpButton = null;

// The two transcripts keep the same shape under different names. Build reads one list for what is
// on screen and another for the row being written; in a conversation with no Chat turns they hold
// the same rows.
const MODES = {
  chat: {
    render: () => SW.ChatMode({ threadId: SW.store.get().thread.id }),
    scroller: 'sw-messages sw-scroll',
    typing: 'typing',
    setMessages: (messages) => SW.store.set({ messages }),
    messages: () => SW.store.get().messages,
  },
  build: {
    render: () => SW.BuildMode({ conversationId: SW.store.get().thread.id, appId: 'app_a' }),
    scroller: 'sw-builder-chat-messages sw-scroll',
    typing: 'buildTyping',
    setMessages: (messages) => SW.store.set({ buildMessages: messages, buildTranscript: messages }),
    messages: () => SW.store.get().buildMessages,
  },
};
const view = MODES[mode];

function pass() {
  for (let i = 0; i < 5; i += 1) {
    hookIndex = 0;
    const nodes = walk(view.render());
    drawn = nodes.find((n) => n.p && n.p.className === view.scroller);
    if (!drawn) throw new Error('no scroller drawn');
    drawn.p.ref.current = el;
    jumpButton = walk(drawn).find((n) => n.t === SW.JumpToLatest) || null;
    const queued = pendingEffects.splice(0, pendingEffects.length);
    if (!queued.length) return;
    queued.forEach((fn) => fn());
  }
  throw new Error('effects did not settle');
}

const message = (id, role, value) => ({ id, role, blocks: [{ type: 'text', value }] });

SW.store.set({
  me: { id: 'u1', name: 'Dana Reed' },
  scope: { id: 'proj', name: 'Demo Project' },
  threads: [],
  apps: [{ id: 'app_a', name: 'App A' }],
  activeApp: { id: 'app_a', name: 'App A' },
  thread: { id: 'conv_a', title: 'A', history: [], touched: [] },
  [view.typing]: 'Thinking…',
});
view.setMessages([message('u0', 'user', 'first question'),
                  message('a0', 'assistant', 'first answer'),
                  message('u1', 'user', 'second question')]);
pass();

const report = [];
for (const act of acts) {
  // Whatever the act adds to the screen makes the content taller, as it would in the browser.
  if (act.act === 'narrate') {
    el.scrollHeight += 40;
    SW.store.set({ [view.typing]: act.text });
  } else if (act.act === 'answer') {
    el.scrollHeight += 200;
    const messages = view.messages();
    view.setMessages([...messages, message(`a${messages.length}`, 'assistant', act.text)]);
  } else if (act.act === 'stream') {
    el.scrollHeight += 40;
    const messages = view.messages().slice();
    const last = messages[messages.length - 1];
    messages[messages.length - 1] = { ...last, blocks: [{ type: 'text',
      value: `${last.blocks[0].value}${act.text}` }] };
    view.setMessages(messages);
  } else if (act.act === 'scroll') {
    el.scrollTop = act.to === 'bottom' ? el.scrollHeight - el.clientHeight : act.to;
    drawn.p.onScroll();
  } else if (act.act === 'jump') {
    if (!jumpButton) throw new Error('no Jump to latest button drawn');
    jumpButton.p.onClick();
  } else if (act.act === 'open') {
    el.scrollHeight = 3000;
    SW.store.set({ thread: { id: act.thread, title: act.thread, history: [], touched: [] } });
    view.setMessages([message('o0', 'user', 'other'), message('o1', 'assistant', 'other')]);
  } else {
    throw new Error(`unknown act ${JSON.stringify(act)}`);
  }
  pass();
  report.push({ act: act.act, scrollTop: el.scrollTop, bottom: el.scrollHeight - el.clientHeight,
                jumpButton: !!jumpButton });
}

console.log(JSON.stringify(report));
process.exit(0);
