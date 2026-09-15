# Sage-only Acceptance Report - 2026-09-14

This report verifies the Sage-only release scope for issue #361. It uses synthetic data, fresh
projects or fresh app repos, controlled gateways for failure cases, and small live-gateway probes
for model reachability and timing. It did not change gateway source, gateway rules, production
policy, deployed app audiences, or existing projects.

## Scope

- Active release: Sage-only data use across Chat, Build, and new Built Apps.
- Deferred work: gateway #32, gateway #33, gateway #34, and Sage #360.
- Baseline main before this worker: `99a3b83ee878f91fe70f5a45c8142fd81b430447`.
- Component versions: OpenCode 1.18.4, Python 3.11.15, Node v22.22.3, FastAPI 0.139.2, httpx 0.28.1, pytest 8.4.2.
- Focused release check: 178 passed, 0 skipped, 1 warning, collected 178, in 372.76 s.

## Controlled Checks

| Area | Evidence |
| --- | --- |
| Real OpenCode sales flow | Chat and Build ran isolated OpenCode against a controlled HTTP gateway. The app calculated North 360, South 420, total 780. The email column was absent from the second gateway request. Data used recorded `response_completed`, the actual requested alias, observed model evidence from the stream, and unknown provider receipt. |
| Supported message paths | File reads, bash/Python output, source-code reads, and child task output stayed visible locally. Later model requests received receipts for known local data results, not copied CSV rows. |
| Semantic coverage | The complaint task sent selected text and stable IDs, not email. The labelled regression `Item arrived broken` was classified as damage. Bad IDs, duplicates, malformed output, policy denial, interrupted batches, source changes, over-limit data, and 10,000 controlled-provider records were checked separately from semantic correctness. |
| New Built Apps | A fresh app helper handled normal completion, structured HTTP refusal, partial stream plus error, truncated EOF, cancellation, cache, fallback, preview path, and deployed path. The controlled gateway changed allowed and refused responses without a rebuild. |
| Browser runtime | Chromium clicked the generated app button. Each case rendered an accessible region and visible status. Refused and interrupted answers stayed incomplete. |
| Handoff and queries | Named-query Data used excludes row values. Preview and deployed query paths share semantics. Chat-to-Build handoff keeps source references and generated Artifact distinction. |

## Live Gateway Probe

The live probe loaded `/Users/subirmansukhani/Desktop/domino-sage/backend/.env` in memory only. It
made two rounds for each model and shape. All rows finished with the expected output.

| Shape | Model | Bytes | Avg first output | Avg whole call |
| --- | --- | ---: | ---: | ---: |
| text | sonnet | 335 | 2.31 s | 3.58 s |
| text | gemini-3.7-flash | 345 | 3.37 s | 3.61 s |
| text | Gemma 4 31B | 340 | 5.49 s | 6.01 s |
| tool | sonnet | 621 | 3.51 s | 4.10 s |
| tool | gemini-3.7-flash | 631 | 3.21 s | 3.29 s |
| tool | Gemma 4 31B | 626 | 4.94 s | 5.08 s |

This is a small live-gateway check. It is not a p95 claim and not proof of gateway-internal policy
coverage.

## Baseline Comparison

The saved prototype baseline is in `docs/performance/2026-09-14-pii-prototype.md`.

- Sonnet baseline copied rows into the gateway and was refused. The combined experiment later got a correct result but had a large startup delay.
- Gemini Flash improved in the small sales experiment: 6.08 s final versus 6.62 s baseline.
- Gemma 4 31B regressed in the small sales experiment: 18.90 s final versus 14.86 s baseline.
- The release probe above only measures small fixed text and tool requests through the live gateway.

## Limits

- Missing provider receipt, decision-stage, cache, fallback, and absent serving-model evidence remain unknown.
- Controlled gateway tests prove Sage and app response handling. They do not prove real gateway policy coverage.
- Direct deployed-app requests and internal gateway fallback do not have a full runtime restriction guarantee in this release.
- No existing project or already deployed app was migrated.
- Semantic quality is checked with labelled fixtures only. Full ID coverage is not a semantic-correctness claim.

Release status: ready for landing, with the deferred gateway and runtime limits above.
