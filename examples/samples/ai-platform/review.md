# TA 검토 자료

- 계정: 111122223333
- 리전: ap-northeast-2
- 수집 구간: 2026-10-18T09:34:34.987831+00:00 ~ 2026-10-18T09:34:34.987831+00:00
- 수집 상태: complete
- 샘플 데이터: True

## 조회 범위와 결과

- ec2 / describe_vpcs: complete, 1개, 오류 없음; 범위 {}
- ec2 / describe_subnets: complete, 7개, 오류 없음; 범위 {}
- ec2 / describe_instances: complete, 8개, 오류 없음; 범위 {}
- elbv2 / describe_load_balancers: complete, 1개, 오류 없음; 범위 {}
- elbv2 / describe_target_groups: complete, 1개, 오류 없음; 범위 {}
- ec2 / describe_internet_gateways: complete, 0개, 오류 없음; 범위 {}
- ec2 / describe_nat_gateways: complete, 0개, 오류 없음; 범위 {}
- ec2 / describe_route_tables: complete, 2개, 오류 없음; 범위 {}
- rds / describe_db_instances: complete, 2개, 오류 없음; 범위 {}
- autoscaling / describe_auto_scaling_groups: complete, 3개, 오류 없음; 범위 {}
- ec2 / describe_vpc_endpoints: complete, 12개, 오류 없음; 범위 {}
- ec2 / describe_transit_gateway_vpc_attachments: complete, 1개, 오류 없음; 범위 {}
- rds / describe_db_clusters: complete, 1개, 오류 없음; 범위 {}
- elasticache / describe_cache_clusters: complete, 0개, 오류 없음; 범위 {}
- kafka / list_clusters_v2: complete, 0개, 오류 없음; 범위 {}
- lambda / list_functions: complete, 0개, 오류 없음; 범위 {}
- eks / list_clusters: complete, 1개, 오류 없음; 범위 {}
- kafka / list_nodes: complete, 0개, 오류 없음; 범위 {}
- opensearch / describe_domains: complete, 0개, 오류 없음; 범위 {}
- opensearch / describe_domain_nodes: complete, 0개, 오류 없음; 범위 {}
- ecs / describe_services: complete, 0개, 오류 없음; 범위 {}
- ecs / describe_tasks: complete, 0개, 오류 없음; 범위 {}
- efs / describe_file_systems: complete, 1개, 오류 없음; 범위 {}
- efs / describe_mount_targets: complete, 3개, 오류 없음; 범위 {}
- redshift / describe_clusters: complete, 0개, 오류 없음; 범위 {}
- ec2 / describe_vpc_peering_connections: complete, 0개, 오류 없음; 범위 {}
- ec2 / describe_vpn_gateways: complete, 0개, 오류 없음; 범위 {}
- ec2 / describe_vpn_connections: complete, 0개, 오류 없음; 범위 {}
- ec2 / describe_security_groups: complete, 4개, 오류 없음; 범위 {}
- ec2 / describe_placement_groups: complete, 1개, 오류 없음; 범위 {}
- fsx / describe_file_systems: complete, 1개, 오류 없음; 범위 {}
- sagemaker / list_endpoints: complete, 0개, 오류 없음; 범위 {}
- sagemaker / list_clusters: complete, 0개, 오류 없음; 범위 {}
- elbv2 / describe_target_health: complete, 3개, 오류 없음; 범위 {"TargetGroupArn": "arn:aws:elasticloadbalancing:ap-northeast-2:111122223333:targetgroup/inference-pods/0000000000000001"}

## 아키텍처 검토 결과

높음 0 · 중간 0 · 낮음 1 — 수집된 설정만으로 판정했으며 실제 트래픽·애플리케이션 수준 이중화는 별도 확인이 필요합니다.

| 심각도 | 영역 | 규칙 | 리소스 | 내용 | 근거 | 권장 조치 |
| --- | --- | --- | --- | --- | --- | --- |
| 낮음 | AI/ML | REL-ASG-AZ | eks-ai-gpu-train-5e6f | 학습 노드 그룹이 한 AZ에만 있음 (강결합 학습은 의도적 단일 AZ) | DescribeAutoScalingGroups.VPCZoneIdentifier → AZ ap-northeast-2a · InstanceType p5.48xlarge | EFA 학습은 단일 AZ가 맞습니다. 체크포인트 저장소와 다른 AZ에서 다시 시작하는 경로(IaC·용량)를 확인하세요. |

## 해석 범위

- AWS best practice 패턴을 따른 합성 샘플이며 실제 계정 데이터가 아닙니다.
- 리스너 규칙·Route 53·Shield·Network Firewall은 수집 범위 밖이라 그리지 않습니다 (권고는 practices에 문구로).
- 설정 관계이며 실제 통신이나 보안 적합성을 의미하지 않습니다.

## 검토할 항목

- 조회 불가·부분 조회 범위의 권한 및 재수집 필요 여부
- 참조만 확인된 리소스와 미확인 배치 정보
- VPC·서브넷·가용 영역 배치가 설계 의도와 일치하는지
- 대상 그룹의 등록 대상과 조회 시점의 TargetHealth 상태
- 실제 통신·보안 적합성 판정은 별도 증거로 검토
