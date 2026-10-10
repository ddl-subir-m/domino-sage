"""A semantic result is chart evidence through the same seam as SQL and CSV totals."""

import json
from dataclasses import replace

from sage.liveread import run
from sage.liveread.held import unsupported_numbers

from .test_analyze_text_counts_judgments_per_group import _args, _turn


def test_text_analysis_retains_the_saved_rows_and_disclosed_judgments(tmp_path):
    turn, _journal = _turn(tmp_path)
    held = []
    turn = replace(turn, hold=held.append)
    reply = json.loads(run.perform("live_read_table", _args(group_by="ACCOUNT_ID"), turn))
    assert len(held) == 1
    assert held[0].columns == ["Record ID", "ACCOUNT_ID", "label"]
    assert held[0].rows[0] == ["500A", "001A", "genuine_ask"]
    assert held[0].disclosed == [reply["selected"]]
    assert unsupported_numbers("There are 2 genuine asks.", held, "") == []
