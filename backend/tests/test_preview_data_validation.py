"""Read outcomes belong to the document that issued the call (#557 P10)."""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from . import test_changed_page_validation as page_tests
from .fake_opencode import Turn

build = page_tests.build
run = page_tests.run

PATH = '/v4/datasetrw/datasets-v2'
QUERY = 'datasetIds=dataset_one&includeTaxonomyTags=true&secret=PRIVATE'


def attach_dataset(project):
    project.workspace.update_bindings(lambda _: [
        {'kind': 'dataset', 'id': 'dataset_one', 'name': 'Sample dataset'}])


@pytest.mark.parametrize('tags,outcome', [([{'label': 'governed'}], 'passed'), ([], 'empty')])
def test_successful_tags_and_empty_tags_remain_distinct(build, tags, outcome):
    orch, project, _ = build
    attach_dataset(project)
    def page(event):
        orch.record_preview_ack(event['validationId'])
        context = orch.capture_preview_read(event['validationId'], PATH, QUERY)
        orch.record_platform_read_failure(200, PATH, context=context, body=json.dumps([
            {'datasetRwDto': {'id': 'dataset_one'}, 'taxonomyTags': tags}]).encode())
    _, done = run(orch, report=page)
    assert done['ok'] is True
    assert done['verification']['stages']['data'] == 'passed'
    assert done['verification']['dataReads'][0]['outcome'] == outcome
    assert 'PRIVATE' not in json.dumps(done)


def test_no_observed_read_is_unverified_not_an_empty_result(build):
    orch, project, _ = build
    attach_dataset(project)
    _, done = run(orch, report=lambda ev: orch.record_preview_ack(ev['validationId']))
    assert done['ok'] is True
    assert done['verification']['stages']['runtime'] == 'passed'
    assert done['verification']['stages']['data'] == 'unverified'
    assert done['verification']['dataReads'] == []


def test_late_reply_after_app_switch_cannot_be_retagged(build):
    orch, project, _ = build
    attach_dataset(project)
    def page(event):
        context = orch.capture_preview_read(event['validationId'], PATH, QUERY)
        project.workspace = SimpleNamespace(app_id='another_app')
        orch.record_platform_read_failure(403, PATH, context=context)
        assert project.platform_read_failure is None
    _, done = run(orch, report=page)
    assert done['verification']['overall'] == 'unverified'
    assert done['verification']['dataReads'][0]['outcome'] == 'pending'


def test_platform_repair_is_one_attempt_and_old_response_is_ignored(build):
    orch, project, oc = build
    attach_dataset(project)
    oc.turns.append(Turn(writes={'src/App.tsx': '// repaired read\n'}))
    old = []
    def page(event):
        orch.record_preview_ack(event['validationId'])
        context = orch.capture_preview_read(event['validationId'], PATH,
                                            'datasetIds=display_name&token=PRIVATE')
        if old:
            orch.record_platform_read_failure(403, PATH, context=old[0])
            assert project.page_validation.data_reads[0]['outcome'] == 'pending'
        old.append(context)
        orch.record_platform_read_failure(404, PATH, context=context, body=b'{}')
    events, done = run(orch, report=page)
    assert len(oc.prompts) == 2
    assert done['ok'] is False
    assert done['verification']['stages']['data'] == 'failed'
    assert done['verification']['dataReads'][0]['reason'] == 'binding_mismatch'
    assert 'display_name' in oc.prompts[1]['text']
    assert 'PRIVATE' not in oc.prompts[1]['text']
    assert len([e for e in events if e['type'] == 'iterate']) == 1


def test_caught_browser_fetch_failure_is_still_a_data_failure(build):
    orch, _, oc = build
    orch._build_policy = replace(orch._build_policy, runtime_repair_limit=0)
    oc.turns.append(Turn(writes={'src/App.tsx': '// retry\n'}))
    def page(event):
        orch.record_preview_ack(event['validationId'])
        orch.record_preview_data_error(event['validationId'], '/api/queries/sales')
    _, done = run(orch, report=page)
    assert done['ok'] is False
    assert done['verification']['dataReads'][0]['reason'] == 'transport_error'


def test_pending_read_is_unverified_and_late_completion_cannot_reopen_it(build):
    orch, _, _ = build
    pending = []
    def page(event):
        orch.record_preview_ack(event['validationId'])
        pending.append(orch.capture_preview_read(event['validationId'], PATH, QUERY))
    _, done = run(orch, report=page)
    assert done['verification']['stages']['data'] == 'unverified'
    orch.record_platform_read_failure(200, PATH, context=pending[0], body=b'[]')
    assert pending[0]['read']['outcome'] == 'pending'


def test_query_failures_from_an_old_page_do_not_change_the_new_result(build):
    orch, project, _ = build
    project.queries = SimpleNamespace(failures=lambda: {'old_query': 'old failure'})
    _, done = run(orch, report=lambda ev: orch.record_preview_ack(ev['validationId']))
    assert done['ok'] is True


def test_proxy_captures_identity_before_the_relay_awaits(monkeypatch):
    from fastapi.testclient import TestClient

    from sage.preview.proxy import make_preview_app
    selected = ['app_one']
    heard = []
    def capture(validation_id, path, query, kind):
        return {'app': selected[0], 'validationId': validation_id}
    def relay(path, query):
        selected[0] = 'app_two'
        return 403, {}, b'{}'
    app = make_preview_app(lambda: '', get_platform=lambda: SimpleNamespace(relay=relay),
                           get_read_context=capture,
                           on_platform_read=lambda status, path, **kwargs: heard.append(kwargs))
    response = TestClient(app).get('/api/domino' + PATH + '?' + QUERY,
                                   headers={'X-Sage-Validation': 'validation_original'})
    assert response.status_code == 403
    assert heard[0]['context'] == {'app': 'app_one', 'validationId': 'validation_original'}


@pytest.mark.parametrize('broken', [True, False])
def test_streamed_read_stays_pending_until_its_body_completes(monkeypatch, broken):
    import httpx
    from fastapi.testclient import TestClient

    from sage.preview.proxy import make_preview_app

    heard = []
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            assert heard == [], 'headers alone do not prove that the data arrived'
            yield b'{"rows":['
            if broken:
                raise httpx.ReadError('truncated')
            yield b']}'
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=Body()))
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(transport=transport, **kwargs))
    app = make_preview_app(lambda: 'http://upstream',
        get_read_context=lambda *args: {'issued': True},
        on_platform_read=lambda status, path, **kwargs: heard.append((status, path, kwargs)))
    TestClient(app, raise_server_exceptions=False).get('/api/queries/sales')
    assert [row[0] for row in heard] == [None if broken else 200]
    assert heard[0][1] == '/api/queries/sales'
    if not broken:
        assert heard[0][2]['body'] == b'{"rows":[]}'


@pytest.mark.parametrize('identifiers', [None, 12, 'PRIVATE', ['dataset_one', 'secret?token=PRIVATE']])
def test_diagnostic_reads_exclude_values_and_accept_malformed_saved_ids(tmp_path, identifiers):
    from .test_build_diagnostic_export import _finished
    event = {'type': 'done', 'ok': False, 'verification': {
        'overall': 'failed', 'stages': {'data': 'failed'},
        'dataReads': [{'kind': 'platform', 'path': PATH + '?token=PRIVATE',
                       'resourceIds': identifiers, 'resourceIdsTruncated': True, 'status': 403, 'outcome': 'failed',
                       'reason': 'access_denied', 'body': 'PRIVATE', 'token': 'PRIVATE'}]}}
    record = _finished(tmp_path, event)
    read = record['buildOutcome']['verification']['dataReads'][0]
    assert read['resourceIds'] == (['dataset_one'] if isinstance(identifiers, list) else [])
    assert read['status'] == 403
    assert read['resourceIdsTruncated'] is True
    assert 'PRIVATE' not in json.dumps(record)


def test_live_and_saved_ui_keep_data_unverified_distinct():
    from .test_build_conversation_return import run as render
    stages = {'runtime': 'passed', 'data': 'unverified'}
    live = render({'pageValidation': 'current', 'verificationStages': stages})['status'][-1]
    saved = render({'savedVerification': 'unverified', 'verificationStages': stages})[-1]
    for row in (live, saved):
        assert row['value'] == 'Page checks passed; data access not verified'
        assert row['ok'] is None and row['warn'] is True


@pytest.mark.parametrize('status,reason', [(401, 'access_denied'), (403, 'access_denied'),
                                         (404, 'not_found_or_hidden'), (504, 'timeout')])
def test_failed_current_reads_keep_their_cause_and_never_become_empty(build, status, reason):
    orch, project, oc = build
    attach_dataset(project)
    oc.turns.append(Turn(writes={'src/App.tsx': '// repair\n'}))
    def page(event):
        orch.record_preview_ack(event['validationId'])
        context = orch.capture_preview_read(event['validationId'], PATH, QUERY)
        orch.record_platform_read_failure(status, PATH, context=context, body=b'[]')
    _, done = run(orch, report=page)
    assert done['ok'] is False
    assert done['verification']['dataReads'][0]['outcome'] == 'failed'
    assert done['verification']['dataReads'][0]['reason'] == reason
    assert len(oc.prompts) == 2


def test_reply_from_replaced_generation_stays_pending(build):
    orch, project, _ = build
    def page(event):
        context = orch.capture_preview_read(event['validationId'], PATH, QUERY)
        project.supervisor.generation += 1
        orch.record_platform_read_failure(403, PATH, context=context)
    _, done = run(orch, report=page)
    assert done['verification']['dataReads'][0]['outcome'] == 'pending'
    assert project.platform_read_failure is None


def test_read_metadata_limit_cannot_produce_a_green_data_check(build):
    orch, _, _ = build
    def page(event):
        orch.record_preview_ack(event['validationId'])
        for _ in range(21):
            context = orch.capture_preview_read(event['validationId'], PATH, QUERY)
            if context is not None:
                orch.record_platform_read_failure(200, PATH, context=context, body=b'[]')
    _, done = run(orch, report=page)
    assert len(done['verification']['dataReads']) == 20
    assert done['verification']['readsTruncated'] is True
    assert done['verification']['stages']['data'] == 'unverified'


@pytest.mark.parametrize('kind,expected_repairs', [('platform', 1), ('query', 0)])
def test_phased_build_does_not_reset_the_data_repair_budget(tmp_path, monkeypatch, kind, expected_repairs):
    from .test_phased_build import _plan_then_phases
    orch, oc, project, _ = _plan_then_phases(tmp_path)
    oc.turns.extend(Turn(writes={'src/Filter.tsx': f'// attempt {i}\n'}) for i in range(3))
    orch._build_policy = replace(orch._build_policy, page_ack_wait_seconds=0.2,
                                 runtime_error_wait_seconds=0)
    monkeypatch.setattr(orch, '_restart_preview_for_config_change', lambda project: None)
    project.supervisor = page_tests.Preview(project.workspace.app_id)
    events = []
    for event in orch.approve_stream():
        events.append(event)
        if event['type'] == 'preview-validation':
            orch.record_preview_ack(event['validationId'])
            path = PATH if kind == 'platform' else '/api/queries/sales'
            context = orch.capture_preview_read(event['validationId'], path, QUERY, kind)
            orch.record_platform_read_failure(403, path, context=context)
    assert len([event for event in events if event['type'] == 'iterate']) == expected_repairs
    assert len(oc.prompts) == 4 + expected_repairs  # plan, three phases, bounded platform repair
    done = next(event for event in events if event['type'] == 'done')
    assert done['ok'] is False
    assert done['verification']['stages']['data'] == 'failed'
    assert project.workspace.has_built()


def test_control_endpoint_keeps_the_document_id_for_caught_data_errors(monkeypatch):
    from fastapi.testclient import TestClient

    from sage.orchestrator import app as app_module

    heard = []
    monkeypatch.setattr(app_module, 'orchestrator', SimpleNamespace(
        record_preview_data_error=lambda identity, path: heard.append((identity, path))))
    response = TestClient(app_module.control_app).post('/api/preview/data-error', json={
        'validationId': 'document_original', 'path': '/preview/api/queries/sales'})
    assert response.status_code == 204
    assert heard == [('document_original', '/preview/api/queries/sales')]


def test_validation_closing_during_body_inspection_keeps_the_request_pending(build, monkeypatch):
    from sage.orchestrator import service
    orch, _, _ = build
    result = service.read_result
    def page(event):
        orch.record_preview_ack(event['validationId'])
        context = orch.capture_preview_read(event['validationId'], PATH, QUERY)
        def finish_after_deadline(*args, **kwargs):
            context['validation'].closed = True
            return result(*args, **kwargs)
        monkeypatch.setattr(service, 'read_result', finish_after_deadline)
        orch.record_platform_read_failure(200, PATH, context=context, body=b'[]')
    _, done = run(orch, report=page)
    assert done['verification']['dataReads'][0]['outcome'] == 'pending'
