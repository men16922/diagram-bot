---
title: 팀 모드와 보안 경계
summary: 앱 로그인(로컬 계정·OpenID Connect), 세션·역할, create_app 미들웨어(TrustedHost, 페이지 토큰, Origin, 본문 제한, 보안 헤더), 감사 로그, 비루프백 TLS 요구와 저장하지 않는 비밀.
sources: [src/diagram_bot/auth.py, src/diagram_bot/expiry.py, src/diagram_bot/oidc.py, src/diagram_bot/server.py, src/diagram_bot/routes/session.py, src/diagram_bot/routes/__init__.py, src/diagram_bot/cli.py, SECURITY.md, docs/OPERATIONS.md, tests/test_team.py, tests/test_oidc.py, tests/test_server.py]
related: [http-api, storage, web-app, chat-agent, browser-egress, aws-access-and-sso, cli]
updated: 2026-10-19
---
# 팀 모드와 보안 경계

> 기본 실행은 `127.0.0.1`에만 열리는 단일 사용자 앱이다. 다른 주소로 열려면 팀 모드(사용자 파일 또는 OIDC, TLS, 허용 호스트)가 필수이고, 그때 로그인·역할·감사가 켜진다. AWS 로그인(Identity Center)은 이와 별개다([[http-api]]의 aws 라우터).

## 핵심
- 팀 모드 여부는 `create_app(users=..., oidc=...)`에서 정해진다. 둘 중 하나라도 있으면 `context.auth = Authenticator(users or {})`, 없으면 `None`(사용자 `local`, 역할 `admin` 취급).
- 역할은 `auth.ROLES = ("viewer", "editor", "admin")`. 서버가 검사하고 프런트엔드는 버튼만 끈다([[web-app]]).
- 비밀번호·세션 토큰·OIDC 토큰·AWS 자격 증명은 응답·로그·스냅샷에 쓰지 않는다. 세션은 서버 메모리에만 있어 재시작하면 모두 다시 로그인한다.

## 비루프백은 TLS 필수 (`cli.run_server`)
- `--host`가 `LOOPBACK_HOSTS`(`127.0.0.1`, `localhost`, `::1`)가 아니면 `(--users 또는 --oidc-config)`, `--certfile`, `--keyfile`, `--allowed-host`가 모두 있어야 시작한다. 하나라도 빠지면 `parser.error`.
- `--certfile`과 `--keyfile`은 함께 지정해야 한다. `secure = bool(args.certfile)`이 `create_app(secure=...)`로 가서 쿠키 `Secure`와 HSTS를 켠다. uvicorn에 `ssl_certfile`·`ssl_keyfile`로 넘긴다.
- 테스트: `tests/test_team.py`의 `test_cli_refuses_unauthenticated_network_exposure_and_manages_users`.

## 로컬 계정 (`auth.py`)
- 사용자 파일: `{"users": [{"name", "role", "password_hash"}]}`. `load_users`는 역할이 `ROLES` 밖이거나 해시가 `pbkdf2_sha256$`로 시작하지 않으면, 또는 사용자가 없으면 `ValueError`.
- `save_user`는 임시 파일을 `0o600`으로 만든 뒤 교체한다. `remove_user`는 없는 이름이면 `ValueError`. CLI는 `diagram-bot user add|remove|list [이름] --file <경로> [--role] [--password-stdin]`(`cli.manage_user`, 비밀번호 12자 이상). 사용자 파일은 데이터 디렉터리와 별개다.
- `hash_password`: PBKDF2-SHA256, 사용자별 16바이트 salt, `ITERATIONS = 600_000`, 형식 `pbkdf2_sha256$<반복>$<salt b64>$<digest b64>`. `verify_password`는 `hmac.compare_digest`.
- `Authenticator.login`: 없는 이름도 `self.dummy` 해시로 한 번 검증해 응답 시간으로 이름 존재가 드러나지 않게 한다. 실패는 클라이언트 주소별로 기록한다.
- 차단: `throttled(client, name)`은 `FAILURE_WINDOW = 15 * 60`초 안의 실패가 (주소, 이름)별로 `FAILURE_LIMIT = 5`번 이상이거나 주소 전체로 `CLIENT_FAILURE_LIMIT = 50`번 이상이면 참. 인증 프록시 뒤에서는 모든 사용자가 같은 주소이므로, 한 계정의 실패가 다른 사용자를 막지 않고 주소 상한은 여러 이름에 걸친 추측만 막는다. `POST /api/login`이 429로 막고 감사에 `throttled`를 남긴다. 성공하면 그 (주소, 이름)의 실패 기록을 지운다.
- 세션: `secrets.token_urlsafe(32)` 토큰 → `{name, role, expires}`, `SESSION_SECONDS = 8 * 3600`(단조 시계, `Authenticator(users, clock=)`로 주입). `session(token)`이 만료된 항목을 지운다. 만료된 세션과 창(`FAILURE_WINDOW`)이 지난 실패 기록은 `session()`·`throttled()`가 불릴 때 `expiry.Purge`로 한꺼번에 지운다(분당 한 번, 타이머 스레드 없음). `start_session(name, role)`은 OIDC처럼 밖에서 확인한 신원용.

## 역할 (`auth.allowed`)
- `GET`·`HEAD`와 `/api/logout`은 모든 역할 허용.
- `/api/collections`·`/api/sso`로 시작하는 변경 요청은 `admin`만.
- 그 밖의 변경 요청(문서 저장·재배치·복원, 스냅샷 가져오기·통합, `/api/xml/align`·`/api/xml/import`·`/api/xml/adopt`, 채팅, `/api/relay/*` 응답)은 `editor`·`admin`. 문서 `?spec=` 미리보기는 GET이라 `viewer`도 되지만 저장할 수 없다(`revision: null`).
- 핸들러 안의 추가 검사: `GET /api/identity`와 `GET /api/audit`은 팀 모드에서 `admin`만, 채팅의 AWS 수집 도구도 `admin`만(`Workspace.allow_collection`), `POST /api/xml/adopt`의 `make_default=true`(서버 전체 기본 규격·표준 등록)도 `admin`만(`routes.documents`, 수집과 같은 등급).

## OpenID Connect (`oidc.py`)
- 설정(`load_config`): `issuer`, `client_id`, `redirect_uri`, `role_claim`, `role_map` 필수. `client_secret` 대신 `client_secret_env`(환경 변수 이름)를 쓸 수 있다. `issuer`·`redirect_uri`는 https(루프백 `localhost`·`127.0.0.1`만 http 허용, `secure_url`). `role_map` 값과 `default_role`은 `viewer`·`editor`·`admin` 중 하나.
- `discovery()`: `<issuer>/.well-known/openid-configuration`의 `issuer`가 설정과 같아야 하고 `authorization_endpoint`·`token_endpoint`가 https여야 한다.
- `start()`: `state`, `nonce`, PKCE `verifier`를 만들고(만료 `PENDING_SECONDS = 600`) `code_challenge_method=S256`으로 인가 URL을 만든다. 라우터는 `state`를 `diagram_bot_oidc` 쿠키(HttpOnly, SameSite=Lax, `path=/auth/oidc`, 600초)에 넣는다. Lax인 이유는 제공자가 교차 사이트 최상위 GET으로 돌려보내기 때문이다.
- `finish(code, state, cookie_state)` 검사 순서:
  1. 쿼리 `state`와 쿠키 값이 `compare_digest`로 같은지(다른 브라우저에서 시작한 로그인 거부)
  2. 대기 항목이 있고 만료 전인지
  3. 토큰 엔드포인트에서 code + `code_verifier`(+ `client_secret`) 교환
  4. ID 토큰의 `iss`, `aud`(목록이면 포함), `nonce`, `exp`를 확인
  5. `userinfo_endpoint`가 있으면 그 `sub`가 ID 토큰과 같아야 하고, 클레임을 합친다
- ID 토큰 서명은 검증하지 않는다. TLS로 토큰 엔드포인트에서 직접 받은 토큰이라는 근거(OIDC Core 3.1.3.7)가 모듈 docstring에 있다.
- 역할: `role_claim` 값(문자열 또는 목록)을 `role_map`으로 바꾼 것 중 가장 높은 역할(`ROLE_ORDER`). 하나도 없으면 `default_role`, 그것도 없으면 거부. 사용자 이름은 `email` → `preferred_username` → `sub`.
- 콜백 성공 시 `/`로 바로 리다이렉트하지 않고 meta refresh HTML을 준다. 같은 사이트 페이지 적재라야 다음 요청에 Strict 세션 쿠키가 실리기 때문이다(`routes/session.py` 주석).

## 미들웨어 (`server.create_app`)
1. **TrustedHostMiddleware** — 허용 호스트는 `LOOPBACK`(`localhost`, `127.0.0.1`, `[::1]`, `testserver`)과 `--allowed-host`. 마지막에 등록해 가장 먼저 실행되므로 모르는 `Host`는 세션·리다이렉트 처리 전에 거부된다(DNS 리바인딩 방어).
2. **`protect_mutations`** (`@app.middleware("http")`):
   - 팀 모드 세션 확인(401 또는 303 → `/login`), `allowed()` 역할 확인(403 + 감사 `denied`).
   - 변경 요청: `Origin`이 있으면 netloc이 `Host`와 같아야 함(403), `/api/login` 외에는 `X-Diagram-Bot` 페이지 토큰 일치(403), 본문 15 MiB 이하(413). `Content-Length`가 넘으면 읽기 전에 거부하고, 길이가 없는(chunked) 본문은 15 MiB까지만 읽는다(`server.read_capped`). 인증 전인 `/api/login`도 메모리를 채울 수 없다.
   - 응답 후: 감사 기록, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `secure`면 `Strict-Transport-Security: max-age=31536000`, `/api/*`와 `/`에 `Cache-Control: no-store`, `/`에 `X-Frame-Options: DENY`.
   - 미들웨어가 먼저 돌려준 401·403·413 응답에는 위 보안 헤더가 붙지 않는다(`call_next` 뒤에서 붙이므로).
- 페이지 토큰은 프로세스당 하나(`secrets.token_urlsafe(32)`)로 사용자별이 아니다. CSRF 방어 수단이고, 인증은 세션 쿠키가 맡는다. 토큰은 `GET /api/bootstrap`이 주며 팀 모드에서는 이 요청에도 세션이 필요하다.
- 세션 쿠키 `diagram_bot_session`: HttpOnly, `secure`일 때 Secure, SameSite=Strict, `path=/`, `max_age=SESSION_SECONDS`. 로그인 페이지 응답에도 `X-Frame-Options: DENY`와 `no-store`가 붙는다.

## 감사 로그 (`auth.AuditLog`)
- 위치: `<data-dir>/audit.log`(`create_app`이 `directory / "audit.log"`로 만든다, [[storage]]). 추가 전용 JSON Lines, 스레드 잠금.
- 한 줄: `{"time": UTC ISO 8601, "user", "action", "target", "result", "client", ...extra}`. 예: 미들웨어 항목은 `action="POST /api/snapshots"`, `result=201`; 수집은 `action="collection"`, `result="complete"`, `snapshot`, `via`(`sdk`, `sso:<역할>`, 브라우저 egress면 `/browser` 접미사).
- 제외: `/api/login`·`/api/logout`(핸들러가 `login`·`logout`으로 기록), `/api/relay/*`. 비밀번호는 기록하지 않는다. OIDC 실패 이유는 200자로 자른다.
- `tail(limit)`은 파일 끝에서부터 읽어 최신 항목부터 돌려주고, 쓰다 끊긴(해석할 수 없는) 줄은 건너뛴다. `GET /api/audit`이 쓴다. 로컬 모드에서도 기록된다(사용자 `local`, `test_local_mode_is_unchanged_and_audited`).

## 저장하지 않는 것
- AWS 키·세션 토큰: 수집은 SDK 인증 체인이나 Identity Center 역할 자격 증명(서버 메모리)으로 하고 앱 데이터에 쓰지 않는다. SSO의 client secret·device code·액세스 토큰도 서버 메모리에만 둔다(`SECURITY.md` 보안 모델 표).
- 브라우저 egress에서도 브라우저는 요청 하나의 서명만 받는다([[browser-egress]]).
- 앱 세션·OIDC 대기 상태는 메모리에만 있다. 사용자 파일에는 해시만 있다.
- 저장 데이터 암호화와 SAML 직접 연동은 범위 밖이다(`SECURITY.md`의 범위 밖 절).

## 바꿀 때 주의
- 새 변경 경로를 추가하면 `allowed()`의 접두사 규칙과 감사 제외 목록을 확인한다. `PUBLIC`에 경로를 더하면 인증 없이 열린다.
- 미들웨어 순서(TrustedHost가 마지막 등록)를 바꾸지 않는다.
- 쿠키 속성·차단 수치·세션 시간을 바꾸면 `docs/OPERATIONS.md`의 공유 환경 절과 `SECURITY.md` 표, `tests/test_team.py`, `tests/test_oidc.py`를 함께 고친다.

## 관련
- [[http-api]] — 엔드포인트별 역할과 상태 코드.
- [[storage]] — `audit.log`가 놓이는 데이터 디렉터리.
- [[chat-agent]] — 수집 도구의 `admin` 제한.
- [[browser-egress]] — 브라우저가 AWS 요청을 보낼 때의 경계.
- [[aws-access-and-sso]] — 앱 로그인과 별개인 AWS(Identity Center) 로그인.
- [[cli]] — `serve`·`user` 하위 명령의 전체 옵션.
- 운영 절차는 `docs/OPERATIONS.md`의 "공유 환경 (팀 모드)", 정책 요약은 `SECURITY.md`의 "보안 모델".
