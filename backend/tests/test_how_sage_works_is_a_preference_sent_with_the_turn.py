"""How Sage works is one preference, posted with the turn (ADR-0070).

Guided is the fallback. The only accepted Direct value is the string ``direct``. This slice
records the choice and does not change arming, agents, or gates.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "prefs_harness.mjs"
_JS = Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js"
_SERVICE = Path(__file__).resolve().parents[1] / "sage" / "orchestrator" / "service.py"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")


def _run(steps: list[dict]) -> list:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(steps), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _method(source: str, name: str) -> str:
    start = source.index(f"\n    {name}(")
    return source[start:source.index("\n    },", start)]


def test_how_sage_works_starts_guided_and_only_direct_is_the_other_answer():
    answers = _run([
        {"viewer": "u1", "op": "get", "name": "howSageWorks"},
        {"op": "set", "name": "howSageWorks", "value": "direct"},
        {"op": "reload"},
        {"op": "get", "name": "howSageWorks"},
        {"op": "set", "name": "howSageWorks", "value": "bare"},
        {"op": "get", "name": "howSageWorks"},
        {"viewer": "u2", "op": "get", "name": "howSageWorks"},
    ])
    assert answers[0] == "guided"
    assert answers[1] is True
    assert answers[3] == "direct"
    assert answers[4] is False
    assert answers[5] == "direct"  # the refused write left the stored choice alone
    assert answers[6] == "guided"  # another person still starts Guided


def test_account_settings_sets_how_sage_works_and_the_composer_does_not():
    drawer = (_JS / "components" / "shell.js").read_text()
    drawer = drawer[drawer.index("SW.SettingsDrawer"):]
    # The label is the pack's assistant name, so the source does not spell Sage.
    assert drawer.count("SW.brand.text('How {assistantName} works')") == 2
    assert "chooseHow" in drawer
    assert "save('howSageWorks', value)" in drawer
    assert "{ label: 'Guided', value: 'guided' }" in drawer
    assert "{ label: 'Direct', value: 'direct' }" in drawer

    composer = (_JS / "components" / "composer.js").read_text()
    assert "howSageWorks" not in composer
    assert "How Sage works" not in composer


def test_both_posters_send_the_choice_and_leave_the_continue_body_alone():
    store = (_JS / "store.js").read_text()
    assert "function postedTurnBody" in store
    assert "url.indexOf('/turn/continue')" in store
    send = _method(store, "async sendMessage")
    build = _method(store, "async sendBuildPrompt")
    assert "postedTurnBody(" in send
    assert "postedTurnBody(" in build
    # The continue click hands its body through these same posters. The helper, not the click,
    # is what keeps that body inside the route's closed key set.
    assert "howSageWorks" not in _method(store, "async continueWithModel")


def test_a_missing_field_is_guided_and_direct_is_what_the_orchestrator_receives(monkeypatch):
    from fastapi.testclient import TestClient

    from sage.orchestrator import app as appmod

    seen: list[tuple[str, object]] = []

    class Streams:
        def prepare_stream_turn(self, turn_id, **_kwargs):
            return type("Ticket", (), {"id": turn_id, "sequence": 1, "epoch": "e"})(), "running"

        def release_stream_turn(self, _ticket) -> None:
            return None

        def chat_stream(self, *_args, **kwargs):
            seen.append(("chat", kwargs.get("how_sage_works")))
            yield {"type": "done", "ok": True}

        def build_stream(self, *_args, **kwargs):
            seen.append(("build", kwargs.get("how_sage_works")))
            yield {"type": "done", "ok": True}

    monkeypatch.setattr(appmod, "orchestrator", Streams())
    client = TestClient(appmod.control_app)
    client.post("/api/threads/thr_a/chat/stream", json={"prompt": "hi"})
    client.post("/api/threads/thr_a/chat/stream", json={"prompt": "hi", "howSageWorks": "direct"})
    client.post("/api/threads/thr_a/chat/stream", json={"prompt": "hi", "howSageWorks": "bare"})
    client.post("/api/project/build/stream", json={"prompt": "build it"})
    client.post("/api/project/build/stream",
                json={"prompt": "build it", "howSageWorks": "direct"})
    client.post("/api/project/build/stream",
                json={"prompt": "build it", "howSageWorks": "guided"})
    assert seen == [
        ("chat", "guided"),
        ("chat", "direct"),
        ("chat", "guided"),
        ("build", "guided"),
        ("build", "direct"),
        ("build", "guided"),
    ]


def test_the_stream_methods_take_the_choice_and_hand_it_inward():
    """The route's keyword has to land on the turn that will later branch, not stop at the door."""
    src = _SERVICE.read_text()
    assert src.count('how_sage_works: str = "guided"') == 4
    assert "how_sage_works=how_sage_works" in src
    assert 'how_sage_works = "direct" if how_sage_works == "direct" else "guided"' in src
