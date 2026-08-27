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
| **cheap implement** (`GLM-5.2`) | **A** | **C** — Etan's "extreme savings" |
| **strong implement** (`gpt-5.4`) | **B** — quality ceiling | **D** |

**A vs C is the experiment.** It is the only pair that isolates what phasing buys. B sets the quality
bar to judge them against. D is the least informative — run it last or not at all.

> **The cheap arm is a stand-in, not Sage's default.** Sage's shipped implement tier is
> `bedrock-qwen3-coder` (qwen3-coder-30b, `MODELS.md:12`), which doesn't exist on an OpenRouter-only
> gateway. `GLM-5.2` ($0.53/$1.67 per 1M) is the substitute: mid-tier, code-capable, ~5× cheaper than
> `gpt-5.4` on input and ~9× on output.
>
> That substitution costs something, and it should be stated in the writeup rather than discovered by
> a reader. A result here is evidence about **phasing**, not about the tier users actually run — if
> GLM-5.2 turns out stronger or weaker than qwen3-coder-30b, A vs C shifts with it. Re-run the pair
> against `bedrock-qwen3-coder` on a gateway that has it before any claim about Sage's defaults.

### E/F — the small-model pair

**Only run these if A vs C comes out ambiguous.** They are not part of the 2×2 and they must never
replace arm A: the 2×2 decides whether to *ship the toggle*, and that decision has to be made about
the tier real users run. "Phasing rescues a model too weak to build with" argues for not using that
model, not for shipping phasing — swap a 7B into arm A and every decision rule below quietly stops
meaning what it says.

|   | single context | phased |
|---|---|---|
| **small implement** (`deepseek-v4-flash-0731` or `mimo-v2.5`) | **E** | **F** |

Two candidates, near-identical in price — `deepseek-v4-flash-0731` at $0.09/$0.18 and `mimo-v2.5` at
$0.11/$0.22 per 1M. Smoke-test both and take whichever completes; at that spread reliability decides
it, not cost. Ignore the **Capabilities** column when choosing: it's display metadata only, never
consulted on the inference path (`routes/aliases.py` writes it, `routes/gateway.py` and
`provider_adapter.py` never read it, and `preflight.py:416` says outright that no per-model
capability table is hard-coded). `mimo-v2.5` showing `tools` and the others not reflects what someone
ticked in a form, not what the models do.

What they add is a different question, and a sharper one: *how far down the capability curve does
phasing let you go?* The likely outcome of A vs C is `A ≈ B` — 30B-coder is probably strong enough
that a fraud review queue never degrades it, phasing has nothing to rescue, and four runs resolve
nothing. E/F increase the effect size until the instrument can see it. **E is not optional if you
run F** — small×phased alone can't tell you whether the small model was fine all along.

**Precondition: smoke-test the candidate before spending a scored run.** Phased mode has a higher
model-capability floor than plain building — each phase has to write a closing summary that rides
along to later phases, respect the `Files` / `Don't touch` split, and not abandon a file that looked
fenced off. That's structured-instruction compliance on top of the tool-calling OpenCode needs to
function at all, and it's exactly what small models are worst at. Build something trivial in
single-context mode first. **If it can't finish a two-component app, it isn't eligible** — because a
failed F can't distinguish "phasing didn't help" from "the model couldn't participate in phasing,"
and that's an uninterpretable run, not a result.

Pick on code-tuning and **documented reliable tool/function calling**, not on price. The cheapest
slugs tend to have the flakiest tool support.

## Before you start

1. **The gateway must be up.** `GATEWAY_BASE_URL` is `https://<host>/apps/<app-id>/v1` — the dogfood
   instance is `https://apps.cloud-dogfood.domino.tech/apps/llm_gateway/v1`. An nginx 404 (HTML, not
   JSON) means the app is stopped — nothing in Sage can be measured until it's back.
2. **Every catalog slot must resolve to a real alias.** Sage's defaults (`bedrock-qwen3-coder`,
   `sonnet`, `qwen-2-5`) don't exist on an OpenRouter-only gateway, and a missing alias is a
   model-not-found *mid-build*, not at startup. Map all six:

   ```bash
   SAGE_MODEL_PLAN=gpt-5.4
   SAGE_MODEL_IMPLEMENT=GLM-5.2
   SAGE_MODEL_ASK=GLM-5.2
   SAGE_MODEL_SOVEREIGN_PLAN=GLM-5.2
   SAGE_MODEL_SOVEREIGN_IMPLEMENT=GLM-5.2
   SAGE_MODEL_SOVEREIGN_ASK=GLM-5.2
   ```

   Names pass through verbatim — `GLM-5.2` must match the alias exactly, capitals included. And each
   alias needs its own **grant**, or every call 403s; a provider that tests green and an alias that
   saves cleanly both look like success without one.
3. **Confirm cost attribution works, once.** Run any one turn, then hit the "Usage & cost" button in
   Sage's header — it deep-links to `<gateway>/#mine` already filtered to `sage-project =
   <owner>/<project>` over the last 30 days. Check that the filter resolves to non-zero Requests /
   Total Tokens / Cost. An empty dashboard means the tag never landed, and every number below is
   unobtainable — stop and fix that first.
4. **Sensitivity lock off.** It forces sovereign models regardless of the Implement assignment and
   would silently void the model arm of every run. On an OpenRouter-only gateway the sovereign
   aliases aren't sovereign anyway — they're the same vendor path under a different name — so the
   lock buys nothing here and costs you the run.

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
   - runs A, C → `GLM-5.2`
   - runs B, D → `gpt-5.4`
   - runs E, F → the small candidate that passed the smoke test

   Or via API: `POST /api/project/model  {"catalog": {"implement": "gpt-5.4"}}`

   Leave **plan** on `gpt-5.4` in every run, E and F included. Varying the planner too would add a
   third dimension to a 2×2, and on the phased arms it would do something worse than that: a weak
   planner writes a plan that doesn't parse into ≥3 steps, `MIN_STEPS` (`plan_steps.py:52`) sends the
   build down the single-context path, and F silently becomes E with no error anywhere. Overrides
   persist to `.sage/model_overrides.json`, so set this *after* the fresh workspace exists, not
   before.

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

**If A vs C comes out ambiguous there are two ways to widen it, on different axes — don't conflate
them.** Smaller *prompt*: repeat the A/C pair with the much smaller **Dataset explorer** starter. If
phasing wins on the big app and loses on the small one, that isn't a contradiction — it's the size
threshold where the per-phase bootstrap tax stops paying for itself, and it's the most useful thing
this experiment could produce for deciding when the toggle should default on. Smaller *model*: the
E/F pair above, which finds the capability floor instead of the size floor. Both are worth having
eventually; neither is worth running before A vs C reports.

## Running one experiment

1. Paste the prompt. Sage gates and proposes a plan.
2. **Record the plan.** `.sage/plan.md` is archived to `.sage/plans/NNN.md` on approve, so copy it
   first if you want it beside the run notes — though the plan document under `.sage/plan-docs/`
   keeps the same text and survives the build, so a lost `plan.md` is recoverable now. For C and D, check the Approve button reads **"Approve & build (N phases)"**. If it
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
| A | `GLM-5.2`     | no  | | | | /11 | |
| B | `gpt-5.4`     | no  | | | | /11 | |
| C | `GLM-5.2`     | yes | | | | /11 | |
| D | `gpt-5.4`     | yes | | | | /11 | |
| E | *small*       | no  | | | | /11 | |
| F | *small*       | yes | | | | /11 | |

E and F are the optional pair — leave them blank unless A vs C came out ambiguous. Fill in which
small model they actually ran, and whether it passed the smoke test, next to the numbers; without
that the rows aren't reproducible.

All six ran through OpenRouter, so the aliases are a naming layer over `z-ai/glm-5.2`,
`openai/gpt-5.4`, etc. Record the **upstream slug**, not just the alias — an alias can be repointed
later and the rows would silently stop meaning what they did.

For C, D and F also record the per-phase split:

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
  is solving a problem you don't have here. Retest with a substantially larger prompt before cutting,
  or run the E/F pair to find the capability floor from the other direction.

For the E/F pair, decided in advance the same way:

- **F ≈ C on quality, E ≪ C** → the strongest result available. Phasing moved the usable floor down a
  whole model class: an 8B built what previously needed a 30B, and the single-context control proves
  the model couldn't have done it alone. This is the blog post, and a better one than a cost delta.
- **E ≈ F** → the small model was fine all along on work this size. Says nothing about phasing;
  says the prompt is too easy to discriminate. Escalate the prompt, not the conclusion.
- **F fails outright** → check the smoke-test precondition before recording anything. A model that
  can't hold the per-phase contract produces an uninterpretable run, not evidence against phasing.
  Look for whether phases completed and summaries were written before blaming the feature.
- **E and F both fail** → wrong candidate model. Not a result; pick another and don't report it.

## Known gotchas

- **A plan with fewer than 3 parsed steps silently builds in one context** (`MIN_STEPS`,
  `plan_steps.py`). The Approve button's "(N phases)" is the only signal. Check it every time.
- **Sage shows no in-app cost figure**, by design — the gateway prices calls with per-alias rates no
  client can see, and Anthropic/Bedrock don't return usage in-band anyway. The dashboard is the only
  source of truth.
- **`#usage` requires gateway admin.** The non-admin `#mine` view has no tag filtering at all.
- **Phased runs create N+1 sessions** (the project session plus one per phase), so a `sage-session`
  group shows several rows for one run. Sum them; cross-check against the `sage-project` delta.
- **A weak model can fail the phase contract without failing the build.** The handoff note is just
  the phase's **last agent text block** (`service.py:2168`), and it's only carried forward `if …
  summary` — so a phase that ends on a tool call with no closing prose contributes nothing, silently,
  and still reports success. Enough of those and F is really N separate cold builds wearing a phased
  label. The notes are in-memory, never written to disk, so the build stream is the only place to
  check: each phase should end with prose, not a tool call. The app looking fine is not evidence the
  mechanism ran.
- **A failed phase aborts the build but keeps earlier phases on disk**, and does not commit. The
  workspace will hold partial work — that's intended, but don't score it as a finished app.
