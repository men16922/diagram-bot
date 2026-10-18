# T3 Level 2: 회사 인증으로 AWS 수집

## 담당·예산
- GPT-6.1 Sol, effort medium. 상한 $12, 세션당 30턴, 세션 ≤3. 컨텍스트 추정 ~70K / 상한 150K.

## 목표
- 회사 인증의 AWS 자격 증명으로 읽기 전용 수집을 돌린다. 서버가 AWS에 못 나가면 브라우저 egress.

## 읽을 것
- `docs/porting/RULES.md` (~0.4K)
- `docs/wiki/aws-access-and-sso.md` §`AwsAccess`, §회사 인증으로 바꿀 때 (~0.6K)
- `docs/wiki/browser-egress.md` §흐름, §회사 환경에서 확인하는 법 (~1K)
- `docs/wiki/collection.md` §핵심, §prepare 훅 (~0.3K)
- `src/diagram_bot/aws_access.py` (~2K, 교체 대상이라 본문 허용)
- `<회사:자격 증명 함수 파일>`

```python
from diagram_bot import api
from diagram_bot.aws_access import AwsAccess  # egress="server" | "browser"(.relay = BrowserRelay)
from diagram_bot.sessions import credentials_session  # {"AccessKeyId","SecretAccessKey","SessionToken"} -> boto3.Session
access = AwsAccess("browser")
session = access.outbound(credentials_session(creds, region), user)
snap = api.collect_snapshot(session, region, expected_account=acct, prepare=access.prepare_for(user))
# 오류: egress.RelayError/RelayDisconnected, ValueError("계정 불일치"), botocore ClientError
```

## 하지 말 것
- AWS 쓰기 API. 자격 증명을 로그·스냅샷·PORTING_LOG에 남기기.
- 실제 계정 대상 테스트. `sso.py`·`egress.py`·`collector.py` 수정.

## 단계
1. `<회사:어댑터 파일>`에 회사 자격 증명 → `credentials_session(creds, region)` 함수 1개.
2. 같은 파일에 `AwsAccess` 하위 클래스: `session`, `model_session`, `collection_session`을 그 함수로. 반환 전 항상 `self.outbound(…, user)`.
3. egress는 `<회사:서버에서 AWS 접근 가능 여부>`로 정한다. 값이 없으면 멈춤. 브라우저면 HTTP 중계(`/api/relay/*`)는 T4에서 장착.
4. 수집 지점: `api.collect_snapshot(…, expected_account=…, prepare=access.prepare_for(user))` → `SnapshotStore(<회사:데이터 경로>).save(snap)`. 회사 DB로 바꾸면 `save/get/list/seed` 시그니처 유지.
5. 오프라인 테스트: 세션에 `session.events.register("before-call", answer)`로 응답(`port_check`의 `collect-offline`과 같은 방식). 네트워크 금지.

## 완료 조건
- `cd <회사:패키지 루트> && python -m diagram_bot.port_check --level 2`
- `<회사:대상 테스트 명령> 2>&1 | tail -20`

## 멈춤 조건
- RULES.md. 추가: 자격 증명 형식 불명, egress 결정 필요, Identity Center 정책 질문.

## 보고
- RULES.md 형식. 넘길 것: 어댑터 클래스 이름, egress, 데이터 경로. 다음: T4.
