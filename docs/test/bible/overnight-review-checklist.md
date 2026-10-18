# Overnight 루프 종료 후 검수 체크리스트

매 overnight 가동(`make overnight-watch`) **종료 후 사람이 수행하는 반복 검수 프로세스**.
자동 검수는 `/overnight-report`가, 사람 판단(무엇이 깨졌나·뭘 고칠까·다음 seed)은 이 체크리스트가 담당한다.

> 이 파일은 **정적 바이블(템플릿)** 이다(`docs/test/bible/`). `/overnight-report`는 마지막 단계에서 아래 B~E를
> **이번 런 사실로 채운 체크박스 인스턴스**(커밋 해시·새 `[blocked]`·ahead 수·잔여 seed)를
> `docs/test/history/<MMDD-HHMM>-overnight-review-checklist.md` 파일로 **생성**한다. 그 파일들은 gitignore —
> 재생성 가능한 산출물이라 커밋하지 않는다. "이번 런에 내가 확인할 리스트"가 곧 그 생성 파일이다.

> 한 줄 흐름:
> `make overnight-status`(끝났나?) → `/overnight-report`(자동 요약 + 런별 체크리스트) → 아래 A~E 처리 → `git push` → 다음 seed

---

## A. 종료 상태 확인
- [ ] `make overnight-status` — 프로세스 종료됨? 종료 사유는?(DONE 소진 / STOP 수동·red잔여물 / MAX_ITER / 연속실패 / 무진행)
- [ ] claude 세션에서 **`/overnight-report`** — 회차 수, 만든 커밋, **게이트 재실측(green?)**, 잔여 `[auto]` 확인.
- [ ] STOP으로 멈췄다면: red 잔여물(사람 검수 필요)이 핵심 신호. `git status` + `scripts/overnight/logs/iter-<N>.log` 마지막 부분 확인.

## B. 루프가 만든 것 검토 (커밋)
- [ ] `git log --oneline <시작HEAD>..HEAD` — 회차별 커밋 훑기. `[recovered]`(잔여물 복구)·`[blocked]` 강조.
- [ ] **커밋별로 `git show <hash>` diff를 읽고 두 줄로 적는다**:
  - ① **무엇이 바뀌었나** — 건드린 파일·추가/수정 테스트·동작 변화를 구체적으로(generic "의도대로인가?" 금지).
  - ② **무엇을 확인하면 되나** — 변경 종류에 맞는 검증: **테스트 추가**→무엇을 보장하나·허위 green/과검출 아닌가, **리팩터/codemod**→동작·공개 API 불변인가, **버그픽스**→근본 원인·재현·회귀 테스트 동반인가, **docs 산문**→문장이 사실인가(테스트 아닌 주장은 `make check`가 못 잡는다 — 실제 파일을 열어 확인).

## C. invariant 결과 분류 (이번 seed의 핵심 산출)
- [ ] **green 박제**: 어떤 무결성 테스트가 통과로 추가됐나 → 회귀를 막는 **영구 게이트** 확보. 좋은 결과.
- [ ] **Blocker surface**: 어떤 항목이 `[blocked]`로 남았나 → 테스트가 잡아낸 **실제 버그**. `NEXT_PLAN.md`의 `[blocked]` 항목 + `PROGRESS_LOG.md` Blocker 메모를 읽는다.
- [ ] **사람 수정 우선순위**: 도메인 불변식이 깨지는 것부터 — 조회 불가를 빈 환경으로 표시 / API 관계를 실제 통신으로 표시 / IP 대상을 EC2로 추측 / 다중 서브넷 ALB 중복 / 편집이 스냅샷을 바꿈 / 골든 해시 재고정 > 그 외.

## D. 반영 (push / 정리)
- [ ] 결과가 좋으면 **`git push`** (로컬 `main` → origin). *(첫 푸시는 `gh repo create <repo> --private --source=. --remote=origin --push`.)*
- [ ] 잘못된 커밋이 있으면: `git revert <hash>` 또는 수정 후 재커밋.

## E. 다음 가동 준비
- [ ] C에서 나온 Blocker 중 **봇이 고칠 수 있는 것**(기계적·결정론)은 새 `[auto]`로, **사람 판단 필요**한 것은 `[manual]`로 NEXT_PLAN에 환류.
- [ ] `[auto]` seed가 소진됐으면 **새 묶음 seeding**(회귀 테스트 / 타입·lint 부채 / codemod / hygiene). seeding 없이 돌리면 즉시 무진행 종료.
- [ ] `make overnight-clean`(STOP/DONE 정리) → 다음 `make overnight-watch`.

---

## 참고
- 하네스 매핑·도메인 불변식: `docs/engineering/interp/INTERPRETATION.md`. 설계·환경변수·종료 조건은 설치된 플러그인 문서.
- 봇이 할 수 없는 것(화면 가독성·PNG 모양·실제 AWS)은 사람 검수: `docs/DEMO.md`, `scripts/verify_demo.py`.
