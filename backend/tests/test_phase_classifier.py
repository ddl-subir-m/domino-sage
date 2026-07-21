"""PhaseClassifier: per-step plan/implement from the message tail, incl. interleaving."""
from sage.router.models import Phase
from sage.router.phase_classifier import classify


def _assistant(*tool_names):
    return {"role": "assistant", "tool_calls": [{"function": {"name": n}} for n in tool_names]}


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
