"""`_CHAT_ARTIFACT_OR_DATA_ASK` carried `s?` on `rows` and `columns` and on nothing else.

So "give me the table" named data and "give me the tables" named none of it, and the difference
decided whether the fallback lane armed a prose-only turn. It failed toward prose-only, which is
the quiet direction: a turn that answers without reading anything looks like a turn that chose to.
"""

from __future__ import annotations

import pytest

from sage.orchestrator.service import _plain_chat_answer_only

PLURALS = ["tables", "charts", "graphs", "plots", "datasets", "samples", "heatmaps", "csvs",
           "matrices", "rows", "columns"]


@pytest.mark.parametrize("noun", PLURALS)
def test_a_plural_data_noun_is_a_data_ask_like_its_singular(noun: str):
    assert _plain_chat_answer_only(f"can you show me the {noun}?") is False
