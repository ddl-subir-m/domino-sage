// What a model control, and the drawer row that reads the Chat one, hold after the server REFUSES a
// model save (#306 for Chat, #323 for Build).
//
// The write is optimistic: `setChatModel` puts the pick into `state.model`/`state.reasoningEffort`
// and notifies before the POST is sent, so the interesting state is the one left behind after the
// POST rejects. Reading the source does not answer it — the pair has to be read from outside, after
// the catch has run, and the drawer has to be drawn against whatever the store then holds.
//
// `setBuildModel` is the same seam a screen down — the same optimistic write of a PAIR, the same
// route, the same catch — so the overlap steps are written once and pointed at one control or the
// other rather than copied. Everything below reads "the pair" for that reason; which two fields
// that is, and which store call moves them, is the step's `control`.
//
// Input on stdin: a list of steps.
//   `control` — `chat` (default) or `build`. Chat by default so #306's steps read unchanged.
//   `start`  — `{model, effort}` the control is on before the person touches it, seeded the way the
//              status poll seeds it (`applyModelStatus`). `model: ""` is a Project where nobody has
//              picked yet, which is the state the drawer's mirror gate can tell from any other.
//   `pick`   — `{model, effort}` the person chooses.
//   `refuse` — whether the POST to /project/model rejects. Both directions are steps here: a save
//              that LANDS must leave the pick standing, and a revert that fired on every save would
//              pass every refusal test while breaking the product.
//   `servesPick` — whether the sensitivity payload carries `chat_picked`. Absent is the one state
//              where the drawer's `ask` row reads the browser's MIRROR of the Chat pick — i.e.
//              `state.model` — rather than the server's own flag (model-assignments.js, #294).
//   `during` — `{model, effort}` saved by a SECOND act while the first POST is still in flight, and
//              landing before it answers. Nothing disables the picker during a save and
//              `applyModelStatus` has a dozen other callers, so this window is open in the product;
//              the step exists because a revert with no guard on it puts the pre-first pair back
//              over a pick the server has already taken.
//   `pendingSecond` — `{model, effort}` picked in the same window and STILL UNANSWERED when the
//              first save is refused. `midFlight` in the report is what the control reads at that
//              moment: the refusal must not yank back a pick whose own save is still out.
//   `readDuring` — run a `loadBuild` while the save is still out, with `/project` answering a body
//              that carries NO model block — which is what `loadBuild` hands `applyModelStatus`
//              whenever that read fails, since it catches into `{}`. Nothing about either pair is
//              written by such an answer, so nothing about one may be confirmed by one either.
//   `thenAlsoRefused` — `{model, effort}` picked in the same window, where BOTH saves are refused
//              and the first answers first. The other order self-heals, which is why `during` does
//              not reach this: the defect is the pair each call captures to put back, not the order
//              the answers arrive in. The second call's "before" is the FIRST call's optimistic
//              value, so a per-call capture restores a pair the server never held.
import fs from 'node:fs';
import vm from 'node:vm';
import { unrefTimeout } from './sandbox_timeout.mjs';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const steps = JSON.parse(fs.readFileSync(0, 'utf8'));

// Both approved, so the lock bars neither. That is what leaves the PICK as the only rule on the
// `ask` row that could explain the server's answer differing from the model the row holds — and so
// the only reason the row can draw a substitute at all. A barred model would open that gate on its
// own and the mirror could not be observed through it.
const APPROVED = ['coder', 'gpt-5.4'];
// The row holds `coder`; the lock answers that its turn runs `gpt-5.4`. Different on purpose: equal
// means nothing moved and the row draws its own model whatever the gate says.
const PANEL = {
  slots: [
    { slot: 'plan', model: 'coder', default: 'coder', assigned: true, effort: null,
      default_effort: null, problem: null, shadowed: false },
    { slot: 'implement', model: 'coder', default: 'coder', assigned: true, effort: null,
      default_effort: null, problem: null, shadowed: false },
    { slot: 'ask', model: 'coder', default: 'coder', assigned: true, effort: null,
      default_effort: null, problem: null, shadowed: false },
  ],
  aliases: [
    { name: 'coder', display_name: 'Qwen3 Coder', capabilities: ['chat'], serving: true,
      problem: null, reasoning_efforts: [] },
    { name: 'gpt-5.4', display_name: 'GPT-5.4', capabilities: ['chat'], serving: true,
      problem: null, reasoning_efforts: ['low', 'medium', 'high'] },
  ],
  error: null,
};

let servesPick = true;
const lock = () => {
  const answer = {
    enabled: true, locked: true, reason: 'dataset', group: 'restricted', approved: APPROVED,
    datasets: [{ id: 'd1', name: 'Claims' }], refusal: null,
    slot_models: { plan: 'coder', implement: 'coder', ask: 'gpt-5.4' },
    picked: false, chat_picked: false,
  };
  if (!servesPick) { delete answer.picked; delete answer.chat_picked; }
  return answer;
};

// Every POST the control makes goes to the same route, so the refusal is armed per step rather than
// per path. Rejecting rather than answering 400: `api.js` turns both into a thrown Error, and the
// reject is the seam the store's own catch stands at.
let refuse = false;
// How many POSTs this step has seen, and a gate the first one waits behind. Together they are the
// overlap: with `during` set only the FIRST save is refused, and it is held until the second has
// already landed, so the catch runs last over a store the server has since moved.
let posts = 0;
let refuseFirstOnly = false;
let gate = null;
// Which POST the gate holds. One at a time, and named by number rather than by "first" or "last",
// so a step orders an overlap by hand instead of by whichever answer happens to come back first.
let holdPost = 0;

const said = [];
const json = (body) => ({
  ok: true, status: 200, headers: { get: () => 'application/json' },
  json: async () => body, text: async () => JSON.stringify(body),
});

// What the server holds, both pairs of it. Kept here rather than echoed out of the request, because
// the route answers `project.status()` and that block carries BOTH pairs on adjacent lines whichever
// control posted (service.py) — a reply narrowed to the posting control's two keys is a shape the
// product never sends.
//
// Its limit, so the next reader does not take it for more than it is: this models a server that
// starts every step holding nothing, and only the driven control's `start` puts anything into it.
// That is honest only because a step drives ONE control — every call below goes through
// `control.set`. A step type that drove both would have to seed both here first, or the second
// control's reply would carry the other pair as empty and confirm a record no server ever held.
const SERVED = { chat_model: '', reasoning_effort: null, picked_model: '', picked_effort: null };
let served = { ...SERVED };

// The control a step drives, and the only place the two differ: which store call moves the pair,
// which two fields it lands in, and which request keys carry it to the server. The route is one
// route for both, so the refusal, the gate and the overlap ordering below are shared outright.
const CONTROLS = {
  chat: {
    set: (m, e) => SW.store.setChatModel(m, e),
    read: () => ({ model: SW.store.get().model, effort: SW.store.get().reasoningEffort }),
    took: (body) => {
      served.chat_model = body.chat_model || '';
      served.reasoning_effort = body.reasoning_effort || null;
    },
  },
  build: {
    set: (m, e) => SW.store.setBuildModel(m, e),
    read: () => ({ model: SW.store.get().buildModel, effort: SW.store.get().buildEffort }),
    took: (body) => {
      served.picked_model = body.pick || '';
      served.picked_effort = body.pick_effort || null;
    },
  },
};
let control = CONTROLS.chat;

// What a LANDED save answers with: `set_model` replies `project.status()`, which `applyModelStatus`
// then writes back over the optimistic pair. Echoed from the request body so a step that saved is
// told apart from one that only wrote optimistically.
let lastPost = null;
// What the control reads while a second save is still out, for the steps that ask.
let midFlight = null;

const sandbox = {
  console, JSON, Math, Date, process, Set, Map, Promise, Array, Object, String, Number, Boolean,
  RegExp, Error, TextEncoder, TextDecoder, URL, URLSearchParams, Blob, ArrayBuffer, Uint8Array,
  setTimeout: unrefTimeout, clearTimeout,
  setInterval: () => 0, clearInterval: () => {},
  encodeURIComponent, decodeURIComponent,
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  requestAnimationFrame: (fn) => fn(),
  location: { href: 'http://localhost/', hash: '#/build' },
  document: { addEventListener() {}, querySelector: () => null, body: {},
              documentElement: { style: { setProperty() {} } } },
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
    useEffect: () => {}, useMemo: (fn) => fn(), useRef: () => ({ current: null }),
    Fragment: 'Fragment',
  },
  antd: {
    Input: Object.assign(function Input() {}, { TextArea: 'Input.TextArea' }),
    Button: 'Button', Dropdown: 'Dropdown', Tag: 'Tag', Tooltip: 'Tooltip', Space: 'Space',
    Drawer: 'Drawer', Select: 'Select', Alert: 'Alert', Spin: 'Spin',
    Typography: { Paragraph: 'Typography.Paragraph' },
    Modal: { confirm: () => ({ update() {}, destroy() {} }) },
    message: {
      success: (a) => said.push(String(a)), info: (a) => said.push(String(a)),
      warning: (a) => said.push(String(a)), error: (a) => said.push(String(a)),
    },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async (url, opts) => {
    await new Promise((r) => setTimeout(r, 0));
    const path = String(url).split('?')[0].replace(/^\.\/api/, '');
    if (path === '/project/model') {
      // This POST's own number, read once. Anything testing `posts` AFTER the gate would be
      // testing the counter as the other POST left it, not this one's place in the order — which is
      // how holding POST 1 open quietly turned it into a save that lands.
      const nth = (posts += 1);
      if (gate && nth === holdPost) await gate;
      if (refuse || (refuseFirstOnly && nth === 1)) throw new Error('Domino refused that model');
      lastPost = JSON.parse((opts && opts.body) || '{}');
      control.took(lastPost);
      return json({ model: { ...served } });
    }
    if (path.startsWith('/project/sensitivity')) return json(lock());
    if (path === '/project/model/assignments') return json(PANEL);
    return json({});
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js', 'components/model-assignments.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

const settle = async () => { for (let i = 0; i < 20; i += 1) await new Promise((r) => setTimeout(r, 0)); };

const walkAll = (node, out = []) => {
  if (!node || typeof node !== 'object') return out;
  if (Array.isArray(node)) { node.forEach((c) => walkAll(c, out)); return out; }
  out.push(node);
  walkAll(node.c, out);
  return out;
};
// What the drawer DRAWS for the row, not what the store holds. The select's closed value is the
// slot's own model until the gate opens, and whatever the lock says the turn runs once it does.
const askRow = () => {
  const nodes = walkAll(SW.ModelAssignmentsDrawer());
  const select = nodes.find((n) => n.t === 'Select' && n.p.id === 'assign-ask');
  const note = nodes.map((n) => n.c).flat(Infinity)
    .filter((c) => typeof c === 'string' && c.startsWith('This runs '))[0] || '';
  return { value: select ? select.p.value : null, note };
};

const report = [];
for (const step of steps) {
  control = CONTROLS[step.control || 'chat'];
  served = { ...SERVED };
  servesPick = step.servesPick !== false;
  refuse = false;
  refuseFirstOnly = false;
  gate = null;
  holdPost = 0;
  posts = 0;
  lastPost = null;
  midFlight = null;
  said.length = 0;
  SW.store.set({
    assignments: PANEL, assignmentsOpen: true, sensitivity: lock(),
    catalog: { plan: ['coder'], implement: ['coder'], ask: ['coder', 'gpt-5.4'] },
    buildRunning: false,
  });
  // The pair the control starts on, put there by a save that LANDS rather than assigned into the
  // store. It has to arrive through `applyModelStatus`, because that is the door the product's own
  // confirmed answers come through — a status read, or a save the server took — and a pair simply
  // written into `state` would be one no answer ever carried. A harness that seeded it the short way
  // reported every revert as broken, which is the same thing the product would do if some other
  // writer reached `state.model`.
  const start = step.start || {};
  await control.set(start.model || null, start.effort || null);
  await settle();
  posts = 0;
  lastPost = null;
  said.length = 0;

  refuse = Boolean(step.refuse);
  const pick = step.pick || {};
  if (step.during) {
    // The first save is refused and held; the second is not, and is awaited to completion before
    // the first is let go. Ordered by hand rather than by timing, so the step asserts the overlap
    // it names instead of whichever answer happened to come back first.
    let release;
    gate = new Promise((r) => { release = r; });
    holdPost = 1;
    refuseFirstOnly = true;
    const first = control.set(pick.model || null, pick.effort || null);
    await settle();
    await control.set(step.during.model || null, step.during.effort || null);
    await settle();
    release();
    await first;
  } else if (step.readDuring) {
    // The save is held open, a read lands in the window, and only then is the refusal let through.
    // The read writes nothing: `/project` answers `{}` here, the same body `loadBuild` substitutes
    // when that endpoint fails.
    refuseFirstOnly = true;
    let release;
    gate = new Promise((r) => { release = r; });
    holdPost = 1;
    const first = control.set(pick.model || null, pick.effort || null);
    await settle();
    await SW.store.loadBuild();
    await settle();
    release();
    await first;
  } else if (step.pendingSecond) {
    // The first save is refused while the second is still out. Read before the second is let go,
    // because the whole question is what the control says in that window — afterwards the second
    // answer settles it either way, and the difference is gone.
    refuseFirstOnly = true;
    let release;
    gate = new Promise((r) => { release = r; });
    holdPost = 2;
    const first = control.set(pick.model || null, pick.effort || null);
    const second = control.set(step.pendingSecond.model || null,
                                         step.pendingSecond.effort || null);
    await first;
    await settle();
    midFlight = control.read();
    release();
    await second;
  } else if (step.thenAlsoRefused) {
    // Both refused, the FIRST answering first. Started back to back with nothing awaited between
    // them, because the optimistic write happens synchronously: the second call therefore reads the
    // FIRST call's optimistic pair as the pair it will put back, which is the whole of the defect.
    // A `settle()` here would let the first refusal run its catch before the second pick is made,
    // and the window would never open — which is how the first draft of this step reported a pass.
    // The second POST is then held until the first refusal has answered, so the two are ordered
    // rather than raced.
    refuse = true;
    let release;
    gate = new Promise((r) => { release = r; });
    holdPost = 2;
    const first = control.set(pick.model || null, pick.effort || null);
    const second = control.set(step.thenAlsoRefused.model || null,
                                         step.thenAlsoRefused.effort || null);
    await first;
    await settle();
    release();
    await second;
  } else {
    await control.set(pick.model || null, pick.effort || null);
  }
  await settle();

  const pair = control.read();
  report.push({
    model: pair.model,
    reasoningEffort: pair.effort,
    posted: lastPost,
    midFlight,
    // Chat only, and null rather than drawn anyway on a Build step: the row this reads is hardcoded
    // to `assign-ask`, whose mirror is the CHAT pick, so under a Build step it would describe the
    // other control while sitting in a Build report. Not a claim that the drawer ignores the Build
    // pair — it mirrors `buildModel` on the `plan` and `implement` rows (model-assignments.js) and
    // nothing here reaches those. That gap, and why this fixture cannot close it, is written down in
    // `test_a_refused_build_model_save_puts_the_pair_back.py`.
    askRow: control === CONTROLS.chat ? askRow() : null,
    said: [...said],
  });
}
console.log(JSON.stringify(report));
process.exit(0);
