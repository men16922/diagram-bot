---
title: 아키텍처 검토 규칙
summary: findings.py의 review(graph, coverage)가 수집된 API 필드만으로 판정하는 47개 규칙(안정성·보안·운영·AI/ML·비용), 결과 필드, 수집 공백 처리, review --fail-on 게이트.
sources: [src/diagram_bot/findings.py, src/diagram_bot/cli.py, src/diagram_bot/report.py, src/diagram_bot/routes/snapshots.py, src/diagram_bot/api.py, src/diagram_bot/agent.py, src/diagram_bot/designer.py, tests/test_findings.py, docs/ARCHITECTURE.md, tests/test_golden.py]
related: [howto-add-review-rule, change-review, multi-account, cli, graph, snapshot-model]
updated: 2026-10-18
---
# 아키텍처 검토 규칙

> `src/diagram_bot/findings.py`의 `review(graph, coverage)`는 그래프 노드의 `attributes`(수집한 API 응답 필드)와 관계만 보고 결정적으로 결과 목록을 만든다. 입력 필드가 없으면 추측하지 않고 아무것도 보고하지 않으며, 읽지 못한 API는 `OPS-COVERAGE` 하나로 알린다.

## 핵심
- 입력: `graph`(`graph.build_graph`의 `nodes`·`edges`)와 `coverage`(스냅샷의 API 호출별 상태 행). `coverage` 기본값은 `()`.
- 판정 대상은 `reference_only`가 아닌 노드뿐이다(`collected(kind)` 헬퍼). 참조로만 등장한 리소스는 판정하지 않는다.
- 심각도 `SEVERITIES = ("high", "medium", "low")`, 영역 `PILLARS = {"reliability": "안정성", "security": "보안", "operations": "운영", "ai": "AI/ML", "cost": "비용"}`.
- 결과는 `id`로 중복 제거 후 (심각도 순서, `pillar`, `rule`, `resource_name`) 순으로 정렬된다. 같은 입력이면 같은 결과(`tests/test_findings.py`가 결정성 확인).
- 실제 트래픽·도달성은 시험하지 않는다. `markdown()` 머리말도 "수집된 설정만으로 판정"이라고 밝힌다.

## 구조
- `review()` 본문: `reliability_review(...)` → `security_review(...)` → `ai_review(...)` → `edge_review(...)` → `OPS-COVERAGE` → 정렬. 호출 순서가 같은 정렬 키 안의 순서를 정한다.
- `reliability_review(nodes, edges, collected, out, add)`: LB·대상 그룹·ASG·RDS·클러스터·캐시·NAT·OpenSearch·MSK·Redshift·EFS·VPN·ECS의 AZ 분산·장애 조치·상태.
- `security_review(edges, collected, add)`: 퍼블릭 접근, 저장·전송 암호화, IMDS, 인터넷 개방 SG 규칙, EFA SG, EKS 엔드포인트.
- `ai_review(graph, nodes, edges, collected, out, add)`: GPU·EFA·SageMaker·Bedrock·FSx 규칙(Well-Architected Generative AI·ML·HPC 렌즈 근거).
- `edge_review(nodes, edges, collected, coverage, add)`: WAF, CloudFront 오리진 노출, 유휴 Elastic IP, TGW blackhole 경로.
- 내부 헬퍼 `add(rule, severity, pillar, key, title, evidence, recommendation)`가 결과 딕셔너리를 만든다. `out(key, relation)`은 한 노드에서 나가는 특정 관계의 엣지 목록(`(source, relation)`로 한 번 색인해 둔 것).
- 모듈 상수: `ADMIN_PORTS`(22, 3389), `DATA_PORTS`(3306·5432·1433·1521·6379·11211·9200·27017·9092·5439·2049), `WORLD`(`0.0.0.0/0`, `::/0`), `AI_PORTS`(8888·6006·8265·10001·5000·7860·8501·11434·8000·8001), 인스턴스 유형 정규식 `TRAINING`(`p4d|p5|p6|trn`), `INFERENCE`(`g\d|inf`), `ACCELERATED`(`(p|g|trn|inf|dl)\d`), `NODE_IPS`(EKS 81, 그 밖 32), `ML_ENDPOINT`(bedrock*·sagemaker.* 서비스 이름).
- `efa_node(attrs)`: EFA 인터페이스(`NetworkInterfaces[].InterfaceType == "efa"`)가 있거나 `TRAINING` 유형이면 강결합 학습 노드.

## 결과 필드
| 필드 | 의미 |
| --- | --- |
| `id` | `"f-" + sha256(f"{rule}\|{key}\|{title}")[:16]`. `OPS-COVERAGE`만 고정값 `f-coverage` |
| `rule`, `severity`, `pillar` | 규칙 ID, `SEVERITIES` 중 하나, `PILLARS` 키 |
| `resource`, `resource_name`, `kind` | 노드 ID, 노드 `name`(없으면 key), 노드 `kind`. `OPS-COVERAGE`는 `None`·`"수집 범위"`·`None` |
| `title` | 한국어 한 줄 요약(개수·포트 같은 값 포함 가능) |
| `evidence` | 판정에 쓴 API 이름과 필드·값 (예: `DescribeDBInstances.MultiAZ = false, 클러스터·복제본 아님`) |
| `recommendation` | 권장 조치. AI 규칙은 Well-Architected 모범 사례 ID(MLSEC03-BP02 등)를 괄호로 단다 |

`summary(findings)`는 `{"high": n, "medium": n, "low": n}`, `markdown(findings)`는 `## 아키텍처 검토 결과` 표(심각도·영역·규칙·리소스·내용·근거·권장 조치, `|`와 줄바꿈 이스케이프)를 돌려준다.

## 규칙 목록 (47개)
안정성(본문):

| 규칙 | 심각도 | 영역 | 조건 |
| --- | --- | --- | --- |
| REL-LB-AZ | high | reliability | 로드밸런서 `AvailabilityZones[].ZoneName`이 2개 미만 |
| REL-TG-AZ | medium | reliability | 대상 그룹의 `registered_target` 대상 AZ가 정확히 1개 (`all` 제외) |
| REL-TG-UNHEALTHY | medium | reliability | 등록 대상 `TargetHealth.State`가 unhealthy/unavailable (조회 시점) |
| REL-ASG-AZ | medium | reliability | 일반 ASG의 `VPCZoneIdentifier` 서브넷이 한 AZ |
| REL-ASG-AZ | low | ai | `TRAINING` 유형 ASG가 한 AZ (강결합 학습은 의도적 단일 AZ라는 알림) |
| REL-GPU-TRAIN-AZ | medium | ai | `TRAINING` 유형 ASG가 여러 AZ (EFA는 AZ를 넘지 못함) |
| REL-GPU-INFER-AZ | medium | ai | `INFERENCE` 유형 ASG가 한 AZ |
| REL-RDS-SINGLE-AZ | medium | reliability | `MultiAZ` 거짓이고 클러스터 멤버·읽기 복제본도 아님 |
| REL-CLUSTER-SINGLE | medium | reliability | DB 클러스터 멤버가 1개(writer만) |
| REL-CLUSTER-AZ | medium | reliability | 멤버가 여럿인데 모두 한 AZ |
| REL-CACHE-FAILOVER | medium | reliability | 복제 그룹 `AutomaticFailover`가 enabled/enabling/없음이 아님 |
| REL-CACHE-STANDALONE | medium | reliability | redis·valkey 캐시 노드에 `ReplicationGroupId` 없음 |
| REL-NAT-CROSS-AZ | low | reliability | 라우팅 테이블 `0.0.0.0/0 → NAT`인데 연결 서브넷 AZ가 NAT의 AZ와 다름 |
| REL-SEARCH-AZ | medium | reliability | OpenSearch `ClusterConfig.ZoneAwarenessEnabled` 거짓 |
| REL-SEARCH-MASTER | low | reliability | 전용 마스터 꺼짐 또는 `DedicatedMasterCount` < 3 |
| REL-MSK-AZ | low | reliability | MSK `BrokerNodeGroupInfo.ClientSubnets` 1~2개 |
| REL-REDSHIFT-AZ | low | reliability | Redshift `MultiAZ`가 enabled 아님 (`#secondary` 노드 제외) |
| REL-EFS-ONE-ZONE | low | reliability | EFS에 `AvailabilityZoneName` 있음(One Zone) |
| REL-VPN-TUNNEL | high/medium | reliability | `vpn_connection` 엣지의 터널 UP 수 < 전체 (0이면 high). 리소스는 고객 게이트웨이 |
| REL-ECS-RUNNING | medium | reliability | `runningCount` < `desiredCount` (조회 시점) |
| REL-ECS-AZ | medium | reliability | `desiredCount` ≥ 2인데 서비스 태스크 AZ가 1개 |

보안(본문):

| 규칙 | 심각도 | 영역 | 조건 |
| --- | --- | --- | --- |
| SEC-RDS-PUBLIC | high | security | DB 인스턴스 `PubliclyAccessible` |
| SEC-ENCRYPTION | medium | security | RDS `StorageEncrypted`, EFS·Redshift `Encrypted`, ElastiCache `AtRestEncryptionEnabled`, OpenSearch `EncryptionAtRestOptions.Enabled`가 명시적으로 `false` |
| SEC-SEARCH-PUBLIC | medium | security | OpenSearch `VPCOptions.SubnetIds` 없음(퍼블릭 엔드포인트) |
| SEC-TRANSIT | low | security | ElastiCache `TransitEncryptionEnabled` false, MSK `ClientBroker`가 PLAINTEXT/TLS_PLAINTEXT |
| SEC-IMDSV1 | medium | security | EC2 `MetadataOptions.HttpTokens == "optional"` |
| SEC-EC2-PUBLIC-IP | low | security | 가속 유형이 아닌 EC2에 `PublicIpAddress` |
| SEC-SG-OPEN | high | security | SG 인바운드가 `WORLD`에서 프로토콜 `-1`(모든 포트) 또는 TCP(`tcp`·`6`) 규칙의 `ADMIN_PORTS`·`DATA_PORTS` 범위 허용. UDP·ICMP·ESP 규칙은 이 서비스들을 열지 않으므로 제외. 포트마다 하나 |
| SEC-AI-PORT-OPEN | high | ai | SG 인바운드가 `WORLD`에서 `AI_PORTS` 중 하나를 포함하는 범위 허용 (프로토콜 `-1` 규칙에는 적용 안 됨) |
| SEC-EFA-SG | medium | ai | EFA 노드의 SG(모두 수집된 경우만)에 같은 그룹 자기 참조 `IpProtocol -1` 규칙 없음. ASG당 하나 |
| SEC-EKS-PUBLIC | medium | security | `endpointPublicAccess`이고 `publicAccessCidrs`에 `0.0.0.0/0` |

AI/ML(`ai_review`, 모두 `pillar = "ai"`):

| 규칙 | 심각도 | 조건 |
| --- | --- | --- |
| SEC-GPU-PUBLIC | high | 가속 인스턴스에 퍼블릭 IP가 있거나 서브넷 `route_class == "public"` (ASG당 하나) |
| REL-GPU-SPOT-EFA | medium | EFA 노드 `InstanceLifecycle == "spot"` |
| OPS-GPU-PLACEMENT | low | EFA 노드가 `cluster` 전략 배치 그룹 밖이고 Capacity Block도 아님 (배치 그룹이 수집되지 않은 이름뿐이면 판정 보류) |
| REL-GPU-SUBNET-IP | medium | p5 ASG의 (MaxSize − 현재 수) × `NODE_IPS` > 선택 서브넷 `AvailableIpAddressCount` 합 |
| SEC-AI-ENDPOINT-DNS | medium | bedrock*/sagemaker.* 인터페이스 엔드포인트 `PrivateDnsEnabled == false` |
| REL-AI-ENDPOINT-AZ | medium | 그 엔드포인트 서브넷 AZ가 같은 VPC의 컴퓨팅(EC2·ECS 태스크) AZ 일부를 빠뜨림 |
| OPS-FSX-SCRATCH | low | Lustre `DeploymentType`이 `SCRATCH`로 시작 |
| REL-FSX-TRAIN-AZ | medium | EFA 노드 AZ가 같은 VPC Lustre 서브넷 AZ에 없음 |
| OPS-GPU-S3-ENDPOINT | low | 가속 인스턴스 VPC에 `.s3` Gateway 엔드포인트 없음 |
| SEC-SAGEMAKER-VPC | medium | SageMaker 엔드포인트 `Subnets` 없음(VPC 밖) |
| REL-SAGEMAKER-AZ | medium | `InstanceCount` < 2 또는 서브넷 AZ < 2 |
| SEC-HYPERPOD-PUBLIC | high | HyperPod `VpcConfig.Subnets` 중 `route_class == "public"` |

엣지·비용·운영(`edge_review`와 본문 끝):

| 규칙 | 심각도 | 영역 | 조건 |
| --- | --- | --- | --- |
| SEC-ALB-WAF | low | security | 인터넷 ALB에 `waf` 표식이 없고 앞단 CloudFront에도 `WebACLId` 없음. `waf_collected()`가 참일 때만 |
| SEC-CF-ORIGIN-OPEN | low | security | CloudFront 오리진 ALB의 SG(모두 수집된 경우만)가 `WORLD` 허용 |
| COST-EIP-IDLE | low | cost | `AssociationId`·`InstanceId`·`NetworkInterfaceId`가 없고 `eip_associated` 엣지도 없음 |
| REL-TGW-BLACKHOLE | low | reliability | TGW 라우팅 테이블 `Routes[].State == "blackhole"` |
| OPS-COVERAGE | low | operations | `coverage` 중 `status`가 `complete`·`None`이 아닌 행이 있음 |

## 수집 공백 처리
- 규칙은 입력이 없으면 침묵한다. 예: `SEC-EFA-SG`·`SEC-CF-ORIGIN-OPEN`은 연결된 SG가 하나라도 수집되지 않았으면 판정하지 않고, `REL-GPU-SUBNET-IP`는 `AvailableIpAddressCount`가 하나라도 없으면 건너뛴다.
- `waf_collected(coverage, node)`: 그 ALB의 계정·리전(행에 `account_id`·`region`이 있으면 비교, 없으면 일치로 간주)에서 `CLOUDFRONT`가 아닌 `wafv2` 행 중 `list_web_acls`가 있고 모든 행이 `complete`여야 한다. 아니면 `SEC-ALB-WAF`를 내지 않는다.
- `OPS-COVERAGE`: 미완료 행 수를 제목에, 앞 8개를 `operation(error_code 또는 status)`로 근거에, 행의 `permission` 값을 모아 권장 조치("읽기 전용 권한 추가 후 다시 수집하세요: ...")에 넣는다.
- 부분 수집이면 항상 `OPS-COVERAGE`(low)가 생기므로 `--fail-on low`는 부분 수집 스냅샷에서 실패한다.

## 호출 지점
- CLI `diagram-bot review SNAPSHOT [--format markdown|json] [--fail-on high|medium|low]`: `review(build_graph(snapshot), snapshot.get("coverage", []))`. JSON은 `{"summary", "findings"}`.
- 게이트: `--fail-on` 심각도의 `SEVERITIES` 인덱스 이하(같거나 더 높은 심각도) 결과가 하나라도 있으면 종료 코드 3, 아니면 0. `--fail-on high`는 high만, `--fail-on low`는 결과가 하나라도 있으면 실패.
- 보고서: `report.write_report`가 `review.md`에 `markdown(review(...))`를 넣는다.
- API: `GET /api/snapshots/{identifier}/findings` → `{"summary", "findings"}` (`routes/snapshots.py`). 라이브러리 `api.review_snapshot(snapshot)`도 같은 형태.
- 채팅 에이전트: `Design.review()`(설계, coverage 없음)와 `review_collected_resources`가 상위 20개 결과를 요약 필드만 돌려준다.
- 여러 계정을 합친 스냅샷도 그대로 검토된다. 합친 `coverage` 행에 `account_id`·`region`이 붙어 `waf_collected`가 계정·리전별로 판정한다([[multi-account]]).

## 바꿀 때 주의
- 결과 `id`는 `rule|key|title` 해시다. 제목 문구를 바꾸면 ID가 바뀐다(같은 리소스에 제목이 다른 결과가 둘이면 둘 다 남는다).
- 모든 샘플은 high·medium 결과가 없어야 한다(`test_reference_samples_have_no_high_or_medium_findings`, `review --fail-on medium` 종료 코드 0).
- `tests/test_golden.py`가 샘플·데모·엣지·설계안과 규칙이 많이 걸리는 변형(`degraded`, `exposed`, `gpu`)의 `review()` 결과 SHA-256을 고정한다. 규칙을 바꾸면 `python tests/test_golden.py`로 `tests/golden.json`을 다시 쓴다.
- 데모 스냅샷의 정확한 결과 집합과 `summary` 값을 `test_demo_findings_name_resource_evidence_and_fix`가 고정한다. 데모에 걸리는 규칙을 바꾸면 이 테스트를 함께 고친다.
- 엣지 규칙은 `tests/fixtures.py`의 `edge_snapshot()`과 `test_edge_address_and_routing_rules_need_their_inputs`가 입력 누락 시 침묵까지 확인한다.
- 규칙 카탈로그 문서는 `docs/ARCHITECTURE.md` 4.1절(엣지 규칙 표)과 5.8절(AI/ML 규칙 표)에만 있다. 본문 REL·SEC 규칙은 코드가 유일한 목록이다.

## 관련
- [[howto-add-review-rule]] — 새 규칙을 추가하는 절차.
- [[change-review]] — 두 스냅샷 사이 설정 변경 비교(검토와 별개).
- [[multi-account]] — 합친 스냅샷의 coverage 행에 계정·리전이 붙는 방식.
- [[cli]] — `review` 종료 코드와 다른 하위 명령.
- [[graph]] — 검토가 읽는 노드·엣지와 파생 필드(`route_class`, `waf`).
- [[snapshot-model]] — `coverage` 행 형식.
