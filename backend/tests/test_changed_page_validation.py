"""Only a document from the current, restarted code can verify a Build (#557 P9)."""
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from sage.build_policy import BuildPolicy
from sage.orchestrator import service
from sage.router.models import Mode

from .fake_opencode import Turn
from .test_an_approved_plan_runs_as_implement import _build


class Preview:
    def __init__(self, app, state='ready'):
        self.app, self.state, self.generation = app, state, 0
        self.restarts = 0

    def retry_start(self, *, explicit=False):
        assert explicit
        self.generation += 1
        self.restarts += 1
        return True

    def status(self):
        return {'appId': self.app, 'generation': f'preview:{self.generation}',
                'state': self.state, 'error': 'ImportError: missing_dependency' if self.state == 'failed' else None}


@pytest.fixture
def build(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(service, 'time', SimpleNamespace(
        time=time.time, monotonic=lambda: clock[0],
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds)))
    orch, oc = _build(tmp_path, [Turn(writes={'src/App.tsx': '// changed\n'})],
                      build_policy=replace(BuildPolicy(), runtime_error_wait_seconds=0.2,
                                           page_ack_wait_seconds=0.5))
    project = orch.project(start_preview=False)
    monkeypatch.setattr(orch, "_restart_preview_for_config_change", lambda project: None)
    project.supervisor = Preview(project.workspace.app_id)
    project.record.write_settings({'skip_planning': True})
    project.control.set_mode(Mode.IMPLEMENT)
    return orch, project, oc


def run(orch, *, report=None):
    events = []
    for event in orch.build_stream('Make the page'):
        events.append(event)
        if event['type'] == 'preview-validation' and report:
            report(event)
    return events, next(e for e in reversed(events) if e['type'] == 'done')


def test_missing_page_report_is_unverified_and_keeps_written_code(build):
    orch, project, _ = build
    events, done = run(orch)
    assert any(e['type'] == 'preview-validation' for e in events)
    assert done['ok'] is True
    assert done['verification']['overall'] == 'unverified'
    assert done['verification']['stages']['page'] == 'unverified'
    assert project.workspace.has_built()
    assert (project.workspace.path / 'src/App.tsx').read_text() == '// changed\n'


def test_only_current_document_ack_can_pass(build):
    orch, project, _ = build
    def acknowledge(event):
        assert orch.record_preview_ack('old-document') is False
        assert orch.record_preview_ack(event['validationId']) is True
    _, done = run(orch, report=acknowledge)
    assert done['verification']['overall'] == 'passed'
    assert project.supervisor.restarts == 1


def test_python_startup_failure_is_a_known_failure_after_clean_code(build):
    orch, project, _ = build
    project.supervisor.state = 'failed'
    orch._build_policy = replace(orch._build_policy, runtime_repair_limit=0)
    _, done = run(orch)
    assert done['ok'] is False
    assert done['verification']['stages']['startup'] == 'failed'
    assert project.workspace.has_built()


def test_reported_runtime_crash_at_repair_cap_never_passes(build):
    orch, _, _ = build
    orch._build_policy = replace(orch._build_policy, runtime_repair_limit=0)
    def crash(event):
        orch.record_preview_ack(event['validationId'])
        orch.record_runtime_error('render crashed', validation_id=event['validationId'])
    _, done = run(orch, report=crash)
    assert done['ok'] is False
    assert done['verification']['stages']['runtime'] == 'failed'


def test_stop_during_page_wait_keeps_work_and_finishes_unverified(build):
    orch, project, _ = build
    _, done = run(orch, report=lambda event: setattr(project, 'stop_requested', True))
    assert done['verification']['overall'] == 'unverified'
    assert (project.workspace.path / 'src/App.tsx').read_text() == '// changed\n'


def test_different_app_cannot_acknowledge_or_report_into_validation(build):
    orch, project, _ = build
    def stale(event):
        project.workspace = type('OtherApp', (), {'app_id': 'other-app'})()
        assert orch.record_preview_ack(event['validationId']) is False
        orch.record_runtime_error('other app crash', validation_id=event['validationId'])
    _, done = run(orch, report=stale)
    assert done['verification']['overall'] == 'unverified'


def test_repair_uses_a_new_document_and_ignores_the_old_crash(build):
    orch, project, oc = build
    oc.turns.append(Turn(writes={'src/App.tsx': '// repaired\n'}))
    ids = []
    def report(event):
        ids.append(event['validationId'])
        orch.record_preview_ack(event['validationId'])
        if len(ids) == 1:
            orch.record_runtime_error('first document crashed', validation_id=ids[0])
        else:
            orch.record_runtime_error('late old crash', validation_id=ids[0])
    _, done = run(orch, report=report)
    assert len(ids) == 2 and ids[0] != ids[1]
    assert project.supervisor.restarts == 2
    assert done['verification']['overall'] == 'passed'


def test_another_preview_attempt_invalidates_the_ack(build):
    orch, project, _ = build
    def supersede(event):
        project.supervisor.generation += 1
        assert orch.record_preview_ack(event['validationId']) is False
    _, done = run(orch, report=supersede)
    assert done['verification']['overall'] == 'unverified'


def test_phased_build_validates_only_the_final_page(tmp_path, monkeypatch):
    from .test_phased_build import _plan_then_phases
    orch, _, project, _ = _plan_then_phases(tmp_path)
    orch._build_policy = replace(orch._build_policy, page_ack_wait_seconds=0.2,
                                 runtime_error_wait_seconds=0)
    monkeypatch.setattr(orch, '_restart_preview_for_config_change', lambda project: None)
    project.supervisor = Preview(project.workspace.app_id)
    events = []
    for event in orch.approve_stream():
        events.append(event)
        if event['type'] == 'preview-validation':
            orch.record_preview_ack(event['validationId'])
    assert project.supervisor.restarts == 1
    assert len([e for e in events if e['type'] == 'done']) == 1
    assert next(e for e in events if e['type'] == 'done')['verification']['overall'] == 'passed'


@pytest.mark.parametrize('case', ['current', 'other', 'hidden'])
def test_workbench_only_reloads_the_visible_matching_app(case):
    from .test_build_conversation_return import run as render
    result = render({'pageValidation': case})
    assert ('sageValidation=validation_current' in result['atValidation']['src']) == (case == 'current')
    assert result['status'][-1]['value'] == 'Code checks passed; runtime not verified'
    assert result['status'][-1]['ok'] is None
    assert result['status'][-1]['warn'] is True


def test_saved_history_keeps_check_kind_and_unverified_warning():
    from .test_build_conversation_return import run as render
    rows = render({'savedVerification': 'unverified'})
    assert rows[0]['value'] == 'Syntax check passed'
    assert rows[1]['value'] == 'Code checks passed; runtime not verified'
    assert rows[1]['warn'] and rows[1]['ok'] is None
    assert render({'savedVerification': 'failed'})[-1]['value'] == 'Stopped — queries failed'


def test_diagnostics_keep_verification_and_safe_failure_stage(tmp_path):
    from .test_build_diagnostic_export import _finished
    event = {'type': 'done', 'ok': True, 'errorCode': 'plan_error', 'errorStage': 'planning',
             'verification': {'overall': 'unverified', 'stages': {'code': 'passed', 'runtime': 'unverified'},
                              'validationId': 'validation_abc', 'codeGeneration': 'bad/private path',
                              'secret': 'not allowed'}}
    record = _finished(tmp_path, event)
    assert record['buildOutcome']['status'] == 'unverified'
    assert record['buildOutcome']['errorStage'] == 'planning'
    assert record['buildOutcome']['verification'] == {
        'overall': 'unverified', 'stages': {'code': 'passed', 'runtime': 'unverified'},
        'validationId': 'validation_abc'}


def test_code_changed_after_ack_cannot_be_verified(build):
    orch, project, _ = build
    def changed(event):
        orch.record_preview_ack(event['validationId'])
        (project.workspace.path / 'src/App.tsx').write_text('// another revision\n')
    _, done = run(orch, report=changed)
    assert done['verification']['overall'] == 'unverified'
    assert orch.record_preview_ack(done['verification']['validationId']) is False


def test_stop_during_validation_does_not_enter_another_repair(build, monkeypatch):
    orch, project, oc = build
    monkeypatch.setattr(orch, '_detect_raw_gateway_calls', lambda *args: [('src/App.tsx', 'bad')])
    _, done = run(orch, report=lambda event: setattr(project, 'stop_requested', True))
    assert done['verification']['overall'] == 'unverified'
    assert len(oc.prompts) == 1
    assert (project.workspace.path / 'src/App.tsx').read_text() == '// changed\n'


def test_server_exit_after_document_load_is_a_known_failure(build):
    orch, project, _ = build
    orch._build_policy = replace(orch._build_policy, runtime_repair_limit=0)
    def exit_after_ack(event):
        orch.record_preview_ack(event['validationId'])
        project.supervisor.state = 'failed'
    _, done = run(orch, report=exit_after_ack)
    assert done['ok'] is False
    assert done['verification']['stages']['startup'] == 'failed'


def test_failed_phase_retains_its_code_check_outcome(tmp_path, monkeypatch):
    from sage.feedback.runner import FeedbackError, FeedbackReport

    from .test_phased_build import _plan_then_phases
    orch, _, _project, _ = _plan_then_phases(tmp_path)
    monkeypatch.setattr(orch._feedback, 'check', lambda path: FeedbackReport(
        ok=False, errors=[FeedbackError('src/data.ts', 1, 1, 'TS2304', 'missing name')]))
    events = list(orch.approve_stream())
    done = next(e for e in events if e['type'] == 'done')
    assert done['ok'] is False
    assert done['verification']['stages']['code'] == 'failed'
    assert done['verification']['stages']['runtime'] == 'not_applicable'
