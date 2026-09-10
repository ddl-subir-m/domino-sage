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
      return ['newThread', 'openThread', 'clearConversation'].map((name) => {
        // The DEFINITION, not the first call site — `store.newThread()` is called from inside this
        // same file, and anchoring on the bare name found that instead and read the wrong body.
        const at = src.search(new RegExp(`\\n {4}(?:async )?${name}\\(`));
        const body = src.slice(at, at + 3000);
        const drop = body.indexOf('dropSessionLock()');
        const read = body.indexOf('refreshSensitivity()');
        return { name, drops: drop !== -1, beforeRead: read === -1 || drop < read };
      });
    })(),
  }));
}
