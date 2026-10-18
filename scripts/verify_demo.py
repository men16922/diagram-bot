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

        def shot(name):
            # Clicking canvas items scrolls the page; capture from the top so the fixed header sits where users see it.
            page.evaluate('window.scrollTo(0, 0)')
            page.screenshot(path=str(args.output / name), full_page=True)
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
        shot('overview.png')
        detail.get_by_role('button', name='상세 구성 보기').click()
        ready()
        expect(page.locator('#view-detail')).to_have_class('view-button active')
        expect(page.locator('#environment-title')).to_have_text('주문 서비스 운영 네트워크')
        start_scale = page.evaluate("() => document.getElementById('editor').contentWindow.diagramBotGraph.view.scale")
        assert start_scale >= 0.6, f'detail view opens at an illegible zoom: {start_scale:.2f}'

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
        shot('workspace.png')
        detail.get_by_role('button', name='선택 리소스 보기').click()
        ready()
        expect(page.locator('#view-focus')).to_have_class('view-button active')
        expect(page.locator('#view-scope')).to_contain_text('표시 5개')
        expect(frame.get_by_text(starts('demo-api'))).to_be_visible()
        expect(frame.get_by_text(named('web-a'))).to_have_count(0)
        shot('focus.png')
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
        with page.expect_download() as event:
            page.locator('#download-findings').click()
        event.value.save_as(args.output / 'findings.csv')
        findings_csv = (args.output / 'findings.csv').read_text(encoding='utf-8-sig')
        assert findings_csv.startswith('"severity",') and '높음' in findings_csv and 'SEC-SG-OPEN' in findings_csv
        page.locator('#export-dialog [aria-label="닫기"]').click()
        shot('workspace-edited.png')

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
        shot('review.png')
        page.locator('#diagram-tab').click()

        # 8. Infrastructure sample: Auto Scaling frame, RDS and route-classified subnets.
        page.locator('#snapshot-select').select_option('sample-three-tier')
        ready()
        # One VPC without TGW, peering or VPN: the snapshot opens in its detail view, not a one-card overview.
        expect(page.locator('#view-detail')).to_have_class('view-button active')
        # The review step's "hidden in this view" notice belongs to the previous snapshot and must not linger.
        expect(page.locator('#toast')).to_be_hidden()
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
        shot('infrastructure.png')
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
        shot('eks-platform.png')
        # 8b. Route filter: the microservices detail (one ALB, four target groups) narrowed to one target group's routes;
        # the others are hidden and counted, off-path resources dimmed; the inspector switches back and forth.
        page.locator('#snapshot-select').select_option('sample-microservices')
        ready()
        expect(page.locator('#view-detail')).to_have_class('view-button active')
        expect(page.locator('#route-filter-label')).to_be_visible()
        expect(frame.get_by_text(starts('catalog-svc'))).to_have_count(1)
        page.locator('#route-filter').select_option(label='cart-svc-ecs')
        ready()
        expect(page.locator('#view-scope')).to_contain_text('경로 필터')
        expect(page.locator('#environment-title')).to_have_text('commerce-services')
        expect(frame.get_by_text(starts('catalog-svc'))).to_have_count(0)
        filtered_xml = page.evaluate('() => state.xml')
        assert 'routes="' in filtered_xml and '경로 필터: 대상 그룹 cart-svc-ecs' in filtered_xml and 'opacity=30' in filtered_xml
        shot('route-filter.png')
        frame.get_by_text(starts('cart-svc-ecs')).first.click()
        detail.get_by_role('button', name='모든 경로 보기').click()
        ready()
        expect(page.locator('#route-filter')).to_have_value('')
        expect(page.locator('#view-scope')).not_to_contain_text('경로 필터')
        frame.get_by_text(starts('orders-svc')).first.click()
        detail.get_by_role('button', name='이 경로만 보기').click()
        ready()
        expect(page.locator('#route-filter')).to_have_value(re.compile('targetgroup/orders-svc/'))
        expect(frame.get_by_text(starts('cart-svc-ecs'))).to_have_count(0)
        page.locator('#view-overview').click()
        ready()
        expect(page.locator('#route-filter-label')).to_be_hidden()
        # 9. Data platform: ECS service frame, OpenSearch per-AZ nodes, read replica, then the TGW VPNs from the analytics VPC.
        page.locator('#snapshot-select').select_option('sample-data-platform')
        ready()
        # Connected VPCs open on the overview, which draws the Transit Gateway and the customer gateways.
        expect(page.locator('#view-overview')).to_have_class('view-button active')
        frame.get_by_text(starts('data-tgw')).click()
        expect(detail).to_contain_text('Transit Gateway')
        frame.get_by_text(starts('corp-dc-cgw-2')).click()
        expect(detail).to_contain_text('198.51.100.20')
        shot('network-overview.png')
        page.locator('[data-vpc="vpc-0a1b2c3d4e5f60007"]').click()
        ready()
        frame.get_by_text(starts('ECS service · data-api')).click()
        expect(detail).to_contain_text('실행 3 / 원하는 3')
        expect(detail).to_contain_text('ECS 서비스 태스크')
        frame.get_by_text(starts('logs-search')).last.click()
        expect(detail).to_contain_text('data 2 · master 1 · standby')
        frame.get_by_text(starts('catalog-db-ro')).click()
        expect(detail).to_contain_text('읽기 복제본 원본')
        shot('data-platform.png')
        page.locator('[data-vpc="vpc-0a1b2c3d4e5f60008"]').click()
        ready()
        # Both VPCs reach on premises through the Transit Gateway: two customer gateways, each with a two-tunnel VPN.
        for cgw in ('corp-dc-cgw-1', 'corp-dc-cgw-2'):
            frame.get_by_text(starts(cgw)).click()
            expect(detail).to_contain_text('2/2 UP')
            expect(detail).to_contain_text('Site-to-Site VPN')

        # 9b. AI platform: private VPC, GPU inference across AZs, single-AZ EFA training, Bedrock endpoint, GPU review rules.
        page.locator('#snapshot-select').select_option('sample-ai-platform')
        ready()
        page.locator('[data-vpc="vpc-0a1b2c3d4e5f60009"]').click()
        ready()
        frame.get_by_text(starts('gpu-train-1')).click()
        expect(detail).to_contain_text('p5.48xlarge')
        expect(detail).to_contain_text('subnet-ai-train-a')
        frame.get_by_text(starts('bedrock-runtime')).click()
        expect(detail).to_contain_text('com.amazonaws.ap-northeast-2.bedrock-runtime')
        frame.get_by_text(starts('EKS node group · gpu-inference')).click()
        expect(detail).to_contain_text('EKS 노드 그룹')
        shot('ai-platform.png')
        page.locator('#review-tab').click()
        expect(page.locator('#review-rows')).to_contain_text('학습 노드 그룹이 한 AZ에만 있음')
        page.locator('#diagram-tab').click()

        # 9c. GPU training: EFA/Capacity Block/placement group labels, FSx for Lustre and HyperPod in the compute subnet; AI review pillar.
        page.locator('#snapshot-select').select_option('sample-gpu-training')
        ready()
        expect(page.locator('#view-detail')).to_have_class('view-button active')
        frame.get_by_text(starts('p5-train-1')).click()
        expect(detail).to_contain_text('Capacity Block')
        expect(detail).to_contain_text('EFA 인터페이스 있음')
        frame.get_by_text(starts('trn2-train-1')).click()
        expect(detail).to_contain_text('trn2-pg')
        frame.get_by_text(starts('training-data')).click()
        expect(detail).to_contain_text('FSx file system')
        frame.get_by_text(starts('hp-slurm')).click()
        expect(detail).to_contain_text('SageMaker HyperPod cluster')
        shot('gpu-training.png')
        page.locator('#snapshot-select').select_option('sample-genai-rag')
        ready()
        frame.get_by_text(starts('rag-reranker')).click()
        expect(detail).to_contain_text('SageMaker endpoint')
        shot('genai-rag.png')

        # 9d. Multi-account: account frames around VPC cards meeting at the Network account's shared Transit Gateway,
        # then two account snapshots imported and merged through the 계정 통합 보기 dialog.
        page.locator('#snapshot-select').select_option('sample-multi-account')
        ready()
        expect(page.locator('#view-overview')).to_have_class('view-button active')
        expect(frame.get_by_text(starts('AWS 계정 · 222222222222'))).to_be_visible()
        frame.get_by_text(starts('org-tgw')).click()
        expect(detail).to_contain_text('111111111111')
        shot('multi-account.png')
        from diagram_bot.samples import SAMPLES
        for i, part in enumerate(SAMPLES['multi-account']()['parts'][1:]):
            (args.output / f'account-{i}.json').write_text(json.dumps(part), encoding='utf-8')
            page.locator('#import-file').set_input_files(args.output / f'account-{i}.json')
            ready()
        page.locator('#combine-button').click()
        page.locator('#combine-rows label', has_text='222222222222').locator('input').check()
        page.locator('#combine-rows label', has_text='333333333333').locator('input').check()
        page.locator('#combine-title').fill('검증 조직')
        page.locator('#combine-submit').click()
        ready()
        expect(page.locator('#toast')).to_contain_text('계정 통합 보기')
        expect(frame.get_by_text(starts('AWS 계정 · 333333333333'))).to_be_visible()

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
        shot('changes.png')

        # 11. Console shell: profile menu, sign-in dialog, docked assistant reflowing the content, responsive widths.
        page.locator('#diagram-tab').click()
        ready()
        page.locator('#profile-button').click()
        expect(page.locator('#profile-menu')).to_be_visible()
        expect(page.locator('#profile-label')).to_have_text('로그인')
        page.locator('.breadcrumbs').click()
        expect(page.locator('#profile-menu')).to_be_hidden()
        page.locator('#profile-button').click()
        page.locator('#signin-button').click()
        expect(page.locator('#signin-dialog')).to_be_visible()
        expect(page.locator('#sso-login')).to_be_visible()
        page.locator('#close-signin').click()
        width = lambda selector: page.evaluate(f"document.querySelector('{selector}').getBoundingClientRect().width")
        content, canvas = width('.content'), width('#editor')
        page.locator('#chat-button').click()
        expect(page.locator('#chat-panel')).to_be_visible()
        expect(page.locator('#chat-button')).to_have_attribute('aria-expanded', 'true')
        page.wait_for_timeout(400)  # the content margin animates for 0.2 s
        panel = width('#chat-panel')
        assert abs((content - width('.content')) - panel) < 2 and width('#editor') < canvas, (content, width('.content'), panel)
        page.locator('.chat-suggestion').first.click()
        expect(page.locator('#chat-input')).to_have_value(page.locator('.chat-suggestion').first.inner_text())
        page.locator('#chat-close').click()
        expect(page.locator('#chat-panel')).to_be_hidden()
        page.wait_for_timeout(400)
        assert abs(width('.content') - content) < 2
        for viewport in (1280, 980, 640):
            page.set_viewport_size({'width': viewport, 'height': 900})
            page.wait_for_timeout(300)
            overflow = page.evaluate('document.documentElement.scrollWidth - window.innerWidth')
            assert overflow <= 1, (viewport, overflow)
            stacked = page.evaluate("getComputedStyle(document.querySelector('.side-nav')).position") == 'static'
            assert stacked == (viewport <= 980), (viewport, stacked)
        page.set_viewport_size({'width': 1600, 'height': 1100})

        # 12. SSO sign-in through the profile menu against a stand-in Identity Center API (the live device-code flow was
        # verified on 2026-10-17): device code, approval, roles, `role @ account (SSO)`, then the collection request
        # carries the SSO session and role. The collection itself is intercepted, never sent to AWS.
        polls, collected, signed_out = [], [], []
        def fake_sso(route):
            url, method = route.request.url, route.request.method
            if url.endswith('/api/sso/defaults'):
                return route.fulfill(json=[{'name': 'corp', 'start_url': 'https://d-0000000000.awsapps.com/start', 'region': 'us-east-1'}])
            if url.endswith('/api/sso/login') and method == 'POST':
                return route.fulfill(json={'id': 'check', 'user_code': 'WXYZ-1234', 'interval': 1, 'status': 'pending',
                                           'verification_uri_complete': 'https://device.sso.us-east-1.amazonaws.com/?user_code=WXYZ-1234'})
            if '/roles' in url:
                return route.fulfill(json={'roles': ['AdministratorAccess', 'CloudOpsReadOnly']})
            if url.endswith('/logout'):
                signed_out.append(url)
                return route.fulfill(json={'logged_out': True})
            polls.append(url)
            return route.fulfill(json={'status': 'pending', 'interval': 1} if len(polls) == 1 else
                                 {'status': 'authorized', 'interval': 1, 'accounts': [{'id': '444455556666', 'name': 'prod'}]})
        def fake_collection(route):
            collected.append(json.loads(route.request.post_data or '{}'))
            route.fulfill(status=422, json={'detail': '검증용: 수집 요청을 가로챘습니다.'})
        page.route('**/api/sso/**', fake_sso)
        page.route('**/api/collections', fake_collection)
        page.locator('#profile-button').click()
        page.locator('#signin-button').click()
        page.locator('#sso-start-url').fill('https://d-0000000000.awsapps.com/start')  # defaults were read when step 11 opened the dialog
        page.locator('#sso-login').click()
        expect(page.locator('#sso-code')).to_have_text('WXYZ-1234')
        expect(page.locator('#sso-role')).to_have_value('CloudOpsReadOnly', timeout=15000)  # read-only role preselected
        page.locator('#sso-use').click()
        expect(page.locator('#profile-label')).to_have_text('CloudOpsReadOnly @ 444455556666 (SSO)')
        page.locator('#profile-button').click()
        expect(page.locator('#profile-detail')).to_contain_text('IAM Identity Center(SSO)')
        page.locator('#collection-button').click()
        expect(page.locator('#collection-identity')).to_contain_text('CloudOpsReadOnly @ 444455556666')
        expect(page.locator('#aws-account')).to_have_value('444455556666')
        page.locator('#start-collection').click()
        expect(page.locator('#collection-progress')).to_contain_text('가로챘습니다')
        assert collected and collected[0]['sso_login'] == 'check' and collected[0]['sso_role'] == 'CloudOpsReadOnly' \
            and collected[0]['expected_account'] == '444455556666' and collected[0]['profile'] is None, collected
        page.locator('#close-collection').click()
        page.locator('#profile-button').click()
        page.locator('#profile-signout').click()
        expect(page.locator('#profile-label')).to_have_text('로그인')
        expect(page.locator('#toast')).to_contain_text('로그아웃했습니다')
        assert signed_out == [signed_out[0]] and signed_out[0].endswith('/api/sso/login/check/logout'), signed_out  # token discarded on the server
        page.unroute('**/api/sso/**')
        page.unroute('**/api/collections')
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
                         'JSON/CSV/review CSV exports', 'relayout keeps note + archives edit', 'restore archived edit',
                         'single-VPC sample opens in detail; infrastructure sample: ASG frame, RDS standby, route-classified subnet',
                         'EKS sample: node group frame, Aurora writer, MSK brokers',
                         'route filter: detail narrowed to one target group (hidden + counted, off-path dimmed), inspector back to all routes and to one route',
                         'network overview (TGW, customer gateways); data platform: ECS service frame, OpenSearch standby AZ, read replica, redundant TGW VPN customer gateways',
                         'AI platform: single-AZ EFA training, Bedrock endpoint, GPU node groups, GPU review',
                         'GPU training: EFA, Capacity Block, placement group, FSx for Lustre, HyperPod; GenAI RAG: SageMaker endpoint',
                         'multi-account: account frames, shared TGW, combine dialog',
                         'snapshot import', 'change review between snapshots',
                         'console shell: profile menu, sign-in dialog, docked assistant reflow, no overflow at 1280/980/640',
                         'SSO sign-in via profile menu (stand-in Identity Center): role @ account, collection carries SSO role']}
    (args.output / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
