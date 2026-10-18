# T0 복사·설치 (셸 단계, 에이전트 없음)

## 담당·예산
- 오케스트레이터가 셸로 실행(서브에이전트 없음). 서브에이전트가 없으면 사람. $0, 컨텍스트 150K 상한 무관.

## 목표
- 목표 Level의 `diagram_bot` 모듈만 회사 저장소에 복사·설치하고 `port_check`를 통과시킨다.

## 읽을 것
- 없음. 아래 명령만.

## 하지 말 것
- 수동 복사·편집. 패키지 이름 변경(`diagram_bot` 유지).

## 단계
1. `python <DB>/scripts/port_copy.py --level <N> --dest <회사:패키지 루트>/diagram_bot --dry-run 2>&1 | tail -15`
2. 같은 명령에서 `--dry-run`을 빼고 실행. T5를 하면 `--level 4 --static-dest <회사:웹 src>/diagram-bot`.
3. 출력된 `pip install …` 줄 실행(`2>&1 | tail -5`). Level 3 이상은 `port_check …needs` 줄(httpx2)도.
4. `cd <회사:패키지 루트> && python -m diagram_bot.port_check --level <N> 2>&1 | tail -30`

## 완료 조건
- `python -m diagram_bot.port_check --level <N>` exit 0

## 멈춤 조건
- `refusing:`(다른 커밋의 MANIFEST 또는 낯선 디렉터리) → 사람에게 `--force` 여부.
- pip 실패, `FAIL` 줄.

## 보고
- RULES.md 형식, 카드 id `T0`. 넘길 것: Level, MANIFEST `commit`, 설치한 패키지. 다음: T1.
