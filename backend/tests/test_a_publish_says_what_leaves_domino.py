"""A publish says what leaves Domino, and refuses nothing for it (#35).

ADR-0012 answered the question this issue could not be written without: **no** Data Source + LLM
Alias combination is refused at publish. So the whole of the protection is a sentence a person
reads, which is why the SURFACE is this ticket's as well as the sentence — `GET /api/publish-check`
(#26) had no caller anywhere in the repo, and "allowed, with the consequence explained" degrades to
plain "allowed" when nothing explains it.

THREE LINES THAT ARE EASY TO ERASE AND ARE THE WHOLE DESIGN.

**Beside `publish_check`, never inside it.** Telling a vendor-backed Alias from a Domino-hosted one
needs the gateway's Alias listing. `publish_check` promises "local and pure ... no network", which
is why the common case costs the publish flow nothing, so this is a second route asked in parallel
and a slow listing can never hold up warnings that were already on the disk.

**Silence means silence.** A listing that did not arrive renders NOTHING — not a spinner, not "Sage
could not check where your data goes". That is the deliberate opposite of `publish_problems`, which
refuses when it cannot check, and the asymmetry is the point: an unverified credential is a hole, an
unwritten notice is not. The payload still tells the two apart with `checked`, on `publish_check`'s
own tri-state discipline — "checked and clean" and "not checked" must not collapse even when a
creator sees the same silence.

**Nothing here can refuse a publish.** Every case below publishes, including the one where the
sentence fires and the one where Sage could not read the listing at all.

Two things deliberately out of scope, named so they do not read as oversights. A Model API is
deployed inside Domino, so calling one is not egress. And an Alias the app calls but never declared
(#94) is invisible to a read of the manifest, which makes the sentence understate; ADR-0012 records
that rather than this closing it.
"""
from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator
from sage.provision.domino import FakeControlPlane
from sage.resources.bindings import Binding
from sage.resources.pinned_model import agents_block
from sage.resources.provider import FakeResourceProvider, LlmAlias, ResourceUnavailable
from sage.resources.publish_egress import egress_notice
from sage.router.models import ModelCatalog

# Two Aliases that differ in the ONE field that decides this: `endpoint_url`. Only a Domino-hosted
# Alias carries one — 12 of the 14 on cloud-dogfood do not — and there is no vendor field on either
# record, which is why the sentence can say "outside Domino" and can never say who runs it.
SONNET = LlmAlias("al-sonnet", "sonnet", "Claude Sonnet 4.6", None, ["chat"], {})
GPT = LlmAlias("al-gpt", "gpt-5.4", "gpt-5.4", None, ["chat"], {})
QWEN = LlmAlias("al-qwen", "qwen-2-5", "Qwen 2.5 (Domino-hosted)", None, ["chat"], {},
                endpoint_url="https://apps.example.tech/endpoints/308f788c/v1")

STORE = Binding("data_source", "ds-dwh", "Snowflake-Data-Warehouse", "Snowflake-Data-Warehouse",
                "DWH", "MARTS", "FCT_USAGE_DAILY", "SnowflakeConfig")
LEDGER = Binding("data_source", "ds-pg", "billing-postgres", "billing-postgres",
                 None, "public", "invoices", "PostgresConfig")
BOUND_SONNET = Binding("llm_alias", "al-sonnet", "sonnet", "Claude Sonnet 4.6")
BOUND_GPT = Binding("llm_alias", "al-gpt", "gpt-5.4", "gpt-5.4")
BOUND_QWEN = Binding("llm_alias", "al-qwen", "qwen-2-5", "Qwen 2.5 (Domino-hosted)")


# ---- the sentence, and when it is written ------------------------------------------------------


def test_the_join_fires_and_names_the_alias_and_the_store():
    notice = egress_notice([STORE, BOUND_SONNET], [SONNET, QWEN])

    assert notice is not None
    assert "the Data Source Snowflake-Data-Warehouse" in notice
    assert "the LLM Alias Claude Sonnet 4.6" in notice
    assert "outside Domino" in notice


def test_it_names_every_store_and_every_offsite_alias():
    # `publish_guard._names`' shape, on both lists: a creator told about one of the two stores their
    # app reads has been told something true and something incomplete.
    notice = egress_notice([STORE, LEDGER, BOUND_SONNET, BOUND_GPT], [SONNET, GPT])

    assert "the Data Sources Snowflake-Data-Warehouse and billing-postgres" in notice
    assert "the LLM Aliases Claude Sonnet 4.6 and gpt-5.4" in notice
    assert "which run outside Domino" in notice


def test_it_never_names_a_vendor():
    """ADR-0012: "An Alias record carries no vendor field; the only signal is the absence of a
    Domino endpoint behind it."

    Two halves, and the structural one is the one that bites. Scanning the prose only catches a
    vendor somebody hardcoded into the wording; what makes "outside Domino" the honest CEILING
    rather than a wording choice is that there is no field to derive a vendor from. An Alias's
    display name is the administrator's own label and is echoed verbatim, exactly as the
    open-visibility refusal echoes a Data Source's."""
    assert not any(f.name in ("vendor", "provider", "publisher", "host")
                   for f in dataclasses.fields(LlmAlias))

    notice = egress_notice([STORE, BOUND_SONNET, BOUND_GPT], [SONNET, GPT])

    for vendor in ("Anthropic", "OpenAI", "AWS", "Bedrock", "Azure", "Google"):
        assert vendor.lower() not in notice.lower()
    # The whole of what it claims about where the model runs.
    assert "outside Domino" in notice


def test_the_sentence_says_publishing_is_not_what_is_being_refused():
    # The ADR's own line: egress does not CHANGE at publish — the rows already went during the build.
    # What publishing changes is volume and attendedness, which is what the sentence is about.
    notice = egress_notice([STORE, BOUND_SONNET], [SONNET])

    assert "for every viewer" in notice
    assert "This doesn't stop the publish." in notice


# ---- and the four silences ---------------------------------------------------------------------


def test_an_app_whose_every_alias_is_sovereign_says_nothing():
    # The call stays inside the platform, so nothing left it. Firing here would be a false alarm on
    # exactly the configuration Domino sells.
    assert egress_notice([STORE, BOUND_QWEN], [QWEN, SONNET]) is None


def test_one_offsite_alias_among_sovereign_ones_still_fires():
    # The join is per Alias, not "are they all sovereign": an app that keeps a Domino-hosted model
    # for most screens and calls a vendor one on a single button is exactly the case worth a
    # sentence, and only the vendor one is named.
    notice = egress_notice([STORE, BOUND_QWEN, BOUND_GPT], [QWEN, GPT])

    assert "the LLM Alias gpt-5.4" in notice
    assert "Qwen" not in notice


def test_an_alias_with_no_store_bound_says_nothing():
    # Not this decision's subject. Firing on every vendor Alias would fire on nearly every app Sage
    # builds and be tuned out inside a week.
    assert egress_notice([BOUND_SONNET], [SONNET]) is None


def test_a_store_with_no_alias_says_nothing():
    assert egress_notice([STORE], [SONNET]) is None


def test_a_listing_that_did_not_arrive_says_nothing():
    # The criterion, and the asymmetry with `publish_problems`, which refuses on `sources=None`. An
    # unverified credential is a hole; an unwritten notice is not.
    assert egress_notice([STORE, BOUND_SONNET], None) is None


def test_a_bound_alias_the_listing_never_offered_says_nothing():
    # There is no record left to carry an `endpoint_url`, so its hosting is UNKNOWN rather than
    # vendor — `endpoint_status` draws the same line, and `stale_bindings` is what reports a Binding
    # that has gone. Guessing "vendor" here would put a sentence in front of a creator about an
    # Alias that is not there.
    assert egress_notice([STORE, BOUND_SONNET], [QWEN]) is None


def test_an_alias_re_registered_under_a_new_id_is_still_matched():
    # `publish_guard._match`'s rule, for the same reason: the manifest keeps id and name because
    # they are authoritative for different things, and matching on id alone would go quiet about a
    # model that is really being called.
    moved = replace(SONNET, id="al-sonnet-v2")

    assert egress_notice([STORE, BOUND_SONNET], [moved]) is not None


# ---- the read beside publish_check --------------------------------------------------------------


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "app.sh").write_text("#!/bin/bash\nexec npx vite preview\n")
    return t


@dataclasses.dataclass
class _CountingResources(FakeResourceProvider):
    """Every Alias listing, counted, so "no join costs no call" is a claim about the wire rather
    than about a branch somebody read. `fails` turns the listing into the failure the creator must
    never be told about."""

    alias_calls: int = 0
    fails: bool = False

    def list_llm_aliases(self) -> list[LlmAlias]:
        self.alias_calls += 1
        if self.fails:
            raise ResourceUnavailable("the gateway is having a bad minute")
        return list(self.aliases)


def _orch(tmp: Path, cp: FakeControlPlane | None = None) -> Orchestrator:
    resources = _CountingResources(aliases=[SONNET, GPT, QWEN])
    orch = Orchestrator(
        workspace_dir=tmp / "mnt" / "code",
        template=_template(tmp),
        gateway=FakeGatewayClient(),
        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"),
        project_id="Sage",
        resources=resources,
        control_plane=cp or FakeControlPlane(),
        domino_project_id="proj-1",
        domino_project_name="Sales dashboard",
    )
    orch.project(start_preview=False)
    return orch


def _bind(orch: Orchestrator, *bindings: Binding) -> None:
    """Bindings written straight to the manifest, because what a Binding COSTS to create is another
    ticket's business and this one is about what is read back off the disk."""
    root = orch.project(start_preview=False).workspace.path
    (root / ".sage").mkdir(exist_ok=True)
    (root / ".sage" / "bindings.json").write_text(json.dumps([b.to_dict() for b in bindings]))


def test_the_orchestrator_read_answers_with_the_sentence(tmp_path: Path):
    orch = _orch(tmp_path)
    _bind(orch, STORE, BOUND_SONNET)

    out = orch.publish_egress()

    assert out["checked"] is True
    assert "Claude Sonnet 4.6" in out["notice"]


@pytest.mark.parametrize("held", [(BOUND_SONNET,), (STORE,), ()],
                         ids=["alias only", "store only", "nothing bound"])
def test_an_app_with_no_join_costs_no_call_at_all(tmp_path: Path, held: tuple[Binding, ...]):
    # The reason the join is checked on the manifest first. Most apps hold one kind of Binding or
    # none, and they must not pay a gateway round trip for a sentence they cannot earn.
    #
    # BOTH one-sided cases, not just the one that reads more naturally: a store with no model is as
    # common as a model with no store, and half a guard here is a listing fetched for nothing on
    # every publish of an app that only ever reads a table.
    orch = _orch(tmp_path)
    _bind(orch, *held)

    assert orch.publish_egress() == {"checked": True, "notice": None}
    assert orch._resources.alias_calls == 0


def test_a_failed_listing_is_reported_as_unchecked_and_shows_nothing(tmp_path: Path):
    # The tri-state, kept rather than collapsed. A creator sees the same silence either way; the
    # payload still knows the difference, which is what stops "could not check" being logged and
    # read as "nothing to say".
    orch = _orch(tmp_path)
    _bind(orch, STORE, BOUND_SONNET)
    orch._resources.fails = True

    assert orch.publish_egress() == {"checked": False, "notice": None}


def test_the_query_check_beside_it_still_reaches_nothing(tmp_path: Path):
    # `publish_check`'s "local and pure ... no network" is load-bearing and is what kept this out of
    # it. Asked with the join in place, so a listing folded in later would be caught here.
    orch = _orch(tmp_path)
    _bind(orch, STORE, BOUND_SONNET)

    orch.publish_check()

    assert orch._resources.alias_calls == 0


def test_the_read_changes_nothing(tmp_path: Path):
    orch = _orch(tmp_path)
    _bind(orch, STORE, BOUND_SONNET)
    root = orch.project(start_preview=False).workspace.path
    before = sorted((p.name, p.read_bytes()) for p in (root / ".sage").iterdir() if p.is_file())

    orch.publish_egress()

    assert sorted((p.name, p.read_bytes()) for p in (root / ".sage").iterdir() if p.is_file()) == before


# ---- and nothing it can refuse ------------------------------------------------------------------


@pytest.mark.parametrize("listing_fails", [False, True])
def test_publish_proceeds_whatever_this_says(tmp_path: Path, listing_fails: bool):
    """ADR-0012's decision, asserted rather than assumed. The read is taken FIRST so that this is
    the case it claims to be — "the sentence fires and the publish still goes", and "Sage could not
    check and the publish still goes" — rather than an app with nothing to say publishing."""
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    _bind(orch, STORE, BOUND_SONNET)
    orch._resources.fails = listing_fails

    said = orch.publish_egress()
    assert said["checked"] is not listing_fails
    assert (said["notice"] is None) is listing_fails

    assert orch.publish()["published"] is True
    assert cp.published


def test_publish_never_asks_this_question(tmp_path: Path):
    # `POST /api/publish` neither calls the read nor cares whether the UI did. A publish that took
    # the listing on itself would be one gateway hiccup away from a refusal ADR-0012 forbids.
    orch = _orch(tmp_path)
    _bind(orch, STORE, BOUND_SONNET)

    orch.publish()

    assert orch._resources.alias_calls == 0


# ---- over the route -----------------------------------------------------------------------------


def test_the_route_answers_with_the_sentence(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    orch = _orch(tmp_path)
    _bind(orch, STORE, BOUND_SONNET)
    monkeypatch.setattr(appmod, "orchestrator", orch)

    r = TestClient(appmod.control_app).get("/api/publish-egress")

    assert r.status_code == 200
    assert r.json()["checked"] is True
    assert "Claude Sonnet 4.6" in r.json()["notice"]


def test_the_route_is_a_second_route_and_not_a_field_on_the_first(tmp_path: Path, monkeypatch):
    # Two routes, so the UI can fire them together. A `notice` spliced into `publish-check` would
    # have made every publish wait on the gateway for a sentence most apps do not earn.
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    orch = _orch(tmp_path)
    _bind(orch, STORE, BOUND_SONNET)
    monkeypatch.setattr(appmod, "orchestrator", orch)
    client = TestClient(appmod.control_app)

    check = client.get("/api/publish-check").json()

    assert set(check) == {"checked", "queries"}


def test_the_route_fails_loudly_rather_than_reporting_no_egress(tmp_path: Path, monkeypatch):
    # A 502 is what the UI treats as "nothing to say", so this is only about the log and the shape:
    # a read that raised must not come back as `notice: null` and be read as a checked, clean app.
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    orch = _orch(tmp_path)
    monkeypatch.setattr(orch, "publish_egress", _raise)
    monkeypatch.setattr(appmod, "orchestrator", orch)

    r = TestClient(appmod.control_app, raise_server_exceptions=False).get("/api/publish-egress")

    assert r.status_code == 502
    assert "notice" not in r.json()


def _raise() -> dict:
    raise RuntimeError("no")


# ---- what the agent is told ---------------------------------------------------------------------


def test_the_model_block_says_where_the_data_goes_on_the_join():
    block = agents_block([BOUND_SONNET], [STORE])

    assert "### Where this app's data goes" in block
    assert "wherever the model runs" in block


def test_the_model_block_says_nothing_about_it_without_a_store():
    # An app with a model and no store has nothing to send it, and every turn pays for this
    # paragraph in context.
    block = agents_block([BOUND_SONNET], [])

    assert "Where this app's data goes" not in block


def test_the_model_block_says_nothing_at_all_without_a_model():
    assert agents_block([], [STORE]) == ""


def test_it_is_written_as_a_consequence_and_not_as_a_rule():
    """ADR-0012 left no rule to state, so a prohibition here would be one the agent can watch the
    app violate on every turn — which is how an agent learns to route around a block, and the
    failure the ADR rejected a blanket refusal over. Conditional for a second reason: this file is a
    function of the disk, and the disk cannot say whether an Alias is Domino-hosted."""
    note = agents_block([BOUND_SONNET], [STORE]).split("### Where this app's data goes")[1]

    for prohibition in ("Do not", "do not", "must not", "never"):
        assert prohibition not in note
    assert "An LLM Alias hosted on Domino answers inside the platform" in note
    assert "Both are allowed" in note


def test_the_block_is_handed_no_listing_to_go_stale():
    """A `sovereign` flag on the Binding record would make this paragraph exact and would rot: an
    Alias's hosting is a live fact, and `.sage/bindings.json` is committed to the creator's repo, so
    it would go stale in the one place nobody re-reads. So the writer takes Bindings and nothing
    else, and the conditional wording is what that precision buys."""
    import inspect

    assert list(inspect.signature(agents_block).parameters) == ["aliases", "sources"]
    assert not any(f.name == "sovereign" for f in dataclasses.fields(Binding))


# ---- the surface, which is where the decision becomes real --------------------------------------

_HARNESS = Path(__file__).resolve().parent / "js" / "build_header_harness.mjs"
_WORKBENCH = Path(__file__).resolve().parents[1] / "sage" / "workbench"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)

# The sentence the server would send, as prose the harness has no way to have built itself.
NOTICE = ("This app reads the Data Source Snowflake-Data-Warehouse and calls the LLM Alias Claude "
          "Sonnet 4.6, which runs outside Domino. Anything the app sends that model leaves Domino "
          "— once this is published, for every viewer, and with nobody watching. This doesn't stop "
          "the publish.")
REFUSED_QUERY = ("The app asks for the query revenue, whose statement uses :since and whose "
                 "declaration does not.")


def _confirm(select: str = "app_b", **step) -> dict:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps([{"publish": "thr_many", "select": select, "confirm": True, **step}]),
        check=False, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])[-1]


@needs_node
def test_the_notice_renders_the_query_warnings_that_had_no_reader():
    # Criterion 1. `GET /api/publish-check` shipped in #26 with a docstring saying "a warning the UI
    # shows", and no UI showed it. This is that UI, and the sentence it renders is the app's own.
    step = _confirm(queries=[REFUSED_QUERY])

    assert "GET /publish-check" in step["calls"]
    assert REFUSED_QUERY in step["confirm"]["content"]
    assert step["confirm"]["alerts"] == ["warning"]


@needs_node
def test_the_notice_renders_the_egress_sentence():
    step = _confirm(notice=NOTICE)

    assert "GET /publish-egress" in step["calls"]
    assert "outside Domino" in step["confirm"]["content"]
    # Not a warning. Nothing is broken — this is what publishing means, said once so the choice is
    # made knowing, which is the whole of what ADR-0012 decided to do instead of refusing.
    assert step["confirm"]["alerts"] == ["info"]


@needs_node
def test_both_reads_are_asked_and_neither_is_awaited_before_the_confirm_opens():
    # Criterion 6's other half. A control that waits on the gateway before anything appears reads as
    # a control that did nothing, so the modal opens on its own two paragraphs and the notice
    # arrives into it.
    step = _confirm(queries=[REFUSED_QUERY], notice=NOTICE)

    assert "GET /publish-check" in step["calls"] and "GET /publish-egress" in step["calls"]
    assert "Only this app goes out." in step["confirm"]["openedWith"]
    assert REFUSED_QUERY not in step["confirm"]["openedWith"]
    assert "outside Domino" not in step["confirm"]["openedWith"]
    # Both, once they land, and the broken query above the consequence: one is a defect to go and
    # fix, the other is a choice to accept.
    assert step["confirm"]["alerts"] == ["warning", "info"]


@needs_node
def test_a_hanging_gateway_does_not_hold_up_the_query_warnings():
    """The whole reason there are two routes rather than one field on the first. The query check is
    local disk and pure Python; the egress read may reach the gateway, be slow, or never answer.
    Gating the render on both — one `Promise.all` is all it takes — puts warnings that were already
    on the disk behind a listing that may never arrive, which is the coupling the split exists to
    prevent."""
    step = _confirm(queries=[REFUSED_QUERY], notice=NOTICE, egressHangs=True)

    assert REFUSED_QUERY in step["confirm"]["content"]
    assert step["confirm"]["alerts"] == ["warning"]
    assert "outside Domino" not in step["confirm"]["content"]


@needs_node
def test_a_hanging_query_check_does_not_hold_up_the_egress_sentence():
    # The same independence in the other direction, so neither read is quietly the other's gate.
    step = _confirm(queries=[REFUSED_QUERY], notice=NOTICE, checkHangs=True)

    assert "outside Domino" in step["confirm"]["content"]
    assert step["confirm"]["alerts"] == ["info"]
    assert REFUSED_QUERY not in step["confirm"]["content"]


@needs_node
def test_a_read_that_failed_leaves_the_confirm_exactly_as_it_opened():
    # Criterion 5, at the surface. No "Sage could not check where your data goes", no spinner left
    # behind — the deliberate asymmetry with `publish_problems`, which refuses when it cannot check.
    step = _confirm(checkFails=True, egressFails=True)

    assert step["confirm"]["content"] == step["confirm"]["openedWith"]
    assert step["confirm"]["alerts"] == []
    assert step["acted"] == "ok"


@needs_node
def test_a_checked_and_quiet_app_adds_nothing_to_the_confirm():
    # The common case: no broken query, no store-and-vendor join. It reads exactly as it did before
    # this shipped, which is what keeps the notice worth reading when it does appear.
    step = _confirm()

    assert step["confirm"]["alerts"] == []
    assert step["confirm"]["content"] == step["confirm"]["openedWith"]


@needs_node
def test_publish_stays_the_primary_action_and_still_publishes():
    # Criterion 6. Nothing here is a refusal: the confirm's own button is unmoved and unstyled by
    # any of it, and pressing it ships the app.
    step = _confirm("app_b", queries=[REFUSED_QUERY], notice=NOTICE)

    assert step["confirm"]["okText"] == "Publish"
    assert step["confirm"]["danger"] is False
    assert "POST /publish" in step["calls"]
    assert [a["id"] for a in step["apps"] if a["published"]] == ["app_a", "app_b", "app_d"]
