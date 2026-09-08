"""What a turn waits for before its first inference, and what it no longer waits for.

Measured live against the real Domino gateway on 2026-09-07: a median turn spent 3.6s — 22% of
itself — before its first inference reached the gateway, and three gates were all of it. A `git
fetch` for the incoming-changes check (0.4-0.6s, every turn), an Alias listing for the model check
(2.5-2.9s, whenever the 60-second cache had gone cold), and the scope classifier's own model call
(0.4-1.3s, every Auto turn on a built app). Serial, one after another, with nothing on screen.

None of the three is deleted. Every one is a guarantee somebody argued for, and the question is when
it is paid for, not whether. Two of them stopped being paid for in front of the turn: the Alias
listing is kept warm off the request path, and the classifier is started early and joined where it
is read (that half is in test_scope.py, beside the three properties it must not break). The `git
fetch` is not, and the first test here is why — the cheap way to make it free is to read the rail's
warm cache, and that is the one thing this gate may not do.

Durations are never asserted. A laptop's numbers are not a Builder's numbers, and a test that
asserted seconds would be measuring this machine. What is assertable locally is the structure: how
many times the work runs, on whose thread, and whether the answer a gate reads is the fresh one.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import ClassVar

import pytest

from sage.orchestrator import service as svc
from sage.orchestrator.service import Orchestrator
from sage.router.models import Mode, ModelCatalog

from .fake_opencode import FakeOpenCode, Turn

# Kept before the autouse fixture below stubs `time.sleep` out: two tests here have to let another
# thread actually get somewhere, and a sleep that returns instantly would prove nothing.
_really_sleep = time.sleep

CATALOG = ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                       plan="p", implement="i", ask="a")


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(path), capture_output=True, text=True,
                          check=True).stdout


class OkFeedback:
    def check(self, path: Path):
        from sage.feedback.runner import FeedbackReport

        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """One scripted word per routed request — the scope classifier is the only caller here."""

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "BUILD"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


class FakeVite:
    made: ClassVar[list] = []

    def __init__(self, workspace, base_prefix: str = "", **_ignored) -> None:
        self.workspace = Path(workspace)
        self.running = False
        FakeVite.made.append(self)

    def start(self, ready_timeout_s: float = 30.0) -> str:
        self.running = True
        return "http://127.0.0.1:5173"

    def stop(self) -> None:
        self.running = False


class FakeQueries:
    def __init__(self, workspace, template=None, **_ignored) -> None:
        self.workspace = workspace
        self.port: int | None = None

    def start(self, *_a, **_k) -> int:
        self.port = 7777
        return self.port

    def stop(self) -> None:
        self.port = None


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _fake_preview(monkeypatch):
    FakeVite.made = []
    monkeypatch.setattr(svc, "ViteSupervisor", FakeVite)
    monkeypatch.setattr(svc, "PreviewQueries", FakeQueries)
    yield
    FakeVite.made = []


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text('{"name": "template"}')
    (t / "AGENTS.md").write_text("# Building this app\n\nSage's rules go here.\n")
    return t


def _orch(tmp: Path, root: Path, turns: list[Turn] | None = None):
    oc = FakeOpenCode(root, turns or [])
    orch = Orchestrator(workspace_dir=root, template=_template(tmp), gateway=ScriptedGateway(),
                        catalog=CATALOG, project_id="Sage", feedback=OkFeedback(),
                        opencode_client=oc)
    return orch, oc


def _repo(tmp: Path) -> Path:
    """A Project volume that is the root of its own repo, with a remote to push to."""
    root = tmp / "mnt" / "code"
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "dev@example.com")
    _git(root, "config", "user.name", "Dev")
    (root / "README.md").write_text("project\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")
    bare = tmp / "remote.git"
    _git(tmp, "init", "-q", "--bare", str(bare))
    _git(root, "remote", "add", "origin", str(bare))
    _git(root, "push", "-q", "-u", "origin", "HEAD")
    return root


def _teammate(tmp: Path) -> Path:
    other = tmp / "other"
    _git(tmp, "clone", "-q", str(tmp / "remote.git"), str(other))
    _git(other, "config", "user.email", "mate@example.com")
    _git(other, "config", "user.name", "Mate")
    return other


def _project(orch: Orchestrator):
    """The bound Project, with the plan gate off so a turn reaches the build path (#74)."""
    project = orch.project(start_preview=False)
    project.record.write_settings({"skip_planning": True})
    project.control.set_mode(Mode.IMPLEMENT)
    return project


def _push_app(root: Path) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "sage: seed app")
    _git(root, "push", "-q")


def _mate_edits(other: Path, rel: str) -> None:
    path = other / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("somebody else was here\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "mate: edit")
    _git(other, "push", "-q")


def _of(events: list[dict], kind: str) -> list[dict]:
    return [e for e in events if e.get("type") == kind]


# --- the incoming-changes gate -------------------------------------------------------------------
#
# The one gate here that is still serial, and deliberately: there is nothing between the top of a
# turn and this gate for a fetch to run beside, and the first place with real work to hide it under
# is past the pre-turn commit — where a refused turn would already have written. So what is under
# test is the guarantee, which the cheap version of this change would have traded away.

def test_the_gate_reads_this_turns_answer_not_the_rails_warm_one(tmp_path: Path):
    """The guarantee the prefetch is not allowed to weaken (#78). The rail keeps a reading up to half
    a minute old for its badge, and honouring THAT would be the cheap version of this change — and
    would let a turn build on top of a teammate's push that landed twenty seconds ago."""
    root = _repo(tmp_path)
    orch, oc = _orch(tmp_path, root)
    project = _project(orch)
    _push_app(root)

    # The rail's cache, warm and about to be wrong: taken before the teammate pushed.
    orch._check_remote(project)
    assert orch._incoming_now().files == []
    _mate_edits(_teammate(tmp_path), f"apps/{project.workspace.app_id}/src/App.tsx")

    events = list(orch.build_stream("add a chart"))

    assert _of(events, "incoming-changes")[0]["files"] == ["src/App.tsx"]
    assert oc.prompts == []                       # stopped before any inference


# --- the model gate's listing --------------------------------------------------------------------
#
# The cache here already worked — half the measured turns paid 0.0s at this gate. The whole of the
# remaining cost was the cold miss on the other half, so what is under test is the keep-warm, not
# the cache.

class Alias:
    def __init__(self, name: str) -> None:
        self.name = name


class CountingResources:
    """Counts listings, and can hold one open so a second caller can be caught racing it."""

    def __init__(self) -> None:
        self.listings = 0
        self.hold: threading.Event | None = None
        self.asked = threading.Event()

    def list_llm_aliases(self):
        self.listings += 1
        self.asked.set()
        if self.hold is not None:
            self.hold.wait(30)
        return [Alias("gpt-5.4")]


def _slots_orch(tmp_path: Path) -> tuple[Orchestrator, CountingResources]:
    root = tmp_path / "mnt" / "code"
    root.mkdir(parents=True)
    orch, _oc = _orch(tmp_path, root)
    resources = CountingResources()
    orch._resources = resources
    orch._endpoint_listing = lambda *_a, **_k: ([], [])
    orch._gateway_mode = "domino"
    return orch, resources


def _settle() -> None:
    for thread in threading.enumerate():
        if thread.name == "sage-slot-listing":
            thread.join(10)


def test_the_listing_is_refreshed_before_it_goes_cold(tmp_path: Path):
    orch, resources = _slots_orch(tmp_path)
    orch._slot_listings_now()
    assert resources.listings == 1

    # Just taken: nothing to do, and nothing spent. An idle Builder polls its rail every thirty
    # seconds and must not re-list the gateway on every one of them.
    orch._slot_listings_due()
    _settle()
    assert resources.listings == 1

    # Inside the refresh lead now, which is where the rail's poll finds it every other time.
    orch._slot_listings_at -= svc._TURN_SLOT_TTL_S * svc._TURN_SLOT_REFRESH_AT
    orch._slot_listings_due()
    _settle()
    assert resources.listings == 2

    # And the turn that follows pays nothing at the gate, because the answer was already there.
    orch._slot_listings_now()
    assert resources.listings == 2


def test_a_turn_arriving_mid_refresh_waits_for_it_rather_than_listing_again(tmp_path: Path):
    """Singleflight. Without it the keep-warm would make the cold turn WORSE, not better: two
    listings racing, both of them the round trip this exists to take off the turn."""
    orch, resources = _slots_orch(tmp_path)
    resources.hold = threading.Event()
    orch._slot_listings_due()
    assert resources.asked.wait(5), "the background refresh never went out"

    answered: list = []
    waiting = threading.Thread(target=lambda: answered.append(orch._slot_listings_now()))
    waiting.start()
    _really_sleep(0.05)
    assert answered == [], "the turn listed the gateway itself instead of joining the one in flight"

    resources.hold.set()
    waiting.join(10)
    _settle()
    assert resources.listings == 1
    assert len(answered) == 1 and [a.name for a in answered[0][0]] == ["gpt-5.4"]


def test_a_gateway_nothing_asks_about_is_not_kept_warm(tmp_path: Path):
    # `_turn_slot_refusal` never reads the pair off a non-Domino gateway, so nothing should be
    # fetched for it either — the same rule the refusal applies, one step earlier.
    orch, resources = _slots_orch(tmp_path)
    orch._gateway_mode = "openai"

    orch._slot_listings_due()
    _settle()

    assert resources.listings == 0
