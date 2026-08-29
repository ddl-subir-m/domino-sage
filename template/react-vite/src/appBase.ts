// The path a Sage app is mounted at, for a router's `basename`.
//
// A published app lives under a prefix that Domino's app proxy strips before the request reaches the
// server: `/apps/<uuid>/`, `/apps-internal/<id>/` and `/u/<owner>/<project>/app/` all reach the same
// app, so the prefix is a property of the link the viewer clicked and is unknowable at build time.
// `serve.py` stamps the path it received into `index.html`, and a shim in `<head>` subtracts that from
// `location.pathname` to leave the prefix — which is what this exports.
//
// A router MUST be given it:
//
//     import { BrowserRouter } from "react-router-dom";
//     import { appBase } from "./appBase";
//     <BrowserRouter basename={appBase}>
//
// Without it the router matches the viewer's whole path (`/apps/<uuid>/reports`) against routes
// written without the prefix (`/reports`), matches nothing, and renders a blank page.
//
// In the dev preview there is no shim, and Vite's own `base` is already the full prefix. `??` rather
// than `||`: an app published at the root reports "", which is an answer, not a missing value.
export const appBase: string =
  ((window as { __SAGE_BASE__?: string }).__SAGE_BASE__ ?? import.meta.env.BASE_URL) || "/";
