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
