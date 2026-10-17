"""Browser acceptance test. Run against a fresh local Diagram Bot workspace."""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from PIL import Image
from playwright.sync_api import expect, sync_playwright


def starts(text):
    """Multi-line draw.io labels render as one element; match the first line."""
    return re.compile('^' + re.escape(text))


def named(text):
    """A resource named `text`: draw.io joins label lines without a separator, so only a longer name is excluded."""
    return re.compile('^' + re.escape(text) + '(?![-_])')


def geometry(xml, resource_suffix):
    for obj in ET.fromstring(xml).iter('object'):
        if obj.get('resourceId', '').endswith(resource_suffix):
            g = obj.find('mxCell/mxGeometry')
            return float(g.get('x')), float(g.get('y'))
    raise AssertionError(resource_suffix)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8765')
    parser.add_argument('--output', type=Path, default=Path('artifacts/browser'))
    parser.add_argument('--chromium', help='Optional preinstalled Chromium executable')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    errors, external = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=args.chromium)
        page = browser.new_page(viewport={'width': 1600, 'height': 1100}, device_scale_factor=1)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda req: external.append(req.url) if req.url.startswith(('http:', 'https:')) and urlparse(req.url).netloc != urlparse(args.url).netloc else None)
        ready = lambda: expect(page.locator('#save-button')).to_be_enabled(timeout=60000)
        page.goto(args.url)
        ready()
        frame = page.frame_locator('#editor')
        detail = page.locator('#detail-body')

        # 1. Overview: summary cards, counts and partial coverage are visible; a VPC card leads to detail.
        expect(page.locator('#view-overview')).to_have_class('view-button active')
        expect(page.locator('#partial-chip')).to_be_visible()
        expect(page.locator('#view-scope')).to_contain_text('수집 20개')
        frame.get_by_text(starts('VPC · 주문 서비스 운영 네트워크')).click()
        expect(detail).to_contain_text('vpc-demo')
        page.screenshot(path=str(args.output / 'overview.png'), full_page=True)
        detail.get_by_role('button', name='상세 구성 보기').click()
        ready()
        expect(page.locator('#view-detail')).to_have_class('view-button active')
        expect(page.locator('#environment-title')).to_have_text('주문 서비스 운영 네트워크')

        # 2. ALB: target groups, registered targets and API evidence.
        frame.get_by_text(starts('demo-alb')).click()
        expect(detail).to_contain_text('DescribeTargetGroups.LoadBalancerArns[]')
        expect(detail).to_contain_text('서브넷 배치 · public-a · ap-northeast-2a')
        detail.get_by_role('button', name='직접 관계 강조').click()
        expect(page.locator('#toast')).to_contain_text('직접 관계 2개')
        frame.get_by_text(starts('demo-web')).click()
        expect(detail).to_contain_text('대상 등록 · web-a · :8443')
        expect(detail).to_contain_text('DescribeTargetHealth.Target.Id')

        # 3. Detail placement of an instance, then the focused-resource view and back.
        frame.get_by_text(named('web-c')).click()
        expect(detail).to_contain_text('subnet-app-c')
        expect(detail).to_contain_text('DescribeInstances.SubnetId')
        page.screenshot(path=str(args.output / 'workspace.png'), full_page=True)
        detail.get_by_role('button', name='선택 리소스 보기').click()
        ready()
        expect(page.locator('#view-focus')).to_have_class('view-button active')
        expect(page.locator('#view-scope')).to_contain_text('표시 5개')
        expect(frame.get_by_text(starts('demo-api'))).to_be_visible()
        expect(frame.get_by_text(named('web-a'))).to_have_count(0)
        page.screenshot(path=str(args.output / 'focus.png'), full_page=True)
        detail.get_by_role('button', name='상세 구성으로 돌아가기').click()
        ready()

        # 4. Partial collection and reference-only resources.
        page.locator('#coverage-button').click()
        expect(page.locator('#coverage-dialog')).to_contain_text('UnauthorizedOperation')
        expect(page.locator('#coverage-dialog')).to_contain_text('ec2:DescribeInstances')
        page.locator('#coverage-dialog [aria-label="닫기"]').click()
        frame.get_by_text(named('i-demo-missing')).click()
        expect(detail).to_contain_text('참조만 확인')
        page.locator('#inventory-tab').click()
        page.locator('#search').fill('web-a')
        expect(page.locator('#inventory-rows tr')).to_have_count(1)
        page.locator('#search').fill('')
        page.locator('#kind-filter').select_option('ec2')
        expect(page.locator('#inventory-rows tr')).to_have_count(4)
        page.locator('#kind-filter').select_option('all')
        page.locator('#diagram-tab').click()
        page.locator('[data-vpc="vpc-staging"]').click()
        ready()
        expect(frame.get_by_text(named('staging-api'))).to_be_visible()
        page.locator('[data-vpc="vpc-demo"]').click()
        ready()
        page.locator('#references').check()
        ready()
        expect(frame.get_by_text(named('demo-web-sg'))).to_be_visible()
        page.locator('#references').uncheck()
        ready()

        # 5. Native edits: move an instance with the keyboard and write a review note.
        generated = page.evaluate("fetch('/api/snapshots/demo/document?view=detail&vpc=vpc-demo&references=false').then(r => r.json())")
        before = geometry(generated['xml'], '/ec2/i-demo-c')
        frame.get_by_text(named('web-c')).click()
        for _ in range(4):
            frame.locator('.geDiagramContainer').press('Shift+ArrowRight')
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
        ready()
        page.locator('[data-vpc="vpc-demo"]').click()
        ready()
        frame = page.frame_locator('#editor')
        expect(frame.get_by_text('시연 검토 완료 · 사용자 메모', exact=True)).to_be_visible()

        # 6. The same edited document goes to .drawio and PNG.
        page.locator('#export-button').click()
        with page.expect_download() as event:
            page.locator('#download-drawio').click()
        event.value.save_as(args.output / 'topology.drawio')
        xml = (args.output / 'topology.drawio').read_text(encoding='utf-8')
        root = ET.fromstring(xml)
        assert root.find('diagram/mxGraphModel') is not None
        assert '시연 검토 완료' in xml and 'relationId=' in xml and 'layout="diagram-bot-layered/2"' in xml
        moved = geometry(xml, '/ec2/i-demo-c')
        assert moved[0] > before[0] and moved[1] == before[1], (before, moved)
        with page.expect_download(timeout=60000) as event:
            page.locator('#download-png').click()
        event.value.save_as(args.output / 'topology.png')
        with Image.open(args.output / 'topology.png') as img:
            double_size = img.size
            assert img.format == 'PNG' and min(img.size) > 500
            assert img.convert('RGB').getextrema()[0][0] < 150
            assert img.convert('RGBA').getpixel((0, 0))[3] == 255
        for scale in ('1', '3'):
            page.locator('#png-scale').select_option(scale)
            page.locator('#png-transparent').set_checked(scale == '1')
            with page.expect_download(timeout=60000) as event:
                page.locator('#download-png').click()
            name = 'topology-transparent.png' if scale == '1' else 'topology-3x.png'
            event.value.save_as(args.output / name)
            with Image.open(args.output / name) as img:
                factor = int(scale) / 2
                assert all(abs(big * factor - small) <= 3 for big, small in zip(double_size, img.size)), (double_size, img.size)
                assert img.convert('RGBA').getpixel((0, 0))[3] == (0 if scale == '1' else 255)
        with page.expect_download() as event:
            page.locator('#download-json').click()
        event.value.save_as(args.output / 'snapshot.json')
        snapshot = json.loads((args.output / 'snapshot.json').read_text(encoding='utf-8'))
        assert snapshot['metadata']['demo'] is True
        assert '시연 검토 완료' not in json.dumps(snapshot, ensure_ascii=False)
        with page.expect_download() as event:
            page.locator('#download-csv').click()
        event.value.save_as(args.output / 'inventory.csv')
        assert 'i-demo-a' in (args.output / 'inventory.csv').read_text(encoding='utf-8-sig')
        page.locator('#export-dialog [aria-label="닫기"]').click()
        page.screenshot(path=str(args.output / 'workspace-edited.png'), full_page=True)

        # 7. Explicit relayout keeps the note and archives the moved layout; restore brings it back.
        page.locator('#reset-button').click()
        expect(page.locator('#toast')).to_contain_text('이전 편집본은 보관')
        ready()
        expect(frame.get_by_text('시연 검토 완료 · 사용자 메모', exact=True)).to_be_visible()
        relaid = page.evaluate("fetch('/api/snapshots/demo/document?view=detail&vpc=vpc-demo&references=false').then(r => r.json())")
        assert geometry(relaid['xml'], '/ec2/i-demo-c') == before and relaid['history']
        page.locator('#restore-button').click()
        expect(page.locator('#toast')).to_contain_text('복원했습니다')
        ready()
        restored = page.evaluate("fetch('/api/snapshots/demo/document?view=detail&vpc=vpc-demo&references=false').then(r => r.json())")
        assert geometry(restored['xml'], '/ec2/i-demo-c') == moved

        # 7b. Architecture review: severity summary, evidence, and a finding opens its resource.
        page.locator('#review-tab').click()
        expect(page.locator('#review-summary')).to_contain_text('높음 1')
        expect(page.locator('#review-rows')).to_contain_text('DescribeSecurityGroups.IpPermissions')
        page.locator('#review-rows .finding').first.click()
        expect(detail).to_contain_text('demo-web-sg')
        page.locator('#severity-filter').select_option('low')
        expect(page.locator('#review-rows')).to_contain_text('ec2:DescribeInstances')
        page.screenshot(path=str(args.output / 'review.png'), full_page=True)
        page.locator('#diagram-tab').click()

        # 8. Infrastructure sample: Auto Scaling frame, RDS and route-classified subnets.
        page.locator('#snapshot-select').select_option('sample-three-tier')
        ready()
        page.locator('[data-vpc="vpc-0a1b2c3d4e5f60001"]').click()
        ready()
        frame.get_by_text(starts('Auto Scaling group · prod-web-asg')).click()
        expect(detail).to_contain_text('Auto Scaling group')
        expect(detail).to_contain_text('3–9 (원하는 3)')
        expect(detail).to_contain_text('DescribeAutoScalingGroups.TargetGroupARNs[]')
        frame.get_by_text(starts('prod-orders-db')).first.click()
        expect(detail).to_contain_text('mysql')
        expect(detail).to_contain_text('Multi-AZ 대기')
        frame.get_by_text(starts('public-a')).click()
        expect(detail).to_contain_text('퍼블릭 · 0.0.0.0/0 → IGW')
        page.screenshot(path=str(args.output / 'infrastructure.png'), full_page=True)
        page.locator('#snapshot-select').select_option('sample-eks-platform')
        ready()
        page.locator('[data-vpc="vpc-0a1b2c3d4e5f60006"]').click()
        ready()
        frame.get_by_text(starts('EKS node group · general')).click()
        expect(detail).to_contain_text('EKS 노드 그룹')
        frame.get_by_text(starts('platform-aurora-1')).click()
        expect(detail).to_contain_text('writer')
        frame.get_by_text(starts('platform-events')).first.click()
        expect(detail).to_contain_text('MSK 클러스터')
        page.screenshot(path=str(args.output / 'eks-platform.png'), full_page=True)
        # 9. Data platform: ECS service frame, OpenSearch per-AZ nodes, read replica, then VPN in the peered VPC.
        page.locator('#snapshot-select').select_option('sample-data-platform')
        ready()
        page.locator('[data-vpc="vpc-0a1b2c3d4e5f60007"]').click()
        ready()
        frame.get_by_text(starts('ECS service · data-api')).click()
        expect(detail).to_contain_text('실행 3 / 원하는 3')
        expect(detail).to_contain_text('ECS 서비스 태스크')
        frame.get_by_text(starts('logs-search')).last.click()
        expect(detail).to_contain_text('data 2 · master 1 · standby')
        frame.get_by_text(starts('catalog-db-ro')).click()
        expect(detail).to_contain_text('읽기 복제본 원본')
        page.screenshot(path=str(args.output / 'data-platform.png'), full_page=True)
        page.locator('[data-vpc="vpc-0a1b2c3d4e5f60008"]').click()
        ready()
        frame.get_by_text(starts('corp-dc-cgw')).click()
        expect(detail).to_contain_text('2/2 UP')
        expect(detail).to_contain_text('Site-to-Site VPN')

        options = page.locator('#snapshot-select option').count()
        page.locator('#import-file').set_input_files(args.output / 'snapshot.json')
        expect(page.locator('#snapshot-select option')).to_have_count(options + 1)
        ready()
        expect(page.locator('#save-state')).to_have_text('자동 생성 구성도')
        # 10. Change review: an imported snapshot with one changed setting compared with the original.
        changed = json.loads((args.output / 'snapshot.json').read_text(encoding='utf-8'))
        next(i for i in changed['raw']['instances'] if i['InstanceId'] == 'i-demo-a')['InstanceType'] = 't3.large'
        changed.pop('graph', None)
        (args.output / 'changed-snapshot.json').write_text(json.dumps(changed, ensure_ascii=False), encoding='utf-8')
        page.locator('#import-file').set_input_files(args.output / 'changed-snapshot.json')
        expect(page.locator('#snapshot-select option')).to_have_count(options + 2)
        ready()
        page.locator('#changes-tab').click()
        page.locator('#compare-select').select_option('demo')
        expect(page.locator('#changes-summary')).to_contain_text('설정 변경 1')
        expect(page.locator('#changes-rows')).to_contain_text('InstanceType: t3.small → t3.large')
        page.screenshot(path=str(args.output / 'changes.png'), full_page=True)
        assert not errors, errors
        assert not external, external
        browser.close()
    report = {'browser': 'Chromium', 'page_errors': errors, 'external_requests': external, 'png_2x_size': double_size,
              'moved_instance': {'before': before, 'after': moved},
              'checks': ['overview summary + counts + partial chip', 'VPC card -> detail view', 'ALB relations + evidence',
                         'relation highlight', 'multi-port registration', 'instance AZ/subnet placement', 'focus view + return',
                         'coverage + IAM permission', 'reference-only target', 'inventory search/type', 'VPC switch',
                         'SG/ENI/EBS references', 'native keyboard move', 'native note edit', 'save/reload',
                         'drawio XML download with moved geometry', 'PNG 2x white', 'PNG 1x transparent', 'PNG 3x',
                         'JSON/CSV exports', 'relayout keeps note + archives edit', 'restore archived edit',
                         'infrastructure sample: ASG frame, RDS standby, route-classified subnet',
                         'EKS sample: node group frame, Aurora writer, MSK brokers',
                         'data platform: ECS service frame, OpenSearch standby AZ, read replica, VPN customer gateway',
                         'snapshot import', 'change review between snapshots']}
    (args.output / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
