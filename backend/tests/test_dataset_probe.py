"""The probe must not fail in the shape of its own answer.

`dataset_probe` exists to settle one design question -- whether a project collaborator inherits a
Dataset role -- and its docstring tells whoever runs it to record the output verbatim. That makes
a parse bug indistinguishable from a permission finding, which is how a working permission gets
written down as a broken one. These pin the reading, not the printing.
"""

from sage.tools.dataset_probe import _dataset_records

# The real body, trimmed: `datasetrw/v2` answers a dict, and every record is nested again under
# `dataset`. Captured from the dogfood deployment, where the endpoint also pages at ten.
_REAL = {
    "datasets": [
        {"dataset": {"id": "66ac0ca011cebc1c76dbdb1c", "name": "forecasts",
                     "projectId": "66abb97411cebc1c76dbda84"}},
    ],
    "metadata": {"offset": 0, "limit": 10},
}


def test_the_real_listing_shape_is_read():
    rows = _dataset_records(_REAL)
    assert rows is not None
    assert (rows[0].get("dataset") or rows[0])["name"] == "forecasts"


def test_a_bare_list_is_still_read():
    """Accepted so a future endpoint that drops the wrapper does not read as unparseable."""
    assert _dataset_records([{"id": "ds1"}]) == [{"id": "ds1"}]


def test_an_empty_listing_is_an_answer_not_a_parse_failure():
    """The one that matters. Seeing NOTHING is the answer D-Q5 most wants -- it is what a Results
    Consumer locked out of the project's own Dataset looks like. Read with `datasets or data`, an
    empty list is falsy, so it came back as the unknown-shape sentinel and the probe told the
    operator its own finding was a bug not to record.
    """
    assert _dataset_records({"datasets": []}) == []
    assert _dataset_records({"data": []}) == []


def test_an_unknown_shape_is_distinguishable_from_an_empty_one():
    """None is reserved for "this file cannot read the body", which prints as a probe bug. It must
    never collide with an empty listing, or the two findings cannot be told apart."""
    assert _dataset_records({"unexpected": 1}) is None
    assert _dataset_records("Public api endpoint not found") is None
    assert _dataset_records({"datasets": "not-a-list"}) is None
