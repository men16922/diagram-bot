# TA 검토 자료

- 계정: 111122223333
- 리전: ap-northeast-2
- 수집 구간: 2026-10-17T05:06:22.012876+00:00 ~ 2026-10-17T05:06:22.012876+00:00
- 수집 상태: complete
- 샘플 데이터: True

## 조회 범위와 결과

- ec2 / describe_vpcs: complete, 1개, 오류 없음; 범위 {}
- ec2 / describe_subnets: complete, 4개, 오류 없음; 범위 {}
- ec2 / describe_instances: complete, 4개, 오류 없음; 범위 {}
- elbv2 / describe_load_balancers: complete, 1개, 오류 없음; 범위 {}
- elbv2 / describe_target_groups: complete, 4개, 오류 없음; 범위 {}
- elbv2 / describe_target_health: complete, 2개, 오류 없음; 범위 {"TargetGroupArn": "arn:aws:elasticloadbalancing:ap-northeast-2:111122223333:targetgroup/catalog-svc/0000000000000001"}
- elbv2 / describe_target_health: complete, 2개, 오류 없음; 범위 {"TargetGroupArn": "arn:aws:elasticloadbalancing:ap-northeast-2:111122223333:targetgroup/orders-svc/0000000000000002"}
- elbv2 / describe_target_health: complete, 2개, 오류 없음; 범위 {"TargetGroupArn": "arn:aws:elasticloadbalancing:ap-northeast-2:111122223333:targetgroup/cart-svc-ecs/0000000000000003"}
- elbv2 / describe_target_health: complete, 2개, 오류 없음; 범위 {"TargetGroupArn": "arn:aws:elasticloadbalancing:ap-northeast-2:111122223333:targetgroup/payments-svc-ecs/0000000000000004"}

## 해석 범위

- AWS best practice 패턴을 따른 합성 샘플이며 실제 계정 데이터가 아닙니다.
- RDS·NAT Gateway·라우팅·보안 그룹 규칙은 수집 범위 밖이라 표시하지 않습니다.
- 설정 관계이며 실제 통신이나 보안 적합성을 의미하지 않습니다.

## 검토할 항목

- 조회 불가·부분 조회 범위의 권한 및 재수집 필요 여부
- 참조만 확인된 리소스와 미확인 배치 정보
- VPC·서브넷·가용 영역 배치가 설계 의도와 일치하는지
- 대상 그룹의 등록 대상과 조회 시점의 TargetHealth 상태
- 실제 통신·보안 적합성 판정은 별도 증거로 검토
