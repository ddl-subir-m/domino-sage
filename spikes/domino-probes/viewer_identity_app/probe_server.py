#!/usr/bin/env python3
"""Viewer-identity probe — deploy as a Domino App, then open it in a browser.

Answers the one question that decides whether Sage can ever query a data source per
VIEWER instead of per publisher: does Domino forward the viewer's identity -- and a
usable TOKEN -- to a published app, and will a Domino API accept that token?

Gated on a SysAdmin-only, irreversible platform setting. If it is off, expect no viewer
token and no forwarded username:
    SecureIdentityPropagationToAppsEnabled
    com.cerebro.domino.apps.extendedIdentityPropagationToAppsEnabled

Reads three things and prints them to stdout (Domino app logs) AND to the page:
  1. Whose identity the CONTAINER has          -> expect the publisher
  2. What identity headers arrive per REQUEST  -> the viewer, if propagation is on
  3. Whether a forwarded token is ACCEPTED by  -> the audience-scope question, and the
     /api/datasource/v1/datasources               most likely place the design dies

SAFETY: never prints a raw token. JWTs are decoded (payload only, no signature check)
and reported as claims. Other secrets are reported as name + length.
"""
import base64, json, os, sys, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

HOST = os.environ.get("DOMINO_API_HOST", "")
PROXY = os.environ.get("DOMINO_API_PROXY", "http://localhost:8899")
PORT = int(os.environ.get("PORT", "8888"))

# Header names that may carry viewer identity. We report every inbound header name
# regardless, so an unexpected one still shows up.
IDENTITY_HINTS = ("authorization", "domino-username", "domino-user", "domino-user-id",
                  "x-domino-username", "x-domino-user", "x-forwarded-user",
                  "x-forwarded-email", "x-auth-request-user", "remote-user")


def jwt_claims(token):
    """Payload claims of a JWT, without verifying the signature. None if not a JWT."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        seg = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(seg))
    except Exception:
        return None
    keep = ("sub", "preferred_username", "email", "aud", "azp", "iss", "exp", "scope", "typ")
    return {k: claims[k] for k in keep if k in claims}


def api(path, token, label):
    """GET a Domino API path with an explicit token. Returns a compact result dict."""
    if not HOST:
        return {"label": label, "error": "DOMINO_API_HOST unset"}
    req = urllib.request.Request(HOST + path, headers={
        "Authorization": "Bearer " + token, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = json.loads(r.read() or b"{}")
        return {"label": label, "status": r.status, "body": summarize(path, body)}
    except urllib.error.HTTPError as e:
        return {"label": label, "status": e.code, "body": (e.read()[:300]).decode("utf8", "replace")}
    except Exception as e:
        return {"label": label, "error": f"{type(e).__name__}: {e}"}


def summarize(path, body):
    if "users/v1/self" in path:
        u = body.get("user", body)
        return {k: u.get(k) for k in ("id", "userName", "email") if k in u}
    if "datasources" in path:
        ds = body.get("dataSources", [])
        return {"count": len(ds), "names": [d.get("name") for d in ds][:10]}
    return str(body)[:200]


def sidecar_token():
    try:
        with urllib.request.urlopen(PROXY + "/access-token", timeout=10) as r:
            return r.read().decode().strip()
    except Exception as e:
        return f"<unavailable: {type(e).__name__}>"


def container_identity():
    """Section 1 -- run once at startup. Whose container is this?"""
    out = {"domino_env": {}, "sidecar_token": None, "self": None}
    for k, v in sorted(os.environ.items()):
        if k.startswith("DOMINO_"):
            secret = any(s in k for s in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
            out["domino_env"][k] = f"<set, {len(v)} chars>" if secret else v
    tok = sidecar_token()
    out["sidecar_token"] = f"<{len(tok)} chars>" if not tok.startswith("<") else tok
    if not tok.startswith("<"):
        out["sidecar_claims"] = jwt_claims(tok)
        out["self"] = api("/api/users/v1/self", tok, "container identity")
    return out


CONTAINER = container_identity()
print("=" * 78, flush=True)
print("SECTION 1 -- CONTAINER IDENTITY (expect the PUBLISHER)", flush=True)
print(json.dumps(CONTAINER, indent=2), flush=True)
print("=" * 78, flush=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # keep Domino's app log readable
        pass

    def do_GET(self):
        report = {
            "all_inbound_header_names": sorted(k.lower() for k in self.headers.keys()),
            "identity_headers": {},
            "forwarded_token": None,
            "forwarded_token_tests": [],
        }

        for name in IDENTITY_HINTS:
            raw = self.headers.get(name)
            if raw is None:
                continue
            if name == "authorization":
                tok = raw.split(None, 1)[1] if " " in raw else raw
                report["identity_headers"][name] = {
                    "scheme": raw.split(None, 1)[0] if " " in raw else "<none>",
                    "length": len(tok), "jwt_claims": jwt_claims(tok)}
                report["forwarded_token"] = tok
            else:
                report["identity_headers"][name] = raw

        # Section 3 -- the audience-scope test. Does a Domino API accept the forwarded token?
        tok = report.pop("forwarded_token")
        if tok:
            report["forwarded_token_tests"] = [
                api("/api/users/v1/self", tok, "whose identity does the FORWARDED token resolve to?"),
                api("/api/datasource/v1/datasources?limit=50", tok, "does it list data sources?"),
            ]
        else:
            report["forwarded_token_tests"] = [
                {"note": "No inbound Authorization header. Identity propagation is OFF, or this "
                         "request did not carry one. Per-viewer querying is not possible as-is."}]

        print("SECTION 2/3 -- REQUEST FROM A VIEWER", flush=True)
        print(json.dumps(report, indent=2), flush=True)

        page = {"container_identity_section_1": CONTAINER, "this_request_sections_2_3": report}
        body = json.dumps(page, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    print(f"viewer-identity probe listening on 0.0.0.0:{PORT}", flush=True)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
