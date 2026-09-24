"""A failed stack check records WHAT failed, not only that a repair turn followed (#534).

A TFL Build on 2026-09-24 spent 66 s (15% of the turn) on a `typecheck_repair` agent turn, and the
downloaded diagnostics said only that one happened. The check's report is the one thing that names
the cause — a placeholder left in, a syntax error, a missing import — and it went to the model and
nowhere else. Codes and a count only: file paths and messages are the app's own text.
"""
from __future__ import annotations

from pathlib import Path

from sage import build_diagnostics as diagnostics
from sage.feedback.runner import FeedbackError, FeedbackReport

from .fake_opencode import Turn
from .ledger import last_turn, needs_ledger
from .test_a_turn_records_where_its_time_went import _no_waiting, _orch  # noqa: F401  (fixture)


class FailsOnce:
    def __init__(self):
        self.calls = 0

    def check(self, path: Path) -> FeedbackReport:
        self.calls += 1
        if self.calls > 1:
            return FeedbackReport(ok=True)
        return FeedbackReport(ok=False, kind="Syntax check", errors=[
            FeedbackError("static/app.js", 1, 1, "SAGE001", "PRIVATE_SENTINEL placeholder"),
            FeedbackError("static/app.js", 9, 4, "SyntaxError", "PRIVATE_SENTINEL token"),
            FeedbackError("app.py", 3, 1, "SyntaxError", "PRIVATE_SENTINEL indent"),
        ])


@needs_ledger
def test_the_check_span_names_the_codes_that_sent_the_turn_back(tmp_path: Path):
    orch = _orch(tmp_path, [
        Turn(text="Built it.", writes={"src/App.tsx": "export default () => 1\n"}),
        Turn(text="Fixed it.", writes={"src/App.tsx": "export default () => 2\n"}),
    ])
    orch._feedback = FailsOnce()
    list(orch.build_stream("add a chart"))

    rec = last_turn()
    checks = [s for s in rec.spans if s.name == "typecheck"]
    assert [(s.fields.get("errors"), s.fields.get("error_codes")) for s in checks] == [
        (3, ["SAGE001", "SyntaxError"]), (0, [])]

    exported = diagnostics.snapshot(rec, {"turnId": "turn_x", "kind": "build"}, terminal=True)
    spans = [s for s in exported["timing"]["spans"] if s["name"] == "typecheck"]
    assert [(s["errors"], s["errorCodes"]) for s in spans] == [(3, ["SAGE001", "SyntaxError"]), (0, [])]
    assert "PRIVATE_SENTINEL" not in str(exported) and "static/app.js" not in str(exported)


def test_the_download_admits_only_code_shaped_strings():
    rec_span = {"name": "typecheck", "depth": 0, "atMs": 0, "ms": 1, "open": False,
                "errors": 2, "error_codes": ["TS2304", "a private path/with space", 7, "SAGE001"]}
    assert diagnostics._check_codes(rec_span) == ["TS2304", "SAGE001"]
