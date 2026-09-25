"""P11 controlled Build acceptance on both selected, seeded stacks.

The model is FakeOpenCode and the preview reports are injected. Files, stack selection,
FeedbackRunner checks, turn recovery, verification events and saved history are real.
Real supervisor/browser coverage is separate; this is not a live model or browser test.
"""
import json
import shutil
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from sage.build_policy import BuildPolicy
from sage.feedback.runner import FeedbackRunner
from sage.gateway.client import GatewayUpstreamError
from sage.orchestrator import service
from sage.orchestrator.service import Orchestrator
from sage.router.models import Mode, ModelCatalog
from sage.workspace.stack import stack_of

from .fake_opencode import FakeOpenCode, Turn, execution_plan
from .test_a_plan_document_captions_without_naming import ScriptedGateway
from .test_changed_page_validation import Preview

ROOT = Path(__file__).resolve().parents[2]
PATH = '/v4/datasetrw/datasets-v2'


@pytest.fixture(params=['react-vite', 'fastapi-antd'])
def selected_stack(request, tmp_path, monkeypatch):
    stack = request.param
    if not shutil.which('node'):
        pytest.skip('real page-script syntax checks need node on PATH')
    dependencies = ROOT / 'template/react-vite/node_modules'
    if stack == 'react-vite' and not (dependencies / 'typescript/bin/tsc').is_file():
        pytest.skip('real TypeScript check needs installed template/react-vite/node_modules')
    clock = [0.0]
    monkeypatch.setattr(service, 'time', SimpleNamespace(
        time=time.time, monotonic=lambda: clock[0],
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds)))
    workspace = tmp_path / 'project'
    gateway = ScriptedGateway()
    gateway.word = 'BUILD'
    oc = FakeOpenCode(workspace, [])
    orch = Orchestrator(
        workspace_dir=workspace, template=ROOT / 'template/react-vite', gateway=gateway,
        catalog=ModelCatalog(sovereign_plan='s', sovereign_implement='s', sovereign_ask='s',
                             plan='p', implement='i', ask='a'),
        project_id='controlled', feedback=FeedbackRunner(), opencode_client=oc,
        build_policy=replace(BuildPolicy(), page_ack_wait_seconds=0.25,
                             runtime_error_wait_seconds=0.05, runtime_repair_limit=1))
    monkeypatch.setattr(orch, '_restart_preview_for_config_change', lambda project: None)
    row = orch.create_app(stack=stack)
    project = orch.project(start_preview=False)
    assert project.workspace.app_id == row['id']
    assert stack_of(project.workspace.path).name == stack
    if stack == 'react-vite':
        assert (project.workspace.path / 'node_modules/typescript/bin/tsc').is_file()
    project.supervisor = Preview(project.workspace.app_id)
    project.record.write_settings({'skip_planning': True})
    project.control.set_mode(Mode.IMPLEMENT)
    entry = 'src/App.tsx' if stack == 'react-vite' else 'static/app.js'
    source = ('export default function App() { return <main>Built</main> }\n'
              if stack == 'react-vite' else "document.body.dataset.controlled = 'built';\n")
    yield SimpleNamespace(orch=orch, project=project, oc=oc, gateway=gateway,
                          stack=stack, entry=entry, source=source)
    project.queries.stop()


def _turn(app, suffix=''):
    return Turn(writes={app.entry: app.source + suffix})


def _run(app, report=None, *, approve=False):
    events = []
    stream = app.orch.approve_stream() if approve else app.orch.build_stream('Build the requested table')
    for event in stream:
        events.append(event)
        if event['type'] == 'preview-validation' and report:
            report(event)
    done = next(e for e in reversed(events) if e['type'] == 'done')
    saved = next(e for e in reversed(app.project.workspace.read_history()) if e['type'] == 'done')
    for key in ('ok', 'decision', 'verification'):
        assert saved.get(key) == done.get(key)
    expected_kind = 'Typecheck' if app.stack == 'react-vite' else 'Syntax check'
    assert all(e['kind'] == expected_kind for e in events if e['type'] == 'typecheck')
    assert stack_of(app.project.workspace.path).name == app.stack
    return events, done


def _ack(app):
    return lambda event: app.orch.record_preview_ack(event['validationId'])


def test_missing_title_keeps_the_selected_stack_and_can_be_approved(selected_stack):
    app = selected_stack
    app.project.record.write_settings({'skip_planning': False})
    app.project.control.set_mode(Mode.PLAN)
    plan = execution_plan(files=app.entry, include_title=False)
    app.gateway.name = GatewayUpstreamError(502, 'https://gateway.invalid', 'name unavailable')
    app.oc.turns.extend([Turn(text=plan), _turn(app)])
    events, _ = _run(app)
    assert next(e for e in events if e['type'] == 'plan-proposed')['plan'] == plan
    doc = app.project.record.read_plan_doc(app.orch.list_plan_docs()[0]['id'])
    assert doc['title'] == ''
    assert not app.project.workspace.read_plan().startswith('# ')
    assert len(app.gateway.repair_requests()) == 1
    assert len(app.oc.prompts) == 1
    _, done = _run(app, _ack(app), approve=True)
    assert done['ok'] is True and done['verification']['overall'] == 'passed'
    assert len(app.oc.prompts) == 2


def test_real_code_errors_exhaust_the_existing_repair_bound(selected_stack):
    app = selected_stack
    bad = ('export default function App() { return missingControlledName }\n'
           if app.stack == 'react-vite' else 'def broken(:\n    pass\n')
    path = app.entry if app.stack == 'react-vite' else 'app.py'
    comment = '//' if app.stack == 'react-vite' else '#'
    for attempt in range(3):
        writes = {app.entry: app.source, path: bad + f'{comment} attempt {attempt}\n'}
        app.oc.turns.append(Turn(writes=writes))
    events, done = _run(app)
    checks = [e for e in events if e['type'] == 'typecheck']
    assert len(checks) == 3 and all(e['ok'] is False for e in checks)
    expected_error = 'TS2304' if app.stack == 'react-vite' else 'SyntaxError'
    assert all(expected_error in e['message'] for e in checks)
    assert done['ok'] is False and 'no progress' in done['decision']
    assert done['verification']['stages']['code'] == 'failed'
    assert len(app.oc.prompts) == 3
    assert not any(e['type'] == 'preview-validation' for e in events)
    assert bad in (app.project.workspace.path / path).read_text()
    assert app.project.workspace.has_built()


def test_startup_failure_after_real_clean_code_is_not_success(selected_stack):
    app = selected_stack
    writes = {app.entry: app.source}
    if app.stack == 'fastapi-antd':
        writes['app.py'] = 'import sage_missing_controlled_dependency\n'
    app.oc.turns.append(Turn(writes=writes))
    app.project.supervisor.state = 'failed'
    app.orch._build_policy = replace(app.orch._build_policy, runtime_repair_limit=0)
    events, done = _run(app)
    assert next(e for e in events if e['type'] == 'typecheck')['ok'] is True
    assert done['ok'] is False
    assert done['verification']['stages']['startup'] == 'failed'
    assert app.project.workspace.has_built()
    assert len(app.oc.prompts) == 1


@pytest.mark.parametrize('repaired', [True, False], ids=['repair-succeeds', 'repair-exhausted'])
def test_runtime_fault_after_reload_obeys_the_repair_bound(selected_stack, repaired):
    app = selected_stack
    app.oc.turns.extend([_turn(app), _turn(app, '// repair\n')])
    documents = []
    def page(event):
        document = event['validationId']
        assert document not in documents
        documents.append(document)
        assert app.orch.record_preview_ack(document)
        if len(documents) == 1 or not repaired:
            app.orch.record_runtime_error('controlled render crash', validation_id=document)
        else:
            app.orch.record_runtime_error('late old crash', validation_id=documents[0])
    _, done = _run(app, page)
    assert len(documents) == len(app.oc.prompts) == 2
    assert app.project.supervisor.restarts == 2
    assert done['ok'] is repaired
    assert done['verification']['stages']['runtime'] == ('passed' if repaired else 'failed')
    assert app.project.workspace.has_built()


@pytest.mark.parametrize('case', ['tags', 'empty', 'wrong-id', 'unauthorized', 'denied', 'not-found',
                                  'timeout', 'transport', 'no-call'])
def test_data_outcomes_keep_failure_empty_and_unverified_distinct(selected_stack, case):
    app = selected_stack
    app.project.workspace.update_bindings(lambda _: [
        {'kind': 'dataset', 'id': 'dataset_one', 'name': 'Synthetic Dataset'}])
    app.oc.turns.extend([_turn(app), _turn(app, '// data repair\n')])
    acknowledged = []
    def page(event):
        acknowledged.append(app.orch.record_preview_ack(event['validationId']))
        if case == 'no-call':
            return
        if case == 'transport':
            app.orch.record_preview_data_error(event['validationId'], '/api/queries/sales')
            return
        requested = 'display_name' if case == 'wrong-id' else 'dataset_one'
        context = app.orch.capture_preview_read(event['validationId'], PATH,
            f'datasetIds={requested}&includeTaxonomyTags=true&token=PRIVATE')
        status = {'unauthorized': 401, 'denied': 403, 'not-found': 404,
                  'timeout': 504, 'wrong-id': 404}.get(case, 200)
        body = json.dumps([{'datasetRwDto': {'id': 'dataset_one'},
                            'taxonomyTags': [{'label': 'Governed'}] if case == 'tags' else []}]).encode()
        app.orch.record_platform_read_failure(status, PATH, context=context, body=body)
    _, done = _run(app, page)
    reads = done['verification']['dataReads']
    assert 'PRIVATE' not in json.dumps(done)
    if case == 'no-call':
        assert acknowledged == [True]
        assert all(done['verification']['stages'][stage] == 'passed'
                   for stage in ('startup', 'page', 'runtime'))
        assert done['ok'] is True and reads == []
        assert done['verification']['stages']['data'] == 'unverified'
        assert len(app.oc.prompts) == 1
    elif case in {'tags', 'empty'}:
        assert done['ok'] is True
        assert done['verification']['stages']['data'] == 'passed'
        assert reads[0]['outcome'] == ('passed' if case == 'tags' else 'empty')
        assert len(app.oc.prompts) == 1
    else:
        assert done['ok'] is False
        assert done['verification']['stages']['data'] == 'failed'
        assert reads[0]['outcome'] == 'failed'
        assert reads[0]['reason'] == {'wrong-id': 'binding_mismatch', 'unauthorized': 'access_denied',
            'denied': 'access_denied',
            'not-found': 'not_found_or_hidden', 'timeout': 'timeout', 'transport': 'transport_error'}[case]
        # The existing bounded repair is for platform failures, not query transport faults.
        assert len(app.oc.prompts) == (1 if case == 'transport' else 2)
    assert app.project.workspace.has_built()
