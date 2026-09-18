"""A permission that resolves to `ask` is a four-minute silence, so the boot refuses it (#407).

Sage runs OpenCode headless. An `ask` has nobody to answer it, so the tool call never returns and
the turn dies on `_CHAT_TOOL_QUIET_TIMEOUT_S` — 240 seconds in which the person is shown
"Thinking…" and then a message about a step that did not finish. Nothing names the permission.

The half of this that is a fix is three keys in `opencode.json`. The half that keeps working is
below: the NEXT undeclared permission has to arrive as a refused boot rather than as that silence.

Nothing here starts OpenCode. `_effective_permission` is a pure function over a rule list, and the
rule lists are the shape `GET /agent` really returns — measured against opencode-ai 1.18.4 on
2026-09-18, whose resolver is `findLast(rule matches)` with `ask` when nothing matches.
"""
from __future__ import annotations

import pytest

import sage.orchestrator.app as appmod


def _agent(name: str, rules: list[tuple[str, str, str]]) -> dict:
    return {"name": name,
            "permission": [{"permission": p, "pattern": q, "action": a} for p, q, a in rules]}


# ---- the resolver -----------------------------------------------------------------------------
# These pin the two properties a wrong guard would get wrong in opposite directions: reading the
# FIRST match would report the fixed config as still broken, and ignoring the no-match fall-through
# would miss a permission nothing mentions at all.


def test_the_last_matching_rule_wins_not_the_first():
    # OpenCode appends the config's rules after its own defaults, so the whole fix in opencode.json
    # depends on later beating earlier. Read first-match-wins and `external_directory: deny` looks
    # like it changed nothing.
    rules = _agent("x", [("external_directory", "*", "ask"),
                         ("external_directory", "*", "deny")])["permission"]
    assert appmod._effective_permission("external_directory", "*", rules) == "deny"


def test_a_permission_no_rule_mentions_falls_through_to_ask():
    rules = _agent("x", [("read", "*", "allow")])["permission"]
    assert appmod._effective_permission("doom_loop", "*", rules) == "ask"


def test_a_wildcard_deny_does_not_swallow_a_later_specific_allow():
    # The order OpenCode itself relies on: it re-appends its tool-output directory AFTER the config,
    # so the agent can still read back its own truncated output under a blanket deny.
    rules = _agent("x", [("external_directory", "*", "deny"),
                         ("external_directory", "/var/tool-output/*", "allow")])["permission"]
    assert appmod._effective_permission("external_directory", "/var/tool-output/x", rules) == "allow"
    assert appmod._effective_permission("external_directory", "/etc/passwd", rules) == "deny"


# ---- the guard --------------------------------------------------------------------------------


def test_a_fully_declared_config_boots():
    agents = [_agent("sage-chat", [("*", "*", "allow"),
                                   ("external_directory", "*", "ask"),
                                   ("external_directory", "*", "deny")])]
    assert appmod._unanswerable_permissions(agents) == []


def test_the_guard_names_the_agent_the_permission_and_the_pattern():
    # A guard that says only "a permission asks" sends the maintainer back to the same search this
    # ticket cost four minutes a turn to do by hand.
    agents = [_agent("sage-chat", [("*", "*", "allow"), ("external_directory", "*", "ask")])]
    found = appmod._unanswerable_permissions(agents)
    assert found == [{"agent": "sage-chat", "permission": "external_directory", "pattern": "*"}]


def test_the_boot_refuses_when_a_permission_resolves_to_ask(monkeypatch, caplog):
    # THE PLANT. `sage-chat` below is otherwise a correct agent — the `*: allow` and the declared
    # `external_directory: deny` are the real shipped shape — and one planted rule that nothing
    # overrides is the whole difference. If this ever passes, the guard has stopped firing.
    planted = [_agent("sage-chat", [("*", "*", "allow"),
                                    ("external_directory", "*", "deny"),
                                    ("doom_loop", "*", "ask")])]
    monkeypatch.setattr(appmod, "_resolved_agent_permissions", lambda: planted)
    stopped: list[int] = []
    monkeypatch.setattr(appmod, "_stop_this_process", lambda: stopped.append(1))
    monkeypatch.setattr(appmod, "PREFLIGHT_PERMISSIONS", dict(appmod.PREFLIGHT_PERMISSIONS))

    with caplog.at_level("ERROR", logger="sage.orchestrator"):
        appmod._run_permission_preflight()

    assert stopped == [1], "the boot was allowed to continue past an unanswerable permission"
    assert appmod.PREFLIGHT_PERMISSIONS["state"] == "unanswerable"
    assert "doom_loop" in caplog.text and "sage-chat" in caplog.text


def test_the_boot_continues_when_nothing_asks(monkeypatch, caplog):
    # The other half of the plant: the same guard, on the same site, with the planted rule removed.
    clean = [_agent("sage-chat", [("*", "*", "allow"),
                                  ("external_directory", "*", "deny"),
                                  ("doom_loop", "*", "deny")])]
    monkeypatch.setattr(appmod, "_resolved_agent_permissions", lambda: clean)
    stopped: list[int] = []
    monkeypatch.setattr(appmod, "_stop_this_process", lambda: stopped.append(1))
    monkeypatch.setattr(appmod, "PREFLIGHT_PERMISSIONS", dict(appmod.PREFLIGHT_PERMISSIONS))

    appmod._run_permission_preflight()

    assert stopped == []
    assert appmod.PREFLIGHT_PERMISSIONS["state"] == "ok"


def test_a_check_that_could_not_run_does_not_stop_the_boot(monkeypatch):
    # "Could not ask OpenCode" is not "OpenCode said ask". Taking the builder down because a query
    # failed would turn every slow start into an outage, which is the failure the slot preflight
    # beside this one is deliberately written to avoid.
    def _boom():
        raise RuntimeError("opencode is not up")

    monkeypatch.setattr(appmod, "_resolved_agent_permissions", _boom)
    stopped: list[int] = []
    monkeypatch.setattr(appmod, "_stop_this_process", lambda: stopped.append(1))
    monkeypatch.setattr(appmod, "PREFLIGHT_PERMISSIONS", dict(appmod.PREFLIGHT_PERMISSIONS))

    appmod._run_permission_preflight()

    assert stopped == []
    assert appmod.PREFLIGHT_PERMISSIONS["state"] == "unreachable"


# ---- the shipped file -------------------------------------------------------------------------


def test_opencode_json_declares_the_permissions_that_would_otherwise_ask():
    # Against the checked-in file, because the fix is three keys in it and a rename or a lost merge
    # is exactly how they would go missing. Values, not just presence: `ask` here is the bug.
    import json
    from pathlib import Path

    cfg = json.loads((Path(__file__).resolve().parents[2] / "opencode.json").read_text())
    permission = cfg["permission"]
    assert permission["external_directory"] == "deny"
    assert permission["doom_loop"] == "deny"
    # `.env.example` is listed last on purpose: last match wins, so denying `*.env.*` before it
    # would otherwise take an ordinary example file down with the real secrets.
    assert list(permission["read"].items()) == [
        ("*.env", "deny"), ("*.env.*", "deny"), ("*.env.example", "allow")]


@pytest.mark.parametrize("agent", ["sage-chat", "sage-ask", "sage-plan", "sage-architect",
                                   "sage-implement"])
def test_no_agent_block_reintroduces_an_ask(agent):
    import json
    from pathlib import Path

    cfg = json.loads((Path(__file__).resolve().parents[2] / "opencode.json").read_text())
    block = cfg["agent"][agent].get("permission", {})
    flat = [v for v in block.values() if isinstance(v, str)]
    flat += [v for d in block.values() if isinstance(d, dict) for v in d.values()]
    assert "ask" not in flat
