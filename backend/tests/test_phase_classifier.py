"""PhaseClassifier: per-step plan/implement from the message tail, incl. interleaving."""
from sage.router.models import Phase
from sage.router.phase_classifier import assess, classify


def _assistant(*tool_names):
    return {"role": "assistant", "tool_calls": [{"function": {"name": n}} for n in tool_names]}


def _call(name, cid):
    """An assistant tool call with an id, so its result can be traced back to the tool."""
    return {"role": "assistant", "tool_calls": [{"id": cid, "function": {"name": name}}]}


def _result(cid, content):
    return {"role": "tool", "tool_call_id": cid, "content": content}


def test_empty_and_fresh_prompt_default_to_plan():
    assert classify([]) is Phase.PLAN
    assert classify(None) is Phase.PLAN
    assert classify([{"role": "user", "content": "build a todo app"}]) is Phase.PLAN


def test_reasoning_turn_is_plan():
    msgs = [{"role": "user", "content": "x"}, {"role": "assistant", "content": "Here's my plan"}]
    assert classify(msgs) is Phase.PLAN


def test_read_and_search_tools_are_plan():
    assert classify([_assistant("read")]) is Phase.PLAN
    assert classify([_assistant("grep", "list")]) is Phase.PLAN


def test_write_tools_are_implement():
    assert classify([_assistant("edit")]) is Phase.IMPLEMENT
    assert classify([_assistant("write")]) is Phase.IMPLEMENT
    assert classify([_assistant("str_replace")]) is Phase.IMPLEMENT


def test_reads_after_a_write_stay_implement():
    # Sticky within a turn: a read between edits must NOT bounce back to planning.
    msgs = [{"role": "user", "content": "x"}, _assistant("write"),
            {"role": "tool", "content": "ok"}, _assistant("read")]
    assert classify(msgs) is Phase.IMPLEMENT


def test_todowrite_is_neutral_and_does_not_flip_back():
    # todowrite is mid-build progress bookkeeping, not re-planning -> stays IMPLEMENT.
    msgs = [{"role": "user", "content": "x"}, _assistant("write"),
            _assistant("read"), _assistant("todowrite")]
    assert classify(msgs) is Phase.IMPLEMENT


def test_new_turn_resets_to_plan_despite_prior_writes():
    # A follow-up prompt starts a fresh turn: prior writes don't leak forward.
    msgs = [{"role": "user", "content": "build it"}, _assistant("write"), _assistant("edit"),
            {"role": "user", "content": "now add a filter"}]
    assert classify(msgs) is Phase.PLAN
    msgs.append(_assistant("read"))
    assert classify(msgs) is Phase.PLAN                       # exploring the new request
    msgs.append(_assistant("edit"))
    assert classify(msgs) is Phase.IMPLEMENT                  # writing for the new request


def test_concentrated_plan_then_sustained_implement():
    steps = [{"role": "user", "content": "start"}]
    assert classify(steps) is Phase.PLAN                      # step 1: planning
    steps.append(_assistant("read"))
    assert classify(steps) is Phase.PLAN                      # step 2: still exploring (neutral)
    steps.append(_assistant("todowrite"))
    assert classify(steps) is Phase.PLAN                      # step 3: writing the plan (neutral)
    steps.append(_assistant("edit"))
    assert classify(steps) is Phase.IMPLEMENT                 # step 4: first write -> implement
    steps.append(_assistant("read"))
    assert classify(steps) is Phase.IMPLEMENT                 # step 5: read mid-build stays implement
    steps.append(_assistant("todowrite"))
    assert classify(steps) is Phase.IMPLEMENT                 # step 6: progress update stays implement
    steps.append(_assistant("write"))
    assert classify(steps) is Phase.IMPLEMENT                 # step 7: still building


# --- Rescue signals (assess) ------------------------------------------------------------------
# The half the write-flip rule never had: a turn that starts failing after its first write is
# pinned to the cheap coder with nothing to pull it back.

def _building():
    # The state every rescue test starts from: one successful write, so base_phase is IMPLEMENT.
    return [{"role": "user", "content": "build it"}, _call("write", "c1"), _result("c1", "ok")]


def test_one_error_does_not_rescue():
    # The flap case, and the whole reason corroboration exists. A single "error:" in build output
    # must not reroute the rest of the turn to the expensive model.
    msgs = _building() + [_call("bash", "c2"), _result("c2", "error: could not resolve ./Foo")]
    s = assess(msgs)
    assert s.phase is Phase.IMPLEMENT
    assert s.reason == "write-flip"
    assert s.errors_since_write == 1


def test_two_errors_rescue_to_plan():
    msgs = _building() + [_call("bash", "c2"), _result("c2", "error: could not resolve ./Foo"),
                          _call("bash", "c3"), _result("c3", "npm ERR! build failed")]
    s = assess(msgs)
    assert s.phase is Phase.PLAN
    assert s.reason == "rescue-errors"
    assert s.base_phase is Phase.IMPLEMENT      # the old rule still says implement — that's the point
    assert s.rescues == 1


def test_a_critical_error_rescues_on_its_own():
    # Hard override: no corroboration needed, a crash has no benign reading.
    msgs = _building() + [_call("bash", "c2"),
                          _result("c2", "Traceback (most recent call last):\n  File ...")]
    s = assess(msgs)
    assert s.phase is Phase.PLAN
    assert s.reason == "rescue-critical"
    assert s.errors_since_write == 1


def test_a_write_after_a_rescue_returns_to_implement():
    # The ratchet case. Without this the rescue is a one-way trip to the expensive tier and the
    # feature is a cost regression: a write is progress, and progress clears the error window.
    msgs = _building() + [_call("bash", "c2"), _result("c2", "error: x"),
                          _call("bash", "c3"), _result("c3", "error: y")]
    assert assess(msgs).phase is Phase.PLAN
    msgs += [_call("edit", "c4"), _result("c4", "ok")]
    s = assess(msgs)
    assert s.phase is Phase.IMPLEMENT
    assert s.reason == "write-flip"
    assert s.errors_since_write == 0
    assert s.rescues == 1                        # the episode still happened, it just ended


def test_a_second_failed_episode_latches_for_the_rest_of_the_turn():
    # Two failed episodes means the cheap model isn't converging. Stop flip-flopping between tiers.
    msgs = _building() + [_call("bash", "c2"), _result("c2", "error: x"),
                          _call("bash", "c3"), _result("c3", "error: y"),
                          _call("edit", "c4"), _result("c4", "ok"),
                          _call("bash", "c5"), _result("c5", "error: x"),
                          _call("bash", "c6"), _result("c6", "error: y")]
    assert assess(msgs).rescues == 2
    msgs += [_call("edit", "c7"), _result("c7", "ok")]       # a write no longer de-escalates
    s = assess(msgs)
    assert s.phase is Phase.PLAN
    assert s.reason == "rescue-latched"


def test_read_results_never_count_as_errors():
    # A grep hit or a source file containing "error:" is not a failure. Tracing the result back to
    # the tool that produced it is what keeps these out, rather than ever-more-careful markers.
    msgs = _building() + [_call("read", "c2"), _result("c2", "error: x\nerror: y"),
                          _call("grep", "c3"), _result("c3", "error: x\nerror: y")]
    assert assess(msgs).phase is Phase.IMPLEMENT


def test_an_untraceable_result_never_counts():
    # No tool_call_id (and the pre-existing tests' bare tool messages): unknown origin, so ignored.
    msgs = _building() + [{"role": "tool", "content": "Traceback (most recent call last):"}]
    assert assess(msgs).phase is Phase.IMPLEMENT


def test_a_new_turn_clears_the_latch():
    # Same turn-boundary rule as the write flip: a fresh prompt starts clean, in PLAN.
    msgs = _building() + [_call("bash", "c2"), _result("c2", "error: x"),
                          _call("bash", "c3"), _result("c3", "error: y")]
    assert assess(msgs).phase is Phase.PLAN
    msgs.append({"role": "user", "content": "try again"})
    s = assess(msgs)
    assert s.phase is Phase.PLAN and s.reason == "no-write"
    assert s.rescues == 0 and s.errors_since_write == 0


def test_results_are_sampled_head_and_tail_and_tagged():
    # What the shim echoes to tune the markers: enough to read, never a file's worth of source.
    # The write's own "ok" result is examined too — a failed edit is a signal, so edit output counts.
    msgs = _building() + [_call("bash", "c2"), _result("c2", "error: boom\nstack line 1\nline 2")]
    assert assess(msgs).samples == ("none ok", "soft error: boom ... line 2")


def test_sampling_keeps_the_last_results_not_the_first():
    # Live 2026-08-13: a 10-result turn kept the first 6, so every failure at the end of the turn —
    # the only part worth reading — was dropped. The recent window is what the scorer acts on.
    msgs = _building()
    for i in range(9):
        msgs += [_call("bash", f"b{i}"), _result(f"b{i}", f"line {i}")]
    s = assess(msgs)
    assert s.examined == 10
    assert len(s.samples) == 6
    assert s.samples[-1] == "none line 8"        # newest kept
    assert "none ok" not in s.samples            # oldest dropped


def test_the_markers_this_image_actually_prints():
    # Every string here was copied out of a real failing build on the Sage image (2026-08-13), not
    # guessed. npm on this image prints lowercase `npm error`, never the `npm ERR!` of older npm.
    for text in ("npm error code E404",
                 "ls: cannot access 'node_modules/.bin/': No such file or directory",
                 "error during build:"):
        msgs = _building() + [_call("bash", "c2"), _result("c2", text),
                              _call("bash", "c3"), _result("c3", text)]
        assert assess(msgs).phase is Phase.PLAN, text


def test_a_benign_npm_warning_is_not_an_error():
    # From the same run, and the reason "not found" is deliberately not a marker: this line is npm
    # telling you it is about to do something normal.
    warn = "npm warn exec The following package was not found and will be installed: tsc@2.0.4"
    msgs = _building() + [_call("bash", "c2"), _result("c2", warn),
                          _call("bash", "c3"), _result("c3", warn)]
    s = assess(msgs)
    assert s.errors_since_write == 0
    assert s.phase is Phase.IMPLEMENT


def test_unmatched_results_are_sampled_too():
    # The fix for the first live run: with matched-only samples, a clean build and a build whose
    # failure the markers missed both logged nothing at all. `examined` is what tells them apart.
    msgs = _building() + [_call("bash", "c2"), _result("c2", "added 1 package in 2s")]
    s = assess(msgs)
    assert s.examined == 2 and s.errors_since_write == 0
    assert s.samples == ("none ok", "none added 1 package in 2s")
    assert s.phase is Phase.IMPLEMENT      # a clean result still changes nothing


def test_read_results_are_not_even_examined():
    # Only shell and write output is read, so the echo can't leak a file the agent happened to open.
    msgs = _building() + [_call("read", "c2"), _result("c2", "some source line")]
    s = assess(msgs)
    assert s.examined == 1                 # the write's own result, and nothing from the read
    assert s.samples == ("none ok",)


def test_classify_is_unchanged_by_any_of_this():
    # Step 1 is observe-only: what actually routes must not move. classify() is base_phase.
    msgs = _building() + [_call("bash", "c2"), _result("c2", "error: x"),
                          _call("bash", "c3"), _result("c3", "Traceback (most recent call last):")]
    assert assess(msgs).phase is Phase.PLAN      # the rescue fires...
    assert classify(msgs) is Phase.IMPLEMENT     # ...and routing ignores it, for now
