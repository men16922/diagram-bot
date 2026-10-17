# TA 검토 자료

- 계정: 111122223333
- 리전: ap-northeast-2
- 수집 구간: 2026-10-17T08:12:43.176490+00:00 ~ 2026-10-17T08:12:43.176490+00:00
- 수집 상태: partial
- 샘플 데이터: True

## 조회 범위와 결과

- ec2 / describe_vpcs: complete, 3개, 오류 없음; 범위 {}
- ec2 / describe_subnets: complete, 5개, 오류 없음; 범위 {}
- ec2 / describe_instances: partial, 4개, 오류 UnauthorizedOperation; 필요 권한 ec2:DescribeInstances; 범위 {}
- elbv2 / describe_load_balancers: complete, 2개, 오류 없음; 범위 {}
- elbv2 / describe_target_groups: complete, 3개, 오류 없음; 범위 {}
- ec2 / describe_security_groups: complete, 2개, 오류 없음; 범위 {}
- elbv2 / describe_target_health: complete, 3개, 오류 없음; 범위 {"TargetGroupArn": "arn:aws:elasticloadbalancing:ap-northeast-2:111122223333:targetgroup/demo-web/456"}
- elbv2 / describe_target_health: complete, 2개, 오류 없음; 범위 {"TargetGroupArn": "arn:aws:elasticloadbalancing:ap-northeast-2:111122223333:targetgroup/demo-api/567"}
- elbv2 / describe_target_health: complete, 1개, 오류 없음; 범위 {"TargetGroupArn": "arn:aws:elasticloadbalancing:ap-northeast-2:111122223333:targetgroup/demo-ip-targets/678"}

## 아키텍처 검토 결과

높음 1 · 중간 4 · 낮음 2 — 수집된 설정만으로 판정했으며 실제 트래픽·애플리케이션 수준 이중화는 별도 확인이 필요합니다.

| 심각도 | 영역 | 규칙 | 리소스 | 내용 | 근거 | 권장 조치 |
| --- | --- | --- | --- | --- | --- | --- |
| 높음 | 보안 | SEC-SG-OPEN | demo-web-sg | 인터넷(0.0.0.0/0)에 SSH 22 허용 | DescribeSecurityGroups.IpPermissions: tcp 22-22 ← 0.0.0.0/0 | 관리 접근은 Session Manager, 데이터 저장소는 애플리케이션 보안 그룹만 허용하도록 좁히세요. |
| 중간 | 안정성 | REL-TG-AZ | demo-api | 대상 그룹의 모든 대상이 한 AZ에 있음 | DescribeTargetHealth 대상 2개의 AZ = ap-northeast-2c | 대상을 두 개 이상의 AZ에 분산하세요 (Auto Scaling 그룹·ECS 서비스의 서브넷을 여러 AZ로). |
| 중간 | 안정성 | REL-TG-AZ | demo-ip-targets | 대상 그룹의 모든 대상이 한 AZ에 있음 | DescribeTargetHealth 대상 1개의 AZ = ap-northeast-2a | 대상을 두 개 이상의 AZ에 분산하세요 (Auto Scaling 그룹·ECS 서비스의 서브넷을 여러 AZ로). |
| 중간 | 안정성 | REL-TG-UNHEALTHY | demo-web | 비정상 대상 1개 | DescribeTargetHealth.TargetHealth.State = unhealthy/unavailable (조회 시점) | 헬스 체크 경로·보안 그룹·애플리케이션 상태를 확인하세요. |
| 중간 | 보안 | SEC-IMDSV1 | staging-api | IMDSv1 허용 (IMDSv2 강제 안 됨) | DescribeInstances.MetadataOptions.HttpTokens = optional | HttpTokens를 required로 바꿔 IMDSv2만 허용하세요. |
| 낮음 | 운영 | OPS-COVERAGE | 수집 범위 | 조회하지 못한 API 1건 — 검토 결과가 불완전할 수 있음 | describe_instances(UnauthorizedOperation) | 읽기 전용 권한 추가 후 다시 수집하세요: ec2:DescribeInstances |
| 낮음 | 보안 | SEC-EC2-PUBLIC-IP | staging-api | 인스턴스에 퍼블릭 IP 직접 할당 | DescribeInstances.PublicIpAddress = 203.0.113.45 | 로드밸런서·NAT 뒤 프라이빗 서브넷에 두고 관리 접근은 Session Manager를 사용하세요. |

## 해석 범위

- 실제 AWS 조회가 아닌 샘플 데이터입니다.
- ENI·EBS는 참조만 표시하고 보안 그룹 규칙은 검토 결과에만 사용합니다.
- 설정 관계이며 실제 통신이나 보안 적합성을 의미하지 않습니다.

## 검토할 항목

- 조회 불가·부분 조회 범위의 권한 및 재수집 필요 여부
- 참조만 확인된 리소스와 미확인 배치 정보
- VPC·서브넷·가용 영역 배치가 설계 의도와 일치하는지
- 대상 그룹의 등록 대상과 조회 시점의 TargetHealth 상태
- 실제 통신·보안 적합성 판정은 별도 증거로 검토
