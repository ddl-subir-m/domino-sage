# Remaining greeting costs (#417)

## Handoff effort

The post-answer `handoff.wants_an_app` call also discarded `catalog.ask_effort`.
Four live production-path calls used the same GLM alias, greeting, 256-token cap and
fresh request IDs. Only the wire effort changed:

| Selected Ask effort | Effort sent | Seconds | Verdict |
|---|---|---:|---|
| low | omitted | 2.717 | CHAT |
| low | low (probe override) | 1.236 | CHAT |
| low | omitted | 1.597 | CHAT |
| low | low (probe override) | 1.232 | CHAT |

A separate request-field assertion failed on the old source: selected Low, field omitted,
valid CHAT in 1.046 seconds. After the fix it passed: Low sent, valid CHAT in 1.236 seconds.
The defect is the lost setting. These small, variable timing samples do not establish a
reliable speedup or classification accuracy across prompts.

The fix sends a supported saved effort only while the Ask model remains the selected model.
A sensitivity move to another model drops the original effort, matching the router's
`_lock_sensitivity` rule. Model default and unsupported values still omit the field.

## Unused OpenCode title

Sage displays `title_from_prompt`, not OpenCode's generated session title. The issue records
extra title calls, also seen in the earlier live greeting log. Current
[OpenCode documentation](https://opencode.ai/docs/agents/#disable) describes `agent.*.disable`,
but that is not evidence that the installed 1.18.4 title generator honors it.

The exploratory real-OpenCode check used a controlled HTTP gateway and counts title versus
answer calls for a first greeting, a repeat, and a fresh conversation. The control removes
the title override. The comparison could not establish a baseline: first-answer timeouts after a title request,
a startup timeout, and an /agent read timeout prevented a valid control. It was tried with
a Git project and the existing tests' directory warm-up. The unfinished probe is kept at
`/tmp/sage-417-title-probe.py`, outside the committed test suite. No title config change is included.
Title generation can run concurrently with the answer: removing an unused call is a cost
saving, not an established latency saving.

## Session warm-up and classification reuse

The deployed startup warms only the OpenCode server. The new workspace still has a larger
first-turn delay, described below. The local comparison found a session-free read that also
initializes the location services: v2 `GET /api/agent?location[directory]=<chat-work>`.
The v1 `/agent` and `/experimental/tool` reads do not do this. Creating a dummy session is
unnecessary, and a dummy must never become a real Thread's session.

The follow-up now calls that v2 read during background startup. It creates only the Chat directory;
it does not create a session, send a model request, or rewrite the active Thread's links or
instructions. The driver's existing request budget bounds it. Failure is logged and leaves the
lazy turn path available. The startup cost still exists, and an immediate turn can wait for it.

Do not drop the post-answer handoff call solely because the pre-turn intent was `other_chat`.
That label means normal Chat behavior, not a conclusive negative answer to the app question,
and the later classifier also reads the assistant response. The already-reused `build_app`
verdict is a separate, positive condition.

## New deployed workspace

The owner supplied run `6ab06878e13dce03e8d44bd7`, workspace
`6ab06878e13dce03e8d44bd4`. Its startup log confirms `d9392aa -> 548e1b8 (main)`.
This includes the earlier intent-effort fix, not the handoff fix in this follow-up.
OpenCode is 1.18.4. Ask/Chat was set to GLM 5.3 OR with explicit Low in the assignment UI;
the Chat composer remained on Model default, and the shim log confirms resolved effort Low.
The existing MIXPANEL__EVENT binding was inherited, so these are greetings with context,
not a test of an empty workspace. No data query ran.

Browser observations on September 20 (seconds from clicking Send):

| Turn | Answer first observed | Busy state gone |
|---|---:|---:|
| First Chat turn after this server boot: `hi` | 20.557 | 22.131 |
| Same conversation: `hello` | 9.979 | 11.140 |
| Fresh conversation, same warm server: `hi` | by 12.927 | by 12.927 |

The first two observations have roughly 1.2-second sampling intervals, not exact first-token
times. The third has a sampling gap from 3.804 to 12.927 seconds, so it supplies an upper bound
only. Creating each new conversation took about three seconds before its user row appeared.

Gateway Request Logs show the following records. Roles are inferred by request order, size,
and the matching Sage logs; the UI does not expose component labels for these rows.

| Turn | UTC shown | Inferred role | Gateway latency | Total tokens |
|---|---|---|---:|---:|
| first `hi` | 23:17:56 | intent | 457 ms | 289 |
| first `hi` | 23:17:59 | title | 1112 ms | 2515 |
| first `hi` | 23:18:10 | answer | 3627 ms | 18748 |
| first `hi` | 23:18:13 | handoff | 1577 ms | 209 |
| repeat `hello` | 23:18:41 | intent | 3505 ms | 342 |
| repeat `hello` | 23:18:45 | answer | 2986 ms | 20747 |
| repeat `hello` | 23:18:48 | handoff | 1301 ms | 211 |
| fresh `hi` | 23:24:56 | intent | 630 ms | 289 |
| fresh `hi` | 23:24:58 | title | 953 ms | 2519 |
| fresh `hi` | 23:25:00 | answer | 3044 ms | 18749 |
| fresh `hi` | 23:25:03 | handoff | 1823 ms | 209 |

The first title and answer records are eleven seconds apart, while each model call takes less
than four seconds. This supports a substantial gap outside the model calls on the first turn.
Do not add all gateway latencies and subtract from the browser total as a precise setup span:
calls can overlap, gateway timestamps have one-second resolution, and their start/end convention
was not verified from this UI. The detailed timing endpoint was blocked by the browser, so the
exact setup work remains unconfirmed on this run.
On the warm fresh-conversation turn, the title and answer records are only two seconds apart.

All three intent results were valid `other_chat`; all three handoff results were `CHAT`.
The first and fresh-conversation turns each made two shim calls; the repeat made one.
Thus the deployed classifier no longer fails on these greetings, but the remaining delay is
not fixed. The title call recurs on new conversations. The third greeting also triggered the
existing no-tool-call warning: that warning does not establish a tool defect because none of
these greetings needed a tool.

No end-to-end speedup from the local handoff change is claimed. The startup change still needs
deployment and validation on Domino. Title suppression remains unfinished; keep #417 open.

## Local startup comparison

The owner requested a local instance. It used the real Sage control app, shim, GLM gateway, and
pinned OpenCode. Configuration and state were isolated under `/tmp/sage-417-local-git`; the user's
global config was not changed. Both server and workspace had Git roots. The Mac's external skills
were excluded, while Sage's own tools and skill were installed. The root template's installed Vite
dependencies were used. These greetings had no bound data, so their absolute times are not a
prediction for the Domino workspace.

On a cold server, the first reply took 83.165 seconds (83.675 seconds to finish). The title call
took 0.8 seconds and the answer 1.7 seconds. The answer call began at 81.3 seconds. OpenCode's log
placed the gap before `watcher backend` and `booting location services`. A prior cold probe also
took 90.707 seconds to the reply. This Mac-specific delay is much larger than the deployed one.

After another restart, a v1 tool-list request returned in 0.110 seconds but did not boot location
services. The v2 agent read then took 80.158 seconds and returned the exact Chat directory in its
`location` field. The log confirmed watcher and location startup before any conversation prompt.
The subsequent first reply took 3.597 seconds (4.144 to finish); the repeat took 2.401 seconds
(2.961 to finish). No dummy session or inference was used to warm it.

These numbers establish that startup can pay the setup cost before the first message. They do not
establish a faster cold boot, zero waiting when a user types during startup, or a Domino speedup.
An earlier warm local conversation took 8.121 seconds on its first greeting, including a five-second
intent timeout; its repeat took 2.791 seconds. Model latency still varies after setup is warm.

The actual patched application was then started with fresh isolated state at
`/tmp/sage-417-local-patched`, without the manual warm-up override. Background startup reached
the Chat-directory readiness message and the permission check covered that directory. Fresh
runtime preparation took about 144 seconds on this Mac, including package/config initialization.
Only then were the greetings sent:

| Turn | First answer | Stream finished | Intent call | Answer call | Handoff call |
|---|---:|---:|---:|---:|---:|
| `hi` | 9.321 s | 17.337 s | 1.968 s | 5.341 s | 8.007 s, timeout |
| `hello` | 3.795 s | 10.589 s | 1.490 s | 1.199 s | 6.782 s |

For `hi`, server setup took zero milliseconds, session creation 95 ms, prompt preparation
937 ms, and dispatch 146 ms. The title call took 856 ms and overlapped the answer call.
The answer call began at 3.783 seconds: the earlier roughly 80-second initialization pause
was absent. For `hello`, the answer call began at 1.779 seconds. The remaining variation is
mainly in model calls, with additional polling delay before the completed answer is observed.
Forwarding Low does not guarantee a fast handoff verdict; its timeout still keeps the stream
open after the answer. This is not a complete fix for greeting latency.

## Browser completion state

The post-answer delay also exposed a client defect. `chat_stream` already releases the server's
turn lock before yielding `done`; its existing regression test asserts that release before
classification. Workbench kept `liveChatTurns`, `chatRunning`, and the Stop target until SSE EOF.
The classifier therefore made the browser show a busy conversation after the server had freed it.

The follow-up now ends the browser's turn at `done` and continues reading later suggestion events.
Completion is idempotent: EOF, an error, or a duplicate `done` must not decrement the count twice
or clear a newer turn's typing state or Stop target. A connection that ends without `done` still
releases its state in `finally`. A delayed suggestion remains visible on its own conversation.
The classifier's model cost remains, but the browser does not wait for it to leave the busy state.

The old browser source failed all five completion-before-EOF cases in the gated-stream harness;
the two no-`done` EOF/error controls passed. The patch passed 57 focused checks covering these
seven cases, existing Chat streams, turn queues, Stop controls, and the server's lock release.
Four additional fault plants were detected: double completion (5 failures), a late suggestion
clearing newer typing (4), omitted EOF/error cleanup (2), and omitted Stop release (7).
The patch was restored after every plant.

Focused checks: 100 startup/handoff/intent cases passed; the separate title probe failed as described above.
Removing effort forwarding made all three supported-effort regression cases fail. Four
default/unsupported/moved-model cases passed both before and after. All six startup fault plants
were detected: omitted warm-up, v1 API, missing directory, ignored HTTP failure, pruned Thread
links, and rewritten instructions. Each plant was removed before the passing focused run.
Lint passes.

## Whole-message greeting classification

The next change removes the pre-turn model call only when the entire trimmed message is
`hi`, `hello`, or `hey`, ignoring case and trailing sentence punctuation. It returns the existing
`plain_answer` intent. Chat still sends the original prompt through the same OpenCode session,
with its history and context. That bounded intent also avoids the later handoff classifier.
It does not supply a canned answer, change the selected model, or remove the Chat prompt/tools.

A greeting prefix does not qualify: `hi, count the users`, a multiline build request, a file
name, a quoted word, and a confirmation such as `yes` still use normal classification. Bound
context alone does not turn a bare greeting into a data request.

The old source failed 13 greeting cases and passed 19 controls. The patch passed 196 focused
cases, including existing Chat turn, classification failure reporting, and degradation counters.
The earlier GLM token-cap regression now uses a non-greeting prompt so it still exercises the
model call rather than passing through the shortcut. A two-turn integration check verifies both
model replies, one retained OpenCode session, persisted user history, and zero classifier calls.

Six live Low GLM greetings after this shortcut, before the streaming fix below, completed in
15.560, 6.382, 2.097, 2.087, 3.118, and 6.192 seconds (the timing ledger includes a few more
milliseconds of finalization). No intent or handoff call ran. The first answer call itself took
12.640 seconds. Removing routing calls therefore does not establish a consistent wall-clock
speedup: the selected model's latency varies substantially.

## Native OpenCode streaming

A local read of the real global `/event` stream found `message.part.delta`, while Sage's
`map_session_event` accepted only the older `session.next.text.delta` flow. The current v1
prompt flow also emits message and part declarations. All 57 native deltas in the first
regression explanation were discarded. Raw deltas began at 8.154 seconds; Sage published only
the completed answer at 10.238 seconds. This is a separate defect from model response time.

`SessionEvents` now also maps native message-part events. It tracks the declared message role
and part type, and emits only assistant text deltas. User prompt text, reasoning, tool input,
unmatched parts, and other sessions do not become answer text. A completed text snapshot
repairs any missed deltas. Repeated final snapshots and tool status updates are deduplicated.
Native tool start/end, message errors, and finish reasons retain their existing internal event
meaning. The old event mapper and the transcript fallback remain available.

The old source failed five native stream cases and passed eight negative controls. The patched
source passed 204 focused driver, Chat, and UI stream checks. Five additional fault plants each
failed the relevant regression: dropping native deltas, exposing reasoning, echoing user text,
repeating final snapshots, and losing tool completion. All plants were restored; the final
combined set of 64 direct regression checks passed.

Live after the fix, the same regression question delivered 49 answer fragments. The first raw
delta arrived at 9.713 seconds and the first Sage delta at 9.953 seconds; `done` arrived at
11.040 seconds. This restart used GLM Model default for that explanation. The evidence is the
restored stream and its 240 ms delivery lag, not a faster model comparison between runs.

After explicitly restoring Low for Ask and Chat, a fresh conversation on the warm server gave:

| Turn | First visible text event | Done | Gateway first-byte wait for answer |
|---|---:|---:|---:|
| `hi` | 1.391 s | 2.301 s | 1.210 s |
| `hello` | 2.424 s | 2.869 s | 2.195 s |
| `hey` | 8.189 s | 8.846 s | 8.090 s |

These times count deltas as visible text; the earlier scripts counted only the completed
`agent` text event. The distinction matters once streaming works. The first turn still made
the unused concurrent title call; none made an intent or handoff call. The slow third turn
spent almost all of its first-text delay waiting for GLM. No fixed sub-two-second guarantee
follows from these samples.

The real local browser rendered a greeting, then removed the working notice and Stop control.
Its sparse snapshots do not provide a separate browser latency measurement. The gated UI
regression above supplies the proof for completion before a delayed classifier stream ends.
The local service was stopped after verification. Domino deployment validation remains pending.

## Controlled GLM first-output investigation

The owner then asked to investigate the remaining model wait and remove overhead that Sage
adds. Direct calls used the production `OpenAICompatibleClient`, the same Domino gateway,
GLM 5.3 OR, Low reasoning, temperature zero, a 256-token output cap, and streaming usage.
They measured first gateway bytes, first visible answer text, completion, response identity,
and input-cache usage. No reasoning text was recorded.

Eight identical short requests first exposed full-response caching. The first ran in 2.507
seconds to text; seven repeats returned one cached response identity in 0.153–0.215 seconds
without usage. Those repeats cannot measure GLM inference. Subsequent probes changed only the
request's `user` metadata to a unique probe ID, preserving the model-facing question. They
returned distinct upstream response IDs and usage records. Input-prefix caching remained active
and is reported separately; bypassing response reuse does not imply cold input processing.

Four uncached short requests, each with 25 input tokens, returned first text in 1.471, 0.883,
1.671, and 0.764 seconds. One took 6.448 seconds to finish despite its first text arriving at
1.671 seconds. First-text latency and completed-answer latency are different measurements.

The instruction comparison alternated short/long/long/short three times. Both asked the same
question, "Say hello in one short sentence." The long request used the actual installed Chat
agent instructions and a saved wrapped user prompt from the isolated local conversation,
with only its final question replaced. It did not include OpenCode's tool schemas or its other
system framing, so this is an instruction comparison, not a replay of the complete Chat wire.

| Request | Samples | Input tokens | Median first text | Range |
|---|---:|---:|---:|---:|
| Short instructions | 6 | 25 | 0.833 s | 0.389–1.689 s |
| Saved Sage instructions | 6 | 5,702 | 1.120 s | 0.496–7.594 s |

The first three long calls had zero cached input tokens; the later three reported 5,504.
Five long calls returned text within 1.448 seconds; one took 7.594 seconds. The sample supports
a modest median difference, not a consistent eight-second cost attributable to instructions.
Provider/gateway queue time, input processing, and scheduling are not separated by this client.

A second alternating comparison held an eight-turn greeting history and the current wrapped
question constant. One form repeated the saved wrapper on all eight older user messages; the
other kept those user questions and assistant answers but omitted the old wrappers. This was
a probe only: it did not edit the saved conversation or change production prompt handling.

| History form | Samples | Input tokens | Median first text | Range |
|---|---:|---:|---:|---:|
| Older user questions without wrappers | 6 | 5,814 | 1.492 s | 0.846–2.707 s |
| Repeated older wrappers | 6 | 15,990 | 1.194 s | 0.664–1.811 s |

Input caching was active in both forms. Removing over ten thousand tokens did not improve
first-text time in these samples. That does not prove prompt size is irrelevant, but it does
not justify removing conversation instructions or context as a demonstrated latency fix.

The Chat polling floor held an arriving first text event for up to 200 ms to limit repeated
polling. The earlier native live probe measured 240 ms between the first raw delta and the first
Sage delta. The fix bypasses that floor until the first text or tool event, then keeps normal
batching. Both text-first and tool-first regressions failed against the old code and passed
with the fix. A fault plant that removed all later batching also failed both checks.

A live replay of the same explanation question measured first raw text and first Sage text at
9.024 seconds, equal at millisecond resolution; completion was 9.937 seconds. This removes the
measured delivery hold, but does not remove the upstream model wait. It is not proof of zero
processing cost. The local capture used the experimental `OPENCODE_DISABLE_MODELS_FETCH=1`
startup flag. That flag is not part of the production patch: its startup comparison was
inconclusive (77.5 seconds with the flag, with earlier ordinary starts also around 80 seconds).

### Actual outgoing greeting request

A capture at the production gateway client's route boundary found eight remaining tools:
`delegated_model_call`, `glob`, `grep`, `live_read_files`, `live_read_query`, `live_read_table`,
`read`, and `skill`. Their schemas occupied about 14 KB. The complete outgoing request was
about 40 KB, with Low reasoning and a 32,000-token output limit. Raw request captures remain
local and are not attached because they can contain local access tokens.

The controlled replay held the actual messages, model and effort constant. Eighteen calls
alternated three variants in balanced order, six samples each. Unique user metadata prevented
full-response reuse. Input-prefix caching remained active. All calls completed with `stop`;
none made a tool call or reached the output limit.

| Actual wire variant | First-text samples (seconds) | Median |
|---|---|---:|
| Unchanged Chat request | 1.879, 1.593, 1.072, 2.234, 4.300, 4.508 | 2.057 s |
| Same request without tools | 1.627, 1.127, 1.521, 1.390, 1.312, 1.299 | 1.351 s |
| Same request with output cap 256 | 6.975, 1.193, 1.343, 5.792, 4.731, 4.487 | 4.609 s |

Tool omission reduced the median by about 0.706 seconds in this sample. Reducing the output cap
did not help and is not part of the patch. These small samples do not establish a latency bound
or distinguish gateway queueing from provider processing.

The implementation omits tools and their choice directive only during an armed, whole-message
Chat greeting. It reuses the same exact greeting recognition as the classifier shortcut and
keeps the model, instructions, history and output budget. The read-only token expires at turn
end; the next question keeps its read, SQL, skill and delegation tools. Unbounded investigation
turns retain their existing behavior. An integration check failed against the old path and
passed after the fix; four negative controls cover other reasons, Build and disarmed turns.
The greeting, shim, Chat and stream checks passed together: 254 passed, two deprecation warnings.
Three tool-filter fault plants were caught and restored: filtering every read-only Chat turn
(three failures), filtering Build too (one failure), and retaining a dangling tool-choice directive
(one failure). The final combined check passed 324 tests, with three deprecation warnings; lint
and diff whitespace checks passed. The full exclusion/repeat gate remains pending. The owner
authorized local instance checks and focused tests while #479 holds the suite slot. Our earlier
queued claim was released explicitly so it does not block that session.


### Live check after the polling and greeting-tool fixes

On the warmed isolated local app, with GLM 5.3 OR and Low selected, a fresh conversation gave:

| Prompt | First visible text | Done |
|---|---:|---:|
| `hi` | 3.106 s | 3.553 s |
| `hello` | 1.770 s | 2.005 s |
| `hey` | 1.116 s | 1.352 s |
| `Explain regression in one sentence.` | 2.125 s | 2.574 s |

The wire capture confirmed no tools or tool-choice field on the three greeting answers, and
all eight normal tools plus `tool_choice=auto` on the following question. All main requests
retained the 32,000-token limit. The initial title call still ran concurrently. The greetings
made no intent or handoff calls; the normal question made its intent call, lasting 549 ms.

The first greeting's main gateway call began at 1.772 seconds and first bytes arrived 1.326
seconds later. Prompt preparation alone took 1.046 seconds on that first turn, then 0–1 ms
on later turns. Thus this patch does not remove every first-turn setup cost. The second and
third greeting calls began at 314 and 96 ms, with gateway first-byte waits of 1.448 and 1.014
seconds. The second turn also recorded 178 ms in turn acquisition.

This run exposed a remaining cold-start problem. OpenCode listened at 02:18:50 UTC but did
not initialize the location until about 02:21:59 UTC, roughly 189 seconds later. Early local
requests timed out after 30 seconds in session creation, before any model call. The probe was
stopped and restarted only after the warm-ready log. Background warm-up shifts this cost; it
does not fix it or guarantee immediate readiness. The local model-fetch-disable flag remained
set for this probe and is still not a production change. No timeout was increased to hide the
failure. The successful timing table above excludes these explicitly reported cold failures.

Scoped review followed the owner's order: streaming/greeting and their tests first, then
startup, handoff and their tests/documentation. Sixteen files changed, split into groups of ten
and six. No unresolved correctness finding in the patch. Kept context/history, model selection,
output limits, and general tools after the comparisons did not justify narrowing them. Cold
OpenCode initialization, first prompt preparation, variable upstream latency, the full suite,
and validation in the newly deployed Domino workspace remain incomplete.

## Cold-start investigation: native Mac loads and a cosmetic gateway read

The earlier 189-second local delay is not evidence of a 189-second Domino/Linux startup.
A model-free reproduction through the real `OpenCodeServer` into pinned OpenCode 1.18.4
isolated a macOS native-library load stall. The request was `GET /api/agent` with Chat's
`location[directory]`, after starting a fresh process against the same local runtime.
No model call, SQL query, or full test suite was involved.

| Local process configuration | Listening | Directory request |
|---|---:|---:|
| Original native services | 1.487 s | 147.470 s |
| File watcher disabled | 1.910 s | 61.966 s |
| File search disabled | 0.640 s | 151.463 s |
| Both disabled, first run | 0.795 s | 0.328 s |
| Both disabled, repeat | 3.248 s | 0.352 s |
| Both disabled, second repeat | 0.849 s | 0.292 s |

`sample` showed OpenCode's main thread inside `dyld::dlopen`, signature registration /
segment mapping, and `fcntl`. `lsof` identified Bun-extracted native files: a 4,822,976-byte
FFF search dylib and a 342,608-byte Parcel watcher node binding. The watcher-only and
FFF-only experiments each captured the remaining library blocked in the system loader.
Even `/global/health` timed out during the pause. The models.dev socket was incidental:
the main thread was blocked in the loader, and the fast comparisons kept model fetching
enabled. The varying slow samples are not additive timing estimates for the two libraries.

Pinned source confirms the two controls:
[search fallback](https://github.com/anomalyco/opencode/blob/v1.18.4/packages/core/src/filesystem/search.ts)
and [file watcher](https://github.com/anomalyco/opencode/blob/v1.18.4/packages/core/src/filesystem/watcher.ts).
For local diagnostic runs only, these environment variables avoid the two native loads:

```sh
OPENCODE_DISABLE_FFF=1
OPENCODE_EXPERIMENTAL_DISABLE_FILEWATCHER=1
```

The first selects OpenCode's ripgrep fallback; the second stops its automatic filesystem
watch events, including Git HEAD notifications. Neither setting was added to production
code, global configuration, the image, or test fixtures. No signing/security setting was
changed. Existing full-suite failures must still complete the original exclusion/repeat
audit; a suite run with these flags would be a different configuration, not a green repeat.

A full local Sage instance with both flags initialized Chat services about 0.4 seconds
after OpenCode listened. Direct checks passed: health 0.049 s, session creation 0.176 s,
and tool registry 0.015 s. The registry retained read/glob/grep, shell, editing, Live read,
delegated model calls and artifact writing. This verifies registration, not successful
execution of every tool. The live answer attempt failed because both the gateway model
listing and completion endpoints returned HTTP 500. No answer latency is claimed for it.
The attempted turn ended without an answer after 91.4 seconds; it is not a startup result.

A separate `cProfile` trace exposed a Sage cost before model dispatch: `_chat_prompt` took
0.377 seconds, with 0.370 seconds in `_alias_label_for -> _alias_listing -> list_llm_aliases`.
This reads the gateway only to label the already-routed model for the prompt. On a cold
successful listing, the provider can make two requests (`/v1/models` and `/api/aliases`).
The measured failing listing made one. This identifies a removable network wait; it does
not retrospectively prove the exact breakdown of the earlier 1.046-second sample.

Fixed prompt preparation to use an available cached label, even if old, or the known
callable model name. Only cosmetic label lookup is cache-only. Attached model chips still
resolve through the current TTL-governed listing, and actual delegated calls retain live
label resolution. No model choice, permission, sensitivity substitution, or history changed.

Regression tests exercise real Chat turns: a cold prompt must make no label-listing request;
a stale cached label must render without a refresh; a model chip must still refresh and
become unreachable if absent. Before the fix: two failures and the chip control passed.
After: 256 focused prompt/delegation/panel/greeting/Chat checks passed, with three existing
deprecation warnings; `make lint` passed. Three fault plants were caught and restored:
reintroduce prompt network reads (two failures), use stale labels for actual delegated calls
(one failure), and use stale chip permissions (one failure). Scoped review of the two code/test
paths found no further change needed. The Mac workaround remains local and has the stated
watch-event limitation. The combined follow-up now spans 17 files; full gate and commit remain
pending while the other session owns the suite slot.

Local evidence: `/tmp/sage-417-cold-start.py`, `cold-start.log`, `cold-sample.txt`,
`cold-no-watcher-sample.txt`, `cold-no-fff-sample.txt`, `cold-app.log`, `cold-prompt-1.txt`,
`cold-label-red.log`, `cold-label-focused.log`, and `cold-label-plants.log` (all with the
`/tmp/sage-417-` prefix where shortened). Probes are outside the repository.

After the code fix, a fresh local Sage process and new Conversation profiled `_chat_prompt`
at approximately 0.001 seconds (840 calls), with no `_alias_listing` or provider request.
The diagnostic wrapper deliberately raised after prompt construction, before model dispatch,
because the gateway was returning 500s. Its 0.138-second terminal event is an intentional
probe stop, not an answer-time measurement. This checks the original prompt path with the
real local app while keeping the setup result separate from the unavailable model service.
Evidence: `/tmp/sage-417-cold-prompt-fixed-1.txt` and
`/tmp/sage-417-cold-one-turn-fixed-turns.json`. All profile/stop wrappers remain in `/tmp`.

## Gateway recovery and completed local Chat check

The owner reported the playground working. A fresh comparison confirmed a visible GLM answer
there and HTTP 200 from the local bearer-token path for both `/v1/models` (0.653 s) and a
playground-shaped non-streaming completion (3.982 s). The same saved credential and unchanged
Sage gateway client then streamed `Hello!`: first text 0.765 s, completion 0.773 s. A unique
request identifier was used; usage reported zero cached input tokens. No client or credential
change explains the recovery. The prior HTTP 500 results were real but transient; their cause
is not established by these probes.

The previously blocked full local Chat check now passes, on GLM Low with all current patches:

| Prompt, same new Conversation | First visible text | Done | Stream closed |
|---|---:|---:|---:|
| hi | 1.753 s | 1.986 s | 1.993 s |
| hello | 1.721 s | 2.275 s | 2.284 s |
| hey | 1.016 s | 1.247 s | 1.253 s |
| Explain regression in one sentence. | 2.337 s | 2.777 s | 2.783 s |

All four turns returned `ok: true`. The first answer's gateway request began at 605 ms and
received first bytes 1,135 ms later; a title call ran concurrently. The next greeting main
calls began at 265 and 100 ms, with first-byte waits of 1,163 and 907 ms. The normal question
retained its classifier (756 ms) and its normal eight tools. Prompt preparation took 2, 4,
3 and 3 ms. This confirms the prompt-label fix on successful turns, not only on the earlier
pre-dispatch diagnostic stop. The local Mac workaround remained enabled; the results do not
claim production/Linux startup or full deployment validation.

Evidence: `/tmp/sage-417-gateway-recovered.json`, `/tmp/sage-417-recovered-chat.log`,
`/tmp/sage-417-recovered-chat-turns.json`, `/tmp/sage-417-recovered-chat-timing.json`, and
`/tmp/sage-417-recovered-app.log`. The local profiler's `cold-prompt-*.txt` files were reused
by this successful run and now contain the post-fix profiles. The earlier 0.377 s profile
was captured in the investigation output above. No production code changed during this
recheck. The local app and probes were stopped afterward; full gate and commit remain pending.

## September 21 main merge and compaction test audit

Merged remote main `5fa56a01519cc7e9d06466a322faad557d175525` with `--no-ff`, including
the test speed improvements and #479 native model routes. The new Chat-done JS harness now
uses main's shared `unrefTimeout` helper for sandbox timers. No production timeout changed.
The local timing table above predates this merge; it is not a new end-to-end measurement
of the merged candidate.

Main collects 7,545 tests; this candidate collects 7,609: 64 added, none removed. Full runs
use the root checkout as their working directory, explicit candidate imports/config/tests,
and the pinned real OpenCode binary. No Mac diagnostic flags are set. With the 64 added
tests deselected, the first run gave 7,540 passed, 1 failed, 4 skipped in 198.60 seconds.
All five real OpenCode cases passed; earlier startup failures did not reproduce.

The remaining failure was `test_compaction_leaves_a_session_the_next_turn_has_taken`.
The required audit reproduced it alone (17 passed, 1 failed), beside the greeting tests
under `-n0` (54 passed, 1 failed), in the full exclusion run (7,540 passed, 1 failed,
4 skipped; 206.96 seconds), and in the full restored run (7,604 passed, 1 failed,
4 skipped; 240.00 seconds). All full runs kept the same 995-file source manifest.
This was a deterministic test-premise failure, not a reported resource-pressure flake.

The test used `hi` to reach its mocked handoff hook and simulate another turn taking the
lock. Exact greetings now skip that hook. Changed only this test to use `continue`, assert
that the hook acquired the lock, and release only a lock acquired by the test. Existing
checks still require compaction to defer while the next turn owns the session and to run
afterward. All 55 compaction/greeting checks passed. Restoring `hi` as a fault plant fails
the new premise assertion; the plant was then restored. Scoped review covered this test
and the JS timer adaptation. `make lint` and whitespace checks passed.

The final full gate on these corrected bytes and the commit are recorded in the #417
worker report. Production workspace validation and fresh Mixpanel end-to-end timings
remain outstanding; the local timing measurements do not replace them.
