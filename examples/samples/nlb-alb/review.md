# TA 검토 자료

- 계정: 111122223333
- 리전: ap-northeast-2
- 수집 구간: 2026-10-17T07:47:10.407901+00:00 ~ 2026-10-17T07:47:10.407901+00:00
- 수집 상태: complete
- 샘플 데이터: True

## 조회 범위와 결과

- ec2 / describe_vpcs: complete, 1개, 오류 없음; 범위 {}
- ec2 / describe_subnets: complete, 4개, 오류 없음; 범위 {}
- ec2 / describe_instances: complete, 4개, 오류 없음; 범위 {}
- elbv2 / describe_load_balancers: complete, 2개, 오류 없음; 범위 {}
- elbv2 / describe_target_groups: complete, 3개, 오류 없음; 범위 {}
- ec2 / describe_internet_gateways: complete, 1개, 오류 없음; 범위 {}
- ec2 / describe_nat_gateways: complete, 2개, 오류 없음; 범위 {}
- ec2 / describe_route_tables: complete, 4개, 오류 없음; 범위 {}
- rds / describe_db_instances: complete, 0개, 오류 없음; 범위 {}
- autoscaling / describe_auto_scaling_groups: complete, 2개, 오류 없음; 범위 {}
- ec2 / describe_vpc_endpoints: complete, 1개, 오류 없음; 범위 {}
- ec2 / describe_transit_gateway_vpc_attachments: complete, 0개, 오류 없음; 범위 {}
- rds / describe_db_clusters: complete, 0개, 오류 없음; 범위 {}
- elasticache / describe_cache_clusters: complete, 0개, 오류 없음; 범위 {}
- kafka / list_clusters_v2: complete, 0개, 오류 없음; 범위 {}
- lambda / list_functions: complete, 0개, 오류 없음; 범위 {}
- eks / list_clusters: complete, 0개, 오류 없음; 범위 {}
- kafka / list_nodes: complete, 0개, 오류 없음; 범위 {}
- opensearch / describe_domains: complete, 0개, 오류 없음; 범위 {}
- opensearch / describe_domain_nodes: complete, 0개, 오류 없음; 범위 {}
- ecs / describe_services: complete, 0개, 오류 없음; 범위 {}
- ecs / describe_tasks: complete, 0개, 오류 없음; 범위 {}
- efs / describe_file_systems: complete, 0개, 오류 없음; 범위 {}
- efs / describe_mount_targets: complete, 0개, 오류 없음; 범위 {}
- redshift / describe_clusters: complete, 0개, 오류 없음; 범위 {}
- ec2 / describe_vpc_peering_connections: complete, 0개, 오류 없음; 범위 {}
- ec2 / describe_vpn_gateways: complete, 0개, 오류 없음; 범위 {}
- ec2 / describe_vpn_connections: complete, 0개, 오류 없음; 범위 {}
- elbv2 / describe_target_health: complete, 2개, 오류 없음; 범위 {"TargetGroupArn": "arn:aws:elasticloadbalancing:ap-northeast-2:111122223333:targetgroup/partner-api-https/0000000000000001"}
- elbv2 / describe_target_health: complete, 1개, 오류 없음; 범위 {"TargetGroupArn": "arn:aws:elasticloadbalancing:ap-northeast-2:111122223333:targetgroup/partner-alb-tcp/0000000000000002"}
- elbv2 / describe_target_health: complete, 2개, 오류 없음; 범위 {"TargetGroupArn": "arn:aws:elasticloadbalancing:ap-northeast-2:111122223333:targetgroup/mqtt-tls/0000000000000003"}

## 해석 범위

- AWS best practice 패턴을 따른 합성 샘플이며 실제 계정 데이터가 아닙니다.
- 보안 그룹 규칙·리스너 규칙·Direct Connect는 수집 범위 밖이라 표시하지 않습니다.
- 설정 관계이며 실제 통신이나 보안 적합성을 의미하지 않습니다.

## 검토할 항목

- 조회 불가·부분 조회 범위의 권한 및 재수집 필요 여부
- 참조만 확인된 리소스와 미확인 배치 정보
- VPC·서브넷·가용 영역 배치가 설계 의도와 일치하는지
- 대상 그룹의 등록 대상과 조회 시점의 TargetHealth 상태
- 실제 통신·보안 적합성 판정은 별도 증거로 검토
