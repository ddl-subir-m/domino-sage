// Which model Build says it will run on, and what a pick writes.
//
// The picker existed in the pre-Workbench UI (`<select id="pick">`) and the backend never stopped
// supporting it: `POST /api/project/model` still takes `pick`, and `llm_router` still honours a
// picked model in Plan and Implement. What was lost is in between — `applyModelStatus` read
// `catalog.ask` and threw the rest of the status away, so nothing in the Workbench could have
// drawn a picker even if one had been written.
//
// That gap is why this harness drives the store rather than assigning to it: the modes and the
// picks go in through `setBuildMode`/`setBuildModel`, which means the catalog reaching the menu
// has to survive the real `applyModelStatus`. Seeding `state.catalog` by hand would test the menu
// against a fact the product does not actually keep.
//
// Input on stdin: a list of steps. `{ "mode": "plan" }` switches modes and reports what the
// composer bar draws; `{ "mode": "plan", "pick": "<id>" }` also clicks that row and reports what
// it wrote. `{ "health": true }` reports the URL the open-weight list is read from.
//
// `{ "mode": "plan", "sensitivity": {...} }` seeds the sensitivity lock (ADR-0043) before drawing,
// so the menu can be asked which rows it closed and what it said about them. Seeded into the store
// rather than served over `/project/sensitivity`, because what is under test here is the menu: the
// read itself is covered in Python, where the approved set is computed.
//
// Nothing is mounted. `createElement` is stubbed to a plain object, so calling the component
// returns the tree it would draw — which is where a menu item and its key are settled.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const steps = JSON.parse(fs.readFileSync(0, 'utf8'));

// --- the server ------------------------------------------------------------
// Four slots, and two of them deliberately share a model: the menu offers MODELS, not slots, so a
// picker that listed one row per slot would show `claude-asker` twice and make the person choose
// between two identical rows.
const CATALOG = {
  plan: 'anthropic/claude-planner',
  implement: 'anthropic/claude-builder',
  ask: 'anthropic/claude-planner',
  sovereign_plan: 'sovereign/plan',
  sovereign_implement: 'sovereign/implement',
  sovereign_ask: 'sovereign/ask',
  // The ASSIGNMENT's own level, which `service.py`'s status payload ships beside each slot and this
  // fixture used to stop short of. Without them nothing here can witness assignment-effort-aware
  // behaviour at all — which is how a tooltip claiming "not at the assignment's level" went both
  // unguarded and unfalsifiable on a ticket whose entire subject is efforts.
  plan_effort: 'medium',
  implement_effort: null,
  ask_effort: null,
};
// What an `openai` gateway adds. One of them is already a configured slot, so the extras list has
// to drop it — offering the same model under two headings is the same duplicate as above.
// A model that signs its tool calls, for the pin (ADR-0032).
const SIGNING_MODEL = 'google/gemini-3.7-flash';

const OPEN_WEIGHT = [
  { id: 'deepseek/deepseek-v3', provider: 'DeepSeek' },
  { id: 'qwen/qwen-2-5', provider: 'Qwen' },
  // Offered so the two effort lists can be told apart through the menu: this is the one alias whose
  // advertised levels and tool-carrying levels differ.
  { id: 'openai/gpt-5.4', provider: 'OpenAI' },
  // Deliberately unreal, and KEEP IT. No alias on any probed deployment carries a colon pair — and
  // that promise about DATA is exactly what `onClick`'s row lookup exists so nobody has to make. A
  // fixture with no such id cannot tell the lookup from the parse, because `splitEffortKey` returns
  // the same answer for every id without one; deleting the lookup left all 74 tests green until
  // this row existed.
  //
  // The general point, because it will recur: the case a guard exists for is the case a fixture
  // omits, precisely because the fixture is built to look like production and the guard is for what
  // production does not produce. This one row has since armed a second, unrelated branch as well
  // (`selectedPick`'s children guard), which is the argument for keeping one impossible value here
  // permanently rather than adding one per guard as each is found unarmed.
  { id: 'weird/a::low', provider: 'Odd' },
  { id: 'anthropic/claude-planner', provider: 'Anthropic' },
];

// Which levels each alias advertises, as the resources listing reports them. Per alias and
// measured (ADR-0049), so the fixture has to be too: a menu that derived them from the name would
// pass here and offer a 400 on the deployment. `claude-builder` advertises none on purpose — the
// row that must stay a plain row is the assertion this file would otherwise never make.
const ALIAS_EFFORTS = {
  'anthropic/claude-planner': ['low', 'medium', 'high'],
  'anthropic/claude-builder': [],
  // Advertises five, keeps one beside tools. The pair that makes the two lists tell each other apart.
  'openai/gpt-5.4': ['none', 'low', 'medium', 'high', 'xhigh'],
  // No levels, so its row stays plain and its own key is what `onClick` receives — the bare branch.
  'weird/a::low': [],
  // A SECOND alias that advertises none, so the empty-enum case is covered by a row that exists.
  // Leaving qwen out of this table entirely made its rows test the MISSING-LISTING path instead —
  // no row at all — which this menu deliberately treats as the opposite fact (see
  // `test_a_missing_alias_listing_is_not_read_as_a_refusal`). Two tests read as the empty-enum case
  // and neither was one.
  'qwen/qwen-2-5': [],
  'deepseek/deepseek-v3': ['low', 'high'],
  'google/gemini-3.7-flash': ['low', 'medium', 'high', 'max'],
};
// The one alias measured to REFUSE its own advertised levels beside tools (ADR-0049's probe table).
// Real name and real narrowing, because a placeholder here could only ever prove the field is
// plumbed, never that the right list reaches the menu.
const ALIAS_WITH_TOOLS = {
  'openai/gpt-5.4': ['none'],
};

// What a MEMBERSHIP row looks like — `api.js`'s `rowFromMember`, which builds `model_llm` from the
// project's own membership file rather than from the Domino listing. It carries BOTH effort lists
// since #295: `_MEMBERSHIP_ONLY_FIELDS`, the `keep` tuple and `bind_llm_alias` all pass the narrow
// one through, so the shape is the listing's.
const MEMBER_ROWS = () => ALIAS_ROWS();
// And the shape written BEFORE that field existed, which `rowFromMember` still has to survive: the
// row exists, the narrow list does not. The menu must read that as no evidence, never as a refusal.
const LEGACY_MEMBER_ROWS = () =>
  ALIAS_ROWS().map(({ reasoning_efforts_with_tools, ...row }) => row);

const ALIAS_ROWS = () => Object.keys(ALIAS_EFFORTS).map((alias) => ({
  id: `llm_alias:${alias.replace('/', '-')}`,
  kind: 'llm_alias',
  alias,
  name: alias,
  capabilities: ['chat'],
  reasoning_efforts: ALIAS_EFFORTS[alias],
  // Both lists, as the server sends them. Equal for every alias here except the one that exists to
  // be unequal: `gpt-5.4` advertises levels it will not take beside function tools, which every
  // Build turn carries. A fixture carrying only the wide list would let the Build menu read the
  // wrong one and stay green.
  reasoning_efforts_with_tools: ALIAS_WITH_TOOLS[alias] || ALIAS_EFFORTS[alias],
}));

let mode = 'auto';
let phase = 'plan';
let picked = null;
let pickedEffort = null;
// Server-computed (ADR-0032). Set by a step, never derived here — the point of the field is
// that the picker cannot work it out, so a harness that derived it would test nothing.
let signingSlot = null;
const calls = [];
const fetched = [];

const json = (body) => ({
  ok: true, status: 200,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

// The shape `Project.status()` returns, narrowed to the model block this reads. `selected_mode` is
// where the picker sits and `mode` is what a running turn is pinned to; they agree here because
// nothing in this file starts a turn.
const status = () => ({
  model: {
    mode, selected_mode: mode, phase, picked_model: picked, picked_effort: pickedEffort,
    chat_model: null, reasoning_effort: null, catalog: CATALOG,
    signing_slot: signingSlot,
  },
});

function serve(url, options = {}) {
  const method = (options.method || 'GET').toUpperCase();
  const path = String(url).replace(/^\.\/api/, '');
  fetched.push(String(url));
  if (path === '/project/model' && method === 'POST') {
    const body = JSON.parse(options.body);
    calls.push(body);
    if ('mode' in body) {
      mode = body.mode;
      // What ModelControl._sync_phase does for the pinned modes, so Auto's "which phase am I in"
      // question is asked of a phase that actually moves.
      if (mode === 'plan' || mode === 'implement') phase = mode;
    }
    if ('pick' in body) picked = body.pick;
    // Stored beside the model and cleared with it, which is what ModelControl.pick does. A server
    // that kept a level over a cleared pick would let the menu look right while the router read a
    // pairing nobody chose.
    if ('pick' in body) pickedEffort = body.pick ? (body.pick_effort || null) : null;
    return json(status());
  }
  // The REAL listing route, so `SW.api.fetchDominoListing`'s own mapper runs rather than being
  // stepped over. Rows shaped as the server sends them and deliberately WITHOUT
  // `reasoning_efforts_with_tools`, which is the shape whose handling the mapper decides: passed
  // through it stays `undefined` ("nobody answered"); defaulted it becomes `[]` ("refuses every
  // level"), and the menu cannot tell those apart afterwards.
  if (path === '/resources') {
    return json({
      data_sources: [], model_apis: [], errors: {},
      llm_aliases: [{
        id: 'id-deepseek', name: 'deepseek/deepseek-v3', display_name: 'DeepSeek v3',
        capabilities: ['chat'], reasoning_efforts: ['low', 'high'],
      }, {
        // The POSITIVE half. With only the row above, the mapper's passthrough could be mistyped or
        // deleted outright and every test would stay green — absent maps to absent either way. This
        // row is the one that fails if the field stops being carried.
        id: 'id-gpt', name: 'openai/gpt-5.4', display_name: 'GPT-5.4',
        capabilities: ['chat'], reasoning_efforts: ['none', 'low', 'high'],
        reasoning_efforts_with_tools: ['none'],
      }],
    });
  }
  if (path === '/assets') return json({ assets: [] });
  return json({});
}

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, setTimeout, clearTimeout,
  setInterval: () => 1, clearInterval: () => {},
  requestAnimationFrame: (fn) => fn(),
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    // Read-once: nothing here is decided by the composer's own state. The mode and the pick both
    // live in the store, and a menu that could only be right after a local re-render has not made
    // the claim this file is about.
    useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
    useEffect: () => {},
    useMemo: (fn) => fn(),
    useRef: () => ({ current: null }),
    Fragment: 'Fragment',
  },
  antd: {
    Input: Object.assign(function Input() {}, { TextArea: 'Input.TextArea' }),
    Button: 'Button', Dropdown: 'Dropdown', Tag: 'Tag', Tooltip: 'Tooltip', Space: 'Space',
    Modal: { confirm: () => ({ update: () => {}, destroy: () => {} }) },
    message: { success() {}, error() {}, info() {}, warning() {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async (url, options) => serve(url, options),
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js', 'components/composer.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

function* walk(node) {
  if (!node || typeof node !== 'object') return;
  if (Array.isArray(node)) { for (const c of node) yield* walk(c); return; }
  yield node;
  yield* walk(node.c);
}
const find = (tree, pred) => [...walk(tree)].find(pred);
const strings = (node) => [...walk(node)].flatMap((n) => (n.c || []).flat(Infinity))
  .filter((c) => typeof c === 'string');

const settle = async () => { for (let i = 0; i < 40; i += 1) await new Promise((r) => setTimeout(r, 0)); };

// Build's props, as `builder.js` mounts them. `showMode` is the flag that says this is Build, and
// it is already true there — the picker's absence was never about the flag.
const BUILD = { onSend() {}, placeholder: 'Describe a change…', disabled: false, showMode: true };
// `{ "chat": true }` on a step draws the CHAT composer instead — the same component with `showMode`
// off, which is how `builder.js` and `chat.js` differ. Only the lock's notice is asked of it: the
// model picker is Build's control and is absent there by design, so a Chat step reports `offered`
// false and no label, and must not be given a `pick`.
let chatMount = false;
const mount = () => SW.Composer(chatMount ? { ...BUILD, showMode: false } : BUILD);

// The control by the words a person reads on it, not by position: `aria-label` is the same in both
// arms, so "is there a picker at all" and "can it be opened" stay separate questions.
const pickerButton = (tree) => find(tree, (n) => n.p['aria-label'] === 'Build model');
const pickerMenu = (tree) => find(tree, (n) => n.t === 'Dropdown' && n.p.menu
  && [...walk(n.c)].some((x) => x.p && x.p['aria-label'] === 'Build model'));
// The composer bar holds more than one Tooltip — the attach button has its own, and it comes
// first in the tree. Found by what it is wrapped around, so this never reports on that one.
const pickerTip = (tree) => find(tree, (n) => n.t === 'Tooltip'
  && [...walk(n.c)].some((x) => x.p && x.p['aria-label'] === 'Build model'));
// A divider carries neither, so both stay undefined rather than false and the JSON drops them —
// the same reading `label` already gets.
// `children` rides along because a row with a submenu is a different offer from a row without one,
// and a reader that flattened them away would let the effort submenu vanish and still report a
// menu that looks exactly right (#295).
const itemRow = (i) => ({
  key: i.key, label: i.label, disabled: i.disabled, title: i.title,
  // Children carry `disabled` and `title` for the reason top-level rows do: the stranded level's
  // whole point is that it is drawn UNCLICKABLE with a sentence saying why, and a reader that kept
  // only the label would pass a regression that made it clickable — which would re-send the very
  // level the row exists to say is refused.
  ...(i.children
    ? { children: i.children.map((c) => ({
        key: c.key, label: c.label, disabled: c.disabled, title: c.title,
      })) }
    : {}),
});
// The switch notice under the box (ADR-0043). `createElement` here is a stub, so a component in
// the tree is an uninvoked function and its words are not in it yet — this calls it the way React
// would, which is also why it can assert the SENTENCE and not just the element's presence. Found by
// the component rather than by its class for the same reason: the class is inside the thing that
// has not run.
const lockNotice = (tree) => {
  const node = find(tree, (n) => typeof n.t === 'function' && n.t.name === 'LockNotice');
  return node ? node.t(node.p) : null;
};

SW.store.set({
  thread: { id: 'conv_1', title: 'A conversation', artifacts: [] },
  scope: { id: 'proj', name: 'Demo Project' },
  messages: [], resourceGroups: {},
  openWeightModels: OPEN_WEIGHT,
  // The listing the composer reads levels off. Seeded here rather than per step because the loop
  // below rewrites `resourceGroups` on every row, and a source that came and went would make the
  // effort submenu appear and disappear for reasons no test is about.
  gatewayAliases: ALIAS_ROWS(),
});

const report = [];
for (const step of steps) {
  if (step.listingRoute) {
    // Through `fetchDominoListing`, not around it: the mapper is the thing under test.
    const read = await SW.api.resourceListing();
    report.push({ step: 'listingRoute', rows: read.groups.model_llm || [] });
    continue;
  }

  if (step.health) {
    // Where the open-weight list comes from. Its own step because /healthz is the one route the
    // Workbench reads off `BASE` — a path that quietly became `./api/healthz` would 404 and leave
    // the picker silently short of every extra option, with nothing else here to notice.
    fetched.length = 0;
    await SW.api.healthz();
    report.push({ step: 'health', fetched: fetched.slice() });
    continue;
  }

  if ('seedPick' in step) {
    // A pick arriving from the SERVER rather than from a click, which is how a stored one reaches a
    // reloaded Workbench — and the only way to hold a level the menu will not currently offer. The
    // step runs before `setBuildMode`, whose status echo is what carries it into the store; the POST
    // handler leaves `pickedEffort` alone unless the body names a `pick`, so the echo is faithful.
    picked = step.seedPick.model;
    pickedEffort = step.seedPick.effort || null;
  }

  if ('listing' in step) {
    // The alias listing absent, which is NOT the same fixture state as an alias advertising no
    // levels — `gatewayAliases` starts empty and a gateway leg that 40x's at boot leaves it that
    // way. Its own step because the menu has to tell the two apart: one is knowledge, the other is
    // the absence of it, and `reasoning_efforts` reads `[]` for both.
    // `'legacy'` is the gateway leg answering with rows that predate the narrow field — the same
    // shape the membership leg can hold. Its own value because the two legs are read by DIFFERENT
    // branches of the composer's source pick, and a contract honoured on one and not the other gives
    // one absence two opposite answers.
    SW.store.set({
      gatewayAliases: step.listing === 'legacy' ? LEGACY_MEMBER_ROWS()
        : step.listing ? ALIAS_ROWS() : [],
    });
  }

  if ('narrow' in step) {
    // The deployment moving under a live pick: an alias stops advertising a level somebody is
    // already standing on. Reachable without anyone doing anything wrong (a default moves, or the
    // measured table narrows when an alias is probed — #280), and there is no other way to reach it
    // from here, because the fixture's listing is otherwise fixed for the whole run.
    ALIAS_EFFORTS[step.narrow.alias] = step.narrow.efforts;
    // BOTH lists, because the Build menu reads the narrow one. Moving only the enum would leave a
    // narrowing aimed at `gpt-5.4` — the one alias whose two lists differ, and so the only one
    // worth aiming at — changing nothing the menu looks at, and the test would pass over an
    // untouched control.
    if (step.narrow.alias in ALIAS_WITH_TOOLS) {
      // `withTools` when the step says so, otherwise the intersection — which is the faithful
      // simulation (a level the enum no longer advertises cannot survive beside tools) but CANNOT
      // WIDEN. Narrowing `gpt-5.4` to ['low','high'] against a with-tools list of ['none'] yields
      // [], and that is correct rather than a bug — it is stated here because the first test aimed
      // at the one alias whose two lists differ would otherwise meet a fixture state it did not ask
      // for and read it as the menu's answer.
      ALIAS_WITH_TOOLS[step.narrow.alias] = step.narrow.withTools
        || step.narrow.efforts.filter((e) => ALIAS_WITH_TOOLS[step.narrow.alias].includes(e));
    }
    SW.store.set({ gatewayAliases: ALIAS_ROWS() });
  }

  if ('signing' in step) {
    // The server reports the slot and the assignment together, so the fixture moves together too.
    signingSlot = step.signing;
    if (step.signing) CATALOG[step.signing] = SIGNING_MODEL;
    // The pin NOT moving the model: the signing slot and the mode slot name the same alias, so
    // `_pin_signing` early-returns the mode slot's decision untouched and its effort is what runs.
    // Without a step that can put one model in two slots with different efforts, nothing here could
    // tell "the pin supplies the effort" from "the mode slot does".
    if (step.alsoSigning) CATALOG[step.alsoSigning] = SIGNING_MODEL;
    if (step.efforts) Object.assign(CATALOG, step.efforts);
  }
  await SW.store.setBuildMode(step.mode);
  await settle();
  // After the mode, because `setBuildMode` goes through a real status write and this does not —
  // seeding first and settling after would leave the lock in place but the notify already spent.
  if ('sensitivity' in step) SW.store.set({ sensitivity: step.sensitivity });
  // The app on screen and the dependency records it holds, which is what the lock's pointer turns
  // on: it names the app (a Project holds many — ADR-0008) and it is only drawn when that app's own
  // list has a declared Dataset to remove. `declaredIn` says which door put it there — a Binding, an
  // Attachment, or a Chat `dsfile:` chip, which writes NEITHER record and so must draw no pointer.
  //
  // Set on every step rather than only where one asks, because the store is shared down the loop:
  // `if ('app' in step)` left a previous step's app standing under every later row, and the next
  // multi-step lock test would have passed for the wrong reason.
  //
  // Written through `set` even though these are the `APP_SCOPED` fields `store.js` says nothing
  // assigns directly. That gate orders concurrent READS of one app's records; here there is no read
  // and no second writer — this is the fixture saying which app is on screen.
  //
  // The rail carries a declared row for every Dataset the lock names, because that listing is where
  // `declared` is read from. Which of them the APP holds is `appDatasets` (default: all of them) —
  // that is the split the pointer turns on, and a step that seeds the lock with two names and the
  // app with one is the mixed-door case.
  chatMount = !!step.chat;
  // What a Chat turn would run. Build reads its pinned slot or the override; Chat reads the picked
  // Alias, and the notice only draws when the lock moved it — so a Chat step has to name one.
  if (step.chat) SW.store.set({ model: step.chatModel || CATALOG.plan, catalogAsk: CATALOG.ask });
  const locked = (step.sensitivity && step.sensitivity.datasets) || [];
  const held = step.declaredIn === 'chip' ? [] : (step.appDatasets || locked);
  const byName = (n) => ({ kind: 'dataset', id: `ds_${n}`, name: n });
  SW.store.set({
    activeApp: step.app ? { id: 'app_1', name: step.app } : null,
    // `model_llm` is the composer's SECOND alias source: `gatewayAliases` empty falls back to it,
    // and in production it carries `reasoning_efforts` too (`provider.py` builds both from one
    // helper). Folded into THIS write rather than set in its own step, because this one runs on
    // every row and would clobber it — which is how the first attempt at it read as a broken
    // fallback rather than a clobbered fixture.
    resourceGroups: {
      ...(step.declaredIn
        ? { dataset: locked.map((n) => ({ id: `dataset:ds_${n}`, name: n, declared: true })) }
        : {}),
      ...(step.resourceAliases
        ? { model_llm: step.resourceAliases === 'legacy' ? LEGACY_MEMBER_ROWS() : MEMBER_ROWS() }
        : {}),
    },
    bindings: step.declaredIn === 'binding' ? held.map(byName) : [],
    appAttachments: step.declaredIn === 'attachment'
      ? held.map((n) => ({ path: `public/data/${n}/rows.csv`, dataset_id: `ds_${n}` }))
      // The pre-manifest workspace `_rehydrate_attached` rebuilds: an entry with a path and no
      // `dataset_id` at all (ADR-0048), which is the one shape that can carry rows and answer no id.
      : step.declaredIn === 'attachment-legacy'
        ? held.map((n) => ({ path: `public/data/${n}/rows.csv` })) : [],
  });
  // A build in flight. `pick` is read live out of ModelControl.snapshot — it has no per-turn pin
  // the way the mode does — so what this control offers mid-turn is its own claim.
  // `turnMode` is the mode the RUNNING turn is pinned to, which the picker does not follow: the
  // mode selector stays live mid-turn, so the two disagree whenever somebody switches during a
  // build. Without a step for it nothing here could set them apart, and the model chip's whole
  // premise — that the turn is running on what it names — went unwitnessed.
  SW.store.set({ buildRunning: !!step.running, buildTurnMode: step.turnMode || step.mode });
  calls.length = 0;

  const before = mount();
  const menu = pickerMenu(before);
  const button = pickerButton(before);
  const row = {
    step: step.pick ? `${step.mode} → pick ${step.pick}` : step.mode,
    // Every child key drawn anywhere in the menu, so a DUPLICATE is visible. Two items sharing a
    // key is something no `selectedKeys` assertion can see — Ant marks one of them and the other is
    // simply unreachable — so the only way to witness it is to count.
    childKeys: (() => {
      const menu = pickerMenu(before);
      if (!menu) return null;
      return menu.p.menu.items
        .flatMap((i) => (i.type === 'group' ? i.children : [i]))
        .flatMap((i) => (i.children || []))
        .map((c) => c.key);
    })(),
    mode: step.mode,
    // Whether an override is OFFERED, which is the Ask claim. A disabled button is not an offer.
    offered: !!menu,
    label: button ? strings(button).join(' ') : null,
    disabled: button ? !!button.p.disabled : null,
    // `disabled` and `title` ride along with every row since ADR-0043: a closed row and the reason
    // it is closed are one claim, and reporting the label alone would let a menu grey a model out
    // and say nothing without this file noticing.
    items: menu ? menu.p.menu.items.map((i) => (i.type === 'group'
      ? { group: i.label, children: i.children.map(itemRow) }
      : itemRow(i))) : null,
    selectedKeys: menu ? menu.p.menu.selectedKeys : null,
    // What a mode with no override says instead, so "you cannot change this" is not silence.
    // Reported whether or not a menu is offered: under the signing pin a mode has BOTH, and
    // gating this on the menu is how the pin's explanation would go unasserted.
    why: String((pickerTip(before) || { p: {} }).p.title || '') || null,
    // A browser dispatches no mouse events on a disabled button, so a Tooltip wrapped straight
    // round one never opens. What sits between them is the difference between an explanation and
    // silence, and it is invisible to every other assertion here.
    wrapsDisabledIn: menu ? null : (() => {
      const tip = pickerTip(before);
      const child = tip && [...walk(tip.c)].find((n) => n.t);
      return child ? String(child.t) : null;
    })(),
    // What the lock says under the box, or null when it says nothing. The whole sentence, because
    // the claim being tested is that the person is TOLD — a boolean would pass over an empty one.
    lockNotice: (() => {
      const notice = lockNotice(before);
      return notice ? strings(notice).join(' ') : null;
    })(),
  };

  if (step.pick) {
    if (!menu) throw new Error(`${step.mode} offers no model menu to pick from`);
    // Two levels deep: a model row that advertises levels is a SUBMENU, so the key a person can
    // actually click is a child of it (#295). Flattening both makes `pick` name the thing clicked
    // rather than the thing it happens to sit under.
    const target = [...menu.p.menu.items].flatMap((i) => (i.type === 'group' ? i.children : [i]))
      .flatMap((i) => (i.children ? i.children : [i]))
      .find((i) => i.key === step.pick);
    if (!target) throw new Error(`${step.mode} has no row keyed ${step.pick}`);
    menu.p.menu.onClick({ key: target.key });
    await settle();
    row.wrote = calls.slice();
    row.serverPick = picked;
    row.serverEffort = pickedEffort;
    // One remount, read twice. The chip and the menu are the two places the pick shows, and a
    // second `mount()` would be a second render of the same state that could only agree with the
    // first while costing a reader the right to assume they came from one draw.
    const after = mount();
    row.afterLabel = strings(pickerButton(after)).join(' ');
    const afterMenu = pickerMenu(after);
    row.afterSelected = afterMenu ? afterMenu.p.menu.selectedKeys : null;
  }
  report.push(row);
}
console.log(JSON.stringify(report));
