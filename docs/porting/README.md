# 포팅 키트 (사람용)

| 카드 | 작업 | 담당 | effort | 상한 $ | 턴 | 컨텍스트 추정 / 150K | 입력 | 완료 명령 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ORCH | 순서 실행·검증 | GPT-6.1 Sol | medium | 3 (예비비) | 30/세션 | ~25K | README 표, RULES, 보고 | 최종 요약 |
| T0 | 복사·설치 | 셸(ORCH) | - | 0 | - | - | 목표 Level, 경로 | `port_check --level <N>` |
| T1 | L0 XML 정렬 호출 | GPT-6 Luna | low | 3 | 30×2 | ~30K | 정렬 호출부 | `port_check --level 0` |
| T2 | L1 Design·검토·CFN | GPT-6 Luna | low | 3 | 30×2 | ~35K | 생성 호출부 | `port_check --level 1` |
| T3 | L2 회사 인증 수집·egress | GPT-6.1 Sol | medium | 12 | 30×3 | ~70K | 자격 증명 함수, egress 여부 | `port_check --level 2` |
| T4 | L3 FastAPI 라우터 | GPT-6.1 Sol | high | 20 | 30×3 | ~90K | 앱 팩토리, 인증 미들웨어 | `port_check --level 3` |
| T5 | React·Vite·iframe | GPT-6.1 Sol | medium | 12 | 30×3 | ~100K | React 엔트리, vite.config | `port_check --level 4` |
| T6 | L4 에이전트 도구 | GPT-6.1 Sol | medium | 13 | 30×3 | ~80K | 에이전트 루프 등록부 | `port_check --level 4` |
| 예비 | 재시도·이어서 | - | - | 34 | - | - | - | - |

- 카드 파일: [T0](T0-copy.md) · [T1](T1-luna-level0.md) · [T2](T2-luna-level1.md) · [T3](T3-complex-level2.md) · [T4](T4-complex-level3.md) · [T5](T5-complex-frontend.md) · [T6](T6-complex-level4.md) · [RULES](RULES.md) · [ORCHESTRATOR](ORCHESTRATOR.md)
- 완료 명령은 `cd <회사:패키지 루트> && python -m diagram_bot.port_check …` + 카드의 회사 테스트. 컨텍스트는 chars/3 추정.
- 순서: T0 → T1 → S → T2 → S → T3 → S → T4 → S → T5 → S → T6. S = 회사 전체 테스트(ORCH, `| tail -40`). 목표 Level 위 카드는 건너뜀.
- 실행: ORCHESTRATOR.md의 한 줄을 Sol 세션에 붙인다. ORCH가 카드마다 서브에이전트를 띄운다.
- 카드 한 줄: `작업 디렉터리=회사 저장소, DB=<경로>. DB/docs/porting/RULES.md와 DB/docs/porting/<카드 파일>만 읽고 그 카드의 작업만 수행한 뒤 멈춰라. 이전 보고: <다음 카드에 넘길 것>`
- 서브에이전트가 없으면: 사람이 같은 순서로 카드 한 줄을 매번 새 세션에 붙이고 S 단계를 직접 돈다.
- 사람이 하는 일: 자격 증명, `<회사:…>` 값, 제품 결정, ORCH가 멈췄을 때.
- 인계: 회사 저장소 루트 `PORTING_LOG.md`(RULES.md 형식). 다음 세션은 「다음 카드에 넘길 것」만 읽는다.
- 예산 $100 = L0 3 + L1 3 + L2 12 + L3 20 + L4 25(T5 12 + T6 13) + 예비 37(ORCH 3 포함). 명목: Luna 카드 $0.1–0.3, Sol 카드 $1.5–6, ORCH ~$1. 가정: 파일은 복사(읽지 않음), 프롬프트 캐시, 카드마다 새 세션.
- 단가(1M 토큰): Luna 입력 $0.10·캐시 ~$0.01·출력 $0.50, Sol 입력 $2·캐시 $0.10·출력 $10. 요청 입력이 272K를 넘으면 요청 전체 할증(Luna 입력 ~2배·출력 1.5배, Sol 입력 $4) → 세션당 150K 상한.
- 지출 한도: 이 포팅 전용 API 키/프로젝트를 만들고 콘솔에서 월 $100 한도와 $10 단위 알림을 건다. 각 콘솔에서 하드 스톱인지 알림뿐인지 확인한다. 알림뿐이면 ORCH 누적 $가 실제 멈춤이고, 끝나면 키를 폐기한다.
