"""**Read again** says which of the failures it was, the way the read beside it does (#405).

#399 taught `_query` to separate three populations and say a different, true sentence for each.
`live_read_again` was not changed, so the two controls on one card disagreed: the agent's read named
a credential Domino would not open, and the button under it said "The store did not answer" about a
store that was never asked.

The fix is a narrow catch by TYPE, not a wider one by case. `ResourceUnavailable` is the population
that has already been through `_store_failure` — branded, scrubbed, and correct about which of the
three things went wrong — so its message travels unchanged, exactly as `_statement` already lets it
travel. Everything left has NOT been through that classifier, so it goes through `readable_error`
before anyone sees it; that scrub is the guard these tests plant against, because it is the one most
easily written as a comment.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from sage.assets.provider import FakeAssetProvider
from sage.liveread import run
from sage.orchestrator.service import Orchestrator
from sage.resources.provider import ResourceUnavailable, ScopeIncomplete
from sage.router.models import ModelCatalog

CARD = {"kind": "table", "binding": "bnd_1", "table": "GONG__CALLS",
        "database": "DWH", "schema": "MARTS", "limit": 2}


@dataclass
class FakeRows:
    columns: list
    rows: list


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "AGENTS.md").write_text("# Building apps in this workspace\n\nTemplate body here.\n")
    return t


def _pressing(tmp_path: Path, monkeypatch, sample_rows) -> Orchestrator:
    """An Orchestrator whose **Read again** reaches a store that fails in one stated way.

    The Turn is handed in rather than built from a live Project, so the press runs the real
    `read_again` -> `_scoped` -> `_table_rows` road and fails at the one place a store can fail.
    """
    orch = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code", template=_template(tmp_path),
        gateway=object(),
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage", assets=FakeAssetProvider(root=tmp_path / "mounts"),
    )
    turn = run.Turn(
        thread_id="thr_a", examples_dir=tmp_path / "examples" / "thr_a",
        bound={"datasource": ("DWH",)},
        source_for=lambda n: object() if n == "DWH" else None,
        binding_for={("datasource", "DWH"): "bnd_1"},
        sample_rows=sample_rows,
    )
    monkeypatch.setattr(orch, "_live_read_turn_for", lambda thread_id: turn)
    return orch


# ---- criterion 1: the two controls agree ---------------------------------------------------------


def test_a_credential_fault_reads_what_the_agents_read_reads(tmp_path: Path, monkeypatch):
    """The contradiction the ticket is named for, on the same source in the same minute.

    `_store_failure` built this sentence to be shown — its docstring says the message reaches the
    user unchanged — so re-branding it here was the whole defect.
    """
    said = ("Sage cannot open DWH: no credentials for user u-9. Retrying will not help — the Data "
            "Source's credentials or connection settings need fixing in Domino.")

    def failing(source, database, schema, table, limit):
        raise ResourceUnavailable(said)

    answer = _pressing(tmp_path, monkeypatch, failing).live_read_again("thr_a", CARD)

    assert answer["refused"] == said
    assert "did not answer" not in answer["refused"]


def test_a_store_that_answered_is_not_reported_as_one_that_did_not(tmp_path: Path, monkeypatch):
    """The other two populations #399 separated travel too, or this catch is #399 written twice."""
    def failing(source, database, schema, table, limit):
        raise ResourceUnavailable("DWH did not answer: SQL compilation error: object does not exist")

    answer = _pressing(tmp_path, monkeypatch, failing).live_read_again("thr_a", CARD)

    assert "SQL compilation error" in answer["refused"]


# ---- criterion 2: a missing level names the level ------------------------------------------------


def test_a_missing_level_behind_the_button_names_the_level(tmp_path: Path, monkeypatch):
    """#404's sentence survives this door.

    It already did — `_table_rows` catches `ScopeIncomplete` by its own type and hands back a
    refusal, so it never reached the swallow. Pinned here at the door the ticket is about, because
    the ticket recorded it as reaching the swallow and nothing else asserts that it does not.
    """
    said = "Sage does not know which database and schema GONG__CALLS is in."

    def failing(source, database, schema, table, limit):
        raise ScopeIncomplete(said)

    answer = _pressing(tmp_path, monkeypatch, failing).live_read_again("thr_a", CARD)

    assert answer["refused"] == said


# ---- criterion 3: what is left is scrubbed before it is shown ------------------------------------


def test_an_unclassified_failure_carries_no_secret_shaped_run(tmp_path: Path, monkeypatch):
    """The plant. A driver holding a client prints its api_key in plaintext, and this arm is the
    only thing between that repr and the page."""
    def failing(source, database, schema, table, limit):
        raise RuntimeError("DataSourceClient(api_key=" + "a" * 64 + ") could not be built")

    answer = _pressing(tmp_path, monkeypatch, failing).live_read_again("thr_a", CARD)

    assert "a" * 64 not in answer["refused"]
    assert "[redacted]" in answer["refused"]


def test_an_unclassified_failure_carries_no_peer_address(tmp_path: Path, monkeypatch):
    """The second condition, planted separately: a gRPC failure names a peer, and mechanism in
    front of someone who asked to see a row is what #399 took out of the sentence beside this one."""
    def failing(source, database, schema, table, limit):
        raise RuntimeError("failed to connect to all addresses; last error: 10.4.21.7:50051")

    answer = _pressing(tmp_path, monkeypatch, failing).live_read_again("thr_a", CARD)

    assert "10.4.21.7:50051" not in answer["refused"]
    assert "[address]" in answer["refused"]


def test_an_unclassified_failure_still_says_something_true_about_itself(tmp_path: Path, monkeypatch):
    """Scrubbed and SHOWN, which is `failure_kind`'s own rule one layer up: whatever we cannot
    classify, we show. A sentence with no cause in it is what cost #399 and #404 two sessions."""
    def failing(source, database, schema, table, limit):
        raise AttributeError("'NoneType' object has no attribute 'columns'")

    answer = _pressing(tmp_path, monkeypatch, failing).live_read_again("thr_a", CARD)

    assert "AttributeError" in answer["refused"]
    assert "'NoneType' object has no attribute 'columns'" in answer["refused"]
    assert "did not answer" not in answer["refused"], "the store was never asked"


def test_a_path_bearing_failure_shows_the_path(tmp_path: Path, monkeypatch):
    """The boundary the generic arm widened, pinned rather than left to be discovered.

    `_scrubbed` rewrites secret-shaped runs and addresses. It does NOT rewrite mount paths or bare
    hostnames, so a file card over an unreadable mount puts its path on the card where the old
    sentence showed nothing. That is a deliberate trade — the path is the diagnosis for that
    failure — and it is asserted here so the next reader can tell it was chosen. If this test is
    what fails, the question is whether the trade still holds, not whether `_scrubbed` should grow.
    """
    def failing(source, database, schema, table, limit):
        raise PermissionError("[Errno 13] Permission denied: '/mnt/data/finance/rows.csv'")

    answer = _pressing(tmp_path, monkeypatch, failing).live_read_again("thr_a", CARD)

    assert "/mnt/data/finance/rows.csv" in answer["refused"]
    assert "PermissionError" in answer["refused"]


# ---- criterion 4: a refusal is an answer, not a fault --------------------------------------------


def test_every_refusal_comes_back_as_a_sentence_and_not_a_raise(tmp_path: Path, monkeypatch):
    """Stated in `live_read_again`'s docstring and not being changed: the person asked whether they
    can see today's rows, and "no" is an answer with a 200 under it."""
    for failure in (ResourceUnavailable("cannot open DWH"), ScopeIncomplete("needs a schema"),
                    RuntimeError("something else entirely")):
        def failing(source, database, schema, table, limit, e=failure):
            raise e

        answer = _pressing(tmp_path, monkeypatch, failing).live_read_again("thr_a", CARD)
        assert answer["refused"], failure


def test_a_card_recording_no_read_is_still_the_callers_fault(tmp_path: Path, monkeypatch):
    """`NoSuchRead` keeps travelling. It is not a sentence anybody is owed — a card with no source
    shows no button — and the narrow catches above must not have turned it into one."""
    def unused(source, database, schema, table, limit):
        raise AssertionError("the record is refused before any store is reached")

    with pytest.raises(run.NoSuchRead):
        _pressing(tmp_path, monkeypatch, unused).live_read_again("thr_a", {"kind": "table"})
