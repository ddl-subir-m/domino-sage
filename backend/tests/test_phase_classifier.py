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
    # Sticky: a read between edits must NOT bounce back to planning.
    msgs = [_assistant("write"), {"role": "tool", "content": "ok"}, _assistant("read")]
    assert classify(msgs) is Phase.IMPLEMENT


def test_explicit_plan_tool_flips_back_to_plan():
    # Only an explicit (re)planning tool moves it back after implementing.
    msgs = [_assistant("write"), _assistant("read"), _assistant("todowrite")]
    assert classify(msgs) is Phase.PLAN


def test_concentrated_plan_then_sustained_implement():
    steps = [{"role": "user", "content": "start"}]
    assert classify(steps) is Phase.PLAN                      # step 1: planning
    steps.append(_assistant("read"))
    assert classify(steps) is Phase.PLAN                      # step 2: still exploring (neutral)
    steps.append(_assistant("grep"))
    assert classify(steps) is Phase.PLAN                      # step 3: still exploring (neutral)
    steps.append(_assistant("edit"))
    assert classify(steps) is Phase.IMPLEMENT                 # step 4: first write -> implement
    steps.append(_assistant("read"))
    assert classify(steps) is Phase.IMPLEMENT                 # step 5: read mid-build stays implement
    steps.append(_assistant("write"))
    assert classify(steps) is Phase.IMPLEMENT                 # step 6: still building
    steps.append(_assistant("todowrite"))
    assert classify(steps) is Phase.PLAN                      # step 7: explicit re-plan
