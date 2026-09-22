# Domino Platform — Technical Reference

Task-oriented reference for building apps against the Domino Datasets, Governance, and Taxonomy APIs. Organized by what you're trying to do, not by history — each section is self-contained: the API to call, working code, and the traps that produce silently-wrong results (empty lists, wrong content, 500s) rather than a clean error.

Extend a section in place when you learn more about that API; don't append new dated entries at the bottom.

---

## 0. Should you call a Domino API at all?

Only if the prompt actually asks for it. Signals that you need one of the sections below: "latest snapshot," "not the read/write head," "reproducible," "governance/approval status," "what changed between versions," "any study," "tagged `study:<ID>`," "only show what I can access."

If none of that is present and you're just handed files, use them directly:
```python
import pandas as pd
df = pd.read_csv("uploaded_files/whatever.csv")
```
Don't pre-build dataset/snapshot/governance/taxonomy machinery the prompt didn't ask for — it's unrequested scope and each added call is a new way to fail in front of the user. Escalate to the sections below only when the prompt's wording crosses into one of those signals.

---

## 1. Auth: use the sidecar token — the incoming request's `Authorization` header is NOT usable against these APIs

```python
def get_sidecar_token():
    return requests.get("http://localhost:8899/access-token", timeout=10).text.strip()
```
This is a short-lived JWT (~5 min) — fetch it per request/short batch, never cache it for the app's lifetime. Use it for every call in this reference (§2–§7): dataset/snapshot listing, Taxonomy, Governance, and content reads.

**Trap — do NOT forward the visiting browser's own `Authorization` header (if your app happens to receive one) to any Domino API expecting `$DOMINO_API_HOST`/nucleus-frontend.** It is tempting, because it looks like the obvious way to get true per-viewer access filtering, and other Domino-authored code snippets (e.g. a dataset's own "landing page" example, `request.headers.get('Authorization')[7:]`) use exactly this pattern for a *different* purpose. In practice it produces:
```
com.nimbusds.jwt.proc.BadJWTException: Token failed audience restriction violation
for expected audiences List(domino-platform, flyteagent-client)
```
i.e. that token's audience is not accepted by nucleus-frontend at all, regardless of which user it represents. Confirmed against a real deployed app (not reproducible from a workspace/notebook session, where no such header exists and the app's own proxy 404s on an ad hoc port before the check even applies — see §8 on validating from a sandbox). **There is currently no confirmed way to obtain a token, scoped to the specific visiting user, that carries an audience nucleus-frontend accepts.** Until there is, every function in this reference reflects what the app's own service identity (the sidecar token) can access — not a per-visitor distinction. If a requirement genuinely needs "only show what *this specific person* can access," say so explicitly rather than silently building a filter on the app's own identity and calling it done — see the note this produces at the end of §7.

---

## 2. Locating a dataset's name/ID

Check `.sage/project-resources.json` at the project root — this is how a Sage-built project tracks datasets a user attached as resources:
```python
import json
data = json.loads(open(".sage/project-resources.json").read())
for item in data["items"]:
    if item["kind"] == "dataset":
        name = item["name"]                       # e.g. "ABC123_ADAE"
        dataset_id = item["id"].split(":", 1)[1]   # "dataset:6aa..." -> "6aa..."
```
If a dataset isn't listed there, ask for it — or, better, ask the user to paste the code snippet from that dataset's own "landing page" in the Domino UI. That snippet is a more reliable source for exact API usage than any generic skill doc (see §4).

For discovering datasets by *tag* across the whole platform rather than one known project, see §6.

---

## 3. Dataset snapshots: listing, and resolving "latest"/"previous"

```python
base = os.environ["DOMINO_API_HOST"]
r = requests.get(f"{base}/api/datasetrw/v1/datasets/{dataset_id}/snapshots",
                  headers=headers, params={"limit": 200, "offset": 0})
snapshots = r.json()["snapshots"]              # [{id, datasetId, creatorId, createdAt, status}, ...]
snapshots.sort(key=lambda s: s["createdAt"])   # ascending = version order; no order_by param exists
for i, s in enumerate(snapshots):
    s["version"] = i                           # 0-based; matches snapshotVersion in governance data
latest = snapshots[-1]
previous = snapshots[-2] if len(snapshots) > 1 else None
```

**Trap — the mutable head is not the latest snapshot.** They can and do differ (observed live: one dataset's head had 110 subjects, its latest snapshot had 96). Whenever "reproducible" or "snapshot, not head" is in scope, always pin an explicit snapshot ID (§4) — never read the dataset's default/head content and assume it matches the latest snapshot.

---

## 4. Reading a dataset's file content, pinned to a specific snapshot

```python
from domino_data.datasets import DatasetClient, DatasetConfig

client = DatasetClient(token=token)                            # token from §1 (or omit to use the sidecar identity by default)
dataset = client.get_dataset(f"dataset-{name}-{dataset_id}")  # this exact handle form - required, see trap
if snapshot_id:
    dataset.update(config=DatasetConfig(snapshot_id=snapshot_id))  # omit to read the mutable head
files = dataset.list_files()          # discover the filename(s); don't assume one
content = dataset.get(filename)       # bytes, pinned to that snapshot
```

**Use this whenever you need file bytes for a *specific* snapshot, or for a dataset that isn't attached to the current project.** The plain-name form, `DatasetClient().get_dataset(name)`, only resolves datasets already wired up as a Data Source in the current project and raises `No Data Source found with name ...` otherwise — it also has no notion of a snapshot at all. The `dataset-<name>-<id>` handle resolves by ID directly, works cross-project, and (via `DatasetConfig(snapshot_id=...)`) supports pinning. None of this is documented in the bundled `domino-data-sdk` skill, and a grep of the SDK source for "snapshot" turns up nothing — don't take that absence as proof the platform can't do it.

**Trap:** build a fresh `DatasetClient()`/`Dataset` per fetch. `dataset.update()` mutates shared state on the object, so reusing one instance across concurrent requests that pin different snapshots will race.

`DatasetClient(token=...)` accepts any valid token in principle — per §1, that's the sidecar token today, not a per-viewer one.

---

## 5. Tying a dataset snapshot to its governance approval status

```python
gov = f"{base}/api/governance/v1"
att = requests.get(f"{gov}/attachment-overviews", headers=headers,
                    params={"identifier.datasetId": dataset_id, "identifier.snapshotId": snapshot_id}
                    ).json()["data"]
if att:
    bundle_id = att[0]["bundle"]["id"]
    bundle = requests.get(f"{gov}/bundles/{bundle_id}", headers=headers).json()             # live policyName/stage
    approvals = requests.get(f"{gov}/bundles/{bundle_id}/approvals", headers=headers).json()  # bare array
```

**Trap:** the `bundle` object embedded inside the attachment-overview can be stale (seen with an empty `policyName` even after a policy was attached). Always re-fetch `GET {gov}/bundles/{id}` directly for current policy/stage rather than trusting the embedded copy.

**Trap:** `ApprovalStatus` values are `PendingSubmission | PendingReview | Approved | ConditionallyApproved | PendingExpiration | Expired` — there is no literal `"Rejected"`. If the use case wants an approved/pending/rejected tri-state, bucket `Expired` as the closest "not approved, and won't become approved" state; don't assume a rejection status exists to check for.

Resolve a `creatorId`/`createdBy` user ID to a display name via:
```python
name = requests.get(f"{base}/api/users/v1/user/{user_id}", headers=headers).json()["user"]["fullName"]
```
**Trap:** the path is singular, `/user/{id}` — the plural `/users/{id}` 404s.

---

## 6. Discovering datasets by tag (e.g. "all datasets tagged `study:<ID>`")

Domino's Taxonomy API models this as namespaces containing tags, applied to entities (datasets, projects, etc.). If studies are tagged with a namespace called e.g. `study` and one tag per study ID:
```python
def taxonomy_get(path, params=None):
    r = requests.get(f"{cluster_url()}/api/taxonomy/v1{path}", headers=headers, params=params)
    r.raise_for_status(); return r.json()

ns_id = next(n["id"] for n in taxonomy_get("/namespaces", {"limit": 200})["data"] if n["label"].lower() == "study")
tags = [t for t in taxonomy_get("/tags", {"namespaceId": ns_id, "limit": 200})["data"] if t["entityCount"]["dataset"] > 0]
for tag in tags:
    datasets = taxonomy_get("/entities", {"tagIds": tag["id"], "entityType": "dataset"})["data"]
    # each entity has entityName (e.g. "ABC123_ADSL") and datasetProps.apiName,
    # which is ALREADY the "dataset-<name>-<id>" handle from §4 - don't rebuild it.
```
This is org-wide catalog metadata (which tags/datasets exist) — use the sidecar token here, same as the access filter in §7.

**Trap — Taxonomy needs the *external* cluster URL, not `$DOMINO_API_HOST`.** `$DOMINO_API_HOST/api/taxonomy/v1/...` can 404 even when Governance and Core both work fine on that host. Derive the external URL from the token's `iss` claim instead of assuming any one host serves every API:
```python
import base64, json, re
def cluster_url():
    token = get_sidecar_token()
    payload = token.split(".")[1]; payload += "=" * (-len(payload) % 4)
    iss = json.loads(base64.urlsafe_b64decode(payload))["iss"]
    return re.sub(r"/auth/realms/.*", "", iss)
```
Don't assume any one skill's claim about which host serves which API holds on every cluster — verify each API family with one `curl` before building on it (governance vs. taxonomy vs. core can each route differently).

**Trap:** passing multiple `tagIds` (repeated) to `/entities` is an **AND** (intersection — an entity must have every tag listed), not an OR. It is not a way to fetch several different tags' entities in one call — loop per tag.

---

## 7. "Only show what the current viewer can access" — not currently solvable from inside the app

Per §1, there is no confirmed way to get a token scoped to the specific visiting browser user that Domino's own APIs will accept — so nothing below actually distinguishes one viewer from another today. What follows is (a) the best available proxy — what the *app's own service identity* can access, via the correct public endpoint — and (b) which endpoints looked like a per-viewer permission check and are not.

**If a prompt requires a genuine per-viewer distinction, say so explicitly rather than quietly implementing an app-identity-scoped filter and calling it done.** The gap is real, not a corner that was cut carelessly.

**The right endpoint for "what can this identity access," paginated and checked for membership** (there is no `tagIds`/direct-id filter, so check membership across pages):
```python
def list_accessible_dataset_ids(token, needed_ids, page_size=500, max_pages=20):
    found, offset = set(), 0
    for _ in range(max_pages):
        r = requests.get(f"{base}/api/datasetrw/v2/datasets", headers={"Authorization": f"Bearer {token}"},
                          params={"limit": page_size, "offset": offset})
        r.raise_for_status()
        items = r.json()["datasets"]          # trap: key is "datasets", not "data" - see below
        found |= {i["dataset"]["id"] for i in items if i["dataset"]["id"] in needed_ids}
        if len(items) < page_size or found >= needed_ids:
            break
        offset += page_size
    return found
```
Call this with the sidecar token per §1. It is the public, documented "Get Datasets the user has access to" endpoint — correct in shape, just not viewer-scoped until the §1 gap is resolved.

**Do NOT reach for either of these expecting a per-viewer permission signal:**
- `GET /api/datasetrw/v1/datasets/{id}`, treating 200-vs-error as an access check. Its error behavior is not a clean 200/403 split for every token shape — this can pass every sandbox test and still be wrong.
- `GET /v4/datasetrw/datasets-v2?tagIds=...&includeHasProjectAccess=true` — the same internal endpoint the Domino web UI calls for tag-filtered dataset listings, superficially attractive (access + dataset info in one call per tag). Any `/v4/...` path (as opposed to `/api/...`) is UI-internal, not part of the public contract, and can return a bare `500 Internal Server Error` for token shapes it isn't hardened for — don't rely on its response shape no matter how convenient it looks.

**Trap:** the response's top-level key is `datasets`, not `data` — most other paginated Domino endpoints in this reference use `data`. Reading `.get("data")` here returns nothing, so the pagination loop exits after "page 1" with zero results and no error — easy to misread as an access denial rather than a wrong key.

---

## 8. General debugging discipline for this API surface

- **Always capture the response body on an HTTP error, not just the status.** `requests`' default `HTTPError` message (`"500 Server Error: Internal Server Error for url: ..."`) discards the body, which is usually the only thing that explains a Domino API failure. Wrap it once, centrally:
  ```python
  try:
      r.raise_for_status()
  except requests.HTTPError as e:
      raise requests.HTTPError(f"{e} | response body: {r.text[:2000]}", response=r) from e
  ```
- **A suspiciously empty result is not proof of "no access" or "no data."** It's just as likely a wrong response key, wrong host, or an intersection filter behaving like AND instead of OR. Print the raw response shape before concluding the platform returned nothing.
- **Verify against the live API/SDK before telling a user something is unsupported.** A bundled skill's documented coverage, or an absence found by grepping SDK source, is a reason to *try something else* — not a reason to report a capability as missing. If the user offers a code snippet from the platform's own UI, treat it as more authoritative than a generic skill doc.
- Endpoint shapes, populated fields, and host routing are cluster/version-dependent. Hit an endpoint once with `curl` and read the real JSON before building on remembered field names — including the ones in this reference.
- **A workspace/notebook session cannot fully simulate a real deployed App's request context.** Anything that depends on what a *real visiting browser user's* request looks like (headers, forwarded tokens) genuinely cannot be validated from here — a `curl` from this sandbox only ever carries this run's own sidecar identity. Concretely, hitting this session's own port through the external authenticated proxy path (`{workspace-proxy-url}/proxy/{port}/...`, using `$VSCODE_PROXY_URI`'s template) returns a plain `404` from the proxy layer itself — a workspace's proxy doesn't expose arbitrary listening ports the way a published App's own routing does, so it isn't a usable stand-in either. When a requirement depends on the real request context of a deployed app, say plainly that it can't be verified from this sandbox, rather than reporting a guess as tested.

---

## 9. Frontend gotchas (not Domino-API-specific)

**CDN script order can silently break Ant Design.** Ant Design 5's UMD bundle calls `dayjs.extend(...)` during its own init; if `dayjs.min.js` loads after `antd.min.js`, this throws mid-script and `window.antd` never gets assigned — a blank `<div id="root">`, no visible error unless the console is checked. Load `dayjs` before `antd`:
```html
<script src="https://unpkg.com/dayjs@1.11.10/dayjs.min.js"></script>
<script src="https://unpkg.com/antd@5.11.2/dist/antd.min.js"></script>
```
After wiring up any CDN-based frontend, load it in a real (even headless) browser and check `console` output before declaring the UI done — a 200 on `index.html` is not proof the app renders. `code.highcharts.com` can 403 in some sandboxed networks; `cdnjs.cloudflare.com/ajax/libs/highcharts/...` is a working alternate host.

**A Highcharts inverted bar chart can render with a broken plot transform if created while its container is still settling** — the container/SVG can report the *correct* final width while the series' internal rotation transform was computed against a different, stale box, producing bars a few pixels long instead of full-width. Force a re-measure after creation:
```js
const chart = Highcharts.chart(el, opts);
requestAnimationFrame(() => chart.reflow());
return () => chart.destroy();  // in the effect's cleanup
```
If a chart renders with plausible data but visually wrong proportions, suspect a stale-container-size race before suspecting the data.

**Playwright's unquoted `text=` locator matches case-insensitively and substring-wide** — `wait_for_selector("text=DIARRHOEA")` can match an unrelated "Diarrhoea" elsewhere on the page and resolve before the thing actually under test has finished loading, producing a false-positive pass. Scope locators to the specific element under test (e.g. `.ant-table-tbody tr:has-text('DIARRHOEA')`) rather than a bare page-wide text match.
