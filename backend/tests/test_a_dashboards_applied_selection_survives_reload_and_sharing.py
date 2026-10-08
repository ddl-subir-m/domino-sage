"""A dashboard's applied selection survives a reload and travels in a copied link (#701).

Symptom: a generated dashboard's month, region and screen lived only in React state, so a reload
lost them and a person could not send a link that reopened the same view. `useViewState` keeps the
explicitly shareable, applied fields in the page's own fragment (`#v=1&...`), validates everything
it reads back, and leaves `#/sage/` — where the keys panel opens — to Sage.

Each stack's helper runs from the template's own file through a minimal hooks runtime, in a fake
page whose history API fires nothing and whose navigations fire popstate and hashchange. Every Node
process here is `subprocess.run`, which waits for it to exit. The hook schedules no timers.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).parent / "js" / "view_state_harness.mjs"
TEMPLATES = pytest.mark.parametrize("template", ["fastapi-antd", "react-vite"])

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")

VIEW = {
    "screen": {"type": "enum", "values": ["revenue", "orders"], "default": "revenue", "shareable": True},
    "month": {"type": "string", "pattern": r"\d{4}-(0[1-9]|1[0-2])", "default": "", "shareable": True},
    "region": {"type": "enum", "values": ["__all__", "EMEA", "AMER", "Zürich"], "default": "__all__",
               "shareable": True},
    "limit": {"type": "integer", "min": 1, "max": 500, "default": 50, "shareable": True},
    "compact": {"type": "boolean", "default": False, "shareable": True},
    "search": {"type": "string", "default": ""},
}
DEFAULTS = {"screen": "revenue", "month": "", "region": "__all__", "limit": 50, "compact": False,
            "search": ""}
PUBLISHED = {"pathname": "/apps/7f3c2a/", "search": "?owner=ops"}


def _drive(template: str, steps: list, *, hash: str = "", schema=None, keys=None,
           history_state=None) -> dict:
    payload = {"template": template, "schema": VIEW if schema is None else schema, "steps": steps,
               "location": {**PUBLISHED, "hash": hash}, "historyState": history_state}
    if keys is not None:
        payload["keys"] = keys
    out = subprocess.run(["node", str(HARNESS)], input=json.dumps(payload),
                         capture_output=True, text=True, timeout=30, check=True)
    return json.loads(out.stdout)


def _state(**changed) -> dict:
    return {**DEFAULTS, **changed}


# ---- reload and copied links ---------------------------------------------------------------------


@TEMPLATES
def test_a_reload_restores_the_applied_selection(template: str):
    got = _drive(template, ["mount"], hash="#v=1&screen=orders&month=2026-03&region=EMEA")
    assert got["snapshots"][0]["state"] == _state(screen="orders", month="2026-03", region="EMEA")
    assert got["writes"] == [], "loading never rewrites the address"


@TEMPLATES
def test_a_copied_link_keeps_the_published_prefix_query_and_history_state(template: str):
    got = _drive(template, ["mount", {"patch": [{"month": "2026-03", "region": "EMEA"}]}],
                 history_state={"from": "the app"})
    assert got["snapshots"][-1]["url"] == "/apps/7f3c2a/?owner=ops#v=1&month=2026-03&region=EMEA"
    assert got["writes"][-1]["state"] == {"from": "the app"}
    reopened = _drive(template, ["mount"], hash="#" + got["snapshots"][-1]["url"].split("#", 1)[1])
    assert reopened["snapshots"][0]["state"] == _state(month="2026-03", region="EMEA")


@TEMPLATES
def test_keys_follow_the_schema_order_and_defaults_are_left_out(template: str):
    got = _drive(template, ["mount", {"patch": [{"compact": True, "limit": 50, "region": "AMER"}]},
                            {"patch": [{"region": "__all__", "compact": False}]}])
    assert got["snapshots"][1]["url"].endswith("#v=1&region=AMER&compact=true")
    assert got["snapshots"][2]["url"] == "/apps/7f3c2a/?owner=ops", "all defaults: no fragment at all"


@TEMPLATES
def test_unicode_values_are_encoded_and_read_back(template: str):
    got = _drive(template, ["mount", {"patch": [{"region": "Zürich"}]}])
    assert got["snapshots"][1]["url"].endswith("#v=1&region=Z%C3%BCrich")
    back = _drive(template, ["mount"], hash="#v=1&region=Z%C3%BCrich")
    assert back["snapshots"][0]["state"]["region"] == "Zürich"


# ---- history -------------------------------------------------------------------------------------


@TEMPLATES
def test_a_filter_change_replaces_and_navigation_pushes(template: str):
    got = _drive(template, ["mount", {"patch": [{"month": "2026-03"}]},
                            {"patch": [{"screen": "orders"}, {"history": "push"}]}])
    assert [w["method"] for w in got["writes"]] == ["replaceState", "pushState"]


@TEMPLATES
def test_back_and_forward_update_the_view_without_writing(template: str):
    got = _drive(template, ["mount", {"patch": [{"month": "2026-03"}]},
                            {"patch": [{"screen": "orders"}, {"history": "push"}]}, "back", "forward"])
    back, forward = got["snapshots"][3:]
    assert back["state"] == _state(month="2026-03")
    assert forward["state"] == _state(month="2026-03", screen="orders")
    assert len(got["writes"]) == 2, "a navigation is read, never written back"


@TEMPLATES
def test_a_hand_edited_hash_updates_the_view_without_a_loop(template: str):
    got = _drive(template, ["mount", {"hash": "#v=1&region=EMEA"}, {"hash": "#v=1&region=EMEA&limit=50"}])
    edited, same = got["snapshots"][1:]
    assert edited["state"] == _state(region="EMEA")
    assert same["renders"] == edited["renders"], "the same values do not render again"
    assert got["writes"] == []


@TEMPLATES
def test_a_filter_change_keeps_the_screen_and_switching_screens_keeps_each_selection(template: str):
    got = _drive(template, ["mount", {"patch": [{"screen": "orders"}, {"history": "push"}]},
                            {"patch": [{"region": "EMEA"}]},
                            {"patch": [{"screen": "revenue"}, {"history": "push"}]}])
    assert got["snapshots"][2]["state"] == _state(screen="orders", region="EMEA")
    assert got["snapshots"][3]["state"] == _state(region="EMEA")


# ---- validation ----------------------------------------------------------------------------------


@TEMPLATES
@pytest.mark.parametrize("hash", [
    "#v=1&region=Mars", "#v=1&region=EMEA&region=AMER", "#v=1&month=2026-13", "#v=1&month=2026-03x",
    "#v=1&limit=0", "#v=1&limit=501", "#v=1&limit=1e2", "#v=1&compact=yes", "#v=2&region=EMEA",
    "#v=1&v=1&region=EMEA", "#region=EMEA", "#v=1&search=secret",
], ids=["unknown-enum", "repeated", "bad-month", "month-suffix", "below-min", "above-max",
        "not-an-integer", "not-a-boolean", "other-version", "repeated-version", "no-version",
        "not-shareable"])
def test_an_invalid_repeated_or_foreign_value_falls_back_to_the_default(template: str, hash: str):
    got = _drive(template, ["mount"], hash=hash)
    assert got["snapshots"][0]["state"] == DEFAULTS


@TEMPLATES
def test_unknown_keys_are_ignored_and_valid_neighbours_kept(template: str):
    got = _drive(template, ["mount"], hash="#v=1&evil=%27%3B%20drop&region=EMEA&limit=200")
    assert got["snapshots"][0]["state"] == _state(region="EMEA", limit=200)


@TEMPLATES
def test_a_patch_with_an_invalid_value_or_unknown_key_changes_nothing(template: str):
    got = _drive(template, ["mount", {"patch": [{"region": "Mars", "limit": 9000, "evil": 1,
                                                 "month": "March"}]}])
    assert got["snapshots"][1]["state"] == DEFAULTS
    assert got["writes"] == []


# ---- privacy -------------------------------------------------------------------------------------


@TEMPLATES
def test_draft_search_text_stays_out_of_the_url(template: str):
    got = _drive(template, ["mount", {"patch": [{"search": "acme payroll", "region": "EMEA"}]}])
    assert got["snapshots"][1]["state"] == _state(search="acme payroll", region="EMEA")
    assert "acme" not in got["snapshots"][1]["url"]
    assert all("acme" not in w["url"] for w in got["writes"])


@TEMPLATES
def test_free_text_cannot_be_declared_shareable(template: str):
    schema = {"search": {"type": "string", "default": "", "shareable": True}}
    got = _drive(template, ["mount"], schema=schema)
    assert got["error"] and "pattern" in got["error"]


# ---- the reserved namespace and the frame ---------------------------------------------------------


@TEMPLATES
def test_a_reserved_route_loads_defaults_without_rewriting_it(template: str):
    got = _drive(template, ["mount"], hash="#/sage/keys")
    assert got["snapshots"][0]["state"] == DEFAULTS
    assert got["snapshots"][0]["url"].endswith("#/sage/keys")
    assert got["writes"] == []


@TEMPLATES
def test_opening_a_reserved_route_keeps_the_last_applied_view(template: str):
    got = _drive(template, ["mount", {"patch": [{"region": "EMEA"}]}, {"hash": "#/sage/keys"},
                            {"patch": [{"month": "2026-03"}]}])
    opened, left = got["snapshots"][2:]
    assert opened["state"] == _state(region="EMEA")
    assert opened["url"].endswith("#/sage/keys")
    assert left["url"].endswith("#v=1&month=2026-03&region=EMEA"), "a later filter may leave it"


@TEMPLATES
def test_the_hook_never_reads_the_top_frame_and_leaves_no_listener(template: str):
    got = _drive(template, ["mount", {"patch": [{"region": "EMEA"}]}, {"hash": "#v=1"}, "back",
                            "unmount"])
    assert got["topReads"] == 0
    assert got["listeners"] == 0


KEYS_ANSWER = [{"name": "CRM_TOKEN", "set": False}]


def test_the_key_settings_deep_link_still_opens():
    got = _drive("fastapi-antd", ["mount"], hash="#/sage/keys", keys=KEYS_ANSWER)
    assert got["dialogs"] == 1
    assert got["snapshots"][0]["url"].endswith("#/sage/keys")
    assert got["writes"] == []


def test_the_keys_link_opens_over_an_applied_view():
    got = _drive("fastapi-antd", ["mount", {"patch": [{"region": "EMEA"}]}, {"hash": "#/sage/keys"}],
                 keys=KEYS_ANSWER)
    assert got["dialogs"] == 1
    assert got["snapshots"][-1]["state"] == _state(region="EMEA")
