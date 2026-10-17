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
재시도와 백오프가 SDK 안에서 처리됩니다. 앱에서 수집할 때는 우측 상단 프로필 메뉴의 `AWS 리소스 수집` → `다른 계정의 역할로 수집`에 역할 ARN과 External ID를 넣습니다.

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

## 공유 환경 (팀 모드)

기본 실행은 단일 사용자용으로 루프백(127.0.0.1)에만 열립니다. 팀이 함께 쓰려면 팀 모드로 실행합니다.

```bash
diagram-bot user add alice --role admin  --file /etc/diagram-bot/users.json   # 비밀번호를 두 번 입력 (12자 이상)
diagram-bot user add bob   --role editor --file /etc/diagram-bot/users.json
diagram-bot user add carol --role viewer --file /etc/diagram-bot/users.json
diagram-bot serve --host 0.0.0.0 --port 8443 --data-dir /var/lib/diagram-bot \
  --users /etc/diagram-bot/users.json --certfile /etc/diagram-bot/tls.crt --keyfile /etc/diagram-bot/tls.key \
  --allowed-host diagrams.corp.example.com
```

- 루프백이 아닌 주소는 사용자 파일, TLS 인증서·키, 허용 호스트 이름이 모두 있어야 열립니다. 하나라도 없으면 시작하지 않습니다.
- 역할: `viewer` 구성도·검토·비교 보기 / `editor` 편집 저장·자동 재배치·복원·스냅샷 가져오기 / `admin` AWS 수집과 감사 로그 조회까지.
- 비밀번호는 PBKDF2-SHA256(600,000회, 사용자별 salt)로 저장되고 사용자 파일은 `0600` 권한으로 만들어집니다. 사용자 파일을 바꾸면 서버를 다시 시작하세요.
- 같은 주소에서 로그인을 5번 실패하면 15분 동안 막힙니다. 세션은 8시간 뒤 만료되고 서버를 다시 시작하면 모두 다시 로그인합니다.
- 쿠키는 HttpOnly·Secure·SameSite=Strict이고 HSTS를 보냅니다. Host 헤더는 `--allowed-host`만 허용합니다.
- **감사 로그:** `<data-dir>/audit.log`(JSON Lines)에 로그인 성공·실패·차단, 로그아웃, 모든 변경 요청(사용자·경로·대상·결과 코드·접속 주소), 수집 결과가 남습니다.
  admin은 `GET /api/audit?limit=200`으로 최근 항목을 볼 수 있습니다. 회사 로그 수집기로 이 파일을 전송하세요.
- **SSO (OpenID Connect):** 회사 IdP(Okta, Microsoft Entra ID, Google Workspace, Keycloak, IAM Identity Center의 OIDC 앱 등)에
  웹 애플리케이션을 등록하고 리다이렉트 URI를 `https://<호스트>:<포트>/auth/oidc/callback`으로 지정한 뒤 `--oidc-config oidc.json`으로 실행합니다.
  로컬 계정(`--users`)과 함께 쓸 수도, SSO만 쓸 수도 있습니다.

  ```json
  {"issuer": "https://login.corp.example.com", "client_id": "diagram-bot", "client_secret_env": "DIAGRAM_BOT_OIDC_SECRET",
   "redirect_uri": "https://diagrams.corp.example.com:8443/auth/oidc/callback", "scope": "openid email profile groups",
   "role_claim": "groups", "role_map": {"cloud-architects": "admin", "platform-team": "editor", "auditors": "viewer"}}
  ```

  Authorization Code + PKCE(S256)로 로그인하고 state(브라우저 쿠키와 대조)·nonce·issuer·aud·만료를 확인합니다. 역할은 `role_claim`의 값을
  `role_map`으로 바꾼 것 중 가장 높은 역할이며, 매핑된 그룹이 없으면 `default_role`이 없는 한 로그인을 거부합니다.
  비밀값은 파일 대신 `client_secret_env`의 환경 변수로 넘기는 것을 권장합니다. SAML만 지원하는 IdP는 인증 프록시를 앞에 두세요.
- 수집에 쓰는 AWS 자격 증명은 서버 프로세스의 SDK 인증 체인(인스턴스 역할 등)입니다. 사용자에게 AWS 키를 입력받지 않습니다.

보고서만 공유하려면 `collect`/`render`로 만든 `index.html`(단일 파일 뷰어)과 `topology.drawio`를 사내 저장소에 올리는 방법도 있습니다.

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
