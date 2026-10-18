---
title: 검토 규칙 추가하기
summary: findings.py에 새 검토 규칙을 넣는 순서 — 위치, 규칙 ID·심각도·영역 관례, 근거·권장 조치 문구, ARCHITECTURE.md 카탈로그, tests/test_findings.py 패턴, 샘플이 review --fail-on medium을 통과해야 하는 조건.
sources: [src/diagram_bot/findings.py, tests/test_findings.py, tests/fixtures.py, src/diagram_bot/samples/, docs/ARCHITECTURE.md]
related: [review-rules, cloudformation, cli]
updated: 2026-10-18
---
# 검토 규칙 추가하기

> 규칙은 `src/diagram_bot/findings.py` 안의 판정 코드 한 덩어리다. 등록 테이블은 없고 `add(...)`를 부르는 순간 규칙이 생긴다. 기준은 "수집된 API 필드만으로 판정, 입력이 없으면 침묵"이다. 아래는 `COST-EIP-IDLE`과 `SEC-ALB-WAF`(둘 다 `edge_review`)를 본보기로 한다.

## 핵심
- 판정 입력은 그래프 노드의 `attributes`, 노드의 파생 필드(`route_class`, `waf`, `db_role` 등), 엣지(`relation`, `qualifiers`, `details`), `coverage`뿐이다. `raw`를 직접 읽지 않는다.
- 필요한 필드가 그래프에 없으면 먼저 수집기(`collector.py`)와 `graph.build_graph`에 넣어야 한다. 이 경우는 규칙 추가보다 큰 작업이다.
- 실제 통신·도달성은 판정하지 않는다. 설정상 가능성만 말하고, 그렇다는 사실을 문구에 밝힌다(`SEC-CF-ORIGIN-OPEN`의 "실제 접근 시험은 하지 않았습니다").

## 1. 놓을 위치 고르기
- 안정성·보안 일반 규칙: `reliability_review(nodes, edges, collected, out, add)` 또는 `security_review(edges, collected, add)`.
- GPU·EFA·SageMaker·Bedrock·FSx: `ai_review(graph, nodes, edges, collected, out, add)`.
- 엣지·주소·라우팅 도메인처럼 `coverage`가 필요한 것: `edge_review(nodes, edges, collected, coverage, add)`.
- 새 함수로 묶을 만큼 크면 `ai_review`·`edge_review`처럼 같은 인자를 받는 함수를 만들고 `review()`에서 `OPS-COVERAGE` 앞에 호출한다.

## 2. 규칙 ID·심각도·영역
- ID: `<영역 접두사>-<대상>-<문제>` 대문자 하이픈. 접두사는 `REL`(안정성), `SEC`(보안), `OPS`(운영), `COST`(비용). AI 규칙도 `REL-GPU-*`, `SEC-AI-*`처럼 같은 접두사를 쓰고 `pillar`만 `"ai"`로 둔다.
- `pillar`는 `PILLARS` 키(`reliability`, `security`, `operations`, `ai`, `cost`) 중 하나여야 한다. 새 영역을 만들면 `PILLARS`에 한국어 이름을 넣어야 `markdown()`이 깨지지 않는다.
- 심각도는 `docs/ARCHITECTURE.md` 1절의 권고 강도에 맞춘다. 기존 관례: 서비스 문서 필수·Well-Architected High risk에 해당하는 노출·단일 장애점은 `high`(예: `REL-LB-AZ`, `SEC-SG-OPEN`), 일반 모범 사례 위반은 `medium`, "we recommend" 수준(REC)이거나 의도적일 수 있는 설정은 `low`(예: `SEC-ALB-WAF`는 C6 REC → low, `REL-TGW-BLACKHOLE`은 격리 의도일 수 있어 low).
- 같은 규칙 ID를 다른 심각도로 재사용하는 예가 있다(`REL-ASG-AZ`: 일반 ASG medium, 학습 그룹 low/ai). 새 규칙은 가능하면 ID 하나에 심각도 하나로 둔다.

## 3. 코드 작성
본보기 `COST-EIP-IDLE`:

```python
joined = {e["source"] for e in edges if e["relation"] == "eip_associated"}
for address in collected("elastic_ip"):
    a = address["attributes"]
    if not (a.get("AssociationId") or a.get("InstanceId") or a.get("NetworkInterfaceId") or address["id"] in joined):
        add("COST-EIP-IDLE", "low", "cost", address["id"], f"연결되지 않은 Elastic IP {a.get('PublicIp', '')}".strip(),
            f"DescribeAddresses: AssociationId·InstanceId·NetworkInterfaceId 없음 (...)", "퍼블릭 IPv4 주소는 ... 해제(ReleaseAddress)하고 ...")
```

- 대상은 `collected(kind)`로 고른다(`reference_only` 제외). 필드는 `.get()`으로 읽고, "값이 명시적으로 거짓"을 볼 때는 `is False`로 없음과 구분한다(`SEC-ENCRYPTION` 패턴).
- 입력이 불완전하면 판정하지 않는다. `SEC-ALB-WAF`는 `waf_collected(coverage, lb)`로 그 계정·리전의 REGIONAL WAF 목록이 모두 `complete`일 때만, `SEC-EFA-SG`·`SEC-CF-ORIGIN-OPEN`은 연결된 SG가 전부 수집됐을 때만 판정한다.
- 여러 인스턴스가 한 ASG에 속하면 결과를 ASG 하나로 묶는다(`ai_review`의 `per_group(rule, instance)`, `SEC-EFA-SG`의 `reported` 집합).
- 결과 키 `key`는 노드 ID다. 관계에서 나온 문제면 의미 있는 쪽 노드(예: `REL-VPN-TUNNEL`은 엣지의 `target`인 고객 게이트웨이)를 쓴다.

## 4. 제목·근거·권장 조치 문구
- `title`: 한국어 한 줄, 무엇이 문제인지. 값(개수·포트·IP)을 넣어도 되지만 결과 `id`가 `rule|key|title` 해시이므로 넣은 값이 바뀌면 ID도 바뀐다.
- `evidence`: `<API 이름>.<필드> = <값>` 형태로 판정에 쓴 필드를 그대로 쓴다. 조회 시점 값이면 `(조회 시점)`을 붙인다(`REL-TG-UNHEALTHY`). 여러 값은 `·`로 잇는다.
- `recommendation`: 구체적 조치 한두 문장. 근거가 되는 Well-Architected 모범 사례 ID나 문서 이름을 괄호로 단다(`(GENSEC01-BP02)`, `(docs/ARCHITECTURE.md C6 · REL02-BP01)`). 의도적일 수 있는 설정이면 그대로 두는 경우도 적는다(`REL-TGW-BLACKHOLE`).
- 문구 안 `|`와 줄바꿈은 `markdown()`이 이스케이프하므로 신경 쓰지 않아도 된다.

## 5. 문서 카탈로그
- `docs/ARCHITECTURE.md`에 규칙 표가 두 곳 있다: 4.1절(엣지·연결 규칙: 규칙 | 심각도 | 판정 | 근거)과 5.8절(AI/ML 규칙). 해당 범주의 표에 한 줄을 더한다. 본문 REL·SEC 규칙은 카탈로그 표가 없으므로 새 범주라면 표를 새로 만들지, 코드만 둘지 정한다.
- 이 위키의 [[review-rules]] 규칙 표와 개수(현재 47개)를 고친다. 페이지의 `updated`도 바꾼다.
- 새 규칙이 CloudFormation 내보내기와 같은 입력을 본다면(예: 유휴 EIP) [[cloudformation]]의 Unsupported 이유 문구와 맞춘다(`iac/network.py`의 `Template.elastic_ips` 사유에 `COST-EIP-IDLE`이 적혀 있다).

## 6. 테스트
- `tests/test_findings.py`의 헬퍼 `findings(snapshot)` = `review(build_graph(snapshot), snapshot["coverage"])`, `rules(snapshot)` = `{(rule, resource_name)}`를 쓴다.
- 양성 사례: 샘플이나 픽스처를 `copy`해 `raw` 필드를 바꾸고 `(규칙, 리소스 이름)`이 나오는지 확인한다(`test_single_az_and_unencrypted_resources_are_reported` 패턴). 엣지 계열은 `tests/fixtures.py`의 `edge_snapshot()`을 쓴다.
- 음성·입력 누락 사례: 입력 필드나 `coverage` 행을 빼거나 `status`를 바꿔 결과가 사라지는지 확인한다(`test_edge_address_and_routing_rules_need_their_inputs`가 WAF coverage를 세 방식으로 망가뜨려 본다).
- 심각도·영역과 근거 문자열 일부를 단언한다(예: `found[key]["pillar"] == "cost"`, 근거에 `10.99.0.0/16 (static)` 포함).
- 데모 스냅샷에 새로 걸리면 `test_demo_findings_name_resource_evidence_and_fix`의 기대 집합과 `summary` 값을 함께 고친다.

## 7. 샘플이 깨끗해야 한다
- `test_reference_samples_have_no_high_or_medium_findings`는 `SAMPLES`의 모든 샘플에서 결과 심각도가 `low`뿐이어야 한다. CLI로는 `diagram-bot demo --sample <키> --output DIR` 후 `diagram-bot review DIR/snapshot.json --fail-on medium`이 종료 코드 0이어야 한다는 뜻이다.
- 새 규칙이 샘플에 medium 이상으로 걸리면 규칙이 틀렸는지(입력 해석), 샘플이 모범 사례를 어겼는지 판단한다. 샘플을 고치면 `samples/builder.py`의 `SAMPLE_VERSION`과 샘플 산출물 재생성(`scripts/render_samples.py`)이 따라온다.

## 8. 마무리 확인
- `make check`(ruff, pytest, 오프라인 스모크)를 통과시킨다.
- 결과가 앱의 `검토 결과` 탭(`GET /api/snapshots/{id}/findings`), `review.md`, 채팅 에이전트의 검토 도구에 자동으로 나온다. 따로 등록할 곳은 없다.

## 바꿀 때 주의
- 결과 정렬·중복 제거는 `id` 기준이다. 같은 리소스에 같은 규칙을 두 번 내면 제목이 같을 때만 하나로 합쳐진다.
- `OPS-COVERAGE`는 고정 ID `f-coverage`를 쓰는 유일한 결과다. 비슷한 집계형 규칙을 만들면 ID 충돌에 주의한다.

## 관련
- [[review-rules]] — 현재 규칙 전체 목록과 결과 필드.
- [[cloudformation]] — 같은 입력을 보는 내보내기의 미지원 사유.
- [[cli]] — `review --fail-on`과 `make check`.
