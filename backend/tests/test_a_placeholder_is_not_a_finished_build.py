"""The first earnings build wrote components but left the shipped entry screen in place."""
from pathlib import Path
from types import SimpleNamespace

from sage.feedback.runner import FeedbackRunner

from .fake_opencode import Turn
from .test_a_dead_alias_stops_the_turn_before_it_starts import (
    PLAN,
    _no_waiting,  # noqa: F401
    _orch,
)

STARTER = (Path(__file__).resolve().parents[2] / "template/react-vite/src/App.tsx").read_text()
COMPONENT = 'export default function Dashboard() { return <h1>Earnings Dashboard</h1> }\n'
CONNECTED = 'import Dashboard from "./components/Dashboard";\nexport default Dashboard;\n'


def test_a_clean_typecheck_does_not_accept_the_starter_screen(tmp_path, monkeypatch):
    (tmp_path / "src/components").mkdir(parents=True)
    (tmp_path / "src/App.tsx").write_text(STARTER)
    (tmp_path / "src/components/Dashboard.tsx").write_text(COMPONENT)
    monkeypatch.setattr("sage.feedback.runner.subprocess.run",
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))

    report = FeedbackRunner().check(tmp_path)

    assert report.ok is False, "Unused components left the user looking at the starter screen"
    assert any(e.file == "src/App.tsx" and "placeholder" in e.message for e in report.errors)
    (tmp_path / "src/App.tsx").write_text(CONNECTED)
    assert FeedbackRunner().check(tmp_path).ok is True


def test_build_repairs_the_entry_before_reporting_success(tmp_path, monkeypatch):
    orch, oc = _orch(tmp_path, turns=[
        PLAN,
        Turn(text="Implemented the dashboard", writes={
            "src/App.tsx": STARTER, "src/components/Dashboard.tsx": COMPONENT}),
        Turn(text="Connected the dashboard", writes={"src/App.tsx": CONNECTED}),
    ])
    list(orch.build_stream("build me a dashboard"))
    runner = FeedbackRunner()

    def check(path):
        # Only tsc is simulated; git, files, feedback and the repair loop are real.
        with monkeypatch.context() as patch:
            patch.setattr("sage.feedback.runner.subprocess.run",
                          lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))
            return runner.check(path)

    monkeypatch.setattr(orch._feedback, "check", check)
    events = list(orch.approve_stream())

    assert [e["ok"] for e in events if e["type"] == "typecheck"] == [False, True]
    assert "placeholder" in oc.prompts[-1]["text"]
    assert orch.project(start_preview=False).app_for_turn().path.joinpath("src/App.tsx").read_text() == CONNECTED
    assert next(e for e in events if e["type"] == "done")["ok"] is True
