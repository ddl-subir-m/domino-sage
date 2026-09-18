"""The data-ask scan read the Data Source's NAME, so punctuation in it picked the tool lane (#421).

On the low-confidence path `answer_only` is `_plain_chat_answer_only(prompt)`, whose second half is
`_CHAT_ARTIFACT_OR_DATA_ASK.search(prompt)` — an alternation carrying `\\bdata\\b`. A store called
`Snowflake-Data-Warehouse` puts that word in the sentence without the person having asked for any
data, so the scan meant to read what was ASKED read what was BOUND instead:

    Snowflake-Data-Warehouse   ->  scan hit   ->  answer_only False  ->  shell, 11 tools, bash
    Snowflake_Data_Warehouse   ->  scan miss  ->  answer_only True   ->  READ-ONLY, 7 tools, no bash

Same store, same sentence, one character of punctuation apart. `_` is a word character, so `\\b`
never fires between `Snowflake` and `Data`, and the read-only lane is the one #407/#408 say cannot
answer this class of question at all.

WHY THE NAMES ARE MASKED RATHER THAN THE ALTERNATION NARROWED. The regex has a real job — telling
"show me the rows" from "what is a Dataset" — and `data` is not the only word reachable this way:
`tables`, `rows`, `columns` and `samples` are all in the alternation and all plausible inside a
store name. Dropping any of them breaks the genuine ask, so the binding is removed from the text
before the ask is scanned. The seven plants below are one per condition of that masking.

THE TRAP THIS FILE EXISTS TO HOLD SHUT. A mask that splits the name on `\\W+` handles the hyphen and
misses the underscore, because `\\W` keeps `_` — which rebuilds the defect inside its own repair.
`test_an_underscored_store_is_masked_however_the_prompt_spells_it` is the row that reds on that
mistake, and the name it BINDS is the underscored spelling, which is the only place the mistake is
visible. Binding the hyphenated name passes under both splits, because `-` is not a word character
and `\\W` recovers the words from it by luck; a mutation run confirmed this file stayed fully green
under `\\W+` until that row bound the underscore. The lane-agreement row below reds on the ORIGINAL
defect, not on this one.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sage.orchestrator.service import _plain_chat_answer_only

from .fake_opencode import Turn
from .test_chat_turn import IntentGateway, ObservedControlOpenCode, _orch

# One sentence, one trailing `?`, only the store's name changed. The prompt is the ticket's, and it
# is question-shaped for every name below, so the data-ask scan is the ONLY condition in
# `_plain_chat_answer_only` that these rows move.
ASK = "Investigate which customers actively use DMM, across Mixpanel, Gong and Salesforce in {}?"

# Real naming conventions, and the first two are the same store. `Sales Data Lake` is here because
# a space is a word boundary too, so it reaches `\bdata\b` exactly as the hyphen does — masking has
# to cover the separator the person typed, not just the one the ticket happened to measure.
STORE_NAMES = [
    "Snowflake-Data-Warehouse",
    "Snowflake_Data_Warehouse",
    "Sales Data Lake",
    "DWH",
    "Prod-Warehouse",
    "ACME_ANALYTICS",
    "Redshift-Cluster",
]

# The classifier score from the reported turn, below `MIN_CONFIDENCE`, which is what puts
# `answer_only` on the else branch at all. Kept here rather than imported so that a later move of
# the threshold reds this file and says which way it moved.
UNSURE = 0.60


def _orch_observed(tmp_path: Path):
    """An orchestrator whose control snapshot is captured at the moment the prompt is sent."""
    orch, oc = _orch(tmp_path, [Turn(text="Answered.")],
                     gateway=IntentGateway({"label": "data_answer", "confidence": UNSURE}),
                     client=lambda ws: ObservedControlOpenCode(ws, [Turn(text="Answered.")]))
    oc.control = orch.project(start_preview=False).control
    return orch, oc


def _lane_for(orch, oc, name: str, prompt: str) -> bool:
    """Run one turn with `name` bound and report whether it was armed read-only.

    The decline is what gets the turn RUN. At 0.60 with a store bound, #401's card is drawn and the
    turn ends without reaching OpenCode; `Just answer this` promises exactly the turn that would
    have run, which is the turn whose lane this ticket is about.
    """
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {"kind": "data_source", "id": "ds1", "name": name})
    orch.decide_thread_investigation(tid, "decline")

    list(orch.chat_stream(tid, prompt))
    assert oc.snapshots, "the question ran"
    return bool(oc.snapshots[-1].read_only_turn)


# ---------------------------------------------------------------------------------------------
# Acceptance criterion 1: one prompt, several store names, one lane.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", STORE_NAMES)
def test_the_lane_is_the_same_whatever_the_store_is_called(tmp_path: Path, name: str):
    """The ticket's table, end to end. Every name here asks the same question of the same store.

    Reds today on the three names carrying a separated `Data`: they lose the read-only arming and
    keep `bash` because of how a customer spelled a warehouse.
    """
    orch, oc = _orch_observed(tmp_path)

    assert _lane_for(orch, oc, name, ASK.format(name)) is True


def test_the_two_spellings_of_one_store_reach_the_same_lane(tmp_path: Path):
    """The reported defect, asserted as an AGREEMENT so a mask that flattens both cannot pass it.

    `Snowflake-Data-Warehouse` and `Snowflake_Data_Warehouse` are one store spelled two ways, and
    today they take two tool lanes. Asserting the pair AGREES first and the value second is what
    stops a repair that answered "shell" for both from reading as a fix.

    This row does NOT hold the `\\W` trap shut — each name here is bound and spelled the same way, so
    a literal mask satisfies it. `test_an_underscored_store_is_masked_however_the_prompt_spells_it`
    is the row for that.
    """
    orch, oc = _orch_observed(tmp_path)

    hyphen = _lane_for(orch, oc, "Snowflake-Data-Warehouse", ASK.format("Snowflake-Data-Warehouse"))
    under = _lane_for(orch, oc, "Snowflake_Data_Warehouse", ASK.format("Snowflake_Data_Warehouse"))

    assert hyphen == under, "one store, two spellings, two tool lanes"
    assert hyphen is True, "neither spelling asked for data, so both answer in prose"


@pytest.mark.parametrize("name", STORE_NAMES)
def test_the_bound_name_is_not_an_ask_however_it_is_spelled(name: str):
    """The same claim on the predicate itself, where the separator is visible in the failure."""
    assert _plain_chat_answer_only(ASK.format(name), [name]) is True


# ---------------------------------------------------------------------------------------------
# The plants. One per condition of the masking, each turning exactly that condition off.
# ---------------------------------------------------------------------------------------------


def test_nothing_bound_leaves_the_store_name_readable_as_prose():
    """Plant one: the binding. Take the bound name away and the scan hits the sentence again.

    This is what proves the masking is doing the work above rather than the sentence having changed
    shape. Nothing bound is a real state — a Thread with no Data Source on it — not an empty name.
    """
    assert _plain_chat_answer_only(ASK.format("Snowflake-Data-Warehouse"), []) is False


def test_an_underscored_store_is_masked_however_the_prompt_spells_it():
    """Plant two: the separator class, aimed where `\\W` actually fails.

    The BOUND name is the underscored spelling, and that is the whole point of this row. `\\W` keeps
    `_`, so splitting the name on `\\W+` leaves `Snowflake_Data_Warehouse` as a single token and the
    mask then only matches that exact string — every other spelling in the prompt goes unmasked and
    `\\bdata\\b` hits it. Splitting on `[^0-9A-Za-z]+` recovers the three words and masks all four.

    Binding the HYPHENATED name instead would pass under both splits, because `-` is not a word
    character and `\\W` recovers the words from it by luck. A mutation run measured exactly that: with
    the split changed to `\\W+`, this file stayed green until this row bound the underscore.
    """
    bound = "Snowflake_Data_Warehouse"
    for spelled in ["Snowflake_Data_Warehouse", "Snowflake-Data-Warehouse",
                    "Snowflake Data Warehouse", "snowflake.data.warehouse"]:
        assert _plain_chat_answer_only(ASK.format(spelled), [bound]) is True, spelled


def test_a_store_named_only_for_a_data_noun_does_not_mask_the_ask():
    """Plant three: the guard on a name that IS the noun.

    For a store called `Rows` the binding and the ask are the same word, so masking it would read
    "show me the rows" as prose and take the shell off a genuine ask — this defect pointing the
    other way. Remove the guard and this row goes green->red.
    """
    assert _plain_chat_answer_only("what are the rows in Rows?", ["Rows"]) is False


@pytest.mark.parametrize("prompt,name", [
    ("what data is in Snowflake-Data-Warehouse?", "Snowflake-Data-Warehouse"),
    ("what data is in Snowflake_Data_Warehouse?", "Snowflake_Data_Warehouse"),
    ("which tables does Prod-Tables-Cluster expose?", "Prod-Tables-Cluster"),
])
def test_asking_for_the_word_the_store_is_named_after_is_still_an_ask(prompt: str, name: str):
    """Plant four: the name is masked as a PHRASE, not as a bag of its words.

    Here the noun the person asked for is also a word inside the store's name, which is the only
    condition that tells the two implementations apart. Masking the phrase removes the binding and
    leaves the standalone `data` the person typed, so the ask still suppresses `answer_only`. Masking
    each word of the name on its own removes every `data` in the sentence and reads a real data ask
    as prose — the defect pointing the other way, and invisible to every other row in this file.

    Found by mutation: the word-by-word mask left the whole file green until these rows existed.
    """
    assert _plain_chat_answer_only(prompt, [name]) is False


@pytest.mark.parametrize("name", ["Data-Set", "Data Set", "Data_Set"])
def test_a_name_is_not_masked_out_of_the_middle_of_a_longer_word(name: str):
    """Plant five: the alphanumeric lookarounds — the separator correction pointed outwards.

    A store called `Data-Set` masks to the pattern `data[^0-9A-Za-z]*set`, which is a PREFIX of the
    word `datasets`. Without the lookarounds the mask eats the first seven letters of the noun the
    person asked for and leaves `what  s are in  ?`, so a real dataset ask reads as prose. With them
    the match has to end on a non-alphanumeric, so `datasets` survives and only the store goes.

    The lookarounds use `[0-9A-Za-z]` rather than `\\w` for the same reason the split does: `\\w` keeps
    `_`, and `Data_Set` is the row that would show it.
    """
    assert _plain_chat_answer_only(f"what datasets are in {name}?", [name]) is False


def test_a_shorter_bound_name_does_not_shred_a_longer_one():
    """Plant six: longest name masked first, which only matters with two items on the Thread.

    A Thread can hold both `Snowflake` and `Snowflake-Data-Warehouse`. Masked in the order the items
    arrive, the short name goes first, takes `Snowflake` out of the middle of the long one, and leaves
    `-Data-Warehouse` behind — where `\\bdata\\b` matches and the original defect survives the fix.
    Longest pattern first consumes the whole name before the short one can bite into it.

    The short name is FIRST in the list on purpose; reversed, this row passes under either order.
    """
    assert _plain_chat_answer_only(ASK.format("Snowflake-Data-Warehouse"),
                                   ["Snowflake", "Snowflake-Data-Warehouse"]) is True


# ---------------------------------------------------------------------------------------------
# Acceptance criterion 2: the genuine ask still suppresses `answer_only`. One per word class.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("prompt", [
    "what are the rows in Snowflake-Data-Warehouse?",
    "what does a chart of signups in Snowflake-Data-Warehouse look like?",
    "can you give me a csv of signups from Snowflake-Data-Warehouse?",
    "which tables in Snowflake-Data-Warehouse hold billing?",
    "what columns does Snowflake-Data-Warehouse expose?",
    "how many samples are in Snowflake-Data-Warehouse?",
    "what does the dataset in Snowflake-Data-Warehouse contain?",
    "what does a plot of churn from Snowflake-Data-Warehouse show?",
    "what is in the graph of revenue in Snowflake-Data-Warehouse?",
    "what does the heatmap of usage in Snowflake-Data-Warehouse say?",
    "what is the matrix of retention in Snowflake-Data-Warehouse?",
])
def test_a_real_data_ask_still_suppresses_answer_only(prompt: str):
    """Plant seven: the scan. Every word class in the alternation, asked FOR rather than named.

    The store is bound in each row, so the mask runs and removes the name — and the noun the person
    actually asked for survives it. Widen the mask past the binding and these reds.
    """
    assert _plain_chat_answer_only(prompt, ["Snowflake-Data-Warehouse"]) is False
