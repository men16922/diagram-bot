# T6 Level 4: 회사 에이전트 루프에 설계 도구 등록 (선택)

## 담당·예산
- GPT-6.1 Sol, effort medium. 상한 $13, 세션당 30턴, 세션 ≤3. 컨텍스트 추정 ~80K / 상한 150K.

## 목표
- 회사 에이전트 루프에 `agent.Workspace`·`designer.Design` 메서드를 도구로 등록한다.

## 읽을 것
- `docs/porting/RULES.md` (~0.4K)
- `docs/wiki/chat-agent.md` §Workspace, §도구 29개 (~0.9K)
- `docs/wiki/howto-add-chat-tool.md` §1. Design 메서드 (~0.7K)
- `src/diagram_bot/agent.py`의 `build_tools` 함수만 (~3.9K, 도구 이름·설명·인자 원본)
- `<회사:에이전트 루프 도구 등록 파일>`

```python
from diagram_bot.agent import SYSTEM_PROMPT, Workspace  # strands 없이 동작
from diagram_bot.designer import DesignError, STANDARDS
w = Workspace(save=store.save, session_factory=lambda region: access.session(user, region),
              region="ap-northeast-2", allow_collection=(role == "admin"), load=store.get)
with w.lock:
    result = w.publish_design(w.design.add_vpc("web", cidr="10.0.0.0/16", azs=3))  # {..., "snapshot_id"}
# 읽기 전용: w.aws_identity, w.collect_aws_resources, w.find_collected_resources, w.review_collected_resources, w.start_from_snapshot
# 설계: w.new_design, w.design.apply_standard / add_* / describe / review
```

## 하지 말 것
- Bedrock·모델 실호출 테스트. AWS 쓰기 도구 추가.
- 도구 설명을 새로 지어내기(`build_tools` docstring 그대로).
- `strands-agents` 설치(회사 루프 대신 `routes.agent`를 쓰는 것은 제품 결정 → 멈춤).

## 단계
1. `build_tools`의 29개 도구마다 회사 도구 1개: 같은 이름·설명·인자.
2. 바꾸는 도구는 `with w.lock:` 안에서 `w.publish_design(w.design.<메서드>(…))`.
3. `DesignError`·`ValueError`·`PermissionError`는 도구 오류 문자열로 모델에 돌려준다.
4. 시스템 프롬프트는 `SYSTEM_PROMPT`.
5. 사용자별 `Workspace` 하나. `allow_collection`은 admin만.
6. 테스트 1개: 가짜 모델이 `new_design` → `add_vpc` → `review_design` 호출, 저장 1회 이상, 네트워크 없음.

## 완료 조건
- `cd <회사:패키지 루트> && python -m diagram_bot.port_check --level 4`
- `<회사:대상 테스트 명령> 2>&1 | tail -20`

## 멈춤 조건
- RULES.md. 추가: 회사 루프의 도구 스키마 형식 불명.

## 보고
- RULES.md 형식. 다음: 끝(오케스트레이터 최종 요약).
