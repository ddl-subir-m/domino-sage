# Viewer-identity probe

Answers the one question that decides whether Sage can ever query a data source **per
viewer** instead of per publisher:

> Does Domino forward the viewer's identity, **and a usable token**, to a published app --
> and will a Domino API accept that token?

Everything else about the resource browser is scoped. This is not.

## Check the flag first -- it may answer this for free

Per-viewer identity propagation is gated on a **SysAdmin-only, irreversible** platform
setting. Ask an admin whether either is enabled on cloud-dogfood:

```
SecureIdentityPropagationToAppsEnabled
com.cerebro.domino.apps.extendedIdentityPropagationToAppsEnabled
```

**If both are off, stop here.** No viewer token will arrive, per-viewer querying is not
available, and the build-time slice is the only option. Publishing this probe would only
confirm a known answer.

## Deploy

**Do not publish this from the sage repo's main branch.** A Domino App runs
`/mnt/code/app.sh`, and this repo's root `app.sh` is the Sage Hub -- publishing would
replace it.

Use a throwaway project instead:

1. Create a new Domino project (any name, e.g. `viewer-identity-probe`).
2. Copy **both** files to that project's root: `app.sh` and `probe_server.py`.
3. Publish it as an App. No environment or dependency changes -- the probe is
   stdlib-only Python and needs no build step.
4. Open the App URL in a browser. It returns JSON and also prints to the App log.

For the real answer, have **a second person** open it, or open it as a different user.
Viewing it as the publisher cannot distinguish publisher identity from viewer identity --
they are the same person, so the test proves nothing.

## Reading the output

| Section | Question | Expected if propagation is OFF |
|---------|----------|--------------------------------|
| 1 -- container identity | Whose identity does the container hold? | The **publisher**. This is the known Q3 finding. |
| 2 -- request headers | Does a viewer identity/token arrive? | No `authorization` header, no `domino-username`. |
| 3 -- forwarded-token tests | Is the token accepted by a Domino API? | Not run -- no token to test. |

Section 3 is the real crux, and the most likely place a live design dies. A viewer token
can arrive and still be **rejected** by `/api/datasource/v1/datasources` because its
audience is scoped to the app rather than the platform API. Section 3 calls
`/api/users/v1/self` with the forwarded token, so the output names *whose* identity it
resolves to -- publisher or viewer -- rather than leaving it to inference.

## Decision this drives

- **Viewer token arrives AND is accepted** -> per-viewer querying is real. The
  `Individual`-credential hazard becomes a policy question, not a blocker, and runtime
  querying is worth designing.
- **Token arrives but is rejected (audience scope)** -> the app can know *who* is looking
  but cannot act as them. Viewer-scoped authorization only: the server filters, using the
  publisher's credentials.
- **No token** -> build-time slice only.

## Safety

Read-only, and it never prints a raw token. JWTs are decoded payload-only with no
signature check and reported as selected claims; other secrets are reported as name and
length. All API calls are GETs.
