"""Observed reads carry bounded metadata and never turn a failure into empty data."""
import json

import pytest

from sage.preview.read_outcomes import read_request, read_result

DATASETS = "/v4/datasetrw/datasets-v2"


def dataset_read(ids="dataset_1"):
    return read_request(DATASETS, f"datasetIds={ids}&includeTaxonomyTags=true")


def test_known_dataset_tags_are_successful_without_retaining_any_values():
    result = read_result(dataset_read(), 200, json.dumps([{
        "datasetRwDto": {"id": "dataset_1", "name": "private name"},
        "taxonomyTags": [{"namespaceLabel": "private tag", "label": "private value"}],
    }]).encode(), bound_ids=["dataset_1"])
    assert result == {"kind": "platform", "path": DATASETS, "resourceIds": ["dataset_1"],
                      "status": 200, "outcome": "passed"}


def test_a_returned_dataset_with_explicit_empty_tags_is_empty():
    body = [{"datasetRwDto": {"id": "dataset_1"}, "taxonomyTags": []}]
    assert read_result(dataset_read(), 200, json.dumps(body).encode())["outcome"] == "empty"


def test_empty_dataset_list_does_not_prove_empty_tags_for_a_requested_id():
    assert read_result(dataset_read(), 200, b"[]")["outcome"] == "passed"
    assert read_result(read_request(DATASETS), 200, b"[]")["outcome"] == "empty"
    query = read_request("/api/queries/summary", kind="query")
    assert read_result(query, 200, b"[]")["outcome"] == "empty"


@pytest.mark.parametrize("body", [b"null", b"{}", b"opaque", None,
    b'[{"datasetRwDto":{"id":"dataset_1"}}]',
    b'[{"datasetRwDto":{"id":"dataset_1"},"taxonomyTags":null}]',
    b'[{"datasetRwDto":{"id":"other"},"taxonomyTags":[]}]',
    b'{"error":"unavailable","rows":[]}',
])
def test_unknown_or_ambiguous_success_does_not_claim_empty(body):
    assert read_result(dataset_read(), 200, body)["outcome"] == "passed"


def test_one_empty_dataset_does_not_hide_an_omitted_requested_dataset():
    result = read_result(dataset_read("dataset_1,dataset_2"), 200,
                         b'[{"datasetRwDto":{"id":"dataset_1"},"taxonomyTags":[]}]')
    assert result["outcome"] == "passed"


def test_capped_dataset_ids_cannot_prove_empty_tags_for_the_whole_request():
    ids = [f"dataset_{n}" for n in range(21)]
    request = dataset_read(",".join(ids))
    body = [{"datasetRwDto": {"id": identifier}, "taxonomyTags": []}
            for identifier in ids[:20]]
    result = read_result(request, 200, json.dumps(body).encode(), bound_ids=ids[:20])
    assert result["outcome"] == "passed"
    assert result["resourceIdsTruncated"] is True
    assert len(result["resourceIds"]) == 20
    assert "reason" not in result  # The omitted ID cannot support a binding-mismatch claim.


def test_capped_ids_do_not_claim_empty_from_a_root_list_but_duplicates_are_complete():
    ids = [f"dataset_{n}" for n in range(20)]
    capped = dataset_read(",".join([*ids, "another_dataset"]))
    assert read_result(capped, 200, b"[]")["outcome"] == "passed"
    duplicate = dataset_read(",".join([*ids, ids[0]]))
    assert "resourceIdsTruncated" not in duplicate
    body = [{"datasetRwDto": {"id": identifier}, "taxonomyTags": []} for identifier in ids]
    assert read_result(duplicate, 200, json.dumps(body).encode())["outcome"] == "empty"


def test_one_tagged_dataset_keeps_a_mixed_response_nonempty():
    body = [{"datasetRwDto": {"id": "dataset_1"}, "taxonomyTags": []},
            {"datasetRwDto": {"id": "dataset_2"}, "taxonomyTags": [{"label": "private"}]}]
    assert read_result(dataset_read("dataset_1,dataset_2"), 200,
                       json.dumps(body).encode())["outcome"] == "passed"


@pytest.mark.parametrize("status", [200, 404])
def test_wrong_bound_id_is_actionable_even_on_a_200(status):
    result = read_result(dataset_read("display_name"), status, b"[]", bound_ids=["dataset_1"])
    assert (result["outcome"], result["reason"]) == ("failed", "binding_mismatch")


@pytest.mark.parametrize("status,reason", [(None, "transport_error"), (401, "access_denied"),
    (403, "access_denied"), (404, "not_found_or_hidden"), (504, "timeout"),
    (500, "unavailable"), (502, "unavailable"), (400, "http_error"), (302, "http_error")])
def test_failure_status_never_becomes_valid_empty(status, reason):
    result = read_result(dataset_read(), status, b"[]", bound_ids=["dataset_1"])
    assert (result["outcome"], result["reason"]) == ("failed", reason)


@pytest.mark.parametrize("status", [401, 403])
def test_access_failure_takes_priority_over_a_known_binding_mismatch(status):
    assert read_result(dataset_read("wrong"), status, bound_ids=["dataset_1"])["reason"] == "access_denied"


def test_empty_named_query_rows_have_no_row_or_parameter_values_in_metadata():
    request = read_request("/api/queries/summary", "token=secret-token&region=private-region", kind="query")
    assert read_result(request, 200, b'{"columns":["private-column"],"rows":[]}') == {
        "kind": "query", "path": "/api/queries/summary", "resourceIds": [],
        "status": 200, "outcome": "empty"}
    populated = read_result(request, 200, b'{"rows":[{"secret-column":"secret-value"}]}')
    assert populated["outcome"] == "passed"
    assert "secret" not in json.dumps(populated) and "private" not in json.dumps(populated)


def test_only_dataset_ids_survive_query_parsing_and_are_bounded():
    request = read_request(DATASETS + "?token=secret&datasetIds=d1%2Cd2&datasetIds=d2,d3&path=private")
    assert request == {"kind": "platform", "path": DATASETS, "resourceIds": ["d1", "d2", "d3"]}
    many = read_request(DATASETS, "datasetIds=" + ",".join(f"d{i}" for i in range(30)))
    assert len(many["resourceIds"]) == 20


@pytest.mark.parametrize("value", ["", "name%20with%20space", "secret%40example.com", "x" * 129, "%0Asecret"])
def test_unsafe_resource_ids_are_flagged_without_retaining_the_value(value):
    request = dataset_read(value)
    assert request["resourceIds"] == []
    assert request["invalidResourceId"] is True
    assert read_result(request, 200, b"[]")["reason"] == "invalid_resource_id"


@pytest.mark.parametrize("path", ["https://host/token", "/unknown/secret", "/api/users/v1/self%0Atoken",
    "/v4/datasetrw/../secret", "/api/datasetrw/" + "x" * 200])
def test_unsafe_or_unknown_paths_are_not_exposed(path):
    assert read_request(path)["path"] == "/unrecognized"


def test_result_drops_untrusted_extra_request_fields():
    request = {**dataset_read(), "token": "secret", "query": "private", "response": "private"}
    result = read_result(request, 204)
    assert result == {**dataset_read(), "status": 204, "outcome": "passed"}


def test_a_failed_query_wrapper_is_not_an_empty_result():
    request = read_request("/api/queries/summary", kind="query")
    assert read_result(request, 200, b'{"rows":[],"error":"unavailable"}')["outcome"] == "passed"


def test_large_success_body_keeps_http_evidence_without_a_value_parse():
    result = read_result(dataset_read(), 200, b" " * (1024 * 1024) + b"[]")
    assert result["outcome"] == "passed"
