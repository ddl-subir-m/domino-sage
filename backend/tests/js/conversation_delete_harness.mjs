// What the rail's Delete tells a person, and what it does when the save behind it does not land
// (ADR-0036).
//
// Two things the source will not show on its own. The dialog copy is a promise — "removed for
// good" — that only became true when `ThreadStore.delete` started purging, and the sentence about
// git history is the limit of that promise; both belong to the same act and neither is asserted
// anywhere else. And the failure is invisible by construction: the files are already gone locally,
// the rail redraws without the row, and a person reading that empty rail would conclude the delete
// finished. It did not reach the remote, so the next workspace start can bring it back.
//
// Input on stdin: `{ "saved": <the `saved` half of the DELETE answer, or null> }`.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { saved } = JSON.parse(fs.readFileSync(0, 'utf8'));

const THREAD = { id: 't-1', title: 'Desk exposure', updatedAt: '2026-09-01T00:00:00Z', touched: [] };

const confirms = [];
const errors = [];
const deleted = [];

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
    // The real Modal never renders here: the config it was handed IS the copy under test, and
    // calling onOk is what pressing the danger button does.
    Modal: { confirm: (cfg) => confirms.push(cfg) },
    message: {
      info: () => {}, success: () => {}, warning: () => {},
      error: (text) => errors.push(String(text)),
    },
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
// The answer the route now gives: the delete happened, and `saved` says whether git heard about it.
SW.api.deleteThread = (id) => { deleted.push(id); return Promise.resolve({ ok: true, saved }); };
SW.store.reloadThreads = () => {};
SW.store.clearConversation = () => {};

SW.conversationMenu(THREAD).onClick({ key: 'delete', domEvent: { stopPropagation: () => {} } });
const dialog = confirms[0] || {};
await (dialog.onOk ? dialog.onOk() : Promise.resolve());

console.log(JSON.stringify({
  title: dialog.title || '',
  content: dialog.content || '',
  okText: dialog.okText || '',
  danger: !!(dialog.okButtonProps && dialog.okButtonProps.danger),
  deleted,
  errors,
}));
