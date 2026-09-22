# Build phase routing: an approved plan no longer re-plans — measured 2026-09-22

Ticket: **#498**. Plan phase: **A** (A1 + A2). Predecessor baseline: **2026-09-21**, recorded in the
issue body and in the memory note `sage-build-baseline-2026-09-21`.

**Read the routing rows as the result. Do not read the wall-clock rows as the result.** The Project's
Model assignments changed between the two runs by more than the one setting that was meant to change,
so every duration below sits against a different pair of models at a different effort. The four
routing criteria are properties of which agent and which slot a turn resolves to; they are
prompt-independent and model-independent, and they are what this study answers. The clock is
recorded because the plan asked for it, and is attributed to nothing.

## Conditions

| | baseline | after |
|---|---|---|
| Date | 2026-09-21 | 2026-09-22 |
| `sage_rev` from `api/diag` | **`e1a5222`** | **`d53aa85`** |
| Landed for this study | — | `1cd87e23` (A1), `d53aa856` (A2) |
| Host | cloud-dogfood workspace `sage-pharma-tables` | same Project, rebuilt image |
| Mode at the composer | Auto | Auto |
| Plan slot | `opus` @ `high` | `sonnet` @ `low` |
| Implement slot | `sonnet` @ `high` | `haiku` @ `none` |
| Prompt | the clinical TFL build script | a substantial CHANGE prompt (see Deviation) |
| Raw | `2026-09-22-build-phase-routing-results.json` |

`sage_rev` was read from `api/diag` and checked against `git ls-remote origin refs/heads/main`
**before** anything else was run. The workspace image clones `origin/main`, so this is the check
that says the measurement is of the landed code and not of a cached layer.

### Source hashes

SHA-256, first 16 hex, of the three files Phase A touched. The post-run column is reproducible from
the commit — the tree was clean at `d53aa856` and remains so:

| file | at `e1a5222` (baseline) | at `d53aa856` (measured) |
|---|---|---|
| `backend/sage/orchestrator/service.py` | `02a909e61f6c217d` | `41001ead7e83ad67` |
| `backend/sage/shim/enforcement.py` | `90266e26591bb12f` | `aa1cd33709952468` |
| `backend/sage/router/phase_classifier.py` | `afb680b7627b8317` | `c7c4bac79b0a2ee6` |

`backend/sage` tree object at `d53aa856`: `b2c1b0a97103b03abab192708962424df575d455`. The pre- and
post-suite hashes were compared during the landing run and were identical; the values were not
transcribed at the time, so the column above is the one derived from the commit rather than a
transcript of that comparison.

## Result — the four routing criteria

The approve turn began 17:17:52Z. Its first two routing lines, from `api/diag/log`:

```
17:17:53  turn: agent=sage-implement gate=False answer_only=False arch=False mode=implement
17:17:55  model policy: requested=None -> resolved=haiku (implement-pinned, phase=implement, ...)
```

| | baseline 2026-09-21 | after | met |
|---|---|---|---|
| approve turn's first policy line | `opus (auto-plan, phase=plan, effort=high)` | `haiku (implement-pinned, phase=implement)` | yes |
| approve turn's agent | OpenCode default — `_MODE_AGENT` has no `Mode.AUTO` entry | `sage-implement` | yes |
| approve turn's mode | `auto` | `implement` | yes |
| plan : implement policy lines **within the approve turn** | plan/opus from the first call | **0 plan : 34 implement** | yes |
| gaps > 60 s in the approve turn's first three calls | 1, of 335 s | **0** | yes |

`implement-pinned` is `Reason.IMPLEMENT_PINNED` — `_resolve_build` branch 4, which is the branch A1
was built to reach. The plan:implement ratio did not merely invert: **the plan phase is absent from
the approve turn**, which is the stronger statement and the one the stop rule for Phase B was
written against.

Session-wide for the baseline, for contrast: 68 `plan/opus` policy lines against 18
`implement/sonnet` — roughly 34 Opus calls to 9 Sonnet across about six turns, about 79% of routed
calls on the expensive model.

## A2 fired live, on the first run, unprompted

```
17:18:17  tool done: apply_patch refused: bytes=6988 lines=170 begin=y end=y ...
17:18:18  model policy: rescue examined=1 errors=1 episodes=0 rescued=no (write-flip)
          patch_refusals=1 ... apply_patch=withdrawn      (×24 once the second refusal landed)
```

Two refusals reached `ERROR_CORROBORATION` and `apply_patch` was withdrawn for the rest of the turn.

**Before A2 this was unreachable.** `enforcement.py` ran `assess()` only in Auto, so a pinned
Implement turn had no `signals` and `patch_withdrawn` could never be computed — and A1 had just moved
the approve turn into exactly that state. Without A2, A1 would have taken the one turn that patches
hardest out of #494's reach and left it offering `apply_patch` to a model that had just failed it
twice. The hole A1 opened was real, it was hit on the first live run, and A2 had already closed it.

## Recorded, not attributed

The catalog change confounds every duration here. These numbers are the measurement the plan asked
for, and no share of them is claimed for Phase A:

| | baseline | after |
|---|---|---|
| plan turn | 0.8 min, 1.5 min | 0.71 min |
| approve turn | ~9 min | **2.36 min**, `typecheck clean`, `ok=true` |
| prompt → working app | ~47 min | ~3.1 min |
| model calls in the approve turn | — | 17 |
| gaps > 60 s in the approve turn | 1 × 335 s (in the first three calls) | 1 × 74 s (later) |
| diag ring, whole session | 46.5 min, 33.8 min (73%) in gaps > 60 s, 13 gaps, all beginning on `model call -> streaming` | — |

A cheaper, lower-effort pair of models explains an unknown share of the wall-clock delta, and no
single run can separate the two causes. The 74 s gap is worth noting only for its **position**: the
baseline's 335 s gap sat inside the approve turn's first three calls, in the plan phase, which is the
phase that no longer exists there.

Second baseline already on record for the other extreme, from
`sage-build-baseline-2026-09-21`: GLM 5.3 at `effort=low`, 21.5 min, ~90 calls, app broken.

## Deviation from the plan, stated plainly

The plan said to re-run the same clinical TFL Build prompt in a fresh Project. **I ran a substantial
change prompt against the already-built app instead** — four summary cards plus SOC expand/collapse.

Reasons: the four routing criteria are properties of routing, so they are prompt-independent; the
wall clock is the only prompt-sensitive number and it was already unattributable because of the
catalog change; and a change prompt cannot fail for platform-data reasons that would muddy the
routing read.

The cost: **there is no like-for-like wall-clock comparison with 2026-09-21, and there will not be
one unless the baseline catalog is restored for a run.** That is owed work, not a closed question.

## Behaviour change reported and not acted on

Pinning `Mode.IMPLEMENT` on the Auto approve door reaches `_resolve_build` branch 4, so an approve
turn now honours a standing Build model pick while the UI's `honours_pick` — which reads
`selected_mode`, and `selected_mode` is deliberately not moved by a turn-scoped pin — reports that it
does not. This was already true of the Plan and Ask doors before Phase A; A1 extends it to the fourth.
Recorded here rather than filed, per `docs/agents/issue-tracker.md`.

## Gates

| | |
|---|---|
| Suite, repo root, `-rs` | **7799 passed, 5 skipped, 0 failed, 7804 collected, exit 0, 3:26** |
| `make lint` on `main` | All checks passed, repo-wide via the target |
| Plants | 8 conditions, 8 reds — see #498 |
| Merge before suite | `c7d65983` was `origin/main` at start; nothing to merge |
