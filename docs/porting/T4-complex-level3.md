# T4 Level 3: 회사 FastAPI에 라우터 장착

## 담당·예산
- GPT-6.1 Sol, effort high. 상한 $20, 세션당 30턴, 세션 ≤3. 컨텍스트 추정 ~90K / 상한 150K.

## 목표
- 회사 FastAPI 앱에 snapshots·documents·aws 라우터를 회사 인증 미들웨어 뒤에 붙인다.

## 읽을 것
- `docs/porting/RULES.md` (~0.4K)
- `docs/wiki/porting-guide.md` §4 (~0.8K)
- `docs/wiki/http-api.md` §핵심, §다른 FastAPI 앱에 붙이기 (~0.6K)
- `docs/wiki/team-mode-security.md` §역할, §미들웨어 (~0.7K)
- `src/diagram_bot/port_check.py`의 `company_app` 함수만 (~0.7K, 장착 예시)
- `<회사:FastAPI 앱 팩토리 파일>`, `<회사:인증 미들웨어 파일>`

```python
from diagram_bot.auth import AuditLog, allowed
from diagram_bot.routes import Context, aws, documents, install_error_handlers, snapshots
from diagram_bot.store import DocumentStore, SnapshotStore
ctx = Context(snapshots=SnapshotStore(DATA), documents=DocumentStore(DATA), aws=access, audit=AuditLog(DATA / "audit.log"))
install_error_handlers(app)  # NotFound 404, Conflict 409, InputError 422
aws_router = aws.router(ctx)  # 종료 때 aws_router.executor.shutdown()
for r in (snapshots.router(ctx), documents.router(ctx), aws_router):
    app.include_router(r, prefix="<회사:접두사>")
```

## 계약
- 회사 미들웨어가 `request.state.user`(str)와 `request.state.role`(viewer | editor | admin)을 채운다. 없으면 admin으로 간주.
- viewer 쓰기 금지는 라우터에 없다: 미들웨어가 `allowed(role, method, path)` 거짓이면 403(`port_check` `roles`가 확인).
- `server.create_app`의 페이지 토큰(`X-Diagram-Bot`)·Origin·15 MiB 제한은 붙지 않는다 → 회사 CSRF·본문 제한으로.

## 하지 말 것
- `server.py` 복사·사용. 라우터 수정. 요청 값으로 파일 경로 받기.

## 단계
1. 앱 팩토리에서 `Context`를 한 번 만든다(T3의 `access`).
2. 미들웨어: user/role 채우기 + `allowed` 검사. 경로는 접두사를 뗀 `/api/…`.
3. CSRF·본문 제한 연결. 회사에 없으면 멈춤.
4. lifespan 종료에서 `executor.shutdown()`.
5. 브라우저 egress면 `/api/relay/*`가 프런트에서 닿는지(같은 출처) 확인.
6. 회사 테스트 1개(`company_app` 패턴, 회사 미들웨어 사용): GET `/api/snapshots`, 문서 GET/PUT 리비전, 낡은 리비전 409, POST `/api/xml/align`, viewer PUT 403.

## 완료 조건
- `cd <회사:패키지 루트> && python -m diagram_bot.port_check --level 3`
- `<회사:대상 테스트 명령> 2>&1 | tail -20`

## 멈춤 조건
- RULES.md. 추가: 역할 매핑 불명, CSRF 방식 결정 필요.

## 보고
- RULES.md 형식. 넘길 것: 접두사, 토큰/CSRF 헤더 이름, 데이터 경로. 다음: T5(프런트 없으면 T6).
