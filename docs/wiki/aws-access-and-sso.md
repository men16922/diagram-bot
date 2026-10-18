---
title: AWS 접근과 Identity Center 로그인
summary: 사용자별 AWS 세션을 만드는 한 곳(aws_access.AwsAccess) — Identity Center device code 로그인, 역할 자격 증명, 프로파일·SDK 체인, AssumeRole, egress 경로 선택.
sources: [src/diagram_bot/aws_access.py, src/diagram_bot/sessions.py, src/diagram_bot/sso.py, src/diagram_bot/expiry.py, src/diagram_bot/routes/aws.py, src/diagram_bot/collector.py, tests/test_sso.py, tests/test_credentials.py]
related: [browser-egress, collection, http-api, team-mode-security, porting-guide]
updated: 2026-10-19
---
# AWS 접근과 Identity Center 로그인

> 앱이 사용자 대신 AWS를 부를 때 쓰는 boto3 세션은 모두 `aws_access.AwsAccess`에서 나온다. 자격 증명은 Identity Center 역할
> 또는 서버의 SDK 체인, 경로는 서버 직접 또는 사용자 브라우저([[browser-egress]]). 회사 인증으로 바꾸려면 이 클래스만 바꾸면 된다.

## 핵심
- **두 가지 로그인은 별개다.** 앱 자체 로그인(팀 모드 계정·OIDC, [[team-mode-security]])과 AWS 로그인(이 페이지)은 따로 동작한다.
- **자격 증명은 서버 메모리에만.** device code, 액세스 토큰, 역할 자격 증명은 `sso.SsoLogins.flows`에만 있고 디스크·응답·로그에 남지 않는다.
- **읽기 전용.** 수집기는 Describe/List/Get 호출만 하며, `role_arn`으로 수집할 때는 `ReadOnlyAccess` 세션 정책을 붙여 AssumeRole 한다(`collector.assume`).

## Identity Center device code 흐름 (`sso.SsoLogins`)

| 단계 | 서버 호출 (boto3) | HTTP API |
| --- | --- | --- |
| 1. 시작 | `sso-oidc.register_client` → `start_device_authorization(startUrl)` | `POST /api/sso/login` {start_url, region} → user_code, 승인 링크 |
| 2. 승인 대기 | `sso-oidc.create_token(grantType=device_code)` (interval마다, `SlowDown` 시 간격 +5초) | `GET /api/sso/login/{id}` → pending / authorized / expired / denied |
| 3. 계정 목록 | `sso.list_accounts(accessToken)` | 같은 응답의 `accounts` |
| 4. 역할 목록 | `sso.list_account_roles` | `GET /api/sso/login/{id}/roles?account_id=` |
| 5. 역할 선택 | — (서버에 선택 기록, 채팅 대화 초기화) | `POST /api/sso/login/{id}/use` {account_id, role} |
| 6. 수집·채팅 | `sso.get_role_credentials` → 역할 세션(요청 때마다 새로 받음) | `POST /api/collections` {sso_login, sso_role, …} |
| 7. 로그아웃 | `sso.logout(accessToken)` | `POST /api/sso/login/{id}/logout` |

- `SsoLogins.lock` 안에서는 AWS를 부르지 않는다. `poll`은 잠금 안에서 flow 상태를 복사하고, 잠금 밖에서 CreateToken·ListAccounts를 부른 뒤, 다시 잠금 안에서 결과를 반영한다(동시에 온 두 번째 poll은 교환을 반복하거나 되돌리지 않는다). Identity Center 응답 하나가 느려도 다른 사용자의 poll·역할 조회·세션이 멈추지 않는다.
- flow는 시작한 앱 사용자(`owner`)에게 묶인다. 다른 사용자는 조회·사용할 수 없다(`SsoLogins.flow`).
- 새 로그인을 시작하면 그 사용자의 승인 전 flow를 버린다. 기한(승인 전 device code의 `expires_at`, 승인 뒤 토큰의 `token_expires_at`)이 `FLOW_GRACE`(1시간) 넘게 지난 flow는 `start`·`flow`가 불릴 때 `expiry.Purge`로 지운다(늦은 poll은 그동안 `expired`를 받는다).
- 시작 URL 기본값: `serve --sso-start-url/--sso-region`(회사 설정) → `~/.aws/config`의 sso-session·프로파일 → 서버 자격 증명으로 `sso-admin.list_instances` 발견
  (브라우저 egress에서는 발견 생략). `AwsAccess.start_urls`, `GET /api/sso/defaults`.
- Identity Center 엔드포인트(`oidc.<region>`, `portal.sso.<region>`)는 CORS를 허용하지 않으므로, 브라우저 egress에서도 이 흐름은 **서버**에서 돈다.

## `AwsAccess` — 세션을 만드는 한 곳

| 메서드 | 쓰는 곳 | 설명 |
| --- | --- | --- |
| `outbound(session, user)` | 아래 메서드 모두 | 브라우저 egress면 세션에 중계를 붙인다 |
| `profile_session(user, profile=None, region=None)` | 신원 확인(`/api/identity`), SSO 역할이 없을 때의 채팅 | 서버 SDK 체인(이름 있는 프로파일, 아니면 기본 체인) + 사용자 경로 |
| `prepare_for(user)` | 수집 (`collect(prepare=…)`) | 수집이 쓰는 모든 세션(AssumeRole 세션 포함)에 같은 경로 적용 |
| `collection_session(user, login, account, role, region)` | 수집 | SSO 역할이면 역할 세션 + 선택 기록, 아니면 `None`(프로파일·SDK 체인) |
| `session(user, region)` | 채팅 도구 | 선택한 SSO 역할, 없으면 `profile_session` |
| `model_session(user, region)` | 채팅 Bedrock 모델 | 역할 자격 증명을 쓸 때 받고 만료 전에 다시 받는 세션(`sessions.refreshing_session`) |
| `choose` / `forget` / `chosen_account` | `/use`, 로그아웃, 채팅 기본 계정 | 사용자별 (login, account, role) |
| `via(sso_role)` | 감사 로그 | `sso:Role` 또는 `sdk`, 브라우저 egress면 `/browser` |

- 세션 자체는 `sessions.py`에서만 만든다: `sdk_session(region, profile)`(SDK 체인), `credentials_session(credentials, region)`(STS의 `AccessKeyId…` 모양과 botocore 갱신 형식 `access_key…` 모두; `collector.assume`, `SsoLogins.session`), `refreshing_session(fetch, region)`. CLI·라이브러리 기본값(`collector.collect`, `agent.Workspace`, `agent.bedrock_model`)도 같은 함수를 쓴다.
- 수집 시 예상 계정 ID와 STS `GetCallerIdentity` 계정이 다르면 수집하지 않는다(`collector.collect`의 `expected_account`).
- 팀 모드에서 AWS 관련 API(`/api/collections`, `/api/sso/*`)는 admin만 쓸 수 있다(`auth.allowed`).

## 회사 인증으로 바꿀 때
- 회사가 이미 Identity Center 로그인과 `GetRoleCredentials`를 갖고 있다면, `AwsAccess.session`/`collection_session`이 그 자격 증명으로
  `sessions.credentials_session(credentials, region)`을 돌려주게 바꾸면 된다. 수집·채팅·검토 코드는 그대로다.
- 백엔드가 AWS에 나갈 수 없으면 `egress="browser"`를 유지한다([[browser-egress]]).

## 바꿀 때 주의
- 토큰·자격 증명을 응답이나 감사 로그에 넣지 않는다(`tests/test_sso.py`가 감사 로그에 비밀이 없음을 확인).
- 역할 자격 증명은 요청 때마다 새로 받는다(만료 대비). 세션을 오래 캐시하지 않는다.

## 관련
- [[browser-egress]] — 서버가 서명하고 브라우저가 보내는 경로.
- [[collection]] — 세션으로 무엇을 호출하는지.
