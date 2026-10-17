"""Browser acceptance test. Run against a fresh local Diagram Bot workspace."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from PIL import Image
from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8765')
    parser.add_argument('--output', type=Path, default=Path('artifacts/browser'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    errors, external = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1600, 'height': 1100}, device_scale_factor=1)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda req: external.append(req.url) if req.url.startswith(('http:', 'https:')) and urlparse(req.url).netloc != urlparse(args.url).netloc else None)
        page.goto(args.url)
        expect(page.locator('#save-button')).to_be_enabled(timeout=60000)
        frame = page.frame_locator('#editor')
        frame.get_by_text('web-a', exact=True).click()
        expect(page.locator('#detail-body')).to_contain_text('i-demo-a')
        expect(page.locator('#detail-body')).to_contain_text('DescribeInstances.SubnetId')
        page.screenshot(path=str(args.output / 'workspace.png'), full_page=True)

        page.locator('#coverage-button').click()
        expect(page.locator('#coverage-dialog')).to_contain_text('UnauthorizedOperation')
        page.locator('#coverage-dialog [aria-label="닫기"]').click()
        page.locator('#inventory-tab').click()
        page.locator('#search').fill('web-a')
        expect(page.locator('#inventory-rows tr')).to_have_count(1)
        page.locator('#search').fill('')
        page.locator('#kind-filter').select_option('ec2')
        expect(page.locator('#inventory-rows tr')).to_have_count(2)
        page.locator('#kind-filter').select_option('all')
        page.locator('#diagram-tab').click()

        page.locator('[data-vpc="vpc-staging"]').click()
        expect(page.locator('#save-button')).to_be_enabled()
        expect(frame.get_by_text('staging-api', exact=True)).to_be_visible()
        page.locator('[data-vpc="vpc-demo"]').click()
        expect(page.locator('#save-button')).to_be_enabled()
        page.locator('#references').check()
        expect(page.locator('#save-button')).to_be_enabled()
        expect(frame.get_by_text('sg-web', exact=True)).to_be_visible()
        page.locator('#references').uncheck()
        expect(page.locator('#save-button')).to_be_enabled()

        # Use native draw.io editing, not a test-only API to change the document.
        page.locator('#note-button').click()
        note = frame.locator('.geDiagramContainer').get_by_text('검토 메모', exact=False).last
        note.dblclick()
        editable = frame.locator('[contenteditable="true"]').last
        expect(editable).to_be_visible()
        editable.fill('시연 검토 완료 · 사용자 메모')
        editable.press('Escape')
        page.locator('#save-button').click()
        expect(page.locator('#save-state')).to_contain_text('저장됨')
        page.reload()
        expect(page.locator('#save-button')).to_be_enabled(timeout=60000)
        frame = page.frame_locator('#editor')
        expect(frame.get_by_text('시연 검토 완료 · 사용자 메모', exact=True)).to_be_visible()
        page.locator('#export-button').click()
        with page.expect_download() as event:
            page.locator('#download-drawio').click()
        event.value.save_as(args.output / 'topology.drawio')
        xml = (args.output / 'topology.drawio').read_text(encoding="utf-8")
        root = ET.fromstring(xml)
        assert root.find('diagram/mxGraphModel') is not None
        assert '시연 검토 완료' in xml
        assert 'resourceId=' in xml and 'relationIndex=' in xml
        with page.expect_download(timeout=60000) as event:
            page.locator('#download-png').click()
        event.value.save_as(args.output / 'topology.png')
        with Image.open(args.output / 'topology.png') as img:
            double_size = img.size
            assert img.format == 'PNG' and min(img.size) > 500
            assert img.convert('RGB').getextrema()[0][0] < 150
            assert img.convert('RGBA').getpixel((0, 0))[3] == 255
        page.locator('#png-scale').select_option('1')
        page.locator('#png-transparent').check()
        with page.expect_download(timeout=60000) as event:
            page.locator('#download-png').click()
        event.value.save_as(args.output / 'topology-transparent.png')
        with Image.open(args.output / 'topology-transparent.png') as img:
            assert all(abs(big - small * 2) <= 2 for big, small in zip(double_size, img.size))
            assert img.convert('RGBA').getpixel((0, 0))[3] == 0
        with page.expect_download() as event:
            page.locator('#download-json').click()
        event.value.save_as(args.output / 'snapshot.json')
        snapshot = json.loads((args.output / 'snapshot.json').read_text(encoding="utf-8"))
        assert snapshot['metadata']['demo'] is True
        assert '시연 검토 완료' not in json.dumps(snapshot, ensure_ascii=False)
        with page.expect_download() as event:
            page.locator('#download-csv').click()
        event.value.save_as(args.output / 'inventory.csv')
        assert 'i-demo-a' in (args.output / 'inventory.csv').read_text(encoding='utf-8-sig')
        page.locator('#export-dialog [aria-label="닫기"]').click()
        page.screenshot(path=str(args.output / 'workspace-edited.png'), full_page=True)
        page.locator('#import-file').set_input_files(args.output / 'snapshot.json')
        expect(page.locator('#snapshot-select option')).to_have_count(2)
        expect(page.locator('#save-button')).to_be_enabled()
        expect(page.locator('#save-state')).to_have_text('자동 생성 구성도')
        assert not errors, errors
        assert not external, external
        browser.close()
    report = {'browser': 'Chromium', 'page_errors': errors, 'external_requests': external, 'png_2x_size': double_size,
              'checks': ['native selection', 'coverage', 'inventory search/type', 'VPC switch', 'references',
                         'native note edit', 'save/reload', 'drawio XML download', 'PNG 2x white', 'PNG 1x transparent',
                         'JSON/CSV exports', 'snapshot import']}
    (args.output / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
