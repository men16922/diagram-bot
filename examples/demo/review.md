# TA 검토 자료

- 계정: 111122223333
- 리전: ap-northeast-2
- 수집 구간: 2026-10-17T05:23:38.795542+00:00 ~ 2026-10-17T05:23:38.795542+00:00
- 수집 상태: partial
- 샘플 데이터: True

## 조회 범위와 결과

- ec2 / describe_vpcs: complete, 3개, 오류 없음; 범위 {}
- ec2 / describe_subnets: complete, 5개, 오류 없음; 범위 {}
- ec2 / describe_instances: partial, 4개, 오류 UnauthorizedOperation; 필요 권한 ec2:DescribeInstances; 범위 {}
- elbv2 / describe_load_balancers: complete, 2개, 오류 없음; 범위 {}
- elbv2 / describe_target_groups: complete, 3개, 오류 없음; 범위 {}
- elbv2 / describe_target_health: complete, 3개, 오류 없음; 범위 {"TargetGroupArn": "arn:aws:elasticloadbalancing:ap-northeast-2:111122223333:targetgroup/demo-web/456"}
- elbv2 / describe_target_health: complete, 2개, 오류 없음; 범위 {"TargetGroupArn": "arn:aws:elasticloadbalancing:ap-northeast-2:111122223333:targetgroup/demo-api/567"}
- elbv2 / describe_target_health: complete, 1개, 오류 없음; 범위 {"TargetGroupArn": "arn:aws:elasticloadbalancing:ap-northeast-2:111122223333:targetgroup/demo-ip-targets/678"}

## 해석 범위

- 실제 AWS 조회가 아닌 샘플 데이터입니다.
- 보안 그룹·ENI·EBS는 참조만 표시합니다.
- 설정 관계이며 실제 통신이나 보안 적합성을 의미하지 않습니다.

## 검토할 항목

- 조회 불가·부분 조회 범위의 권한 및 재수집 필요 여부
- 참조만 확인된 리소스와 미확인 배치 정보
- VPC·서브넷·가용 영역 배치가 설계 의도와 일치하는지
- 대상 그룹의 등록 대상과 조회 시점의 TargetHealth 상태
- 실제 통신·보안 적합성 판정은 별도 증거로 검토
