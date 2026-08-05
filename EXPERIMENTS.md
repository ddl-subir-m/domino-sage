# Phased build: the cost/quality experiment

The question this answers, in Etan's words: *"if it's 98% the same quality but token costs go from
$80 to $1, that's a great blog post."* And its corollary — if it isn't, cut the feature.

## What is actually being tested

Two independent levers, which is why the toggle and the model are separate controls:

| | which model executes | how the work is split |
|---|---|---|
| lever | Model → Model assignments → **Implement** | Auto mode → **Build in phases** |

**Vary them independently or the result is uninterpretable.** If one switch flipped both and the run
came back cheap and good, you could not say whether the saving came from the cheap model, from
phasing, or whether phasing merely stopped the cheap model falling over. Hence a 2×2.

| | single context | phased |
|---|---|---|
| **cheap implement** (`bedrock-qwen3-coder`) | **A** — today's default | **C** — Etan's "extreme savings" |
| **strong implement** (`gpt-5.4`) | **B** — quality ceiling | **D** |

**A vs C is the experiment.** It is the only pair that isolates what phasing buys. B sets the quality
bar to judge them against. D is the least informative — run it last or not at all.

> **`bedrock-qwen3-coder` is qwen3-coder-30b** (`MODELS.md:12`) — the 30B model from the call is
> already Sage's default implement tier. So the *cheap* arm is the untouched baseline and the
> *strong* arm is the deliberate change. Don't go looking for a dumber model to plug in; A is it.

## Before you start

1. **The gateway must be up.** `GATEWAY_BASE_URL` is
   `https://apps.cloud-dogfood.domino.tech/apps/llm_gateway/v1`. An nginx 404 (HTML, not JSON) means
   the app is stopped — nothing in Sage can be measured until it's back.
2. **Confirm cost attribution works, once.** Run any one turn, then open the dashboard
   (`<gateway>/#usage`, the "Usage & cost" button in Sage's header) and check that **`sage-project`
   appears in the Group By dropdown** as "By Tag: sage-project", with your `<owner>/<project>` row
   carrying non-zero Requests / Total Tokens / Cost. If the tag isn't there, every number below is
   unobtainable — stop and fix that first. Requires gateway admin.
3. **Sensitivity lock off.** It forces sovereign models regardless of the Implement assignment and
   would silently void the model arm of every run.

## Per-run setup

Each run **must start from a clean workspace.** The first-build plan gate only fires when
`has_built` is false, and a second run in the same workspace builds on top of the first app instead
of building it — different work, incomparable cost. There is no reset endpoint, so:

```bash
# fresh workspace per run; restart Sage pointed at it
export SAGE_WORKSPACE_DIR=/path/to/runs/run-A
```

Then, in order:

1. **Set the Implement model.** Model chip → Model assignments → Implement:
   - runs A, C → `bedrock-qwen3-coder`  (the default; leave it alone)
   - runs B, D → `gpt-5.4`

   Or via API: `POST /api/project/model  {"catalog": {"implement": "gpt-5.4"}}`

   Leave **plan** on `gpt-5.4` in all four runs. Varying the planner too would add a third dimension
   to a 2×2. Overrides persist to `.sage/model_overrides.json`, so set this *after* the fresh
   workspace exists, not before.

2. **Set the phased toggle.** Auto mode chip → **Build in phases**:
   - runs A, B → off
   - runs C, D → **on**

   **Turn it on before the planning turn, not before approving.** The toggle changes the *shape* the
   plan is written in; a plan written without it won't parse into steps and will silently build in
   one context, quietly turning run C into a duplicate of run A.

3. **Note the starting totals.** In the dashboard, group by `tag:sage-project`, find your row, and
   record Total Tokens and Cost (0 if the row doesn't exist yet). The dashboard is cumulative and
   its time filter is day-granular, so **each run's cost is the delta across it** — that's the
   measurement, not the absolute figure.

## The prompt

Identical, verbatim, in all four runs. Any wording change invalidates the comparison. This is Sage's
shipped **Fraud review queue** starter:

```
Build a fraud review queue: a table of flagged transactions with fraud score, amount, and status;
filters by score and date; a row detail drawer; and approve/escalate actions. Use synthetic data
with truncation-safe tables and empty and zero-result states.
```

Chosen over the smaller starters for three reasons, each load-bearing:

- **It's large enough for single-context to actually degrade.** The premise of phasing is that a
  cheap model comes apart as context accumulates. On a small app run A never gets there, phasing has
  nothing to fix, and the only thing measured is bootstrap overhead — a rigged result. This one has
  a data model, a table, filters, a drawer, actions and two empty states: naturally 5–7 phases.
- **Its parts share one data model.** The transaction type runs through the table, the filters, the
  drawer and the actions, which makes this the best available probe for **cross-phase amnesia** —
  phase 4 inventing a second `Transaction` shape because it never saw phase 1. That's the failure
  that would kill the feature on quality rather than cost, and typecheck only catches some of it. A
  prompt whose parts don't share state cannot surface it at all.
- **Two distinct empty states** (nothing flagged; filters match nothing) — checkable, and among the
  first details a degraded model drops.

**Optional fifth run, if A vs C comes out ambiguous:** repeat the A/C pair with the much smaller
**Dataset explorer** starter. If phasing wins on the big app and loses on the small one, that isn't
a contradiction — it's the size threshold where the per-phase bootstrap tax stops paying for itself,
and it's the most useful thing this experiment could produce for deciding when the toggle should
default on.

## Running one experiment

1. Paste the prompt. Sage gates and proposes a plan.
2. **Record the plan.** Save `.sage/plan.md` before approving — it's archived to `.sage/plans/NNN.md`
   on approve. For C and D, check the Approve button reads **"Approve & build (N phases)"**. If it
   just says "Approve & build", the plan didn't parse into ≥3 steps and this run is really an A/B
   run — note it and rerun rather than reporting it as phased.
3. Approve. Don't intervene, don't send follow-ups — one turn in, one build out. If it fails, record
   the failure as the result; a build that doesn't finish is a real outcome, not a void run.
4. Record wall-clock from approve to the final `done`.
5. Refresh the dashboard, re-read Total Tokens and Cost for `sage-project`, and subtract.
6. **For C and D only:** group by `tag:sage-session` to get the per-phase split. Each phase runs in
   its own session, so this is where the per-session bootstrap tax shows up — see below.
7. Screenshot the finished app.

## Scoring quality

Etan's method is a subjective 1–10 and that's fine as the headline, but record the concrete checks
too so "98% the same" is defensible:

| check | from the prompt |
|---|---|
| Table of flagged transactions renders | ✅ / ❌ |
| Shows **fraud score**, **amount** and **status** | ✅ / ❌ |
| Filter by **score** works | ✅ / ❌ |
| Filter by **date** works | ✅ / ❌ |
| Row click opens a **detail drawer** | ✅ / ❌ |
| **Approve** and **escalate** actions present and wired | ✅ / ❌ |
| Synthetic data is realistic, not `foo`/`bar`/lorem | ✅ / ❌ |
| **Empty state** (nothing flagged) present and actionable | ✅ / ❌ |
| **Zero-result state** (filters match nothing) is distinct from it | ✅ / ❌ |
| Long cells truncate **with tooltips** | ✅ / ❌ |
| App compiles and renders without console errors | ✅ / ❌ |

**Also check specifically for cross-phase amnesia in runs C and D** — this is the failure mode
phasing is most likely to introduce, and cost numbers can't see it. Grep the workspace for a
duplicated transaction type or two components rendering the same data differently:

```bash
grep -rn "interface Transaction\|type Transaction" src/
```

More than one definition, or a drawer whose fields don't match the table's, is amnesia — record it
even when the app looks right in the preview.

Plus: **did it need follow-up turns to be usable?** A build that's cheap but takes three rounds of
fixes isn't cheap. Note any turn you'd have had to send.

## Results

| run | implement | phased | tokens | cost | wall clock | checks passed | subjective 1–10 |
|-----|-----------|--------|--------|------|-----------|---------------|-----------------|
| A | `bedrock-qwen3-coder` | no  | | | | /11 | |
| B | `gpt-5.4`             | no  | | | | /11 | |
| C | `bedrock-qwen3-coder` | yes | | | | /11 | |
| D | `gpt-5.4`             | yes | | | | /11 | |

For C and D also record the per-phase split:

| phase | session id | input tokens | output tokens | cost |
|-------|-----------|--------------|---------------|------|

## Reading the result

**The number that decides the feature is the input-token floor across phases in run C.** Every phase
pays a fresh session bootstrap — OpenCode re-reads `AGENTS.md` and project context each time. At
~3k tokens × 6 phases that's ~18k tokens of pure repetition, and on a build this size it can exceed
whatever phasing saves. The per-phase table is what makes it visible: if every phase's input tokens
start at a similar high floor regardless of how small the step is, that floor is the tax.

Decision rules, decided in advance so the numbers aren't read to taste:

- **C ≈ B on quality, and C ≪ B on cost** → the feature works. This is the blog post.
- **C ≈ A on cost** → phasing bought nothing; the bootstrap tax ate it. Cut the toggle.
- **C < A on quality** → phasing actively hurt, most likely cross-phase amnesia (phase 4 reinventing
  something phase 1 built, which typecheck can't catch). Look at the app for duplicated types or
  components before concluding anything about cost.
- **A ≈ B on quality already** → the cheap model never needed rescuing on work this size, so phasing
  is solving a problem you don't have here. Retest with a substantially larger prompt before cutting.

## Known gotchas

- **A plan with fewer than 3 parsed steps silently builds in one context** (`MIN_STEPS`,
  `plan_steps.py`). The Approve button's "(N phases)" is the only signal. Check it every time.
- **Sage shows no in-app cost figure**, by design — the gateway prices calls with per-alias rates no
  client can see, and Anthropic/Bedrock don't return usage in-band anyway. The dashboard is the only
  source of truth.
- **`#usage` requires gateway admin.** The non-admin `#mine` view has no tag filtering at all.
- **Phased runs create N+1 sessions** (the project session plus one per phase), so a `sage-session`
  group shows several rows for one run. Sum them; cross-check against the `sage-project` delta.
- **A failed phase aborts the build but keeps earlier phases on disk**, and does not commit. The
  workspace will hold partial work — that's intended, but don't score it as a finished app.
