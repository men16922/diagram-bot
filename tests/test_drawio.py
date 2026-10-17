from xml.etree import ElementTree as ET

import pytest

from diagram_bot.demo import demo_snapshot
from diagram_bot.drawio import cell_id, generate_document, validate_document
from diagram_bot.editor import install_editor
from diagram_bot.graph import build_graph


def test_native_hierarchy_and_links_are_consistent():
    snapshot = demo_snapshot()
    xml = generate_document(snapshot)
    assert xml == generate_document(snapshot)
    root = validate_document(xml)
    objects = {o.attrib['id']: o for o in root.findall('.//object')}
    ids = {'0', '1', *objects}
    graph = build_graph(snapshot)
    for cell in root.findall('.//mxCell'):
        for attr in ('parent', 'source', 'target'):
            if attr in cell.attrib:
                assert cell.attrib[attr] in ids
    for node in graph['nodes']:
        if node['kind'] == 'ec2' and not node['reference_only']:
            obj = objects[cell_id(node['id'])]
            subnet = next(e['target'] for e in graph['edges'] if e['source'] == node['id'] and e['relation'] == 'in_subnet')
            assert obj.find('mxCell').attrib['parent'] == cell_id(subnet)
        if node['kind'] == 'load_balancer':
            obj = objects[cell_id(node['id'])]
            assert obj.find('mxCell').attrib['parent'] == cell_id(next(n['id'] for n in graph['nodes'] if n['kind'] == 'vpc'))
    assert len(root.findall('.//object[@relationIndex]')) == 3
    assert all(o.attrib['evidence'].startswith('Describe') for o in root.findall('.//object[@relationIndex]'))


def test_references_and_ip_targets_do_not_invent_placement():
    snapshot = demo_snapshot()
    assert 'sg-web' not in generate_document(snapshot, 'vpc-demo')
    assert 'sg-web' in generate_document(snapshot, 'vpc-demo', True)
    snapshot['raw']['target_groups'][0]['TargetType'] = 'ip'
    snapshot['raw']['target_health'][0]['Descriptions'] = [{'Target': {'Id': '10.20.1.42', 'Port': 443}}]
    root = validate_document(generate_document(snapshot, 'vpc-demo'))
    target = next(o for o in root.findall('.//object') if '/registered_target/' in o.get('resourceId', ''))
    assert target.get('label') == '10.20.1.42:443'
    assert target.find('mxCell').get('parent') == 'references'


def test_empty_unknown_scope_and_xml_labels():
    snapshot = demo_snapshot()
    label = '<script>"& 검토</script>'
    snapshot['raw']['instances'][0]['Tags'][0]['Value'] = label
    root = validate_document(generate_document(snapshot))
    assert any(o.get('label') == label for o in root.findall('.//object'))
    with pytest.raises(ValueError):
        generate_document(snapshot, 'vpc-missing')
    snapshot['raw'] = {}
    assert ET.fromstring(generate_document(snapshot)).tag == 'mxfile'


@pytest.mark.parametrize('xml', [
    '<html/>', '<mxfile><diagram>compressed</diagram></mxfile>',
    '<!DOCTYPE mxGraphModel [<!ENTITY x SYSTEM "file:///etc/passwd">]><mxGraphModel>&x;</mxGraphModel>',
    '<mxGraphModel>',
])
def test_reject_invalid_or_unsafe_xml(xml):
    with pytest.raises(Exception):
        validate_document(xml)


def test_editor_checksum_failure_keeps_existing_files(tmp_path):
    archive = tmp_path / 'invalid.war'
    archive.write_bytes(b'not the official archive')
    destination = tmp_path / 'editor'
    destination.mkdir()
    marker = destination / 'keep.txt'
    marker.write_text('existing')
    with pytest.raises(ValueError, match='SHA-256'):
        install_editor(archive, destination)
    assert marker.read_text() == 'existing'
