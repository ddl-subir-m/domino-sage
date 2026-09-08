// What ⏎ does in a rename box.
//
// Escape has always closed these boxes — antd binds it — and ⏎ did nothing, because an Input
// outside a <form> does nothing with the key and antd's own confirm never looks at it. The box is
// one field and one word, so the mouse trip to the button was the whole cost.
//
// Three claims, none of them greppable. The field carries a handler at all AND opens focused, which
// are one behaviour: a binding on a field nobody is typing in is not a binding. ⏎ runs the same save
// the button runs, rather than a second copy of it that could drift. And a save that does not land
// holds the box open, so the typed name is still there to try again — the helper closes the box
// itself, so this is the one thing it could get wrong that antd used to get right.
//
// `conversation_delete_harness` is the prior art: nothing is mounted, and the config handed to
// `Modal.confirm` IS what is under test. The Conversation's box stands for all three — the Built
// App's and the plan's go through the same `SW.util.confirmOnEnter`.
//
// Input on stdin: `{ "act": "enter" | "button" | "enter-refused" }`.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { act } = JSON.parse(fs.readFileSync(0, 'utf8'));

const THREAD = { id: 't-1', title: 'Desk exposure', updatedAt: '2026-09-01T00:00:00Z', touched: [] };
const TYPED = 'Desk exposure by region';

const confirms = [];
const destroyed = [];
const patched = [];

const sandbox = {
  console, JSON, Object, String, Array, Error, Map, Set, Promise, Date, Math, Number, Boolean,
  RegExp, encodeURIComponent, decodeURIComponent, setTimeout, clearTimeout, setInterval,
  clearInterval, URLSearchParams, TextEncoder, TextDecoder, URL,
  fetch: () => Promise.reject(new Error('the harness makes no requests')),
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  document: {
    title: '', documentElement: { style: { setProperty: () => {} } },
    addEventListener: () => {}, removeEventListener: () => {}, getElementById: () => ({}),
  },
  location: { search: '', pathname: '/', href: 'http://localhost/', hash: '#/chat' },
  addEventListener: () => {}, removeEventListener: () => {},
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
    useEffect: () => {},
    useRef: () => ({ current: null }),
    Fragment: 'Fragment',
  },
  antd: {
    Tooltip: 'Tooltip', Button: 'Button', Input: 'Input', Dropdown: 'Dropdown',
    // The instance antd hands back is the only way a box can be closed from the outside, which is
    // what pressing ⏎ has to do and what pressing the button never had to.
    Modal: {
      confirm: (cfg) => {
        confirms.push(cfg);
        const instance = { destroy: () => destroyed.push(cfg.title) };
        return instance;
      },
    },
    message: { info: () => {}, success: () => {}, warning: () => {}, error: () => {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  EventSource: function () {},
  SW: {},
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'prefs.js', 'api.js', 'router.js', 'store.js',
                 'components/conversation-list.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

SW.store.set({ me: { id: 'u1', name: 'Dana Reed' }, threads: [THREAD], apps: [], thread: THREAD });
SW.api.patchThread = (id, body) => {
  patched.push(body.title);
  return act === 'enter-refused'
    ? Promise.reject(new Error('the save did not land'))
    : Promise.resolve({ ok: true });
};
SW.store.reloadThreads = () => {};

SW.conversationMenu(THREAD).onClick({ key: 'rename', domEvent: { stopPropagation: () => {} } });
const dialog = confirms[0] || {};
const field = dialog.content || { p: {} };

// Typing, then the key or the button. The typed name is what tells the two apart from a box that
// merely closed: a ⏎ that saved the name it opened with saved nothing.
if (field.p.onChange) field.p.onChange({ target: { value: TYPED } });
if (act === 'button') {
  await dialog.onOk().catch(() => {});
} else if (field.p.onPressEnter) {
  field.p.onPressEnter();
}
// The helper saves through promises, so let them run out before reading what happened.
for (let i = 0; i < 5; i += 1) await new Promise((r) => setTimeout(r, 0));

console.log(JSON.stringify({
  title: dialog.title || '',
  okText: dialog.okText || '',
  field: field.t || '',
  bound: typeof field.p.onPressEnter === 'function',
  focused: !!field.p.autoFocus,
  patched,
  destroyed,
}));
