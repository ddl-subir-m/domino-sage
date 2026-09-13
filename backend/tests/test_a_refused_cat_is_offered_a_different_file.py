"""The offer at the end of the card has to reach everyone a different file would actually help.

`is_file` is set on exactly one path: where `read_path_from_tool_call` finds a path on the matching
assistant `tool_call`. A `bash cat transactions.csv` or a `grep` carries no path, so its rows land
on the same `is_file: False` as the person's typed question — and the card, reading `is_file` to
decide whether to say ", or attach a different file", denied the offer to the one population it fits
best. Their rows DID come out of a file. Sage just never learned its name (#312).

So the card reads a second field. `is_file` answers "can Sage name the file"; `is_fetched` answers
"did the turn read this, or did somebody type it", and the offer hangs off the second. The two
questions come apart in exactly one place, which is the population this file is named for.

Everything here is drawn from rows a real refused turn wrote. Hand-built carriers would let the
flags agree with the card while the search that produces them disagrees with both.
"""
from __future__ import annotations

import shutil

import pytest

from .test_a_one_file_conversation_is_not_told_to_carry_on import (
    POISON,
    _cat,
    _found_row,
    _one_file,
    _said,
)

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

OFFER = ", or attach a different file."


def _carriers(tmp_path, where: str, payload):
    """The `withhold-found` carriers a real refused turn writes for `payload`.

    Each population gets its own directory: `_found_row` seeds a whole Project, and four of them
    under one `tmp_path` collide on the template before a single assertion runs.
    """
    return _found_row(tmp_path / where, payload)["carriers"]


@_needs_node
def test_the_offer_reaches_every_population_whose_rows_came_out_of_a_file(tmp_path):
    """All four labels the card can draw, in one card each, in one test.

    Three tests asserting one population apiece would all pass with the population never drawn —
    the point here is the BOUNDARY, and a boundary needs both sides in front of it. The two file
    populations differ only in whether a name came with the rows; the two prose ones differ only in
    who wrote them. The offer has to split them the first way, not the second.
    """
    named = _carriers(tmp_path, "named", _one_file())
    catted = _carriers(tmp_path, "catted", [*_one_file()[:3], *_cat(
        "t1", "transactions.csv", f"name,ssn\nJ Doe,{POISON}")])
    typed = _carriers(tmp_path, "typed", [{"role": "system", "content": "You are Sage."},
                                          {"role": "user", "content": f"is {POISON} in the panel?"}])
    answered = _carriers(tmp_path, "answered", [
        {"role": "system", "content": "You are Sage."},
        {"role": "user", "content": "what did you just say?"},
        {"role": "assistant", "content": f"It reads J Doe,{POISON}."}])

    assert [c["label"] for c in named] == ["transactions.csv"]
    assert [c["label"] for c in catted] == ["something a tool read"]
    assert [c["label"] for c in typed] == ["the message you sent"]
    assert [c["label"] for c in answered] == ["an earlier answer in this conversation"]

    assert OFFER in _said(named), "a named file has always had the offer"
    assert OFFER in _said(catted), "#312: the rows came out of a file, so a different one helps"
    assert OFFER not in _said(typed), "nothing was ever attached to swap"
    assert OFFER not in _said(answered), "Sage's own words are not a file either"


def test_the_row_says_which_material_the_turn_read_rather_than_which_it_can_name(tmp_path):
    """The field the card reads, at the seam that writes it.

    `is_file` and `is_fetched` agree on three of the four populations. This pins the one where they
    do not, in both directions: a `cat` is fetched and unnamed, and a typed question is neither.
    """
    catted = _carriers(tmp_path, "catted", [*_one_file()[:3], *_cat(
        "t1", "transactions.csv", f"name,ssn\nJ Doe,{POISON}")])
    assert catted[0]["is_file"] is False
    assert catted[0]["is_fetched"] is True

    named = _carriers(tmp_path, "named", _one_file())
    assert named[0]["is_file"] is True and named[0]["is_fetched"] is True

    typed = _carriers(tmp_path, "typed", [{"role": "system", "content": "You are Sage."},
                                          {"role": "user", "content": f"is {POISON} in the panel?"}])
    assert typed[0]["is_file"] is False and typed[0]["is_fetched"] is False


@_needs_node
def test_a_mention_is_data_and_still_not_something_to_swap(tmp_path):
    """The reason `is_data` could not be the field sent (#312's candidate A, ruled out).

    Since #290 `is_data` is `is_tool or _carries_mention`, so `is_data and not is_fetched` names
    exactly one population: a carrier LABELLED "the message you sent" that carries an @mention's
    inlined descriptor. `is_fetched` is narrower by that case and nothing else.

    This pins a CALL, not a deduction, and the call is worth stating against itself: a person who
    @mentioned a file would in fact be helped by attaching a different one. What rules them out is
    the label. The card names this carrier as the message they typed, and withholding it takes
    their question away along with the descriptor — so the offer would name the file half of a
    carrier whose other half is their words. If that trade is ever re-argued, it is re-argued on
    the label, and this assertion is the thing to overturn first.
    """
    from sage.shim.chat_paths import MENTION_MARK

    mentioned = _carriers(tmp_path, "mentioned", [
        {"role": "system", "content": "You are Sage."},
        {"role": "user",
         "content": f"chart this {MENTION_MARK}\npath: /mnt/data/panel.csv\nname,ssn\nJ Doe,{POISON}"},
    ])
    assert [c["label"] for c in mentioned] == ["the message you sent"]
    assert mentioned[0]["is_fetched"] is False, "a mention is data, and it is not a fetch"
    assert OFFER not in _said(mentioned)


@_needs_node
def test_a_card_redrawn_from_a_row_written_before_the_field_existed_keeps_its_offer(tmp_path):
    """`withhold-found` rows are appended to a transcript and re-rendered whenever it is reopened.

    Rows written before `is_fetched` existed carry only `is_file`, and a card that read the new
    field alone would take the offer away from every named file already on screen. The two agree by
    construction on anything written from here on — a named file IS a `read` result — so the pair
    costs nothing and covers the rows this change cannot go back and rewrite.
    """
    old_row = [{"key": "file:/mnt/data/transactions.csv", "label": "transactions.csv",
                "is_file": True}]
    assert OFFER in _said(old_row)
