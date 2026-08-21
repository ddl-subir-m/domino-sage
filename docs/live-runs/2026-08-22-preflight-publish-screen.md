# Live run — #21, #27, #32, #9

Four issues, one deployment, one pass. All four are code-complete and none is verified; this is the
only work any of them has left. Run them in this order — each step leaves the deployment in the state
the next one wants, and #9 is last because it is the only one that spends a real prediction.

**Where:** cloud-dogfood, builder on the current `main` (`826a955` or later).
**Before anything:** the Environment must have been rebuilt since `826a955`. #32 is agent guidance
baked into `/opt/sage` at image build time, so on a stale image #32 cannot pass and the other three
still can. If you are not sure, check the cache bust reached the image:

```bash
grep SAGE_CACHE_BUST /opt/sage/environment/Dockerfile   # expect 2026-08-22-unreachable-data-screen
```

Record the result in this file as you go. **A step you skipped is worth writing down as skipped** —
that is the whole lesson of #25.

---

## 0. Stage the one thing that expires

#21 needs an LLM Alias whose Hosted GenAI Endpoint is not Running. That case exists right now:
`local-domino-llm` points at `Mistral-7B-Instruct-v02`, which is `Stopped`, and the alias was granted
to the Sage caller on 2026-08-21 to settle Q1 of the spike. **Nobody is guarding that.** If someone
starts the endpoint or drops the grant, #21 becomes unverifiable until another one turns up.

Confirm it still holds before planning the rest of the session:

```bash
cd /mnt/code/spikes/domino-probes
GATEWAY_BASE_URL=https://apps.cloud-dogfood.domino.tech/apps/llm_gateway/v1 python3 alias_endpoint_join_probe.py
```

Expect, under **THE JOIN**:

```
JOIN  local-domino-llm  via url  -> Stopped  in/v1/models=yes  endpoint=Mistral-7B-Instruct-v02
```

- `in/v1/models=yes` and `Stopped` → good, continue.
- `in/v1/models=NO` → the grant is gone. Get it back, or pick another joined alias whose endpoint is
  not Running from the same output.
- No `Stopped` row at all → someone started it. Read the endpoint table for another candidate; if
  every joined alias is Running, **#21 cannot be verified this session** — say so and move on.

⚠️ Do **not** stop `qwen-2-5` to manufacture a case. It is `Consumer` access in another team's
project and the only Running endpoint of 18.

---

## 1. #21 — preflight names the model that will not answer

Two entry points, and the ticket asks for both. The Binding one needs no restart, so do it first.

### 1a. A Binding pointing at a stopped endpoint

1. Open any app in the builder.
2. Resources rail → LLM Aliases → pick **`local-domino-llm`**. It should bind.
3. Reload the builder so the session-open preflight runs.

**Expect**, as an amber warning in the chat pane with a "Show it in Resources" button, and the row
badged in the rail:

> This app is recorded using the LLM Alias Local Domino LLM, whose Hosted GenAI Endpoint
> Mistral-7B-Instruct-v02 is Stopped. Its calls will fail. Start that endpoint, or pick a different
> Alias, before you build on it.

Or read it without the UI:

```bash
curl -s localhost:8888/api/preflight | python3 -m json.tool
```

```
{"bindings": {"state": "problems", "bindings": [{"kind": "llm_alias", ..., "message": "…is Stopped…"}]}}
```

**What each outcome means**
- The sentence appears → criterion 1 met for a Binding, and the premise the whole issue rests on is
  confirmed in the product rather than only in a probe.
- `state: ok` → the join missed. Compare the alias's `endpoint_url` against the endpoint's `url` in
  the probe output above; the join strips one trailing `/v1`, and anything else about the two URLs
  differing is a real bug.
- `state: unreachable` with a Domino API error → the endpoints listing failed. Not a #21 failure —
  it is the "could not check" path working. Retry.

Unbind `local-domino-llm` afterwards, or #27 and #32 below inherit a warning that has nothing to do
with them.

### 1b. A model slot pointing at a stopped endpoint

Needs an Environment Variable and a restart, so it is the more expensive half.

1. Set `SAGE_MODEL_SOVEREIGN_PLAN=local-domino-llm` on the Sage Environment (`_build_catalog`,
   `orchestrator/app.py:115` — note the `_PLAN` suffix; the `.env.example` name
   `SAGE_MODEL_SOVEREIGN` is **not** the one the orchestrator reads).
2. Restart the workspace.

**Expect** in the App/workspace log at startup:

```
preflight: Sage's sovereign_plan model is set to the LLM Alias local-domino-llm, whose Hosted GenAI
Endpoint Mistral-7B-Instruct-v02 is Stopped. Turns that route to sovereign_plan will fail. Start
that endpoint, or pick a different model for that slot.
```

and the same sentence in the builder as an amber warning with an "Open model assignments" button.

```bash
curl -s localhost:8888/healthz | python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin)['preflight_slots'], indent=2))"
```

**What to check beyond the sentence**
- `status` is the verbatim Domino word (`Stopped`), not a paraphrase — the log line is where a
  maintainer reads it.
- Exactly **one** warning for that slot. Two would mean `unresolved_slots` and the endpoint check
  both fired, which they are supposed to be unable to do.
- Reassigning the slot in the model panel takes the warning down.

Put `SAGE_MODEL_SOVEREIGN_PLAN` back afterwards.

### 1c. The cost criterion

Point every slot at vendor aliases (the default catalog does), leave no LLM Alias Binding, and
restart. The endpoints listing should not be fetched at all. There is no log line for this — the
observable is that a gateway with no hosted alias behaves exactly as it did before #21. If you want
it proven rather than assumed, note that as unproven; the unit test covers it and a live run does not
add much.

---

## 2. #27 — the hub's Publish says which queries will not answer

Needs an app with a broken query **committed to the default branch**, because the hub reads git, not
a workspace.

1. In the builder, on an app with a bound Data Source, open `.sage/queries.json` and add a query that
   uses a placeholder it never declares — the mistake an agent actually makes:

```json
{"name": "revenue", "binding": "<the bound source id>",
 "sql": "SELECT REGION, SUM(ARR_USD) FROM FCT_SUBSCRIPTION_REVENUE WHERE MONTH >= :since",
 "params": []}
```

2. **Commit it.** Stop the workspace, or run a build turn — the hub cannot see an uncommitted file.
3. Open the hub gallery. Click **Publish** on that app.

**Expect** an amber banner, *not* the confirm dialog:

> ⚠ Some of this app's queries won't answer
> *(the app's own sentence about `revenue` and `:since`)*
> Open the app in the builder and ask the agent to fix them, or publish now and deploy the app with
> them still broken.
> **[ Publish anyway ]**

Or read it directly. The hub is a Domino App, not a port on your workspace, so this runs from the
hub's own workspace (where `SAGE_CONTROL_PORT` defaults to 8888, same as the builder) or against the
hub's browser URL:

```bash
curl -s "localhost:8888/api/apps/<project-id>/publish-check" | python3 -m json.tool
```

**What each outcome means**
- Banner appears, and **"Publish anyway" publishes** → criteria 1 and 2 met. Click it; the publish
  must go through. This informs, it does not refuse.
- `{"checked": true, "queries": []}` → the commit did not land, or the query is not actually broken.
  Check the file is on the branch.
- `{"checked": false, ...}` → Sage could not check. Read the hub log for
  `publish-check: couldn't read the manifests from …`. Not a clean bill, and not a block either —
  confirm the publish still works.
- The confirm dialog appears instead of the banner → the gate did not run. That is the bug.

**Then check the other half, which is easy to forget:** publish a *clean* app from the gallery. It
must go straight to the confirm dialog it always had, with no banner and no extra step.

---

## 3. #32 — a screen whose data is unreachable says so everywhere

The only one that needs a **fresh build** on the rebuilt image, because it is testing what the agent
writes.

1. Bind a Data Source and scope it to a table (`BigQuery_Demo` /
   `advertising_claus_murmann.clickstream` is the path already proven in #25).
2. Ask for a dashboard with filters — the shape that failed on 2026-08-21: hero stats, a filter
   panel, charts. Let the build finish. Since #24 the queries answer, so the preview should show a
   working app with real rows.
3. Now make the data unreachable: **unbind the Data Source** in the Resources rail. Every query then
   names a Binding the app no longer records, and `serve.py` refuses each one with its own sentence —
   which is the same state a published app reaches when its store is down.
4. Reload the preview.

**Expect** — and this is the whole issue, so read the *whole screen*, not the data pane:

| | Pass | Fail (what was observed on 2026-08-21) |
|---|---|---|
| Filter controls | Disabled, with a reason beside them | Live selects holding only `All` |
| Primary button | Not a filled button that does nothing | Filled "Apply filters", does nothing |
| Hero pills / headings | Gone, or dimmed with the same reason | "Date range ready", "Platform and device filters" |
| Overall reading | **"not yet"** | "working, but empty" |

**What each outcome means**
- The screen reads "not yet" → criteria 1, 2, 3 and 5 met. Screenshot it into this file; it is the
  before/after the issue is written around.
- The data pane is right and the controls still look live → the guidance did not land. Check the
  image was rebuilt (step 0), then check the rule is actually in the app's `AGENTS.md`:
  `grep -c "WHOLE SCREEN says so" /mnt/code/AGENTS.md` — 0 means a stale image, 1 means the agent read
  it and did not follow it, which is a different and more interesting failure worth recording.

Re-bind the Data Source afterwards.

---

## 4. #9 — a published app gets a prediction from a Model API

Last, because it is the only step that spends a real prediction, and the only one whose criterion has
been open since August with nothing blocking it.

1. Resources rail → Model APIs → pick one that is **Running**.
2. Paste Domino's own sample request snippet from that model's Overview page when asked. Sage parses
   the URL and token out of it and verifies them against the model.
   - A refusal renders under the paste box with the model's own words beneath it. A **400 is a pass**
     — it means the token authenticated and only the body was wrong.
3. Ask for a small app that calls the model and shows the prediction.
4. Publish.
5. **Open the published app in its own tab.** Not through the `/modelproducts` wrapper — the wrapper
   makes console checks lie.
6. Trigger a prediction.

**Expect** a prediction rendered in the browser, from a `fetch` the page made directly to the model.

**What each outcome means**
- A prediction appears → criterion 4 closes, and #9 closes with it.
- 401 → the pasted token is stale or was copied from the wrong Overview tab. Sage checks the id at
  paste time, so this should have been caught earlier; if it was not, that is a finding.
- A failure with the model's own message shown raw in monospace → criterion 3 working as reworded.
  Worth recording as a pass for that criterion even if the prediction itself did not land.

---

## What to do with the results

For each issue: comment the outcome on the ticket, and **close only what actually ran**. Anything
skipped or blocked goes in the "not run" list below, named, so the next session starts from the truth
rather than from an assumption.

### Result — fill in

| Issue | Ran? | Outcome |
|---|---|---|
| #21 Binding (1a) | | |
| #21 slot (1b) | | |
| #21 cost (1c) | | |
| #27 warning | | |
| #27 clean app keeps its old flow | | |
| #32 whole-screen state | | |
| #9 prediction in the browser | | |

### Not run, and why

-
