// Which answer about the model block the store settles on when a `/project` READ and a newer answer
// are in the air together (#324).
//
// `loadBuild` issues `/project` and writes the model block straight off the result — between the
// await and the `applyAppScope` guard three lines below it. Reading the source does not answer what
// that leaves on the control: the pair has to be read from outside, after both answers have landed,
// with the order they land in fixed by hand rather than by timing.
//
// TWO conditions, and they pull in opposite directions, so they are separate steps. A READ that is
// no longer the newest answer must not write (`staleReadDuringSave`, `raceReads`). An ACT must
// always write, including when a read completed while its own POST was still out
// (`saveAnswersAfterRead`) — a guard put on the POST sites would pass the first two and revert
// every save made during a poll.
//
// Input on stdin: a list of steps, one key each.
//   `plainRead`           — one `loadBuild` on its own. The guard must cost nothing here.
//   `staleReadDuringSave` — a `loadBuild` whose `/project` is held open, a model save that LANDS
//                           inside that window, and only then the read's answer — which still
//                           carries the model the server held when the read started.
//   `raceReads`           — two `loadBuild` calls, the FIRST one held. The second carries the newer
//                           answer, so it is the one that has to be standing when both are done.
//   `saveAnswersAfterRead`— a save held open, a whole `loadBuild` completing under it serving the
//                           PRE-save block, and only then the POST's answer. The server has taken
//                           the pick by then, so the control must show it.
//   `failedReadUnderRead` — a `loadBuild` carrying the newer block held open, and a second one
//                           whose `/project` FAILS answering under it. A read that said nothing
//                           must not take the head of the queue from one that has something to say.
//   `refusedAfterStaleRead`— the stale read above, then a second save the server REFUSES. What the
//                           refusal puts back is `chatConfirmed`, which `applyModelStatus` writes
//                           and a stale read would therefore corrupt in a way no field on `state`
//                           shows until this moment (#306, #323).
//
// Store only: no component is mounted. Every field asserted is one `applyModelStatus` writes.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const steps = JSON.parse(fs.readFileSync(0, 'utf8'));

// --- server ------------------------------------------------------------

const CATALOG = {
  plan: ['coder'], implement: ['coder'], ask: ['coder', 'gpt-5.4'],
  plan_effort: null, implement_effort: null, ask_effort: null,
};

// The two answers, told apart on every field a person can move: the Chat pair, the Build pair and
// the mode. Equal on any one of them would let a store that wrote nothing at all look like a store
// that wrote the right thing.
const OLD = {
  mode: 'auto', selected_mode: 'auto', phase: 'plan', signing_slot: '',
  picked_model: 'coder', picked_effort: null,
  chat_model: 'coder', reasoning_effort: null,
  catalog: CATALOG,
};
const NEW = {
  ...OLD, selected_mode: 'implement',
  picked_model: 'gpt-5.4', picked_effort: 'high',
  chat_model: 'gpt-5.4', reasoning_effort: 'high',
};

// What the server holds. A save moves it, exactly as `/project/model` answers `project.status()` in
// the product — so the POST's reply is the whole block and not just the two keys it was sent.
let served = { ...OLD };

// Which `/project` read waits, and on what. Ordered by hand rather than by latency so a step
// asserts the overlap it names instead of whichever answer happened to come back first.
let projectReads = 0;
let projectHold = 0;
let projectGate = null;
// Which `/project` read rejects. `loadBuild` catches it into `{}`, so this is the route saying
// nothing about the model block rather than saying something wrong.
let projectFail = 0;
// The block each `/project` read answers with, by read number. The last entry stands for every
// read after it, so a step only lists the reads it cares about.
let projectBodies = [OLD];

// The same, for the one POST route both model controls go to.
let posts = 0;
let postHold = 0;
let postGate = null;
// Which POST the server refuses. `api.js` turns a reject into a thrown Error, which is the seam
// the store's own catch — and the pair it restores — stands at.
let postRefuse = 0;

const json = (body) => ({
  ok: true, status: 200, headers: { get: () => 'application/json' },
  json: async () => body, text: async () => JSON.stringify(body),
});

const sandbox = {
  console, JSON, Math, Date, process, Set, Map, Promise, Array, Object, String, Number, Boolean,
  RegExp, Error, TextEncoder, TextDecoder, URL, URLSearchParams, Blob, ArrayBuffer, Uint8Array,
  setTimeout, clearTimeout,
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
    message: { success() {}, info() {}, warning() {}, error() {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async (url, opts) => {
    await new Promise((r) => setTimeout(r, 0));
    const path = String(url).split('?')[0].replace(/^\.\/api/, '');
    if (path === '/project') {
      // This read's own number, read once — testing the counter after the gate would be testing
      // where the OTHER read left it rather than this one's place in the order.
      const nth = (projectReads += 1);
      const body = projectBodies[Math.min(nth - 1, projectBodies.length - 1)];
      if (projectGate && nth === projectHold) await projectGate;
      if (nth === projectFail) throw new Error('the project read failed');
      return json({ attached: [], scratch: [], model: body });
    }
    if (path === '/project/model') {
      const nth = (posts += 1);
      if (postGate && nth === postHold) await postGate;
      if (nth === postRefuse) throw new Error('Domino refused that model');
      const body = JSON.parse((opts && opts.body) || '{}');
      if ('chat_model' in body) {
        served = { ...served, chat_model: body.chat_model || '',
                   reasoning_effort: body.reasoning_effort || null };
      }
      if ('pick' in body) {
        served = { ...served, picked_model: body.pick || '', picked_effort: body.pick_effort || null };
      }
      return json({ model: { ...served } });
    }
    return json({});
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

const settle = async () => { for (let i = 0; i < 20; i += 1) await new Promise((r) => setTimeout(r, 0)); };

// Every field of the model block a person can move, so a step can say which ANSWER is standing
// rather than only which model is.
const snapshot = () => {
  const s = SW.store.get();
  return {
    model: s.model, effort: s.reasoningEffort,
    buildModel: s.buildModel, buildEffort: s.buildEffort,
    mode: s.buildMode,
  };
};

const hold = () => {
  let release;
  const gate = new Promise((r) => { release = r; });
  return { gate, release };
};

const report = [];
for (const step of steps) {
  served = { ...OLD };
  projectReads = 0; projectHold = 0; projectGate = null; projectFail = 0; projectBodies = [OLD];
  posts = 0; postHold = 0; postGate = null; postRefuse = 0;

  if (step.plainRead) {
    // Read 1 seeds the control on OLD; read 2 carries NEW and is the only thing in the air.
    projectBodies = [OLD, NEW];
    await SW.store.loadBuild();
    await SW.store.loadBuild();
    await settle();
  } else if (step.staleReadDuringSave) {
    projectBodies = [OLD];
    await SW.store.loadBuild();
    await settle();
    // The read that is about to go stale. Held at the route, so the save below lands inside its
    // window and the answer it eventually carries is the PRE-save block.
    const h = hold();
    projectGate = h.gate;
    projectHold = 2;
    const read = SW.store.loadBuild();
    await settle();
    await SW.store.setChatModel('gpt-5.4', 'high');
    await settle();
    h.release();
    await read;
    await settle();
  } else if (step.raceReads) {
    projectBodies = [OLD, OLD, NEW];
    await SW.store.loadBuild();
    await settle();
    const h = hold();
    projectGate = h.gate;
    projectHold = 2;
    const first = SW.store.loadBuild();
    await settle();
    // Issued second and answered first, which is the only arrangement that tells "newest answer
    // wins" apart from "last answer to land wins".
    await SW.store.loadBuild();
    await settle();
    h.release();
    await first;
    await settle();
  } else if (step.saveAnswersAfterRead) {
    projectBodies = [OLD, OLD];
    await SW.store.loadBuild();
    await settle();
    // The save is held at the route, so the server has not taken the pick yet from the reading
    // side's point of view: the `loadBuild` under it answers with the PRE-save block and is right
    // to write it. The POST's own answer comes last and is the newest thing anybody said.
    const h = hold();
    postGate = h.gate;
    postHold = 1;
    const save = SW.store.setChatModel('gpt-5.4', 'high');
    await settle();
    await SW.store.loadBuild();
    await settle();
    h.release();
    await save;
    await settle();
  } else if (step.failedReadUnderRead) {
    projectBodies = [OLD, NEW];
    await SW.store.loadBuild();
    await settle();
    // Read 2 has the newer block and is held. Read 3 goes out after it and fails, so `loadBuild`
    // hands `applyModelStatus` the `{}` it substitutes — an answer with nothing in it, which must
    // not take the queue from the one still coming.
    const h = hold();
    projectGate = h.gate;
    projectHold = 2;
    projectFail = 3;
    const good = SW.store.loadBuild();
    await settle();
    await SW.store.loadBuild();
    await settle();
    h.release();
    await good;
    await settle();
  } else if (step.refusedAfterStaleRead) {
    projectBodies = [OLD];
    await SW.store.loadBuild();
    await settle();
    const h = hold();
    projectGate = h.gate;
    projectHold = 2;
    const read = SW.store.loadBuild();
    await settle();
    await SW.store.setChatModel('gpt-5.4', 'high');
    await settle();
    h.release();
    await read;
    await settle();
    // Now a second save, refused. The control puts back the pair `applyModelStatus` last RECORDED,
    // so this reads out `chatConfirmed` — which nothing else can.
    postRefuse = 2;
    await SW.store.setChatModel('coder', null);
    await settle();
  } else {
    throw new Error(`unknown step ${JSON.stringify(steps)}`);
  }
  report.push(snapshot());
}
console.log(JSON.stringify(report));
