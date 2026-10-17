import hashlib
import json
import time

import pytest
from fastapi.testclient import TestClient

from diagram_bot.demo import demo_snapshot
from diagram_bot.samples import SAMPLES
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
    assert {s['id'] for s in client.get('/api/snapshots').json()} == {'demo'} | {f'sample-{k}' for k in SAMPLES}
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
    assert generated['view']['kind'] == 'detail' and generated['counts']['visible'] < generated['counts']['collected'] + generated['counts']['references']
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
    assert client.get('/api/snapshots/demo/document?view=focus&resource=missing').status_code == 422
    assert client.get('/api/snapshots/demo/document?view=sideways').status_code == 422


def test_views_are_isolated_documents(client):
    urls = ['/api/snapshots/demo/document?view=overview', '/api/snapshots/demo/document?view=detail&vpc=vpc-demo',
            '/api/snapshots/demo/document?view=detail&vpc=vpc-staging', '/api/snapshots/demo/document?view=focus&resource=i-demo-a',
            '/api/snapshots/demo/document?view=focus&resource=i-demo-a&references=true']
    documents = [client.get(url).json() for url in urls]
    assert [d['view']['kind'] for d in documents] == ['overview', 'detail', 'detail', 'focus', 'focus']
    assert len({d['xml'] for d in documents}) == len(urls)
    focus = documents[3]
    assert focus['view']['resource'].endswith('/ec2/i-demo-a') and len(focus['visible']) == focus['counts']['visible'] == 4
    edited = focus['xml'].replace('web-a', 'focused web-a')
    assert client.put(urls[3], json={'xml': edited, 'revision': 0}).json()['revision'] == 1
    assert all(client.get(url).json()['revision'] == 0 for url in urls[:3] + urls[4:])
    # A node ID and a raw resource ID address the same focus document.
    node_id = focus['view']['resource']
    assert client.get('/api/snapshots/demo/document?view=focus&resource=' + node_id).json()['xml'] == edited


def test_relayout_archives_edit_and_keeps_notes_then_restore(client):
    url = '/api/snapshots/demo/document?view=detail&vpc=vpc-demo'
    generated = client.get(url).json()['xml']
    note = ('<mxCell id="note-1" value="검토 메모 · 유지" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="900" y="40" width="220" height="85" as="geometry"/></mxCell>')
    moved = generated.replace('</root>', note + '</root>').replace('x="24" y="112"', 'x="300" y="400"')
    assert client.put(url, json={'xml': moved, 'revision': 0}).json()['revision'] == 1
    assert client.post(url.replace('/document?', '/document/relayout?'), json={'revision': 0}).status_code == 409
    relaid = client.post(url.replace('/document?', '/document/relayout?'), json={'revision': 1}).json()
    assert relaid['revision'] == 2 and 'note-1' in relaid['xml'] and '검토 메모 · 유지' in relaid['xml']
    assert 'x="300" y="400"' not in relaid['xml'] and 'x="24" y="112"' in relaid['xml']
    assert relaid['xml'].count('note-1') == 1
    assert [h['revision'] for h in relaid['history']] == [1]
    restored = client.post(url.replace('/document?', '/document/restore?'), json={'revision': 2, 'from_revision': 1}).json()
    assert restored['revision'] == 3 and restored['xml'] == moved
    assert [h['revision'] for h in restored['history']] == [1, 2]
    assert client.post(url.replace('/document?', '/document/restore?'), json={'revision': 3, 'from_revision': 9}).status_code == 404


def test_legacy_snapshot_and_detail_document_stay_usable(client, tmp_path):
    data = tmp_path / 'data'
    legacy = client.get('/api/snapshots/demo').json()
    for edge in legacy['graph']['edges']:
        del edge['id'], edge['qualifiers']
    (data / 'snapshots' / 'legacy.json').write_text(json.dumps(legacy), encoding='utf-8')
    key = hashlib.sha256(b'legacy|vpc-demo|False').hexdigest()
    old_xml = client.get('/api/snapshots/demo/document?vpc=vpc-demo').json()['xml'].replace('web-a', 'legacy web-a')
    (data / 'documents' / f'{key}.json').write_text(json.dumps({'xml': old_xml, 'revision': 4, 'saved_at': 'x'}), encoding='utf-8')
    upgraded = client.get('/api/snapshots/legacy').json()
    assert all(e['id'].startswith('rel-') for e in upgraded['graph']['edges'])
    assert [(e['source'], e['target']) for e in upgraded['graph']['edges']] == [(e['source'], e['target']) for e in legacy['graph']['edges']]
    document = client.get('/api/snapshots/legacy/document?vpc=vpc-demo').json()
    assert document['revision'] == 4 and 'legacy web-a' in document['xml']


@pytest.mark.parametrize('fail', [False, True])
def test_background_collection_result_and_failure(client, monkeypatch, fail):
    def collect(region, profile, expected, session=None, role_arn=None, external_id=None):
        assert (region, profile, expected, session, role_arn, external_id) == ('ap-northeast-2', 'demo-sso', '111122223333', None, None, None)
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
        assert len(client.get('/api/snapshots').json()) == 1 + len(SAMPLES)
    else:
        assert job['status'] == 'partial'
        assert client.get('/api/snapshots/' + job['snapshot_id']).status_code == 200


def test_findings_endpoint(client):
    result = client.get('/api/snapshots/demo/findings').json()
    assert result['summary']['high'] == 1
    assert result['findings'][0]['rule'] == 'SEC-SG-OPEN' and result['findings'][0]['resource'].endswith('/security_group/sg-web')
    assert client.get('/api/snapshots/sample-three-tier/findings').json()['findings'] == []


def test_diff_endpoint(client):
    imported = client.post('/api/snapshots', json=SAMPLES['three-tier']()).json()['id']
    result = client.get(f'/api/snapshots/{imported}/diff/sample-three-tier').json()
    assert result['comparable'] and not any(result['summary'].values())
    assert client.get('/api/snapshots/demo/diff/missing').status_code == 404


def test_collection_request_validates_role_arn(client):
    bad = client.post('/api/collections', json={'region': 'ap-northeast-2', 'expected_account': '111122223333',
                                                'role_arn': 'arn:aws:iam::1:user/admin'})
    assert bad.status_code == 422


def test_outdated_samples_are_regenerated(tmp_path):
    """Stored samples of an older SAMPLE_VERSION are replaced; the demo snapshot is left alone."""
    from diagram_bot.samples import SAMPLE_VERSION
    data = tmp_path / 'data'
    with TestClient(create_app(data, tmp_path / 'no-editor')):
        pass
    path = data / 'snapshots' / 'sample-three-tier.json'
    old = json.loads(path.read_text(encoding='utf-8'))
    old['metadata']['sample_version'] = SAMPLE_VERSION - 1
    old['metadata']['title'] = 'outdated'
    path.write_text(json.dumps(old), encoding='utf-8')
    with TestClient(create_app(data, tmp_path / 'no-editor')):
        pass
    fresh = json.loads(path.read_text(encoding='utf-8'))['metadata']
    assert fresh['sample_version'] == SAMPLE_VERSION and fresh['title'] != 'outdated'


def test_combine_account_snapshots_into_one_network_view(client):
    parts = SAMPLES['multi-account']()['parts']
    ids = [client.post('/api/snapshots', json=part).json()['id'] for part in parts[1:]]
    combined = client.post('/api/snapshots/combine', json={'ids': ids, 'title': '운영 조직'})
    assert combined.status_code == 201
    snapshot = client.get(f"/api/snapshots/{combined.json()['id']}").json()
    assert snapshot['metadata']['combined'] and snapshot['metadata']['accounts'] == ['222222222222', '333333333333']
    xml = client.get(f"/api/snapshots/{combined.json()['id']}/document?view=overview").json()['xml']
    assert 'AWS 계정 · 222222222222' in xml and 'AWS 계정 · 333333333333' in xml
    assert client.post('/api/snapshots/combine', json={'ids': [ids[0], ids[0]]}).status_code == 422


def test_identity_reports_the_signed_in_credentials(client, monkeypatch):
    """The profile menu shows who local credentials are (STS GetCallerIdentity); failures explain themselves."""
    class Sts:
        def get_caller_identity(self):
            return {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/architect", "UserId": "x"}

    class Session:
        def __init__(self, profile_name=None, **kwargs):
            self.profile = profile_name

        def client(self, name, **kwargs):
            if self.profile == "broken":
                raise RuntimeError("no credentials")
            return Sts()
    monkeypatch.setattr("diagram_bot.server.boto3.Session", Session)
    assert client.get('/api/identity').json() == {"account": "123456789012", "arn": "arn:aws:iam::123456789012:user/architect", "profile": None}
    assert client.get('/api/identity?profile=dev').json()["profile"] == "dev"
    failed = client.get('/api/identity?profile=broken')
    assert failed.status_code == 422 and "no credentials" in failed.json()["detail"]
