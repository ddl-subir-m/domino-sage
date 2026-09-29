"""On 2026-09-29 a mimo judging batch hung 100s and failed, then failed again on retry, and 25 of 102
transcripts went unjudged. The text route capped output at 4,096 tokens and mimo's reasoning counts
against it: on the Gateway, a 50-snippet batch spent 1,130 to 4,095 reasoning tokens before a
~900-token answer, and one run in four came back `incomplete: max_output_tokens` with no text.
"""

from __future__ import annotations

import json

from sage.liveread import run

from .test_csv_text_analysis_data_used import analysis_args, labels_for, setup_turn, sse


def test_a_judging_request_asks_for_room_to_reason_and_answer(tmp_path):
    asked: list[dict] = []

    def provider(request):
        asked.append(request)
        return sse(json.dumps(labels_for(request)))

    turn, _data, _journal, _source = setup_turn(tmp_path, provider=provider)
    json.loads(run.perform("live_read_files", analysis_args(batch_size=3), turn))

    assert asked
    assert all(request.get("max_tokens", 4096) >= 16_000 for request in asked)
