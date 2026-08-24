// Reports runtime errors from the live preview back to the Sage builder so the agent can autofix
// them. A render/runtime throw compiles clean (tsc can't see it) but blanks the preview, so this
// is the only channel that closes the feedback loop on it — the build loop waits for a report and
// feeds the message + stack back as another iteration.
//
// DEV-only: a published Domino App has no Sage backend to report to. The endpoint is derived from
// Vite's base (`<prefix>/preview/` in dev) by swapping the trailing `preview/` for the control
// app's `api/preview/runtime-error`; both are same-origin behind the one proxy.
const API = import.meta.env.BASE_URL.replace(/preview\/?$/, "") + "api/";
const ENDPOINT = API + "preview/runtime-error";

// Is the agent editing these files right now? The error boundary asks so it can tell a crash Sage is
// already part-way through fixing from one the creator has to deal with themselves.
//
// Best-effort by design: every failure path answers "no", which yields the blunt crash card. That is
// the safe direction to be wrong in — claiming a fix is coming when none is would be the damaging
// one. DEV-only for the same reason the reporter is: a published Domino App has no builder to ask.
export async function buildIsRunning(): Promise<boolean> {
  if (!import.meta.env.DEV) return false;
  try {
    const res = await fetch(API + "project/build/state");
    if (!res.ok) return false;
    const body: unknown = await res.json();
    return !!(body as { running?: boolean }).running;
  } catch {
    return false;
  }
}

let last = "";

export function reportRuntimeError(message: string, stack?: string): void {
  if (!import.meta.env.DEV) return;
  const key = message + "\n" + (stack || "");
  if (key === last) return; // collapse duplicate reports of the same error (window + boundary)
  last = key;
  try {
    void fetch(ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, stack: stack || "" }),
      keepalive: true,
    });
  } catch {
    /* best-effort: never let the reporter itself throw */
  }
}

// Catch throws outside React's render tree (event handlers, async effects, promises) that the
// error boundary never sees.
if (import.meta.env.DEV) {
  window.addEventListener("error", (e) => {
    reportRuntimeError(e.message || String(e.error), e.error?.stack);
  });
  window.addEventListener("unhandledrejection", (e) => {
    const r = e.reason;
    reportRuntimeError(r?.message || String(r), r?.stack);
  });
}
