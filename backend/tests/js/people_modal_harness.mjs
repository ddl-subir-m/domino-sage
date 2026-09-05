// What the People modal and the collaborator stack put on screen, given a `/api/members` answer.
//
// Read off the rendered tree rather than grepped out of the source, for ADR-0014's reason: a grep
// cannot tell our word from a code comment, and once a word is a `{token}` there is nothing in the
// source to grep for at all.
//
// Nothing is mounted. `createElement` is stubbed to a plain object, so calling the component
// returns a tree of data — mounting would test antd rather than the branch under test.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const spec = JSON.parse(fs.readFileSync(0, 'utf8'));

// One render's hook state. `render()` below rewinds the cursor and replays the effects, so a
// close and a reopen are two renders over one persisting set of slots — the same thing React does.
const slots = [];
let cursor = 0;
let effects = [];

const sandbox = {
  console, JSON, Object, String, Array, Error, Map, Set, Promise, Date, Math, Number, Boolean,
  RegExp, encodeURIComponent, decodeURIComponent, setTimeout, clearTimeout, setInterval,
  clearInterval, URLSearchParams,
  fetch: () => Promise.reject(new Error('the harness makes no requests')),
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  document: {
    title: '',
    documentElement: { style: { setProperty: () => {} } },
    addEventListener: () => {},
    removeEventListener: () => {},
  },
  location: { search: '', pathname: '/', href: 'http://localhost/', hash: '' },
  addEventListener: () => {},
  removeEventListener: () => {},
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    // The initial value, so `picked` is the empty selection a modal opens on.
    // State is held per call slot and survives a re-render, which is the whole point of the claim
    // about `picked`: the modal stays mounted across a close, so React keeps what was picked.
    useState: (init) => {
      const slot = slots.length > cursor ? slots[cursor] : (slots[cursor] = {
        v: typeof init === 'function' ? init() : init,
      });
      const i = cursor++;
      return [slot.v, (next) => { slots[i].v = typeof next === 'function' ? next(slot.v) : next; }];
    },
    // Run effects, rather than skipping them: the reset on close IS an effect, and a harness that
    // ignored effects would report the bug as fixed whatever the code did.
    useEffect: (fn) => { effects.push(fn); },
    useMemo: (fn) => fn(),
  },
  antd: {
    Modal: 'Modal',
    Input: { TextArea: 'TextArea' },
    Select: 'Select',
    Button: 'Button',
    Tooltip: 'Tooltip',
    Popover: 'Popover',
    Empty: { PRESENTED_IMAGE_SIMPLE: 'PRESENTED_IMAGE_SIMPLE' },
    Badge: 'Badge',
    Alert: 'Alert',
    message: { success: () => {}, error: () => {} },
  },
  icons: { UserAddOutlined: 'UserAddOutlined', BellOutlined: 'BellOutlined' },
  EventSource: {},
  SW: {},
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

for (const f of ['store.js', 'components/collab.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
// util.js is not loaded — it pulls in more of the shell than this branch needs, and an avatar is a
// picture with no words in it.
sandbox.SW.Avatar = function Avatar() { return null; };

sandbox.SW.store.set({
  peopleOpen: true,
  scope: { id: 'p1', name: 'quick-start' },
  members: spec.members || [],
  directory: spec.directory || [],
  ownerId: spec.ownerId || '',
  selfId: spec.selfId || '',
  membersConnected: spec.connected === true,
  membersError: spec.error || '',
  membersLoading: spec.loading === true,
});

// Every string in the tree, props included: a caption, an okText and a placeholder are props rather
// than children, and each of them is a line somebody reads.
function stringsIn(node, out = []) {
  if (typeof node === 'string') out.push(node);
  else if (Array.isArray(node)) node.forEach((n) => stringsIn(n, out));
  else if (node && typeof node === 'object') {
    Object.values(node.p || {}).forEach((v) => stringsIn(v, out));
    (node.c || []).forEach((n) => stringsIn(n, out));
  }
  return out;
}

// A Remove button is a Button whose children say Remove. Counting them is how "the owner's row
// offers no Remove" is checked without mounting anything.
function removeButtons(node, out = []) {
  if (Array.isArray(node)) node.forEach((n) => removeButtons(n, out));
  else if (node && typeof node === 'object') {
    if (node.t === 'Button' && stringsIn(node.c).includes('Remove')) out.push(node);
    Object.values(node.p || {}).forEach((v) => removeButtons(v, out));
    (node.c || []).forEach((n) => removeButtons(n, out));
  }
  return out;
}

// The options a picker offers, which is the whole claim about who is offerable.
function pickerOptions(node) {
  let found = null;
  (function walk(n) {
    if (found) return;
    if (Array.isArray(n)) return n.forEach(walk);
    if (!n || typeof n !== 'object') return;
    if (n.t === 'Select' && n.p && n.p.options) { found = n.p.options; return; }
    Object.values(n.p || {}).forEach(walk);
    (n.c || []).forEach(walk);
  })(node);
  return (found || []).map((o) => o.value);
}

function render() {
  cursor = 0;
  effects = [];
  const tree = sandbox.SW.PeopleModal();
  effects.forEach((fn) => fn());
  return tree;
}

let modal = render();

// Pick somebody, close, reopen — and see whether the selection came back with the modal.
let reopenedWith = null;
if (spec.reopen) {
  cursor = 0;
  sandbox.SW.PeopleModal();          // a render whose useState calls hand back the live setters
  slots[0].v = spec.reopen;          // what the creator picked
  sandbox.SW.store.set({ peopleOpen: false });
  render();                          // the close, whose effect is the thing under test
  sandbox.SW.store.set({ peopleOpen: true });
  modal = render();
  reopenedWith = slots[0].v;
}

const stack = sandbox.SW.CollaboratorStack({ onOpen: () => {} });

// The confirm behind Remove, which no click reaches from here. It is named on the component for
// exactly that reason — destructive copy has to be readable without triggering the destruction.
const confirm = sandbox.SW.PeopleModal.removalConfirm({ id: 'u-grace', name: 'Grace Hopper' });

console.log(JSON.stringify({
  said: stringsIn(modal),
  reopenedWith,
  confirm: [confirm.title, confirm.content],
  removable: removeButtons(modal).length,
  offers: pickerOptions(modal),
  stackSaid: stringsIn(stack),
}));
