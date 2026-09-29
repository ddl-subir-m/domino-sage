"""Two things ended a mimo investigation on 2026-09-29, neither of them the question.

`analyze_text` refused six calls in a row with "Labels must be a non-empty list of strings.", and
the log held nothing about what had been sent. Then one failed Gateway listing read, taken on a
model call seven minutes in, ended the turn with "The gateway route cannot be checked now". The
shim serves Chat and Build alike, so that second failure could end either.
"""

from __future__ import annotations

import json

import pytest

from sage.gateway.capabilities import RouteCapability
from sage.gateway.protocol import Protocol
from sage.liveread import run

from .test_csv_text_analysis_data_used import analysis_args, setup_turn
from .test_native_policy import body, shim


def test_labels_sent_as_a_json_string_are_judged_as_the_list_they_spell(tmp_path):
    turn, _data, _journal, _source = setup_turn(tmp_path)

    reply = json.loads(run.perform("live_read_files", analysis_args(
        labels='["delivery", "damage", "billing"]'), turn))

    assert reply["coverage"]["processed"] == 12


@pytest.mark.parametrize(("labels", "sent"), [
    ("delivery, damage, billing", "a string"),
    ([], "an empty list"),
    (["delivery", ""], "a list with an empty or non-string item"),
    ({"a": "delivery"}, "an object"),
])
def test_a_refused_label_list_says_what_arrived(tmp_path, caplog, labels, sent):
    def provider(_request):
        raise AssertionError("model called")

    turn, _data, _journal, _source = setup_turn(tmp_path, provider=provider)

    with caplog.at_level("INFO", logger="sage.liveread"):
        said = run.perform("live_read_files", analysis_args(labels=labels), turn)

    assert said.startswith("Labels must be a non-empty JSON array of strings")
    assert said.endswith(f"This call sent {sent}.")
    assert f"This call sent {sent}." in caplog.text, "the log is the only record of a refusal"


def _flaky(protocol):
    enforcement, _control = shim(protocol, effort="high")
    good = RouteCapability(protocol, True, ("none", "high"), ("none", "high"), "")
    answers = [good]

    def resolve(_model):
        answer = answers.pop(0) if answers else None
        if answer is None:
            raise ValueError("The gateway route cannot be checked now. Retry the model listing.")
        return answer

    enforcement.resolve_capability = resolve
    return enforcement


@pytest.mark.parametrize("protocol", [Protocol.MESSAGES, Protocol.RESPONSES])
def test_a_failed_listing_read_mid_turn_dispatches_on_the_last_known_route(protocol):
    from sage.shim.native import prepare_native

    enforcement = _flaky(protocol)
    first, *_ = prepare_native(enforcement, body(protocol), protocol, "p", "ses_test")

    again, *_ = prepare_native(enforcement, body(protocol), protocol, "p", "ses_test")

    assert again == first, "the second call must carry the same effort the route accepted"


def test_a_first_read_that_blips_once_is_read_again_before_the_turn_is_failed():
    from sage.shim.native import prepare_native

    enforcement, _control = shim(Protocol.RESPONSES)
    reads = []

    def blip_once(_model):
        reads.append(1)
        if len(reads) == 1:
            raise ValueError("The gateway route cannot be checked now. Retry the model listing.")
        return RouteCapability(Protocol.RESPONSES, True, ("none", "high"), ("none", "high"), "")

    enforcement.resolve_capability = blip_once

    prepare_native(enforcement, body(Protocol.RESPONSES), Protocol.RESPONSES, "p", "ses_test")

    assert len(reads) == 2


def test_a_model_never_resolved_still_refuses_when_the_listing_stays_down():
    from sage.shim.native import prepare_native

    enforcement, _control = shim(Protocol.RESPONSES)
    reads = []

    def down(_model):
        reads.append(1)
        raise ValueError("The gateway route cannot be checked now. Retry the model listing.")

    enforcement.resolve_capability = down

    with pytest.raises(ValueError, match="cannot be checked now"):
        prepare_native(enforcement, body(Protocol.RESPONSES), Protocol.RESPONSES, "p", "ses_test")
    assert len(reads) == 2, "one more read, not a loop"
