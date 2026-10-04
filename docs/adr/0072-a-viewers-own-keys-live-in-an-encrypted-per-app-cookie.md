---
status: accepted
extends: ADR-0008 (a Project holds many Built Apps, published from the builder's own Project — so a
  published App already starts with the builder's Project variables), ADR-0071 (Sage writes a
  Project's secrets as Domino Project variables and holds none at rest)
---

# A viewer's own keys live in an encrypted, per-App cookie

A Built App reads a key with `secret("NAME")` (`template/fastapi-antd/sage_secrets.py`, #644). The
builder's value is the Domino Project variable of that name, which a published App gets at start
because it is published from the builder's Project (ADR-0008). A viewer of the published App may
want to use their own key instead: their own CRM token, their own Model API token. `secret()`
returns the viewer's value when they set one, else the builder's, else the default.

## Where a viewer's value lives

In the viewer's browser, in one cookie per App: `sage_keys_<appId>`, holding their `{name: value}`
map sealed with AES-GCM (`cryptography`). The key is HKDF-SHA256 over the Project variable
`SAGE_APP_KEY` (32 random bytes, which the secrets backend creates once before any publish or
preview and never overwrites, #641), with the app id as its info. The cookie is `HttpOnly`,
`Secure`, `SameSite=Strict`, and its `Path` is the App's own base. The server cannot know that base,
because Domino's proxy strips it, so the page sends it when it saves.

The server keeps nothing. A database or a file per viewer would make Sage, or the App, a store of
other people's credentials, which ADR-0071 says Sage is not. A Domino user-level variable cannot be
written safely: `PUT /v4/users/environmentVariables` replaces the whole map (#640).

The cookie is bound to its App in two ways. A different app id gives a different key, so App B's
server cannot open App A's cookie even if a browser sent it. And the `Path` keeps the browser from
sending it to App B at all.

## Which names a viewer may set

Only the names the App's own code reads. At publish (`refresh_entry_script`) and when the preview
starts, Sage scans the app's own Python for `secret("NAME")` and `secret('NAME')` literals and
writes `sage_keys.json` beside the server: names, plus notes from the Project's `.sage/secrets.json`.
It never holds values. Names starting with `SAGE_` or `DOMINO_` are left out, because a viewer must
not be able to replace Sage's or the platform's own variables. The `/sage/keys` routes accept no
other name and never return a value, only whether each one is set. They refuse any request sent
with `Sec-Fetch-Site: cross-site`.

The Your keys button is Sage-owned (`static/sage/keys.js`), and `sage_serve.py` adds it to every page
it serves, so the model never writes key-entry code. It stays hidden on an App that reads no key.

## Without `SAGE_APP_KEY` or `cryptography`

`secret()` falls back to the environment, and the Your keys page says viewer keys are unavailable.
`cryptography` entered the backend venv's lockfile with #644. Both the published App and the preview
serve from that venv (`/opt/sage/backend/.venv`), so **viewer keys need an Environment built after
#644**. An older image keeps serving builder keys.

## Accepted risk: one origin for every App

All Domino Apps share one origin, so one App's page can make requests to another App's endpoints,
and the browser attaches the viewer's cookie for that App's path. Encryption stops another App from
reading or forging the keys. It does not stop that App from triggering a request that uses them.
`SameSite=Strict` and the cross-site refusal do not help here, because the request is same-site.
We accepted this for the POC (#640). The fix is a per-App origin, which Domino does not offer today.

## Consequences

- A changed builder secret reaches a published App on its next publish. A viewer's change takes
  effect on their next request.
- Rotating `SAGE_APP_KEY` silently clears every viewer's keys: their cookies stop opening, and they
  are read as empty.
- A cookie holds about 3.8 KB, so a viewer's keys for one App must fit in that. A save that would
  overflow it is refused with a message rather than dropped by the browser.
