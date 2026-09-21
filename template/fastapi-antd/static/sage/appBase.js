// Where this app is mounted, and the one place every other helper builds a URL from.
//
// A published app lives under a prefix that Domino's app proxy strips before the request reaches the
// server: `/apps/<uuid>/`, `/apps-internal/<id>/` and `/u/<owner>/<project>/app/` all reach the same
// app, so the prefix is a property of the link the viewer clicked and unknowable ahead of time.
// `sage_serve.py` stamps the path it received into the page, and a shim in <head> subtracts that
// from `location.pathname` to leave the prefix — which is `sage.base`.
//
// Use `sage.url("data/<slug>/<file>")` for anything the app fetches from itself. Never a
// leading-slash path: `/data/...` asks the apps host for a file with no app id in it.
//
// `sage.preview` is true only while Sage's builder serves the page. The helpers use it to reach the
// builder — runtime errors, model calls — and a published app has no builder to reach.
//
// Sage owns this file. Do not edit it.
window.sage = window.sage || {};

// `??` rather than `||`: an app published at the root reports "", which is an answer, not a missing
// value. The trailing `|| "/"` keeps a root-mounted app's URLs absolute-from-root.
sage.base = (window.__SAGE_BASE__ ?? "") || "/";
sage.preview = window.__SAGE_PREVIEW__ === true;

sage.url = function url(path) {
  return sage.base.replace(/\/$/, "") + "/" + String(path).replace(/^\/+/, "");
};
