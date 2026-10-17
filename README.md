# Diagram Bot — AWS 현행 구성도·리소스 내역 자동 생성

기존 AWS IAM Identity Center(SSO) 인증을 boto3에서 재사용하여 한 계정·한 리전의 리소스를 읽고, 명시적인 ID·ARN 관계로 구성도를 생성합니다. LLM이나 외부 구성도 서비스는 사용하지 않습니다.

저장소: [men16922/diagram-bot](https://github.com/men16922/diagram-bot). 개발 및 push 방법은 [GITHUB.md](GITHUB.md)에 있습니다. 현재 Python 패키지와 실행 명령은 `diagram-bot`입니다. CI는 AWS 인증 없이 테스트와 패키지 빌드를 수행합니다.

다음 버전은 draw.io 기반 구성도 편집과 `.drawio`·PNG 다운로드를 목표로 합니다. 화면·구조·AWS 지원 범위와 단계별 완료 기준은 [AWS 구성도 탐색 도구 개발 계획](docs/PLAN.md)에 정리했습니다. 계획에 기재한 신규 기능은 아직 구현하지 않았습니다.

## 실행

Python 3.11 이상이 필요합니다.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'

# 인증 없이 기능 확인
diagram-bot demo --output reports/demo

# 이미 설정된 SSO 프로파일로 인증 (사용자 로컬 환경에서 실행)
aws sso login --profile my-sso-profile
diagram-bot collect --profile my-sso-profile --region ap-northeast-2 \
  --expected-account 123456789012 --output reports/current
```

`reports/current/index.html`을 브라우저에서 엽니다. 서버·CDN·네트워크 연결 없이 구성도, 필터, 속성, 관계 근거, 조회 상태, 내역을 볼 수 있습니다. 계정은 프로파일과 `--expected-account`, 리전은 `--region`으로 선택합니다. 계정 ID가 다르면 리소스를 조회하지 않고 종료합니다. 현재 버전에는 브라우저 기반 SSO 로그인이나 계정 목록 선택 화면이 없습니다.

`--profile`을 생략하면 boto3의 기본 인증 체인을 사용합니다. 기존 `AWS_CONFIG_FILE`·`AWS_PROFILE`·credential_process 설정도 SDK가 처리합니다. 키나 토큰을 코드에 저장하지 않습니다. 환경에서 특정 AWS identity의 프로파일/설정 파일을 제공했다면 해당 선택자를 그대로 사용하세요.

권한 예시는 `readonly-policy.json`에 있습니다. 수집기는 `sts:GetCallerIdentity`와 나열된 Describe API만 호출합니다. STS 호출 자체에는 별도의 Allow 권한이 필요하지 않지만 인증 및 접속은 가능해야 합니다. 정책 파일은 자동 적용하지 않습니다.

## 산출물

| 파일 | 내용 |
| --- | --- |
| `index.html` | 자체 포함 구성도·속성 패널·검색·종류/관계 필터·조회 상태 |
| `snapshot.json` | 원본 응답 데이터, 정규화 그래프, API별 성공/실패, 수집 시각·범위 |
| `inventory.csv` | 같은 스냅샷에서 생성한 내역, 참조 여부, 속성, 수집 구간·상태 |
| `review.md` | 동일 데이터의 조회 결과 및 TA 검토 항목; 자동 적합성 판정은 아님 |

HTML의 JSON/CSV 다운로드는 **전체 스냅샷**, SVG 다운로드는 **현재 화면 필터** 기준입니다. SVG는 브라우저에서 확인할 수 있습니다. AWS 속성과 태그가 산출물에 들어가므로 내부 자료와 동일하게 취급하세요. CSV의 수식으로 해석될 수 있는 텍스트에는 작은따옴표를 붙입니다.

기존 스냅샷을 다시 렌더링하려면:

```bash
diagram-bot render reports/current/snapshot.json --output reports/rerendered
pytest -q
```

## 범위와 관계 근거

| 리소스·관계 | API 근거 |
| --- | --- |
| VPC, 서브넷과 AZ | DescribeVpcs, DescribeSubnets.VpcId/AvailabilityZone |
| EC2 → VPC·서브넷 | DescribeInstances.VpcId/SubnetId |
| EC2 → 보안 그룹·ENI·EBS | DescribeInstances의 식별자 참조 |
| ELBv2 → VPC·여러 서브넷·보안 그룹 | DescribeLoadBalancers |
| 대상 그룹 ↔ 로드밸런서 | DescribeTargetGroups.LoadBalancerArns |
| 대상 그룹 → 등록 대상 | DescribeTargetHealth.Target, TargetType |

ALB를 포함한 ELBv2 리소스를 조회합니다. Classic ELB는 포함하지 않습니다. ALB는 여러 서브넷에 속할 수 있으므로 VPC 수준에 한 번만 표시하고 서브넷 배치 관계를 선택해 확인합니다. EC2는 서브넷 내부에 표시합니다. 그룹 박스는 필터된 리소스의 배치를 설명하기 위해 남을 수 있습니다.

보안 그룹·ENI·EBS는 참조 노드입니다. 이 리소스들의 상세 속성은 직접 수집하지 않습니다. 등록 대상의 EC2가 조회되지 않았으면 참조 노드로 남기며 존재하지 않는다고 단정하지 않습니다. IP·Lambda 대상은 별도 등록 대상으로 표현하며 IP가 같다는 이유로 EC2와 합치지 않습니다. 관계에는 API/필드 근거와 `inferred: false`가 기록됩니다.

보안 그룹의 허용 규칙을 트래픽으로 해석하지 않습니다. 리스너·라우팅·RDS·EKS·Flow Logs·변경 비교는 이번 범위 밖입니다. TargetHealth는 조회 시점의 상태이며 실제 요청 흐름의 증거가 아닙니다.

## 실패와 정확성

- SDK paginator가 제공하는 모든 페이지를 순회합니다. 표준 재시도는 최대 4회 시도이며 연결/읽기 타임아웃은 10/30초입니다.
- `complete`: 해당 API 범위 조회 완료. 0개인 경우에만 조회 결과가 비어 있다고 볼 수 있습니다.
- `partial`: 뒤쪽 페이지 실패. 앞서 조회한 리소스는 보존합니다.
- `unavailable`: 한 페이지도 조회하지 못함. 리소스가 없다는 의미가 아닙니다.
- `not_discovered`: 대상 그룹 조회 실패로 미발견 그룹의 대상 조회가 불가능함.
- 일부 실패 시에도 보고서를 생성하고 `collect`는 종료 코드 2를 반환합니다. 정상 수집은 0, 인증/계정 불일치/파일 오류는 1입니다.
- API 순차 조회이므로 실행 중 리소스 변경에 따른 불일치가 가능하며 원자적 스냅샷은 아닙니다. 수집 시작·종료 시각 및 API별 시각을 기록합니다.

## 검증 범위

pytest 테스트는 botocore Stubber로 EC2/ELB 페이지네이션, 페이지 중간 권한 오류, 빈 결과와 조회 불가 구분, 계정 불일치, 대상 조회 누락을 확인합니다. 그래프 테스트는 여러 서브넷의 ALB, 미조회 참조, IP 대상 분리, HTML/CSV 안전 처리, 동일 데이터 출력을 확인합니다.

이 작업 환경에는 AWS 인증이 연결되어 있지 않아 실제 계정의 API 권한·네트워크·SSO 만료/갱신은 검증하지 못했습니다. `examples/demo`는 의도적으로 부분 조회 상태를 포함한 샘플입니다.
