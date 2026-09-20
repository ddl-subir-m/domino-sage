# Greeting latency on GLM, 2026-09-20 (#417)

## Reproduction

The owner's existing cloud-dogfood workspace served revision `5f818a6`, confirmed by the
handoff log on the measured turns. In conversation `thr_1a0c0caff59ab9ec96d32`, send `hi`, then
`hello` after the turn settles. GLM 5.3 OR was selected. MIXPANEL__EVENT was attached to the
conversation when it was created. This is a fresh conversation in an already-running workspace,
**not a cold-workspace measurement**.

Browser observations are bounds, not exact server durations:

- First greeting: the reply was visible at 20.954 seconds, with “Sage is working” still visible.
  The busy state was gone at 29.898 seconds. Early sampling did not locate the first visible token.
- Repeat greeting: no reply at 6.605 seconds; reply and no busy state at 8.855 seconds.

The Ask and Chat assignment panel explicitly showed `GLM 5.3 OR`, beside the helper text
“Default is sonnet.” The explicit selection overrides that default. The live `chat intent`
warning named GLM on both turns. An unavailable Sonnet does not explain these two turns.

The authenticated Domino run stdout API returned the current workspace log. The browser could
not open the timing diagnostic (`ERR_BLOCKED_BY_CLIENT`); the connector was configured for a
different Domino host. No browser credentials were extracted. The log established:

- `hi`: intent classification timed out after 5 seconds; the worker subsequently reported
  `fallback=invalid-json model=GLM 5.3 OR`.
- `hello`: intent classification also reported invalid JSON.
- Both turns performed a separate post-answer handoff classification, returning CHAT.
- Main-model first-byte log entries were 0.8 and 1.1 seconds on the first turn, 1.4 seconds
  on the repeat. These are not full model durations or time to visible answer.
- Chat save logs were 1902 ms (first) and 1709 ms (idle). Current save is queued after the turn;
  these durations must not be added to user-visible latency without overlap evidence.

## Classifier probe

Replay the production `chat_intent._SYSTEM` and `_user_payload` through the same gateway,
with the production temperature, JSON response format, streaming mode and 160-token cap.
Use a fresh `user` request field for each call to avoid exact gateway response-cache hits.
Only the classifier's reasoning effort changes. See
[measured results](2026-09-20-glm-chat-intent.json).

| Request | Result | Wall time |
|---|---|---:|
| Model default, cap 160, `hi` | Empty content, finish=length, all 160 tokens spent on reasoning | 1.690 s |
| Low, cap 160, `hi` | Valid other_chat JSON | 1.067 s |
| Low, cap 1024, `hi` | Valid JSON, but exceeds the 5-second caller budget | 11.837 s |
| Low, cap 160, six bound-context repeats | All valid; four greetings and two distinct-user questions | 0.925–2.385 s |

The six repeats returned `other_chat` for greetings and `data_answer` for the data questions.
Increasing the cap is not a demonstrated latency fix. Low at the existing cap has evidence;
this small sample does not establish classification accuracy for all prompts or model providers.

[Z.ai documents](https://docs.z.ai/guides/llm/glm-5.3) that GLM 5.3 reasons by default and supports
low/high/max. The actual gateway behavior above, not the alias name alone, is the basis for the
explicit Low setting. The user's answer-model effort remains independent.

## Narrow fix

The intent classifier read `catalog.ask` but discarded `catalog.ask_effort`. It now sends the
saved Ask effort when the measured no-tools capability table accepts it. Model default, invalid
saved efforts and unsupported routes still omit the field. No automatic Low fallback was added.

A live call through the production `chat_intent.start` path with `ask_effort="low"` established
the defect and the correction:

| Source | Selected effort | Sent effort | Result | Wall time |
|---|---|---|---|---:|
| Before | low | omitted | Valid other_chat | 2.086 s |
| After | low | low | Valid other_chat | 1.747 s |

The before call succeeded despite the omitted field. This confirms that default-effort truncation
is intermittent, not guaranteed. The request-field assertion failed before the fix and passed
afterward; the two wall times alone do not establish a reliable latency improvement.

The regression cases cover explicit Low on bare and provider-prefixed GLM aliases, preservation
of other supported levels, Model default, and invalid or unsupported saved levels. The change is
limited to Chat intent. Other classifiers have not been modified. Lint and diff checks pass.

Focused verification: **164 passed, zero skipped**, with two existing Starlette deprecation
warnings. Removing effort forwarding produced the expected **5 failed, 5 passed** in the ten
new regression cases. The fixed run included all ten plus the related Chat turn and degraded
classifier tests:

```sh
uv run --project backend --extra dev pytest -q -rs -c backend/pyproject.toml \
  backend/tests/test_glm_chat_intent_keeps_room_for_its_verdict.py \
  backend/tests/test_chat_turn.py \
  backend/tests/test_a_degraded_classifier_names_its_model.py
```

The active #474 suite completed and released its claim before the focused run started.
No overlap occurred. Full commit-gate results and the tested tree are recorded in the #417 mailbox.

## Remaining work

The first-boot session/title cost is not isolated by this run. The separate handoff call still
runs for a valid `other_chat` classification. Fixing intent truncation alone does not remove all
greeting latency. The deployed workspace has not received a change, and there is no end-to-end
post-fix speed claim. #417 stays open.
