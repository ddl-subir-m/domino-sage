"""What an @mention gives the MODEL, on both halves of the Workbench (#250).

Measured 2026-09-11, and it took a day to find because every theory started from "which file did
the agent read". None of them had. A Chat question — "sample 3 rows from @<a raw table>" — put three
customers' emails, card numbers and SSNs into the USER MESSAGE, because the mention inlined
`describe`'s `detail`, and `detail` is the schema plus two or three verbatim rows.

OpenCode replays a session's message list on every request, so those rows were re-sent for
thirty-five minutes at byte-identical offsets, across two Conversations, and refused a build turn
in an app created eleven minutes later whose own three attachments were clean. Nothing a person
could remove reached them.

`describe` has always emitted the other half. These pin that both callers take it.
"""
from __future__ import annotations

from pathlib import Path

from sage.orchestrator.describe import describe
from sage.orchestrator.service import _mention_block
from sage.shim import refusal_scan

POISONED = ("transaction_id,cardholder_name,email,card_number,ssn\n"
            "t1,A A,a@example.com,4871715921430428,111-22-3333\n"
            "t2,B B,b@example.com,4871715921430429,444-55-6666\n"
            "t3,C C,c@example.com,4871715921430430,777-88-9999\n")


def _refused(text: str) -> bool:
    """The deployed rule set, not a paraphrase of it — the same patterns the shim scans with."""
    return any(p.search(text) for _, p in refusal_scan._PATTERNS)


def test_the_block_a_mention_hands_over_carries_no_refusable_value(tmp_path: Path):
    f = tmp_path / "card_panel_transactions_RAW.csv"
    f.write_text(POISONED)
    d = describe(str(f))
    assert _refused(d["detail"]), "the fixture must be poisonous or this proves nothing"
    assert not _refused(_mention_block(d))


def test_it_still_says_enough_to_write_code_against(tmp_path: Path):
    """Withholding rows is only defensible if what is left answers the question the rows were
    answering: which columns exist, what they hold, and how to parse them."""
    f = tmp_path / "t.csv"
    f.write_text("ticker,report_date,side,consensus_eps\n"
                 "VLTA,2026-09-11,LONG,1.25\n"
                 "ACME,2026-09-18,SHORT,0.90\n")
    said = _mention_block(describe(str(f)))
    assert "ticker: string" in said
    assert "report_date: date (YYYY-MM-DD)" in said, "the spelling, so the agent can parse it"
    assert "side: string (LONG | SHORT)" in said, "the vocabulary, so it doesn't filter on 'long'"
    assert "consensus_eps: float" in said
    assert "Sample rows" not in said


def test_a_file_with_no_shape_keeps_the_rendering_it_had(tmp_path: Path):
    """Only tabular files have a `shape`. For the rest `detail` is already derived — `_describe_json`
    says in its own docstring that it emits key paths and types and never values — so falling back
    to it takes nothing away and keeps this change to the surface that had the defect."""
    f = tmp_path / "conf.json"
    f.write_text('{"retries": 3, "endpoint": "https://example.invalid"}')
    d = describe(str(f))
    assert d["shape"] == ""
    assert _mention_block(d) == d["detail"] != ""


def test_both_halves_of_the_workbench_ask_the_same_function(tmp_path: Path):
    """Chat's `_chat_mention_files` and Build's `_resolve_mentions` are the same decision made
    twice. Last time they were allowed to drift, and the drift is what this fixes — so the rule
    lives in one function and these two callers are the only ones that render a mention."""
    import inspect

    from sage.orchestrator import service
    src = inspect.getsource(service)
    assert src.count("_mention_block(d)") == 2
    # The rendering both of them feed. If a third caller appears it has to come through here too.
    assert '"detail": str(d.get("detail") or "")' not in src
