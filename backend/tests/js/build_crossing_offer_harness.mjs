// Drives the real `SW.store.chipsNotInApp`, `SW.store.crossingOffer` and `SW.store.crossChipsToApp`
// over the state a Build tab actually holds: this Conversation's chips, plus the selected app's
// Bindings and Attachments.
//
// Reading the source cannot show what this is for. The offer is a JOIN across three lists written by
// three different acts, in two id spaces — a chip carries `data_source:ds_1` where a Binding carries
// `{kind, id}` — and the bug the join prevents is a bar that offers to move a chip the app already
// holds, which is one click that appears to do nothing.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const spec = JSON.parse(fs.readFileSync(0, 'utf8'));

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
  },
  location: { search: '', pathname: '/', href: 'http://localhost/' },
  antd: { message: { success: () => {}, error: () => {}, warning: () => {} }, Modal: {} },
  React: {},
  EventSource: function () {},
  SW: {},
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(ROOT + 'util.js', 'utf8'), sandbox, { filename: 'util.js' });
vm.runInContext(fs.readFileSync(ROOT + 'store.js', 'utf8'), sandbox, { filename: 'store.js' });

const { SW } = sandbox;

// What the store reads per app. The click's own refresh goes back through these, so the lists it
// reports afterwards are the ones a real one would have read.
function serve(app) {
  SW.api = {
    crossChatContext: (id) => {
      served.push(id);
      return Promise.resolve(app.answer || { ok: true, crossed: [], refused: [] });
    },
    project: () => Promise.resolve({ attached: app.attachedAfter || app.attached || [] }),
    bindings: () => Promise.resolve({ bindings: app.boundAfter || app.bindings || [] }),
    // The crossing attaches files and binds Datasets, so it asks the lock again by name. Served
    // here rather than stubbed away: a harness that let that read throw would have the click's own
    // refresh die half-done and still look like a pass.
    sensitivity: () => {
      asked.push('sensitivity');
      return Promise.resolve(app.sensitivity || { enabled: false, locked: false });
    },
  };
}
let served = [];
// Every read the click made, in order: a crossing can arm the lock, and the only way to tell that
// it re-asks from one that never does is whether the request went out.
let asked = [];

const out = [];
for (const c of spec.cases || []) {
  served = [];
  asked = [];
  serve(c);
  // `app: null` is a real state — Build with nothing selected — so it is honoured rather than
  // defaulted, which is the whole point of the case that sends it.
  const app = c.app === undefined ? { id: 'app_a', name: 'Desk exposure' } : c.app;
  SW.store.set({
    thread: { id: c.conversation || 'conv_1', title: 'The desk talk', artifacts: [] },
    // A case may load the lock, off or on. Left unset it reads as absent, which is the one state that
    // asks for it again unconditionally — so a gate tested only there is a gate nobody has tested.
    sensitivity: c.sensitivity === undefined ? null : c.sensitivity,
    attachments: c.chips || [],
    apps: app ? [{ id: app.id, name: app.name }] : [],
    activeApp: app,
    bindings: c.bindings || [],
    appAttachments: c.attached || [],
  });
  const row = {
    notInApp: SW.store.chipsNotInApp().map((e) => e.name),
    offer: SW.store.crossingOffer(),
  };
  if (c.press) {
    row.crossed = await SW.store.crossChipsToApp();
    row.posted = served;
    row.asked = asked;
    // The same question again, off the lists the click's own refresh left behind: what a person
    // reads after pressing the button, not what the answer claimed.
    row.notInAppAfter = SW.store.chipsNotInApp().map((e) => e.name);
    row.offerAfter = SW.store.crossingOffer();
    // What the STORE kept, rather than what the answer said: the reason a chip draws comes off this,
    // and reading the answer back would only prove the harness can echo its own fixture.
    row.refusedState = SW.store.get().crossingRefused;
  }
  out.push(row);
}
console.log(JSON.stringify(out));
