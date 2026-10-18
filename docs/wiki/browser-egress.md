---
title: 브라우저 egress (서버 서명, 브라우저 전송)
summary: 서버가 AWS에 나갈 수 없고 Permission Set이 회사 IP만 허용하는 환경에서, botocore가 만든·서명한 요청을 before-send에서 사용자 브라우저로 넘겨 PC IP에서 보내는 방식.
sources: [src/diagram_bot/egress.py, src/diagram_bot/aws_access.py, src/diagram_bot/routes/aws.py, src/diagram_bot/routes/agent.py, src/diagram_bot/static/js/relay.js, tests/test_egress.py, scripts/verify_browser_egress.py]
related: [aws-access-and-sso, collection, chat-agent, web-app, http-api, porting-guide]
updated: 2026-10-19
---
# 브라우저 egress (서버 서명, 브라우저 전송)

> `serve --aws-egress browser`. Identity Center 로그인과 `GetRoleCredentials`는 서버, 그 역할로 하는 **모든 AWS API 요청은 사용자 브라우저**가
> PC의 출구 IP에서 보낸다. 서버의 boto3가 요청을 만들고 SigV4로 서명하고 응답을 해석하므로 수집기·검토·채팅 도구 코드는 바뀌지 않는다.

## 왜 필요한가
- 서버(예: EC2)의 방화벽이 AWS API로 나가는 길을 막는다.
- Permission Set이 `aws:SourceIp`로 회사 공인 IP 밖의 호출을 거부한다 → 허용된 출구 IP는 사용자 PC다.

## 흐름

```
 서버 스레드 (수집·채팅)                         BrowserRelay                     브라우저 탭 (js/relay.js)
 boto3 client.describe_vpcs()
   → serialize → SigV4 sign (역할 자격 증명)
   → before-send  ── outgoing(request) ──▶ pending[id] (사용자별) ◀── GET /api/relay/next?tab=… (long-poll 20초, async)
      (전송 대신 대기)                                                    fetch(url, signed headers, body) → AWS (CORS)
                                           answer(id, result)       ◀── POST /api/relay/{id} {status, headers, body(base64)}
   ← AWSResponse(status, headers, body) ── botocore가 해석·재시도·페이지 넘김
```

- `BrowserRelay.attach(session, owner)`가 세션 이벤트 `before-send`에 **맨 앞**(`register_first`)으로 핸들러를 건다. 핸들러가 응답을 돌려주면
  botocore는 HTTP 전송을 건너뛴다(`botocore.endpoint`의 `before-send` 처리).
- `outgoing()`은 `https://*.amazonaws.com(.cn)`만 허용하고, **서명에 포함된 헤더 + Authorization**만 보낸다(Host·Content-Length는 브라우저가 설정).
  User-Agent·`amz-sdk-*` 같은 서명 밖 헤더를 빼서 CORS preflight를 좁힌다.
- **탭과 심장 박동.** 탭마다 무작위 `tab` id로 폴링한다(`BrowserRelay.tabs[(owner, tab)]`: 열린 폴 수와 마지막 폴 시각). `relay.js`는 받은 요청을 백그라운드로 보내고 곧바로 다음 폴을 열므로, **열린 폴이 곧 그 탭의 심장 박동**이다(오래 걸리는 Bedrock 호출 중에도 탭이 살아 있다). 열린 폴이 없고 `idle`(15초) 동안 폴이 없거나, 폴 도중 연결이 끊긴 탭은 닫힌 탭이다. 연결 끊김은 `receive()`의 `http.disconnect`로 안다(`routes/aws.py`의 `relay_next`; 앱의 `BaseHTTPMiddleware` 뒤에서는 `Request.is_disconnected()`가 끊김을 보지 못한다).
- **닫힌 탭이 가져간 요청.** 기다리는 `send()`가 `tick`(1초)마다 그 탭을 확인한다. 읽기 작업(`Describe*`·`List*`·`Get*`, `READ`)은 두 번 보내도 해가 없으므로 그 사용자의 다른 열린 탭에 다시 넘긴다. 그 밖의 요청(Bedrock `Converse` 등)은 이미 AWS에 닿았을 수 있어 다시 보내지 않고 즉시 `RelayDisconnected("AWS 요청을 맡은 브라우저 탭이 닫혔습니다…")`. 열린 탭이 하나도 없으면 읽기도 즉시 실패한다. `timeout`(90초)은 살아 있는 탭이 답하지 않을 때만 남는 상한이다.
- **비동기 롱 폴.** `GET /api/relay/next`는 `async` 엔드포인트이고 `BrowserRelay.wait_next()`가 `asyncio.Event`(서버 스레드가 `call_soon_threadsafe`로 깨움)로 기다리므로, 열린 탭 수만큼 워커 스레드를 잡지 않는다(기본 40개 스레드 풀이 채팅·저장·중계 응답에 남는다). 동기 `next()`는 테스트·임베딩용으로 남아 있다.
- 브라우저가 앱을 열고 있지 않으면(살아 있는 탭이 없음) 즉시 `RelayDisconnected("브라우저 중계가 연결되어 있지 않습니다…")`. 시간 안에 응답이 오지 않아도 `RelayDisconnected`다. `RelayDisconnected`는 `RelayError`의 하위 클래스이고, 수집기는 이것만 다시 던져 수집 작업 전체를 실패시킨다(남은 서비스를 모두 `조회 불가`로 채운 가짜 partial 스냅샷을 저장하지 않는다). 요청 하나가 막힌 경우(CORS·프록시, status 0)는 일반 `RelayError`로 그 서비스만 `조회 불가`가 된다.
  요청 하나의 왕복 제한 `timeout` 90초, fetch 실패(status 0)는 `RelayError`(네트워크·CORS·프록시). `RelayError`는 botocore 재시도 대상이 아니라 바로 실패한다.
- 요청·응답은 사용자(`owner`)에 묶인다: 다른 사용자는 가져가거나 답할 수 없다.

## 적용 범위
- **수집:** `routes/aws.py` → `collect(..., prepare=aws.prepare_for(user))` — 프로파일·SSO 역할·AssumeRole 세션 모두.
- **신원 확인:** `/api/identity`의 STS 호출.
- **채팅:** 도구의 읽기 호출(`AwsAccess.session`)과 Bedrock 호출. 이 모드에서 Bedrock은 **비스트리밍 Converse**(`bedrock_model(streaming=False)`,
  `routes.agent.default_chat`) — 중계가 응답 전체를 나르기 때문. 채팅 화면의 도구 진행 스트리밍은 그대로다.
- **서버에 남는 것:** Identity Center(`sso-oidc`, `portal.sso`) — CORS 미지원(2026-10-18 preflight 403/404 확인). 시작 URL 자동 발견(`sso-admin`)은 이 모드에서 하지 않는다.

## CORS 확인 결과 (2026-10-18, 실제 Chromium, 서명된 요청)
수집 서비스 18종(STS, EC2, ELBv2, Auto Scaling, RDS, ElastiCache, Redshift, ECS, EKS, Lambda, EFS, OpenSearch, MSK, FSx, SageMaker,
CloudFront, WAFv2, Direct Connect)과 Bedrock Runtime이 모두 200으로 응답했다. 서버의 AWS 통신을 막은(`HTTPS_PROXY=http://127.0.0.1:9`) 상태에서
앱 UI로 수집 42/42 완료, 채팅 1턴(Bedrock) 성공, AWS 요청 46개가 모두 브라우저에서 나갔다(`scripts/verify_browser_egress.py`).

## 보안 성질
- 서버는 **자기 boto3 클라이언트가 만든 요청만** 서명한다. "이 요청을 서명해 달라"는 API가 없어 임의 서명 오라클이 되지 않는다.
- 브라우저는 요청별 서명(그 요청에만 유효, 최대 15분)과 세션 토큰 헤더를 받지만 비밀 키는 받지 않으므로 새 요청을 만들 수 없다.
- 브라우저가 돌려준 응답은 그 사용자 자신의 스냅샷에만 영향을 준다.
- 중계 응답 POST는 요청마다 감사 로그에 남기지 않고, 수집·채팅 단위로 남긴다(`via: …/browser`).

## 회사 환경에서 확인하는 법
```bash
HTTPS_PROXY=http://127.0.0.1:9 NO_PROXY=127.0.0.1,localhost \
  diagram-bot serve --aws-egress browser --sso-start-url https://d-1234567890.awsapps.com/start --port 8796
python scripts/verify_browser_egress.py --url http://127.0.0.1:8796   # 브라우저가 호출한 AWS 호스트·수집·채팅 결과 보고
```
스크립트는 자체 Chromium을 띄워 서버의 SDK 체인(로컬 자격 증명 버튼)으로 로그인한다. Identity Center 경로는 device code 승인이 사람 몫이라 앱에서 직접 로그인하고 DevTools Network에서 `*.amazonaws.com` 요청이 브라우저에서 나가는지 확인한다.

## 바꿀 때 주의
- 새 서비스를 수집하면 그 엔드포인트의 CORS를 실제 브라우저로 확인한다(서명된 요청 기준). CORS가 없으면 이 모드에서 그 호출은 `RelayError`로 `조회 불가`가 된다.
- 테스트: `tests/test_egress.py` — 서명 헤더만 전달, 실패가 매달리지 않음, 닫힌 탭의 읽기는 다른 탭으로·나머지는 즉시 실패, 비동기 폴이 스레드를 잡지 않음, 에뮬레이터에서 중계 수집 = 직접 수집, HTTP 중계 API 종단 간.
- 재전송 대상은 `READ` 정규식(작업 이름)으로만 정한다. 쓰기·과금 호출을 이 목록에 넣지 않는다.

## 관련
- [[aws-access-and-sso]] · [[web-app]](js/relay.js) · [[http-api]](`/api/relay/*`) · [[porting-guide]]
