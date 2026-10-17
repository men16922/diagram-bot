# 운영 안내

설치, 권한 구성, 여러 계정·리전 수집, CI 연동, 데이터 보관, 문제 해결을 정리합니다. 보안 설계는 [SECURITY.md](../SECURITY.md)를 보세요.

## 설치

```bash
python -m pip install .            # Python 3.11–3.13
diagram-bot setup-editor           # 고정 버전 draw.io 편집기(최초 1회, 오프라인 설치는 --archive draw.war)
diagram-bot --version
diagram-bot serve                  # http://127.0.0.1:8765
```

## 권한

- **단일 계정:** 수집에 쓰는 사용자·역할에 [`readonly-policy.json`](../readonly-policy.json)을 붙입니다.
  모두 `Describe*`/`List*`/`Get*` 액션이며, 수집기가 호출하는 목록과 같은지는 테스트로 확인합니다.
  권한이 일부 없으면 그 범위만 `조회 불가`로 표시되고, 검토 결과에 필요한 권한이 나옵니다.
- **여러 계정 (권장):** 각 멤버 계정에 [`deploy/readonly-role.cfn.json`](../deploy/readonly-role.cfn.json)을 배포합니다.
  AWS Organizations를 쓰면 CloudFormation StackSet(서비스 관리형)으로 한 번에 배포할 수 있습니다.
  - `CollectorPrincipalArn`: 수집을 실행하는 계정의 역할(또는 사용자) ARN
  - `ExternalId`: 공유 비밀값(8자 이상). 수집할 때 `--external-id`로 전달합니다.
  - 역할 세션은 수집기가 ReadOnlyAccess 세션 정책을 함께 걸어 맡으므로, 역할 정책을 실수로 넓혀도 쓰기 작업은 할 수 없습니다.

## 수집

```bash
# 현재 SSO 프로파일 계정, 두 리전
diagram-bot collect --profile prod-sso --expected-account 111122223333 --region ap-northeast-2,us-east-1 --output reports/2026-10-17

# 여러 계정 × 여러 리전 (역할 ARN의 계정을 예상 계정으로 확인)
diagram-bot collect --profile security-tooling \
  --role-arn arn:aws:iam::111122223333:role/DiagramBotReadOnly \
  --role-arn arn:aws:iam::444455556666:role/DiagramBotReadOnly \
  --external-id "$DIAGRAM_BOT_EXTERNAL_ID" --region ap-northeast-2,us-east-1 --output reports/2026-10-17
```

대상이 둘 이상이면 `reports/2026-10-17/<계정>-<리전>/`마다 `snapshot.json`·`topology.drawio`·`index.html`·`inventory.csv`·`review.md`가 생깁니다.
종료 코드는 0 = 모두 완료, 2 = 일부 대상이 부분 조회, 1 = 시작하지 못한 대상이 있음입니다. 실패한 대상이 있어도 나머지 대상은 계속 수집합니다.

수집기는 서비스마다 스레드 하나로 호출하고(같은 서비스의 호출은 순서대로) adaptive 재시도를 씁니다. 스로틀링이 잦은 계정에서도
재시도와 백오프가 SDK 안에서 처리됩니다. 앱에서 수집할 때는 `AWS에서 새로 수집` → `다른 계정의 역할로 수집`에 역할 ARN과 External ID를 넣습니다.

## CI·정기 점검

```bash
# 높음 심각도 결과가 있으면 실패 (종료 코드 3)
diagram-bot review reports/today/snapshot.json --fail-on high

# 어제와 달라진 구성이 있으면 실패 (종료 코드 4) — 드리프트 감지
diagram-bot diff reports/yesterday/snapshot.json reports/today/snapshot.json --fail-on-change --format markdown > drift.md
```

`--format json`으로 받으면 다른 도구에 연결할 수 있습니다. 검토 결과는 수집한 설정만으로 판정하며, 판정에 필요한 API를 읽지 못하면
그 항목은 결과를 내지 않고 `OPS-COVERAGE`로 누락 사실을 알립니다.

## 데이터 보관

| 위치 | 내용 | 비고 |
| --- | --- | --- |
| `~/.cache/diagram-bot/drawio/32.4.1` | 편집기 | `XDG_CACHE_HOME`으로 변경 |
| `~/.local/share/diagram-bot/snapshots` | 수집 스냅샷(JSON) | 원본 수집 데이터. 덮어쓰지 않습니다. |
| `~/.local/share/diagram-bot/documents` | 보기별 편집본과 최근 20개 이력 | 원본과 분리 |

`serve --data-dir`로 위치를 바꿀 수 있습니다. 백업은 이 디렉터리를 복사하면 되고, 스냅샷 파일은 다른 환경에서 `스냅샷 JSON 가져오기`로 열 수 있습니다.
스냅샷에는 리소스 ID·IP·태그가 들어 있으므로 회사 데이터 분류 기준에 맞는 저장소에 보관하세요.

## 공유 환경

앱은 단일 사용자 로컬 실행을 전제로 루프백에만 바인딩합니다. 팀이 함께 쓰려면 다음 중 하나를 고르세요.

- 보고서 공유: `collect`/`render`로 만든 `index.html`(단일 파일 뷰어)과 `topology.drawio`를 사내 저장소에 올립니다.
- 원격 데스크톱·Cloud9·SSM 포트 포워딩(`aws ssm start-session --document-name AWS-StartPortForwardingSession`)으로 각자 접속합니다.

인증 없는 상태로 `0.0.0.0`에 노출하는 실행 옵션은 제공하지 않습니다.

## 문제 해결

| 증상 | 원인과 조치 |
| --- | --- |
| `계정 불일치` | 프로파일이나 역할이 다른 계정을 가리킵니다. `aws sts get-caller-identity --profile …`로 확인하세요. |
| `ExpiredToken`·`InvalidClientTokenId` | SSO 세션 만료 또는 잘못된 키입니다. `aws sso login --profile …` 후 다시 수집하세요. |
| `AccessDenied` (AssumeRole) | 역할 신뢰 정책의 `CollectorPrincipalArn`·External ID가 맞는지 확인하세요. |
| 일부 항목 `조회 불가` | 해당 권한이 없습니다. 검토 결과의 `OPS-COVERAGE`나 수집 범위 패널에 필요한 IAM 액션이 나옵니다. |
| 편집기가 열리지 않음 | `diagram-bot setup-editor`를 실행하세요. 체크섬이 다르면 설치를 중단하고 기존 파일을 유지합니다. |
| 다른 창에서 저장했다는 오류 | 같은 보기를 두 창에서 편집했습니다. 새로고침하면 마지막 저장본과 이력(복원 가능)이 보입니다. |

## 업그레이드

스냅샷 `schema_version`은 1을 유지합니다. 이전 버전 스냅샷은 읽을 때 관계 ID를 채우며, 기존 상세 보기 편집본도 그대로 열립니다.
새 버전에서 생긴 리소스 종류는 다시 수집해야 나타납니다. 변경 내역은 [CHANGELOG.md](../CHANGELOG.md)에 있습니다.
