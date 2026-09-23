"""Feedback runner parsing + circuit breaker logic (Step 5)."""
import json
from pathlib import Path

from sage.feedback.circuit_breaker import CircuitBreaker
from sage.feedback.runner import FeedbackError, FeedbackReport, FeedbackRunner


def test_report_message_and_signature():
    r = FeedbackReport(ok=False, errors=[FeedbackError("a.tsx", 3, 1, "TS2304", "x")])
    assert "Fix these" in r.as_agent_message()
    assert "a.tsx:3:TS2304" == r.signature()
    assert FeedbackReport(ok=True).as_agent_message() == "Syntax check passed. No errors."


def test_parse_py_compile_extracts_errors():
    from sage.feedback.runner import parse_py_compile

    out = (
        '  File "/ws/app.py", line 12\n'
        "    def broken(:\n"
        "               ^\n"
        "SyntaxError: invalid syntax\n"
    )
    errs = parse_py_compile(out, Path("/ws"))
    assert errs == [FeedbackError("app.py", 12, 1, "SyntaxError", "invalid syntax")]


def test_parse_node_check_extracts_errors():
    from sage.feedback.runner import parse_node_check

    out = (
        "/ws/static/app.js:7\n"
        "  const x = ;\n"
        "            ^\n"
        "\n"
        "SyntaxError: Unexpected token ';'\n"
        "    at wrapSafe (node:internal/modules/cjs/loader:1378:20)\n"
    )
    errs = parse_node_check(out, Path("/ws"))
    assert errs == [FeedbackError("static/app.js", 7, 1, "SyntaxError", "Unexpected token ';'")]


def test_a_no_build_app_is_compiled_and_parsed_not_typechecked(tmp_path):
    """A fastapi-antd workspace (#490) has no types to check. The end-of-turn check compiles every
    .py, parses every script the page loads, and reads the placeholder — each planted here, one
    per condition, so a green report means the check ran and not that it was skipped."""
    import shutil

    (tmp_path / ".sage").mkdir()
    (tmp_path / ".sage" / "settings.json").write_text('{"stack": "fastapi-antd"}')
    (tmp_path / "static" / "vendor").mkdir(parents=True)
    (tmp_path / "app.py").write_text("x = 1\n")
    (tmp_path / "static" / "app.js").write_text("const app = 1;\n")
    # A vendored bundle is never the agent's: unparseable on purpose, and it must not be read.
    (tmp_path / "static" / "vendor" / "big.js").write_text("this is not javascript\n")

    report = FeedbackRunner().check(tmp_path)
    assert report.ok, report.as_agent_message()
    assert report.as_agent_message() == "Syntax check passed. No errors."

    (tmp_path / "app.py").write_text("def broken(:\n    pass\n")
    report = FeedbackRunner().check(tmp_path)
    assert not report.ok
    assert [(e.file, e.code) for e in report.errors] == [("app.py", "SyntaxError")]
    assert report.as_agent_message().startswith("Syntax check found 1 error(s)")

    (tmp_path / "app.py").write_text("x = 1\n")
    (tmp_path / "static" / "app.js").write_text("const x = ;\n")
    report = FeedbackRunner().check(tmp_path)
    if shutil.which("node"):
        assert [(e.file, e.code) for e in report.errors] == [("static/app.js", "SyntaxError")]
    else:
        assert report.ok and "node is not on PATH" in report.raw

    (tmp_path / "static" / "app.js").write_text("h('main', { className: 'sage-placeholder' })\n")
    report = FeedbackRunner().check(tmp_path)
    assert [(e.file, e.code) for e in report.errors] == [("static/app.js", "SAGE001")]


def test_breaker_stops_on_clean():
    b = CircuitBreaker()
    assert b.record("", resolved=True).action == "stop"


def test_breaker_no_progress():
    b = CircuitBreaker(no_progress_limit=3)
    assert b.record("sig", resolved=False).action == "continue"   # turn 1
    assert b.record("sig", resolved=False).action == "continue"   # turn 2 (repeat 1)
    assert b.record("sig", resolved=False).action == "stop"       # turn 3 (repeat 2 -> limit)


def test_breaker_max_iterations():
    b = CircuitBreaker(max_iterations=2, no_progress_limit=99)
    assert b.record("a", resolved=False).action == "continue"
    assert b.record("b", resolved=False).action == "stop"


def test_breaker_time_budget():
    t = [0.0]
    b = CircuitBreaker(max_seconds=10, no_progress_limit=99, clock=lambda: t[0])
    assert b.record("a", resolved=False).action == "continue"
    t[0] = 11.0
    assert b.record("b", resolved=False).action == "stop"


def test_implement_prompt_names_the_same_check_the_gate_runs():
    """The agent's own check has to check what Sage checks (#41), restated for the no-build stack.

    There is no config file left to drift out of step (that was `tsconfig.json`'s failure mode,
    fixed for react-vite by pinning the prompt to `FeedbackRunner`'s own default) — `check_python_
    stack` runs two fixed commands, so the only remaining risk is the prompt's PROSE describing them
    going stale by hand-editing. Pinned on the literal commands rather than on an attribute, because
    `FeedbackRunner` no longer holds one to pin to.
    """
    config = json.loads((Path(__file__).resolve().parents[2] / "opencode.json").read_text())
    prompt = config["agent"]["sage-implement"]["prompt"]
    assert "py_compile" in prompt
    assert "node --check" in prompt
