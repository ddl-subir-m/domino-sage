"""A build whose app Sage could not run says why, not only "The app wasn't run" (#662, #657).

Live (2026-10-06): every build turn that night ended "Code checks passed. The app wasn't run.",
including one that shipped a page crashing on first render. The sentence was true and useless: it did
not say whether the preview never started, never loaded the page, or was stopped, and those need
different things from the person reading it.
"""
from __future__ import annotations

import pytest

from .test_build_conversation_return import run as render
from .test_changed_page_validation import build, run  # noqa: F401


def test_a_page_the_preview_never_loaded_says_so(build):  # noqa: F811
    orch, _, _ = build
    _, done = run(orch)
    assert done["verification"]["overall"] == "unverified"
    assert done["verification"]["reason"] == "the preview didn't load the changed page within 0.5s"


def test_a_preview_that_never_started_says_so(build):  # noqa: F811
    orch, project, _ = build
    project.supervisor.state = "starting"
    _, done = run(orch)
    assert done["verification"]["reason"] == "the preview didn't start within 0.5s"


def test_a_stopped_check_says_it_was_stopped(build):  # noqa: F811
    orch, project, _ = build
    _, done = run(orch, report=lambda event: setattr(project, "stop_requested", True))
    assert done["verification"]["reason"] == "the build was stopped"


def test_a_check_that_passed_gives_no_reason(build):  # noqa: F811
    orch, _, _ = build
    _, done = run(orch, report=lambda event: orch.record_preview_ack(event["validationId"]))
    assert done["verification"]["overall"] == "passed"
    assert "reason" not in done["verification"]


@pytest.mark.parametrize("reason, said", [
    ("the preview didn't start within 10s",
     "Code checks passed. The app wasn't run — the preview didn't start within 10s."),
    (None, "Code checks passed. The app wasn't run."),
])
def test_the_status_line_carries_the_reason(reason, said):
    rows = render({"savedVerification": "unverified", "verificationReason": reason})
    assert rows[-1]["value"] == said
