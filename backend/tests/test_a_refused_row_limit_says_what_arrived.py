"""Two 2026-09-29 mimo replays each drew "The row limit must be a positive integer." from an
analyze_text call, and nothing recorded what the call had sent — the log keeps the refusal, and
the thread keeps no tool arguments. The refusal now names the value, so the model can correct it
and the log says what it was.
"""

from __future__ import annotations

import pytest

from sage.liveread import run

from . import test_csv_calculation_data_used as calc
from . import test_csv_text_analysis_data_used as text


@pytest.mark.parametrize("sent,shown", [(0, "0"), (-1, "-1"), (50.0, "50.0"), ("all", '"all"')])
def test_analyze_text_names_the_row_limit_it_refused(tmp_path, sent, shown):
    turn, _data, _journal, _source = text.setup_turn(tmp_path)
    reply = run.perform("live_read_files", text.analysis_args(row_limit=sent), turn)
    assert "positive integer" in reply and f"sent {shown}" in reply
    assert "Omit row_limit" in reply


@pytest.mark.parametrize("sent,shown", [(0, "0"), (50.0, "50.0")])
def test_calculate_names_the_row_limit_it_refused(tmp_path, sent, shown):
    turn, _data, _journal = calc.setup_turn(tmp_path)
    reply = run.perform("live_read_files", calc.args(row_limit=sent), turn)
    assert "positive integer" in reply and f"sent {shown}" in reply
    assert "Omit row_limit" in reply
