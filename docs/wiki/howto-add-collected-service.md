---
title: 수집 서비스 추가 방법
summary: 새 AWS 읽기 호출을 수집기부터 정책·그래프·라벨·보기·배치·검토·CloudFormation·샘플·테스트·문서까지 잇는 순서. Direct Connect와 Elastic IP를 추가한 방식을 기준으로 한다.
sources: [src/diagram_bot/collector.py, readonly-policy.json, deploy/readonly-role.cfn.json, src/diagram_bot/graph.py, src/diagram_bot/static/js/labels.js, src/diagram_bot/views.py, src/diagram_bot/layout.py, src/diagram_bot/drawio.py, src/diagram_bot/findings.py, src/diagram_bot/iac/, src/diagram_bot/diff.py, src/diagram_bot/samples/, tests/test_collector.py, tests/test_aws_emulator.py, tests/test_graph_report.py, tests/test_drawio.py, tests/test_findings.py, tests/test_iac.py, tests/fixtures.py]
related: [collection, graph, views, layout-and-drawio, samples, snapshot-model]
updated: 2026-10-18
---
# 수집 서비스 추가 방법

> 새 읽기 호출 하나는 수집기·정책 두 개·그래프·화면 라벨·보기 분류·배치·(검토 규칙)·CloudFormation·샘플·테스트·문서를 함께 바꿔야
> 끝난다. 아래 순서는 가장 최근에 추가한 Direct Connect(연결·VIF·게이트웨이·게이트웨이 연결)와 Elastic IP(`describe_addresses`)를
> 그대로 따른다.

## 핵심
- 원칙: 읽기 API만, API 필드로만 조인, 거부·부분 결과도 coverage에 남긴다([[collection]], [[graph]]).
- 각 단계에 짝이 되는 테스트가 있다. 하나라도 빠지면 `make check`(ruff 린트 + 오프라인 pytest + CLI 보고서 왕복)가 실패하거나, 실패하지 않더라도 화면에서 리소스가 조용히 사라진다.

## 1. 수집기 (`collector.py`)
1. 목록 호출은 `OPERATIONS`에 `(service, operation, 응답 키, raw 이름)`을 넣는다. Direct Connect 예: `("directconnect", "describe_connections", "connections", "dx_connections")` 외 2개, EIP 예: `("ec2", "describe_addresses", "Addresses", "elastic_ips")`. 응답 키가 중첩이면 점 표기(`"DistributionList.Items"`).
2. IAM 이름이 boto3 이름과 다르면 `IAM_PREFIX`(서비스 접두사) 또는 `ACTIONS`(대소문자, 예: `ListWebACLs`)에 넣는다. `collector.permission`의 결과가 실제 IAM 액션과 같아야 한다.
3. boto3 paginator가 없고 마커로 넘기는 호출이면 `MARKERS`에 등록한다(`list_web_acls` → `NextMarker`).
4. 리소스별 상세 호출이 필요하면 `collect` 안에 `def 이름(log)` 함수를 만들어 `call(log, service, operation, key, params)`로 부르고 `{raw 키: 행}`을 돌려준 뒤 `details` 튜플에 넣는다. 예: `direct_connect(log)`가 게이트웨이마다 `describe_direct_connect_gateway_associations`. 2단계 입력으로만 쓰는 목록은 반환 전 `raw.pop` 목록에 넣는다.
5. 전역 API는 클라이언트 리전을 정한다. 리전 무관 응답이면 그대로(CloudFront), 특정 리전에서만 응답하면 `clients["…/global"]`처럼 별도 클라이언트와 `GLOBAL_REGION`(WAF CLOUDFRONT 범위).
6. `metadata.scope`에 유형 이름(예: `"DirectConnect"`, `"ElasticIP"`)을, 해석 한계가 있으면 `limitations`에 문장을 더한다.

## 2. 정책 파일
- `readonly-policy.json`의 `Action`과 `deploy/readonly-role.cfn.json`의 `CollectionRole` 인라인 정책(알파벳 순, `sts:GetCallerIdentity` 포함)에 **목록·상세 호출 모두**의 액션을 넣는다.
- 지키는 테스트: `test_deploy_template_grants_exactly_the_collector_actions`(템플릿 = 정책 ∪ `sts:GetCallerIdentity`, 액션은 `Describe`/`List`/`Get`/`Search`로만 시작), `test_edge_routing_and_direct_connect_calls_keep_partial_and_denied_results`(실제 coverage의 `permission` ⊆ 정책). 2단계 호출은 `OPERATIONS`에 없으므로 둘째 테스트처럼 실제로 호출되는 시나리오가 있어야 확인된다.

## 3. 그래프 (`graph.py`)
1. 서비스별 함수를 만든다. 예: `direct_connect(raw, region, nodes, node, link)`, `elastic_ips(raw, nodes, node, link)`. `build_graph`의 호출 순서에 넣는다(다른 노드를 참조하면 그 뒤에).
2. `node(kind, ID, {**attrs, "DisplayName": …}, "근거API")`로 노드를 만든다. 삭제·거부 상태는 건너뛴다(`direct_connect`의 `connectionState` 검사).
3. `link(source, kind, ID, relation, "API.필드" 근거, details, qualifiers)`로 잇는다. 같은 두 노드 사이에 관계가 여럿일 수 있으면 `qualifiers={"destination": …}`를 준다(VIF ID, 경로 CIDR, 오리진 ID). 상태처럼 바뀌는 값은 `details`에만.
4. 짐작하지 않는다: 근거 필드가 없으면 관계를 만들지 않고 `properties(node, (라벨, 값))`나 파생 필드로 남긴다(다른 리전 DX 연결, 연결 없는 EIP). EIP처럼 도형 대신 소유자 라벨에 보일 값은 소유자 노드에 파생 필드(`elastic_ips`)로 쓴다. 수집 노드를 다시 쓰면 파생 필드가 사라지므로 모든 노드를 만든 뒤에 쓴다.
5. 계정 전역 리소스면 `GLOBAL_KINDS`, 계정 간 공유되는 리소스면 `SHARED_KINDS`에 넣는다.

## 4. 화면 라벨 (`static/js/labels.js`, `drawio.py`)
- `names`에 종류, `relationNames`에 관계의 한국어 표시 이름. 빠지면 화면이 원래 키를 그대로 보여 주고 `tests/test_labels_and_wiki.py`가 실패한다([[graph]]).
- draw.io 관계 셀 이름은 `drawio.RELATIONS`(그려지는 관계만). 속성 패널에 보일 값은 `properties`로 충분하고, 특수 필드는 `static/app.js`의 `fields` 목록.

## 5. 보기 분류 (`views.py`)
새 종류·관계가 어디에 속하는지 정한다([[views]]).
- 서브넷 안에 그릴 종류 → `SUBNET_KINDS`. 도형 없이 다른 것의 라벨·배지로만 보일 종류 → `UNDRAWN_KINDS`(EIP, 웹 ACL, TGW 라우팅 테이블).
- VPC 상세에 함께 그릴 리전 연결 → `REGIONAL`, AWS 밖으로 나가면 `REMOTE`와 `SITE_KINDS`(DX: `dx_virtual_interface`, `dx_connection`). 전체 구성 허브면 `HUB_ORDER`와 `overview_links`(DX: `dx_gateway`, `dx_gateway_association`). 리전 밖 엣지 → `EDGE`.

## 6. 배치·아이콘 (`layout.py`, `drawio.py`)
- `layout.resource_label`에 종류별 라벨(이름 → 유형 → 핵심 사실). DX: `dx_gateway`(ASN), `dx_connection`(대역폭·위치·VIF 수, 참조면 "(참조)"). EIP: NAT·EC2·LB 라벨에 `EIP …`.
- 위치: VPC 연결 행이면 `EDGE_KINDS`, 데이터 센터 상자면 `outside_vpc`(SITE_KINDS로 자동), 전체 구성에서 VPC 밖이 정상인 종류는 `overview`의 `outside` 제외 목록(빠지면 "VPC 외 · 배치 미확인" 카드가 생겨 `test_overview_cards_differ_per_sample_and_hide_drawn_groups`가 실패). 요약 카드 아이콘은 `GLANCE_KINDS`.
- 아이콘: `drawio.RESOURCE_ICONS`(리소스 수준) 또는 `ICONS`(서비스 타일, DX는 `direct_connect`). 변형이 필요하면 `layout.icon_variant`.

## 7. 검토 규칙 (`findings.py`, 있으면)
- API 필드로만 판정하고 근거(`evidence`)에 API 이름을 쓴다. EIP 예: `edge_review`의 `COST-EIP-IDLE`(연결 필드가 모두 없고 `eip_associated`도 없을 때, `low`, `cost`). 조회가 불완전하면 판정하지 않는다(`waf_collected`).
- 테스트: `tests/test_findings.py::test_edge_address_and_routing_rules_need_their_inputs` 같은 "입력이 있을 때만" 테스트.
- 수집할 때마다 바뀌는 상태 필드는 `diff.VOLATILE`에 넣어 변경 비교가 설정 변경으로 오인하지 않게 한다.

## 8. CloudFormation 초안 (`iac.py`)
- 표현할 수 있으면 `Template.build`의 단계 목록에 함수를 넣는다(EIP: `elastic_ips` — NAT·NLB 주소는 소유자와 함께, 인스턴스 주소는 `AWS::EC2::EIP` + `EIPAssociation`).
- 표현할 수 없으면 `not_expressible`에서 `self.skip(Type, Id, 이유, 이름)`으로 `Metadata.DiagramBot.Unsupported`에 남긴다(DX 연결·VIF·게이트웨이, CloudFront, WAF). 지어내지 않는다.
- 테스트: `tests/test_iac.py::test_routing_domains_edge_and_dedicated_links_are_drafted_or_listed`, cfn-lint 테스트, 모든 샘플 내보내기 테스트.

## 9. 샘플 (`samples/catalog.py`)
1. `Sample`에 도우미를 만든다. 새 키는 `self.raw.setdefault(키, [])`로 만들어 쓰지 않는 샘플에 영향이 없게 한다(`dx_gateway`, `direct_connect`, `elastic_ip`).
2. `Sample.snapshot`의 선택 coverage 목록에 `(service, operation, raw 키, params)`를 넣는다(`if key in self.raw`로 걸러짐). `scope` 목록에도 유형 이름.
3. 알맞은 샘플에 넣는다(DX → `hybrid`, EIP → `three-tier`·`nlb-alb`). `SAMPLE_VERSION`을 올린다.
4. 전체 구성 관계가 바뀌면 `test_overview_draws_the_network_between_vpcs`의 샘플별 기대 목록을 고친다.
5. `scripts/render_samples.py`로 `examples/samples`를 다시 만든다([[samples]]).

## 10. 테스트
- **Stubber**(`tests/test_collector.py`): `Session.clients`에 서비스 이름을 넣는다. `stub_listing(stubs, overrides)`가 모든 `OPERATIONS`에 빈 응답을 넣고, 응답 키가 중첩이면 `EMPTY`에 빈 응답 모양을 등록한다. 새 호출의 정상·거부(`add_client_error` → `unavailable`, `permission` 확인)·부분 결과를 `test_edge_routing_and_direct_connect_calls_keep_partial_and_denied_results`처럼 검사하고 `assert_no_pending_responses`로 호출 수까지 맞춘다. 서비스를 하나하나 나열하는 `test_full_collection_flags_undiscovered_targets_and_flattens_instances`에도 응답을 더한다.
- **에뮬레이터**(`tests/test_aws_emulator.py`): 환경 fixture의 `importlib.import_module(f"moto.{name}.models")` 목록에 서비스를 넣고, 가능하면 `build()`에서 리소스를 만들어 그래프까지 확인한다(EIP: NAT의 EIP와 남는 EIP로 `eip_associated`·`COST-EIP-IDLE`). moto가 구현하지 않은 호출은 `EMULATOR_GAPS`에 넣는다(DX의 `describe_virtual_interfaces`, `describe_direct_connect_gateways`).
- **그래프**: `tests/fixtures.py::edge_snapshot`처럼 작은 합성 스냅샷을 만들고 `test_edge_elastic_ips_routing_domains_and_direct_connect_join_on_api_fields`처럼 관계·근거·참조·결정성을 검사한다.
- **그리기**: `test_edge_and_direct_connect_views_are_readable`처럼 overview·detail·focus에서 `assert_readable`, `assert_no_shared_segments`, `assert_routed`, `assert_captions_clear`와 아이콘 문자열(`resIcon=mxgraph.aws4.direct_connect;`)을 확인한다. 샘플에 넣었으면 샘플 단위 테스트가 자동으로 돈다([[layout-and-drawio]]).

## 11. 문서
- `README.md`의 수집 서비스 목록, `docs/OPERATIONS.md`(권한), `CHANGELOG.md`, `docs/STATUS.md`, 그리고 이 위키의 [[collection]](OPERATIONS 표), [[snapshot-model]](raw 키), [[graph]](종류·관계), [[samples]]의 `updated`와 내용.

## 바꿀 때 주의
- 정책 파일 두 개와 `OPERATIONS`는 테스트로 묶여 있다. 하나만 고치면 `make check`가 실패한다.
- 관계 이름과 한정자는 저장된 문서의 `relationId`가 된다. 나중에 바꾸면 편집본의 관계 연결이 끊긴다([[graph]]).

## 관련
- [[collection]] — 수집기 구조.
- [[graph]] — 조인 규칙과 관계 ID.
- [[views]] — 분류 집합.
- [[layout-and-drawio]] — 배치 불변식.
- [[samples]] — 샘플 도우미와 버전.
