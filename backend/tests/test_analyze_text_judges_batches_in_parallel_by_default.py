"""On 2026-09-29 a mimo investigation judged 9 of 59 call transcripts before the ten-minute turn
ceiling, one Gateway batch at a time, because no call named `max_concurrency` and the default was 1.
"""

from __future__ import annotations

import json
import threading

from sage.liveread import run
from sage.liveread.text_analysis import MAX_CONCURRENCY

from .test_csv_text_analysis_data_used import analysis_args, labels_for, setup_turn, sse


def test_a_call_that_names_no_concurrency_runs_the_most_batches_at_once(tmp_path):
    together = threading.Barrier(MAX_CONCURRENCY, timeout=5)

    def provider(request):
        together.wait()
        return sse(json.dumps(labels_for(request)))

    turn, _data, _journal, _source = setup_turn(tmp_path, provider=provider)
    reply = json.loads(run.perform("live_read_files", analysis_args(batch_size=3), turn))

    assert reply["coverage"]["processed"] == 12
    assert reply["coverage"]["failed"] == 0
