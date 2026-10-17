import time

import pytest
from fastapi.testclient import TestClient

from diagram_bot.demo import demo_snapshot
from diagram_bot.server import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / 'data', tmp_path / 'no-editor')) as client:
        client.headers['X-Diagram-Bot'] = client.get('/api/bootstrap').json()['token']
        yield client


def test_local_ui_and_missing_editor(client):
    boot = client.get('/api/bootstrap').json()
    assert not boot['editor_ready']
    assert 'Diagram Bot' in client.get('/').text
    assert client.get('/static/app.js').status_code == 200
    assert client.get('/editor/js/app.min.js').status_code == 503
    assert client.get('/api/snapshots').json()[0]['id'] == 'demo'
    assert client.get('/api/snapshots/demo').json()['metadata']['demo'] is True


def test_mutation_protection_and_import_validation(client):
    assert client.post('/api/snapshots', json=demo_snapshot(), headers={'X-Diagram-Bot': 'wrong'}).status_code == 403
    assert client.post('/api/snapshots', json=demo_snapshot(), headers={'Origin': 'https://example.invalid'}).status_code == 403
    assert client.post('/api/snapshots', json={'schema_version': 8}).status_code == 422
    assert client.get('/', headers={'Host': 'evil.invalid'}).status_code == 400
    sample = demo_snapshot()
    sample['graph'] = {'untrusted': 'discard'}
    result = client.post('/api/snapshots', json=sample)
    assert result.status_code == 201
    saved = client.get('/api/snapshots/' + result.json()['id']).json()
    assert saved['raw'] == sample['raw']
    assert 'nodes' in saved['graph'] and 'untrusted' not in saved['graph']


def test_document_persistence_conflict_and_scope(client, tmp_path):
    url = '/api/snapshots/demo/document?vpc=vpc-demo'
    generated = client.get(url).json()
    original_snapshot = client.get('/api/snapshots/demo').json()
    xml = generated['xml'].replace('web-a', 'Reviewed web-a')
    saved = client.put(url, json={'xml': xml, 'revision': 0})
    assert saved.status_code == 200
    assert saved.json()['revision'] == 1
    assert client.put(url, json={'xml': xml, 'revision': 1}).json()['revision'] == 1
    assert client.put(url, json={'xml': xml, 'revision': 0}).status_code == 409
    assert client.get(url).json()['xml'] == xml
    assert client.get(url + '&reset=true').json()['xml'] == generated['xml']
    assert client.get(url + '&references=true').json()['revision'] == 0
    assert client.get('/api/snapshots/demo').json() == original_snapshot
    with TestClient(create_app(tmp_path / 'data', tmp_path / 'no-editor')) as reopened:
        assert reopened.get(url).json()['xml'] == xml
    assert client.put(url, json={'xml': '<html/>', 'revision': 1}).status_code == 422
    assert client.get('/api/snapshots/demo/document?vpc=unknown').status_code == 422
    assert client.put('/api/snapshots/demo/document?vpc=unknown', json={'xml': xml, 'revision': 0}).status_code == 422


@pytest.mark.parametrize('fail', [False, True])
def test_background_collection_result_and_failure(client, monkeypatch, fail):
    def collect(region, profile, expected):
        assert (region, profile, expected) == ('ap-northeast-2', 'demo-sso', '111122223333')
        if fail:
            raise ValueError('Account mismatch')
        return demo_snapshot()
    monkeypatch.setattr('diagram_bot.server.collect', collect)
    result = client.post('/api/collections', json={'region': 'ap-northeast-2', 'profile': 'demo-sso', 'expected_account': '111122223333'})
    assert result.status_code == 202
    for _ in range(100):
        job = client.get('/api/collections/' + result.json()['id']).json()
        if job['status'] not in ('queued', 'running'):
            break
        time.sleep(0.01)
    if fail:
        assert job['status'] == 'failed' and 'Account mismatch' in job['error']
        assert len(client.get('/api/snapshots').json()) == 1
    else:
        assert job['status'] == 'partial'
        assert client.get('/api/snapshots/' + job['snapshot_id']).status_code == 200
