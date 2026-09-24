"""Explicit text references take one bounded, auditable path before Build starts (#516)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from sage.assets.provider import FakeAssetProvider
from sage.driver.opencode import with_attachment_listing
from sage.liveread import reference, run
from sage.liveread.data_use import DataUse
from sage.orchestrator.describe import describe

from .test_a_dropped_mention_reaches_the_agents_prompt import _orch


def _authorized(tmp_path: Path, name: str = "requirements.md", body: str = "# Requirements\nBuild it"):
    path = tmp_path / name
    path.write_text(body)
    return reference.authorize(tmp_path, [{"path": name}], name)


_WORD_DOCUMENT = (
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body>{}</w:body></w:document>"
)


def _docx(tmp_path: Path, body: str, name: str = "requirements.docx") -> Path:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", _WORD_DOCUMENT.format(body))
    return path


def _pdf(tmp_path: Path, texts: list[str], name: str = "requirements.pdf", *,
         encrypted: bool = False) -> Path:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    path = tmp_path / name
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=612, height=792)
        font = writer._add_object(DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }))
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})
        })
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode())
        page.replace_contents(stream)
    if encrypted:
        writer.encrypt("secret")
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def test_exact_heading_selection_uses_the_shared_bounded_handler(tmp_path: Path):
    authorized = _authorized(
        tmp_path,
        body=("# TFL shell\nLayout only\n\n## Programming Notes\n"
              "UNIQUE-RULE: count distinct subjects\n\n## Footnotes\nDo not send this section"),
    )
    assert authorized is not None

    prepared = reference.prepare(authorized, prompt="Follow the Programming Notes exactly")

    assert prepared is not None
    assert prepared.selected_selector == "Programming Notes"
    assert "UNIQUE-RULE" in prepared.text
    assert "Do not send this section" not in prepared.text
    assert prepared.truncated is False


def test_no_unique_heading_phrase_selects_the_bounded_whole_document(tmp_path: Path):
    authorized = _authorized(tmp_path, body="# First\none\n\n# Second\ntwo")
    assert authorized is not None

    prepared = reference.prepare(authorized, prompt="Follow the attached document")

    assert prepared is not None
    assert prepared.selected_selector == ""
    assert prepared.text == "# First\none\n\n# Second\ntwo"


def test_explicit_heading_must_exist_once(tmp_path: Path):
    authorized = _authorized(tmp_path, body="# Notes\none\n# Notes\ntwo")
    assert authorized is not None

    prepared = reference.prepare(authorized, selector="Notes")

    assert prepared is not None
    assert prepared.status == "heading_not_unique"
    assert "missing or is not unique" in prepared.text


def test_document_text_and_source_bytes_are_bounded(tmp_path: Path):
    authorized = _authorized(tmp_path, body="x" * (reference.MAX_SELECTED_CHARS + 27))
    assert authorized is not None

    prepared = reference.prepare(authorized)

    assert prepared is not None
    assert len(prepared.text) == reference.MAX_SELECTED_CHARS
    assert prepared.truncated is True
    assert prepared.selected_characters == reference.MAX_SELECTED_CHARS + 27

    too_big = tmp_path / "too-big.txt"
    with too_big.open("wb") as handle:
        handle.truncate(reference.MAX_SOURCE_BYTES + 1)
    authorized_big = reference.authorize(tmp_path, [{"path": too_big.name}], too_big.name)
    assert authorized_big is not None
    refused = reference.prepare(authorized_big)
    assert refused is not None and refused.status == "source_too_large"


def test_docx_paragraphs_and_table_rows_use_the_shared_bounded_handler(tmp_path: Path):
    path = _docx(
        tmp_path,
        "<w:p><w:r><w:t>UNIQUE DOCX RULE</w:t></w:r></w:p>"
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Arm</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>n (%)</w:t></w:r></w:p></w:tc></w:tr></w:tbl>",
    )
    authorized = reference.authorize(tmp_path, [{"path": path.name}], path.name)
    assert authorized is not None

    prepared = reference.prepare(authorized)

    assert prepared is not None and prepared.status == "prepared"
    assert prepared.source_type == "docx"
    assert "UNIQUE DOCX RULE" in prepared.text
    assert "Arm | n (%)" in prepared.text
    event, _reply = reference.data_use(prepared, purpose="fixed")
    assert "UNIQUE DOCX RULE" not in json.dumps(event)
    assert "Arm | n (%)" not in json.dumps(event)


def test_docx_rejects_an_uncompressed_document_xml_above_the_limit(tmp_path: Path):
    path = _docx(tmp_path, "x" * reference.MAX_SOURCE_BYTES)
    assert path.stat().st_size < reference.MAX_SOURCE_BYTES
    authorized = reference.authorize(tmp_path, [{"path": path.name}], path.name)
    assert authorized is not None

    prepared = reference.prepare(authorized)

    assert prepared is not None and prepared.status == "document_xml_too_large"
    assert prepared.text == "The Word document XML exceeds the 8 MiB extraction limit."


def test_docx_deep_xml_uses_a_bounded_iterative_traversal(tmp_path: Path):
    depth = 1_500
    body = ("<w:sdt>" * depth + "<w:p><w:r><w:t>DEEP DOCX RULE</w:t></w:r></w:p>"
            + "</w:sdt>" * depth)
    path = _docx(tmp_path, body)
    authorized = reference.authorize(tmp_path, [{"path": path.name}], path.name)
    assert authorized is not None

    prepared = reference.prepare(authorized)

    assert prepared is not None and prepared.status == "prepared"
    assert "DEEP DOCX RULE" in prepared.text


def test_pdf_selection_is_one_based_deduplicated_sorted_and_content_free(tmp_path: Path):
    path = _pdf(tmp_path, ["UNIQUE PAGE ONE", "PRIVATE PAGE TWO", "UNIQUE PAGE THREE"])
    authorized = reference.authorize(tmp_path, [{"path": path.name}], path.name)
    assert authorized is not None

    prepared = reference.prepare(authorized, pages=[3, 1, 3])

    assert prepared is not None and prepared.status == "prepared"
    assert prepared.source_type == "pdf"
    assert prepared.source_pages == 3
    assert prepared.selected_pages == (1, 3)
    assert prepared.processed_pages == (1, 3)
    assert "UNIQUE PAGE ONE" in prepared.text and "UNIQUE PAGE THREE" in prepared.text
    assert "PRIVATE PAGE TWO" not in prepared.text
    event, reply = reference.data_use(prepared, purpose="fixed")
    assert event["coverage"]["selected_pages"] == [1, 3]
    assert event["coverage"]["processed_pages"] == [1, 3]
    assert event["coverage"]["extracted_characters"] > 0
    assert reply["pages"] == [1, 3]
    assert "UNIQUE PAGE" not in json.dumps(event)


def test_pdf_default_stops_at_twenty_pages_and_eight_thousand_characters(tmp_path: Path):
    texts = [(f"PAGE {number} " + "x" * 500) for number in range(1, 26)]
    path = _pdf(tmp_path, texts)
    authorized = reference.authorize(tmp_path, [{"path": path.name}], path.name)
    assert authorized is not None

    prepared = reference.prepare(authorized)

    assert prepared is not None and prepared.status == "prepared"
    assert prepared.selected_pages == tuple(range(1, 21))
    assert prepared.processed_pages == tuple(range(1, 17))
    assert prepared.pages_truncated is True
    assert prepared.sent_characters == reference.MAX_SELECTED_CHARS
    assert prepared.truncated is True
    assert "PAGE 21" not in prepared.text


def test_pdf_page_cap_is_separate_from_text_truncation(tmp_path: Path):
    path = _pdf(tmp_path, [f"short page {number}" for number in range(1, 26)])
    authorized = reference.authorize(tmp_path, [{"path": path.name}], path.name)
    assert authorized is not None

    prepared = reference.prepare(authorized)

    assert prepared is not None and prepared.status == "prepared"
    assert prepared.source_pages == 25
    assert prepared.selected_pages == tuple(range(1, 21))
    assert prepared.processed_pages == tuple(range(1, 21))
    assert prepared.pages_truncated is True
    assert prepared.sent_characters == prepared.selected_characters
    assert prepared.truncated is True


def test_pdf_descriptor_tolerates_one_bad_page_but_reference_preparation_fails_closed(
        tmp_path: Path, monkeypatch):
    from pypdf._page import PageObject

    path = _pdf(tmp_path, ["GOOD PAGE ONE", "BAD PAGE TWO", "GOOD PAGE THREE"])
    extract_text = PageObject.extract_text

    def flaky_extract(self, *args, **kwargs):
        text = extract_text(self, *args, **kwargs)
        if "BAD PAGE TWO" in text:
            raise ValueError("synthetic page failure")
        return text

    monkeypatch.setattr(PageObject, "extract_text", flaky_extract)

    descriptor = describe(str(path))
    authorized = reference.authorize(tmp_path, [{"path": path.name}], path.name)
    assert authorized is not None
    prepared = reference.prepare(authorized)

    assert descriptor["kind"] == "pdf"
    assert "could not be parsed" not in descriptor["summary"]
    assert "p1=13, p2=0, p3=15" in descriptor["detail"]
    assert prepared is not None and prepared.status == "malformed_document"


def test_scanned_encrypted_malformed_and_invalid_pdf_selections_fail_closed(tmp_path: Path):
    from pypdf import PdfWriter

    scanned = tmp_path / "scanned.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with scanned.open("wb") as handle:
        writer.write(handle)
    encrypted = _pdf(tmp_path, ["SECRET"], name="encrypted.pdf", encrypted=True)
    malformed = tmp_path / "malformed.pdf"
    malformed.write_bytes(b"%PDF-1.7\nnot a document")

    def prepared(path: Path, **kwargs):
        authorized = reference.authorize(tmp_path, [{"path": path.name}], path.name)
        assert authorized is not None
        return reference.prepare(authorized, **kwargs)

    assert prepared(scanned).status == "no_extractable_text"
    assert prepared(encrypted).status == "encrypted_document"
    assert prepared(malformed).status == "malformed_document"
    searchable = _pdf(tmp_path, ["one", "two"], name="searchable.pdf")
    assert prepared(searchable, pages=[]).status == "invalid_page_selection"
    assert prepared(searchable, pages=[0]).status == "page_out_of_range"
    assert prepared(searchable, pages=list(range(1, 22))).status == "too_many_pages"


def test_pdf_over_limit_withheld_and_unauthorized_inputs_fail_closed(tmp_path: Path):
    too_big = tmp_path / "too-big.pdf"
    with too_big.open("wb") as handle:
        handle.truncate(reference.MAX_SOURCE_BYTES + 1)
    authorized = reference.authorize(tmp_path, [{"path": too_big.name}], too_big.name)
    assert authorized is not None
    prepared = reference.prepare(authorized)
    assert prepared is not None and prepared.status == "source_too_large"

    searchable = _pdf(tmp_path, ["PRIVATE PDF RULE"], name="private.pdf")
    manifest = [{"path": searchable.name}]
    withheld = reference.prepare_explicit(
        tmp_path, manifest, [searchable.name], prompt="use it",
        withheld={"file:" + searchable.name},
    )
    assert len(withheld) == 1 and withheld[0].status == "withheld"
    assert "PRIVATE PDF RULE" not in withheld[0].prompt_block()
    assert reference.authorize(tmp_path, manifest, "unmentioned.pdf") is None


def test_docx_and_pdf_plan_records_reprepare_through_the_shared_policy(tmp_path: Path):
    docx = _docx(
        tmp_path,
        "<w:p><w:r><w:t>RESTARTED DOCX RULE</w:t></w:r></w:p>",
    )
    pdf = _pdf(tmp_path, ["RESTARTED PDF RULE"])
    manifest = [{"path": docx.name}, {"path": pdf.name}]
    first = reference.prepare_explicit(
        tmp_path, manifest, [docx.name, pdf.name], prompt="Follow both documents"
    )

    saved = [reference.plan_record(item) for item in first]
    replayed = reference.prepare_plan_records(tmp_path, manifest, saved)

    assert [item.source_type for item in replayed] == ["docx", "pdf"]
    assert "RESTARTED DOCX RULE" in replayed[0].text
    assert "RESTARTED PDF RULE" in replayed[1].text


def test_pdf_planning_failure_remains_truthful_after_the_source_is_replaced(tmp_path: Path):
    path = tmp_path / "requirements.pdf"
    path.write_bytes(b"%PDF-1.7\nnot a document")
    manifest = [{"path": path.name}]
    authorized = reference.authorize(tmp_path, manifest, path.name)
    assert authorized is not None
    failed = reference.prepare(authorized)
    assert failed is not None and failed.status == "malformed_document"
    saved = [reference.plan_record(failed)]
    _pdf(tmp_path, ["TEXT PLANNING NEVER SAW"], name=path.name)

    replayed = reference.prepare_plan_records(tmp_path, manifest, saved)

    assert len(replayed) == 1
    assert replayed[0].status == "malformed_document"
    assert "TEXT PLANNING NEVER SAW" not in replayed[0].prompt_block()


def test_authorization_is_exact_honors_withholding_and_does_not_expand_a_folder(tmp_path: Path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "one.md").write_text("one")
    (tmp_path / "docs" / "private.md").write_text("private")
    manifest = [{"path": "docs/one.md"}, {"path": "docs/private.md"}]

    assert reference.authorize(tmp_path, manifest, "docs/missing.md") is None
    assert reference.authorize(tmp_path, manifest, "docs") is None
    assert reference.authorize(
        tmp_path, manifest, "docs/one.md", {"file:docs/one.md"}
    ) is None
    assert reference.prepare_explicit(
        tmp_path, manifest, ["docs/one.md"], prompt="use it"
    )[0].source == "docs/one.md"

    withheld = reference.prepare_explicit(
        tmp_path, manifest, ["docs/private.md"], prompt="use it",
        withheld={"file:docs/private.md"},
    )[0]
    assert withheld.status == "withheld"
    assert "private" not in withheld.prompt_block().casefold().replace("private.md", "")


def test_plain_text_does_not_claim_to_select_a_markdown_heading(tmp_path: Path):
    authorized = _authorized(tmp_path, name="requirements.txt", body="Programming Notes\nrule")
    assert authorized is not None

    prepared = reference.prepare(authorized, selector="Programming Notes")

    assert prepared is not None
    assert prepared.status == "heading_not_supported"
    assert "rule" not in prepared.text


def test_invalid_utf8_is_a_bounded_capability_result(tmp_path: Path):
    path = tmp_path / "requirements.txt"
    path.write_bytes(b"\xff\xfe\x00")
    authorized = reference.authorize(tmp_path, [{"path": path.name}], path.name)
    assert authorized is not None

    prepared = reference.prepare(authorized)

    assert prepared is not None
    assert prepared.status == "not_text"
    assert "not valid UTF-8" in prepared.text


def test_early_failures_persist_only_the_bounded_normalized_selector(tmp_path: Path):
    padded = " " * 200_000 + "SECRET"
    missing = reference.Authorized("missing.md", tmp_path / "missing.md")
    too_big = tmp_path / "too-big.md"
    with too_big.open("wb") as handle:
        handle.truncate(reference.MAX_SOURCE_BYTES + 1)
    invalid = tmp_path / "invalid.md"
    invalid.write_bytes(b"\xff")
    nul = tmp_path / "nul.md"
    nul.write_bytes(b"text\x00not-text")

    prepared = [
        reference.prepare(missing, selector=padded),
        reference.prepare(reference.Authorized(too_big.name, too_big), selector=padded),
        reference.prepare(reference.Authorized(invalid.name, invalid), selector=padded),
        reference.prepare(reference.Authorized(nul.name, nul), selector=padded),
    ]

    assert [item.status for item in prepared if item is not None] == [
        "unavailable", "source_too_large", "not_text", "not_text",
    ]
    for item in prepared:
        assert item is not None
        assert item.requested_selector == "SECRET"
        event, _reply = reference.data_use(item, purpose="fixed")
        assert len(json.dumps(event)) < 2_000


def test_empty_document_is_a_correlated_bounded_failure(tmp_path: Path):
    authorized = _authorized(tmp_path, body="")
    assert authorized is not None

    prepared = reference.prepare(authorized)

    assert prepared is not None and prepared.status == "empty_document"
    event, reply = reference.data_use(prepared, purpose="Use the requirements")
    data = DataUse()
    data.record(event, reply, lambda _event: None, "turn-1")
    _request, used = data.prepare({
        "model": "policy-model",
        "messages": [{"role": "user", "content": prepared.prompt_block()}],
    })
    assert used == {event["operation_id"]}
    list(data.observe(iter([
        b'data: {"choices":[{"finish_reason":"stop"}]}\n\n'
    ]), {"model": "policy-model"}, used))
    request = data.events("turn-1")[0]["requests"][0]
    assert request["requested_alias"] == "policy-model"
    assert request["state"] == "response_completed"


def test_resolved_target_cannot_escape_the_authorized_root_without_a_trusted_target(tmp_path: Path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside-reference.md"
    outside.write_text("PRIVATE OUTSIDE CONTENT")
    link = tmp_path / "requirements.md"
    link.symlink_to(outside)

    assert reference.authorize(tmp_path, [{"path": link.name}], link.name) is None


def test_data_use_metadata_contains_no_document_text_and_restores_without_row_fields(tmp_path: Path):
    authorized = _authorized(tmp_path, body="# Rules\nPRIVATE UNIQUE REQUIREMENT")
    assert authorized is not None
    prepared = reference.prepare(authorized)
    assert prepared is not None
    event, reply = reference.data_use(prepared, purpose="Follow the requirements")
    journal: list[dict] = []
    data = DataUse()
    data.record(event, reply, journal.append, "turn-1")

    assert "PRIVATE UNIQUE REQUIREMENT" not in json.dumps(journal)
    restored = DataUse()
    restored.restore(journal, lambda _event: None)
    reopened = restored.operations[event["operation_id"]][1]
    assert reopened["coverage"] == event["coverage"]
    assert "columns" not in reopened and "local_reference" not in reopened


def test_first_gateway_request_is_attributed_to_the_prepared_reference(tmp_path: Path):
    authorized = _authorized(tmp_path, body="# Rules\nUNIQUE REQUEST RULE")
    assert authorized is not None
    prepared = reference.prepare(authorized)
    assert prepared is not None
    event, reply = reference.data_use(prepared, purpose="Follow the requirements")
    data = DataUse()
    data.record(event, reply, lambda _event: None, "turn-1")

    request, used = data.prepare({
        "model": "GLM 5.3 OR",
        "messages": [{"role": "user", "content": prepared.prompt_block()}],
    })
    chunks = [b'data: {"choices":[{"finish_reason":"stop"}]}\n\n']
    assert list(data.observe(iter(chunks), request, used)) == chunks

    recorded = data.events("turn-1")[0]
    assert used == {event["operation_id"]}
    assert recorded["requests"][0]["requested_alias"] == "GLM 5.3 OR"
    assert recorded["requests"][0]["state"] == "response_completed"


def test_gateway_refusal_is_recorded_without_persisting_document_text(tmp_path: Path):
    authorized = _authorized(tmp_path, body="# Rules\nUNIQUE REFUSED RULE")
    assert authorized is not None
    prepared = reference.prepare(authorized)
    assert prepared is not None
    event, reply = reference.data_use(prepared, purpose="Follow the requirements")
    journal: list[dict] = []
    data = DataUse()
    data.record(event, reply, journal.append, "turn-1")
    request, used = data.prepare({
        "model": "policy-model",
        "messages": [{"role": "user", "content": prepared.prompt_block()}],
    })

    list(data.observe(iter([
        b'data: {"error":{"type":"guardrail_blocked","message":"denied"}}\n\n'
    ]), request, used))

    recorded = data.events("turn-1")[0]
    assert recorded["requests"][0]["state"] == "failed"
    assert recorded["requests"][0]["failure"] == "refused"
    assert "UNIQUE REFUSED RULE" not in json.dumps(journal)


def test_generic_read_of_a_prepared_document_is_still_replaced_with_a_receipt(tmp_path: Path):
    authorized = _authorized(tmp_path, body="# Rules\nUNIQUE LOCAL RESULT")
    assert authorized is not None
    prepared = reference.prepare(authorized)
    assert prepared is not None
    event, reply = reference.data_use(prepared, purpose="Follow the requirements")
    data = DataUse()
    data.record(event, reply, lambda _event: None, "turn-1")
    request = {"messages": [
        {"role": "assistant", "tool_calls": [{"id": "read-1", "function": {
            "name": "read", "arguments": json.dumps({"filePath": "requirements.md"})}}]},
        {"role": "tool", "tool_call_id": "read-1", "content": "UNIQUE LOCAL RESULT"},
    ]}

    prepared_request, _used = data.prepare(request)

    assert "UNIQUE LOCAL RESULT" not in prepared_request["messages"][1]["content"]
    assert "full tool output remains local" in prepared_request["messages"][1]["content"].lower()


def test_model_callable_document_operation_uses_the_same_handler(tmp_path: Path):
    authorized = _authorized(
        tmp_path, body="# Layout\nwide\n\n## Programming Notes\nUNIQUE CALLABLE RULE"
    )
    assert authorized is not None
    journal: list[tuple[dict, dict]] = []
    turn = run.Turn(
        thread_id="thread-1",
        examples_dir=tmp_path / "examples",
        reference_for=lambda source: authorized if source == "requirements.md" else None,
        record_data_use=lambda event, reply: journal.append((event, reply)),
    )

    reply = json.loads(run.perform("live_read_files", {
        "operation": "document", "dataset": "upload", "path": "requirements.md",
        "heading": "Programming Notes", "purpose": "Follow the programming rules",
    }, turn))

    assert reply["selected"].endswith("UNIQUE CALLABLE RULE")
    assert reply["selector"] == "Programming Notes"
    assert journal[0][0]["operation"] == "document_reference"
    assert "UNIQUE CALLABLE RULE" not in json.dumps(journal[0][0])


def test_model_callable_pdf_operation_accepts_bounded_pages(tmp_path: Path):
    path = _pdf(tmp_path, ["PAGE ONE", "PRIVATE PAGE TWO", "PAGE THREE"])
    authorized = reference.authorize(tmp_path, [{"path": path.name}], path.name)
    assert authorized is not None
    journal: list[tuple[dict, dict]] = []
    turn = run.Turn(
        thread_id="thread-1",
        examples_dir=tmp_path / "examples",
        reference_for=lambda source: authorized if source == path.name else None,
        record_data_use=lambda event, reply: journal.append((event, reply)),
    )

    reply = json.loads(run.perform("live_read_files", {
        "operation": "document", "dataset": "upload", "path": path.name,
        "pages": [3, 1, 3], "purpose": "Follow selected PDF pages",
    }, turn))

    assert reply["pages"] == [1, 3]
    assert "PAGE ONE" in reply["selected"] and "PAGE THREE" in reply["selected"]
    assert "PRIVATE PAGE TWO" not in reply["selected"]
    assert journal[0][0]["coverage"]["selected_pages"] == [1, 3]
    assert "PAGE ONE" not in json.dumps(journal[0][0])


def test_document_operation_bounds_selector_metadata_and_ignores_model_purpose(tmp_path: Path):
    secret = "UNIQUE DOCUMENT CONTENT THAT MUST NOT ENTER METADATA"
    authorized = _authorized(tmp_path, body=f"# Rules\n{secret}")
    assert authorized is not None
    journal: list[tuple[dict, dict]] = []
    turn = run.Turn(
        thread_id="thread-1",
        examples_dir=tmp_path / "examples",
        reference_for=lambda _source: authorized,
        record_data_use=lambda event, reply: journal.append((event, reply)),
    )

    reply = json.loads(run.perform("live_read_files", {
        "operation": "document", "dataset": "upload", "path": "requirements.md",
        "heading": "x" * 200_000, "purpose": secret * 4_000,
    }, turn))

    event = journal[0][0]
    assert reply["status"] == "selector_too_long"
    assert event["requested_selector"] == ""
    assert event["purpose"] == "Use an explicitly referenced attachment"
    assert secret not in json.dumps(event)


def test_build_prepares_only_the_explicit_markdown_reference_before_dispatch(tmp_path: Path):
    orch, oc = _orch(tmp_path)
    orch._assets = FakeAssetProvider()
    project = orch.project(start_preview=False)
    shell = orch.upload_file(
        "shell.md",
        b"# TFL shell\nlayout\n\n## Programming Notes\nUNIQUE BUILD RULE\n",
    )["path"]
    private = orch.upload_file(
        "ADSL.csv", b"USUBJID,ARM\nPRIVATE-CSV-SENTINEL,Active\n"
    )["path"]

    events = list(orch.build_stream(
        "Follow the Programming Notes in the shell and use the attached ADSL at runtime", [shell]
    ))
    first = oc.prompts[0]
    outgoing = with_attachment_listing(first["text"], first["attachments"])

    assert "UNIQUE BUILD RULE" in outgoing
    assert "PRIVATE-CSV-SENTINEL" not in outgoing
    assert first["attachments"][0]["path"] == shell
    assert "BEGIN PREPARED REFERENCE" in first["attachments"][0]["detail"]
    document_events = [event for row in events for event in row.get("dataUsed", [])
                       if event.get("operation") == "document_reference"]
    assert len(document_events) == 1
    assert "UNIQUE BUILD RULE" not in json.dumps(document_events)
    assert private not in outgoing
    _request, used = project.shim.data_use.prepare({
        "model": "GLM 5.3 OR", "messages": [{"role": "user", "content": outgoing}]
    })
    assert used == {document_events[0]["operation_id"]}
    assistant_parts = oc.messages(first["session"])[0]["content"]
    assert not [part for part in assistant_parts if isinstance(part, dict)
                and part.get("type") == "tool"]


def test_build_prepares_one_docx_copy_on_the_first_outgoing_request(tmp_path: Path, caplog):
    orch, oc = _orch(tmp_path)
    orch._assets = FakeAssetProvider()
    project = orch.project(start_preview=False)
    source = _docx(
        tmp_path,
        "<w:p><w:r><w:t>UNIQUE FIRST DOCX RULE</w:t></w:r></w:p>"
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Column A</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>Column B</w:t></w:r></w:p></w:tc></w:tr></w:tbl>",
        name="source.docx",
    )
    shell = orch.upload_file("requirements.docx", source.read_bytes())["path"]
    private = orch.upload_file("private.pdf", b"%PDF-1.7\nPRIVATE PDF SENTINEL")["path"]

    events = list(orch.build_stream("Follow the attached Word requirements", [shell]))
    first = oc.prompts[0]
    outgoing = with_attachment_listing(first["text"], first["attachments"])

    assert outgoing.count("UNIQUE FIRST DOCX RULE") == 1
    assert outgoing.count("Column A | Column B") == 1
    assert "PRIVATE PDF SENTINEL" not in outgoing
    assert private not in outgoing
    document_events = [event for row in events for event in row.get("dataUsed", [])
                       if event.get("operation") == "document_reference"]
    assert len(document_events) == 1
    assert document_events[0]["source_type"] == "docx"
    assert "UNIQUE FIRST DOCX RULE" not in json.dumps(document_events)
    history = project.app_for_turn().read_history(project.build_conversation)
    assert "UNIQUE FIRST DOCX RULE" not in json.dumps(history)
    assert "UNIQUE FIRST DOCX RULE" not in (project.app_for_turn().read_plan() or "")
    diagnostics = project.record.path / ".sage" / "build-diagnostics.json"
    assert not diagnostics.exists() or "UNIQUE FIRST DOCX RULE" not in diagnostics.read_text()
    assert "UNIQUE FIRST DOCX RULE" not in caplog.text
    assistant_parts = oc.messages(first["session"])[0]["content"]
    assert not [part for part in assistant_parts if isinstance(part, dict)
                and part.get("type") == "tool"]


def test_forged_disk_manifest_and_retargeted_attachment_symlink_are_not_authorized(tmp_path: Path):
    orch, oc = _orch(tmp_path)
    orch._assets = FakeAssetProvider()
    project = orch.project(start_preview=False)
    shell = orch.upload_file("shell.md", b"# Rules\nORIGINAL SAFE RULE\n")["path"]
    outside = tmp_path / "outside.md"
    outside.write_text("PRIVATE RETARGETED SENTINEL")
    link = project.workspace.path / shell
    link.unlink()
    link.symlink_to(outside)
    forged_path = "public/data/forged/requirements.md"
    forged_link = project.workspace.path / forged_path
    forged_link.parent.mkdir(parents=True)
    forged_link.symlink_to(outside)
    project.workspace.write_attachments([
        *project.workspace.read_attachments(),
        {"dataset_id": "ds_sales_2026", "dataset": "sales_2026",
         "file": "README.md", "path": forged_path, "size": outside.stat().st_size},
    ])

    turn = orch._live_read_turn_for("thread-1")

    assert turn.reference_for is not None
    assert turn.reference_for(shell) is None
    assert turn.reference_for(forged_path) is None
    list(orch.build_stream("Follow the attached rules", [shell]))
    outgoing = with_attachment_listing(oc.prompts[0]["text"], oc.prompts[0]["attachments"])
    assert "PRIVATE RETARGETED SENTINEL" not in outgoing
    assert "could not be authorized" in outgoing


def test_single_file_folder_mention_stays_a_description_without_transferring_text(tmp_path: Path):
    orch, oc = _orch(tmp_path)
    orch._assets = FakeAssetProvider()
    orch.project(start_preview=False)
    attached = orch.attach_file("ds_sales_2026", "README.md")["path"]
    folder = attached.rsplit("/", 1)[0]

    events = list(orch.build_stream("Use this attached folder", [folder]))
    outgoing = with_attachment_listing(oc.prompts[0]["text"], oc.prompts[0]["attachments"])

    assert "Monthly revenue" not in outgoing
    assert "folder mention stays a bounded description" in outgoing
    assert not [event for row in events for event in row.get("dataUsed", [])
                if event.get("operation") == "document_reference"]


def test_approval_does_not_treat_the_broad_attachment_list_as_explicit_references(tmp_path: Path):
    orch, oc = _orch(tmp_path)
    orch._assets = FakeAssetProvider()
    project = orch.project(start_preview=False)
    shell = orch.upload_file(
        "shell.md", b"# Rules\nPRIVATE APPROVAL SENTINEL\n"
    )["path"]
    project.workspace.write_plan("# Plan\n\n1. Build the approved app")

    events = list(orch.approve_stream())
    first = oc.prompts[0]
    outgoing = with_attachment_listing(first["text"], first["attachments"])

    assert first["attachments"][0]["path"] == shell
    assert "PRIVATE APPROVAL SENTINEL" not in outgoing
    assert "does not carry an explicit structured reference" in outgoing
    assert not [event for row in events for event in row.get("dataUsed", [])
                if event.get("operation") == "document_reference"]
