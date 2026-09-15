"""#296: stored Chat model choices remain selectable when listing fails."""
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from sage.orchestrator import app as appmod
from sage.resources.provider import FakeResourceProvider, LlmAlias, ResourceUnavailable
from sage.router import models

from .test_a_build_pick_carries_its_own_effort import TOOLS, _client, _sent
from .test_build_says_which_model_it_will_run import _drawn


def _offline(orch, monkeypatch, status=None):
    def unavailable():
        raise ResourceUnavailable('The LLM Gateway did not answer.', status)
    monkeypatch.setattr(orch._resources, 'list_llm_aliases', unavailable)


@pytest.mark.parametrize('kind', ['llm_alias', 'model_llm'])
@pytest.mark.parametrize('status', [None, 503])
def test_bound_offline_pick_uses_local_choices_and_sends_them(tmp_path, monkeypatch, kind, status):
    _, orch = _client(tmp_path, monkeypatch)
    orch.bind_llm_alias('id-gemini')
    if kind == 'model_llm':
        orch.project().record.update_project_resources(lambda rows: [{**r, 'kind': kind} for r in rows])
    before = orch.project().record.read_project_resources()
    monkeypatch.setitem(models.REASONING_EFFORTS, 'gemini-3.7-flash', ('low',))
    monkeypatch.setitem(models.EFFORTS_WITH_TOOLS, 'gemini-3.7-flash', ('low',))
    _offline(orch, monkeypatch, status)
    row, = orch.list_project_resources()
    drawn, = _drawn([{'mode': 'plan', 'chat': True, 'chatModel': row['alias'],
                      'listing': False, 'resourceAliases': [row]}])
    assert drawn['chatModelKeys'] == ['gemini-3.7-flash']
    assert [i['key'] for i in drawn['chatEffortItems']] == ['default', 'low']
    client = TestClient(appmod.control_app, raise_server_exceptions=False)
    response = client.post('/api/project/model', json={'chat_model': row['alias'], 'reasoning_effort': 'low'})
    assert response.status_code == 200, response.text
    assert response.json()['model']['chat_model'] == row['alias']
    assert response.json()['model']['reasoning_effort'] == 'low'
    control = orch._chat_project().control
    token = control.arm_chat('offline-choice')
    try:
        sent = _sent(control, orch._chat_project().shim.catalog, tools=TOOLS)
        assert sent['model'] == row['alias']
        assert sent['reasoning_effort'] == 'low'
    finally:
        control.disarm_chat(token)
    assert orch.project().record.read_project_resources() == before
    response = client.post('/api/project/model', json={'chat_model': row['alias'], 'reasoning_effort': 'high'})
    assert response.status_code == 400
    assert 'invalid reasoning_effort' in response.json()['error']


@pytest.mark.parametrize('capabilities,model,message', [
    (['chat'], 'not-bound', 'unknown model'),
    (['embeddings'], 'gemini-3.7-flash', 'not a chat model'),
])
def test_offline_cache_does_not_allow_unknown_or_embedding_models(tmp_path, monkeypatch, capabilities, model, message):
    _, orch = _client(tmp_path, monkeypatch)
    alias, = orch._resources.list_llm_aliases()[:1]
    orch._resources = FakeResourceProvider([replace(alias, capabilities=capabilities)])
    orch.bind_llm_alias(alias.id)
    _offline(orch, monkeypatch)
    with pytest.raises(ValueError, match=message):
        orch.set_chat_pick(model, None)
    assert orch._chat_project().control.snapshot().chat_model is None


@pytest.mark.parametrize('verdict', ['embeddings', 'absent'])
def test_live_verdict_takes_precedence_over_cached_chat_capability(tmp_path, monkeypatch, verdict):
    _, orch = _client(tmp_path, monkeypatch)
    alias = orch._resources.list_llm_aliases()[0]
    orch._resources = FakeResourceProvider([replace(alias, description='Original description')])
    orch.bind_llm_alias(alias.id)
    before = orch.project().record.read_project_resources()
    orch._resources = FakeResourceProvider([] if verdict == 'absent' else [
        replace(alias, capabilities=['embeddings'], description='Current description')])
    with pytest.raises(ValueError, match='unknown model' if verdict == 'absent' else 'not a chat model'):
        orch.set_chat_pick(alias.name, None)
    assert orch.project().record.read_project_resources() == before
    row, = orch.list_project_resources()
    assert row['description'] == 'Original description'
    assert row['capabilities'] == ['chat']


@pytest.mark.parametrize('status', [401, 403, 404])
def test_listing_refusal_is_not_replaced_by_cached_permission(tmp_path, monkeypatch, status):
    _, orch = _client(tmp_path, monkeypatch)
    orch.bind_llm_alias('id-gemini')
    _offline(orch, monkeypatch, status)
    with pytest.raises(ResourceUnavailable):
        orch.set_chat_pick('gemini-3.7-flash', None)


@pytest.mark.parametrize('model,effort', [('gpt-5.4', None), ('gpt-5.4', 'none'), ('sonnet', None)])
def test_offline_default_and_explicit_none_remain_distinct(tmp_path, monkeypatch, model, effort):
    _, orch = _client(tmp_path, monkeypatch)
    orch._resources = FakeResourceProvider([LlmAlias('model-id', model, model, None, ['chat'], {})])
    orch.bind_llm_alias('model-id')
    _offline(orch, monkeypatch)
    orch.set_chat_pick(model, effort)
    control = orch._chat_project().control
    assert control.snapshot().reasoning_effort == effort
    token = control.arm_chat('offline-default')
    try:
        sent = _sent(control, orch._chat_project().shim.catalog, tools=TOOLS)
        assert sent['model'] == model
        assert sent.get('reasoning_effort') == effort
        assert ('reasoning_effort' in sent) == (effort is not None)
    finally:
        control.disarm_chat(token)


def test_non_model_membership_cannot_supply_an_offline_model(tmp_path, monkeypatch):
    _, orch = _client(tmp_path, monkeypatch)
    orch.add_project_resource({'id': 'dataset:wrong-kind', 'kind': 'dataset', 'name': 'Data',
                               'alias': 'gemini-3.7-flash', 'capabilities': ['chat']})
    _offline(orch, monkeypatch)
    with pytest.raises(ValueError, match='unknown model'):
        orch.set_chat_pick('gemini-3.7-flash', None)
