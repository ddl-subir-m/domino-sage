/**
 * The `setTimeout` a harness hands its sandbox: the app's timers fire while the harness is
 * alive and do not keep it alive once the harness is done.
 *
 * A browser tab does not wait for a pending timer before it is "finished", but a Node process
 * does: it exits when its last referenced handle goes, and a real `setTimeout` is one. So a
 * harness that printed its result in 40ms sat for 4s more, because `store.js` had scheduled
 * `refreshProblems` behind `PREFLIGHT_SETTLE_MS = 4000` — measured 2026-09-20, 0.03s of CPU
 * against 4.05s of wall, in 31 tests of the suite. `process.exit` after the print is the wrong
 * fix: it does not wait for a pipe to drain, and the Python side reads the last line of stdout.
 *
 * The harness's own waits (`settle`, `new Promise((r) => setTimeout(r, 0))`) keep using the real
 * global, which is referenced, so they still hold the process open until they resolve.
 */
export function unrefTimeout(fn, ms, ...args) {
  return globalThis.setTimeout(fn, ms, ...args).unref();
}

/**
 * A `setTimeout` that only fires when the harness says time has passed, for a claim about what
 * the app does seconds or minutes later. `advance(ms)` runs every timer due by then in order and
 * lets the reads each one starts land before the next fires. Nothing real is scheduled, so it
 * cannot hold the process open either.
 */
export function fakeClock() {
  let now = 0;
  let seq = 0;
  const timers = new Map();
  const flush = () => new Promise((resolve) => globalThis.setImmediate(resolve));
  return {
    setTimeout: (fn, ms) => {
      seq += 1;
      timers.set(seq, { at: now + (Number(ms) || 0), fn });
      return seq;
    },
    clearTimeout: (id) => { timers.delete(id); },
    async advance(ms) {
      const until = now + ms;
      for (;;) {
        for (let i = 0; i < 5; i += 1) await flush();
        let next = null;
        for (const [id, t] of timers) if (t.at <= until && (!next || t.at < next[1].at)) next = [id, t];
        if (!next) break;
        timers.delete(next[0]);
        now = next[1].at;
        next[1].fn();
      }
      now = until;
    },
  };
}
