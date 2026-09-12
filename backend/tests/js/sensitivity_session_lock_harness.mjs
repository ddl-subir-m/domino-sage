// Drives the real SW.util sentences against the two shapes the lock can hold.
//
// Reading the source cannot show what this is for. The lock has two reasons and only one of them
// can name a Dataset: once a Conversation has run a turn under the lock, the transcript carries the
// rows and the creator can unbind the Dataset without lifting anything (ADR-0043). Every sentence
// drawn from `datasets` is then false, and the false version is worse than a vague one — it sends
// the creator to a panel row that is no longer there to remove something that is already gone.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const spec = JSON.parse(fs.readFileSync(0, 'utf8'));

const sandbox = {
  console, JSON, Object, String, Array, Error, Map, Set, Promise, Date, Math, Number, Boolean,
  RegExp, encodeURIComponent, decodeURIComponent,
  setTimeout, clearTimeout, setInterval, clearInterval, URLSearchParams,
  fetch: () => Promise.reject(new Error('the harness makes no requests')),
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  document: {
    title: '', documentElement: { style: { setProperty: () => {} } },
    addEventListener: () => {},
  },
  location: { search: '', pathname: '/', href: 'http://localhost/' },
  antd: { message: {}, Modal: {} },
  React: {},
  EventSource: function () {},
  SW: {},
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(ROOT + 'store.js', 'utf8'), sandbox, { filename: 'store.js' });
vm.runInContext(fs.readFileSync(ROOT + 'util.js', 'utf8'), sandbox, { filename: 'util.js' });

const out = (spec.cases || []).map((c) => ({
  bySession: sandbox.SW.util.lockedBySession(c.sensitivity),
  locked: sandbox.SW.util.isLocked(c.sensitivity),
  phrase: sandbox.SW.util.declaredPhrase(c.sensitivity),
  reason: sandbox.SW.util.lockReason(c.sensitivity, c.picked || 'gpt-5.4'),
  wayOut: sandbox.SW.util.sessionWayOut(c.sensitivity),
  // The lock still narrows the picker the same way under either reason — the reason changes the
  // sentence, never who is refused.
  approved: sandbox.SW.util.isApproved(c.sensitivity, c.picked || 'gpt-5.4'),
}));
if (spec.cases) console.log(JSON.stringify(out));

// The way out has to actually work. `clearConversation` is what "Start a new chat" runs first, and
// it is synchronous and reaches no network — so the session lock is dropped there rather than
// re-read. Driven through the real store, because the claim is about that function and not about a
// sentence: a lock still drawn over an empty screen makes the one instruction the copy gives look
// like it did nothing.
if (spec.wayOut) {
  // Three doors change which conversation is open, and every one of them has to drop a lock that
  // belongs to the one being left. `clearConversation` is the only one reachable without a network:
  // the other two mint or fetch a Thread first, so they are driven at the seam they share.
  const drops = (sensitivity, door) => {
    sandbox.SW.store.set({ sensitivity, thread: { id: 'thr_a' } });
    door();
    return sandbox.SW.store.get().sensitivity;
  };
  const clear = () => sandbox.SW.store.clearConversation();
  console.log(JSON.stringify({
    session: drops(spec.wayOut.session, clear),
    declared: drops(spec.wayOut.declared, clear),
    // What the other two doors run before their read. Named from the source rather than driven,
    // because `newThread` and `openThread` both await the server first — the claim under test is
    // that the drop happens BEFORE the read, since the read leaves the last answer standing when
    // it fails, and that is the whole failure this guards.
    doors: (() => {
      const src = fs.readFileSync(ROOT + 'store.js', 'utf8');
      const DEF = (name) => new RegExp(`\\n {4}(?:async )?${name}\\(`);
      return ['newThread', 'openThread', 'clearConversation'].map((name) => {
        // The DEFINITION, not the first call site — `store.newThread()` is called from inside this
        // same file, and anchoring on the bare name found that instead and read the wrong body.
        const at = src.search(DEF(name));
        // The window is only evidence if it was found. A renamed or deleted method leaves
        // `at === -1`, `src.slice(0)` scans the whole file, and every flag below answers about
        // some other door's body — a wrong answer that reads exactly like a right one.
        const found = at !== -1;
        // To the NEXT method rather than a fixed number of characters. It was `at + 3000`, and a
        // comment added inside one of these doors pushed `refreshSensitivity()` past the end of the
        // window — where `read === -1` is indistinguishable from the door that legitimately makes no
        // read, so the ordering assertion below passed without testing anything. A window measured
        // in characters is a window that expires.
        const rest = src.slice(at + 1);
        const next = rest.search(DEF('[a-zA-Z_$][\\w$]*'));
        const body = next === -1 ? rest : rest.slice(0, next);
        const drop = body.indexOf('dropSessionLock()');
        const read = body.indexOf('refreshSensitivity()');
        return {
          name,
          found,
          drops: drop !== -1,
          beforeRead: read === -1 || drop < read,
          // Whether this door reads the lock at all. `beforeRead` passes when it does not, which is
          // true of `clearConversation` — so without this, a read that moved out of the scanned body
          // reads as a door in the clear. That happened once, to a character-counted window.
          reads: read !== -1,
          // No door loads the app scope itself (#264). It hangs off the lock read below instead, so
          // that a Chat open with the gate off still reads nothing it never needed.
          loadsApp: body.indexOf('loadAppList(') !== -1,
        };
      });
    })(),
    // Where the app scope IS loaded, and on what condition. The notice's pointer needs the selected
    // app's Bindings, and on the Chat route nothing else loads them — but an unconditional load in
    // the doors broke the promise that opening a Chat reads no rail list it has never needed, which
    // holds for every deployment with the gate off. Gated on the lock, and awaited before the state
    // is applied so the notice arrives whole rather than sprouting its way out a beat later.
    lockRead: (() => {
      const src = fs.readFileSync(ROOT + 'store.js', 'utf8');
      const at = src.search(/\n {2}function refreshSensitivity\(/);
      const body = src.slice(at + 1, src.indexOf('\n  }', at));
      const load = body.indexOf('loadAppList(');
      return {
        // Same reason as `found` above: renamed away, `at === -1` scans from the top of the file
        // and every flag here answers about whatever happens to sit there.
        found: at !== -1,
        loadsApp: load !== -1,
        gatedOnLock: /if \(SW\.util\.isLocked\(read\) && !state\.activeApp\) await loadAppList\(\)/
          .test(body),
        beforeApply: load !== -1 && load < body.indexOf('state.sensitivity = read'),
      };
    })(),
  }));
}
