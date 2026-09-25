// Reports runtime errors from the live preview back to the Sage builder so the agent can autofix
// them. A throw at render time or in a handler blanks the page and leaves its message in the browser
// console only, so this is the channel that closes the feedback loop on it — the build loop waits
// for a report and feeds the message and stack back as another iteration.
//
// Preview-only: a published Domino App has no Sage backend to report to. `sage.preview` is stamped
// into the page by the server Sage's builder runs, and never by a published one. The endpoint is the
// builder's, derived from the preview's base (`<prefix>/preview`) by swapping the trailing `preview`
// for `api/`; both are same-origin behind the one proxy.
//
// Sage owns this file. Do not edit it.
window.sage = window.sage || {};

(function () {
  const API = sage.base.replace(/\/preview\/?$/, "").replace(/\/$/, "") + "/api/";
  const ENDPOINT = API + "preview/runtime-error";
  // Fixed when this document loads; a later app selection cannot retag its reports.
  const validationId = new URLSearchParams(window.location.search).get("sageValidation") || "";
  // Capture the document identity when its own data fetch is issued. The proxy observes HTTP
  // results; this catches a rejected fetch even if app code catches it and renders an empty state.
  if (sage.preview && validationId) {
    const originalFetch = window.fetch.bind(window);
    const previewBase = new URL(sage.base.replace(/\/$/, "") + "/", window.location.href).pathname;
    window.fetch = (input, init) => {
      const url = new URL(input instanceof Request ? input.url : String(input), window.location.href);
      if (url.origin !== window.location.origin
          || !(url.pathname.startsWith(previewBase + "api/queries/")
               || url.pathname.startsWith(previewBase + "api/domino/"))) {
        return originalFetch(input, init);
      }
      const headers = new Headers(init?.headers || (input instanceof Request ? input.headers : undefined));
      headers.set("X-Sage-Validation", validationId);
      return originalFetch(input, { ...init, headers }).catch((error) => {
        void originalFetch(API + "preview/data-error", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ validationId, path: url.pathname }), keepalive: true,
        }).catch(() => {});
        throw error;
      });
    };
  }
  function acknowledgePage() {
    if (!sage.preview || !validationId || document.visibilityState === "hidden") return;
    void fetch(API + "preview/ack", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ validationId }), keepalive: true,
    }).catch(() => {});
  }
  if (document.readyState !== "complete") {
    document.addEventListener("DOMContentLoaded", acknowledgePage, { once: true });
  } else { acknowledgePage(); }

  // Is the agent editing these files right now? The error boundary asks so it can tell a crash Sage
  // is already part-way through fixing from one the creator has to deal with themselves.
  //
  // Best-effort by design: every failure path answers "no", which yields the blunt crash card. That
  // is the safe direction to be wrong in — claiming a fix is coming when none is would be the
  // damaging one.
  sage.buildIsRunning = async function buildIsRunning() {
    if (!sage.preview) return false;
    try {
      const res = await fetch(API + "project/build/state");
      if (!res.ok) return false;
      const body = await res.json();
      return !!(body && body.running);
    } catch {
      return false;
    }
  };

  let last = "";

  sage.reportRuntimeError = function reportRuntimeError(message, stack) {
    if (!sage.preview) return;
    const key = message + "\n" + (stack || "");
    if (key === last) return; // collapse duplicate reports of the same error (window + boundary)
    last = key;
    try {
      void fetch(ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, stack: stack || "", validationId }),
        keepalive: true,
      }).catch(() => {});
    } catch {
      /* best-effort: never let the reporter itself throw */
    }
  };

  // Catch throws outside React's render tree (event handlers, async effects, promises) that the
  // error boundary never sees.
  if (sage.preview) {
    window.addEventListener("error", (e) => {
      sage.reportRuntimeError(e.message || String(e.error), e.error && e.error.stack);
    });
    window.addEventListener("unhandledrejection", (e) => {
      const r = e.reason;
      sage.reportRuntimeError((r && r.message) || String(r), r && r.stack);
    });
  }
})();
