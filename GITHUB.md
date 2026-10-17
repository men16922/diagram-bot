# GitHub 개발 안내

저장소: https://github.com/men16922/diagram-bot

기본 브랜치는 `main`입니다. Python 패키지와 CLI 명령 이름은 `diagram-bot`입니다.

## 개발 환경 구성

```bash
git clone https://github.com/men16922/diagram-bot.git
cd diagram-bot
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test,browser]' build
pytest -q
python -m build
diagram-bot setup-editor
diagram-bot serve --data-dir reports/workspace
```

## 변경 사항 올리기

```bash
git switch -c feature/my-change
# 변경 후 테스트 실행
pytest -q
git add <변경한-파일>
git commit -m "변경 내용"
git push -u origin feature/my-change
gh pr create --base main
```

## CI와 추적 범위

- `pyproject.toml`: 설치 가능한 Python 패키지와 `diagram-bot` CLI.
- `.github/workflows/ci.yml`: Python 3.11/3.12/3.13 패키지 테스트와 Chromium 기반 실제 draw.io 편집·다운로드 검증. 브라우저 산출물은 Actions artifact로 보관.
- `.github/pull_request_template.md`: 변경 내용과 검증 결과 기록.
- `.gitignore`: 가상환경, 빌드 결과, `.env`, `.aws`, 키 파일, 수집 산출물 제외.
- `examples/demo`: 실제 AWS 정보가 아닌 합성 샘플만 예외적으로 포함.

CI는 모의 AWS 응답과 샘플 데이터로 실행하며 AWS/GitHub 추가 시크릿을 요구하지 않습니다. 실행 결과는 저장소의 Actions에서 확인합니다.

실제 수집은 `--output reports/current`처럼 `reports/` 아래로 출력하면 HTML을 포함한 산출물 전체가 Git 추적에서 제외됩니다.

## Overnight Harness (Codex)

프로젝트 초기 설정은 `.claude/harness-config.json`에 있으며 기본 엔진은 Codex입니다.
실행 코드와 스킬은 설치된 플러그인에서 읽고, 프로젝트에는 설정·문서·검증 명령만 둡니다.
`CODEX_HOME`을 사용하는 설치 경로도 Makefile이 탐색합니다.

```bash
make check                  # 오프라인 테스트 + CLI 산출물 재생성 검증
make overnight-where        # 설치된 플러그인과 runner 경로
```

`docs/AGENT_BRIEF.md` → `docs/STATUS.md` → `docs/NEXT_PLAN.md` 순서로 문맥을 읽습니다.
현재 승인된 `[auto]` 작업은 0개입니다. 다음 실행은 과제를 정한 뒤 사용합니다.

```bash
make overnight-clean
MAX_ITER=1 make overnight-codex-once
make overnight-status
make overnight-stop
```

자동 실행은 로컬 커밋까지 수행할 수 있으므로, 사용자가 남긴 변경 사항과 초기 설정을 먼저
정리한 상태에서 시작합니다. 자동 push·실제 AWS 조회·배포는 루프 범위에서 제외됩니다.
외부 알림은 기본적으로 비활성화했습니다. Codex 모델·effort는 기존 CLI 설정을 따릅니다.

초기화 검증은 `harness-init.sh --check`, `make overnight-where`, `make check`와
**MAX_ITER=0 시작·종료 점검**까지 완료했습니다. 이 점검은 모델을 호출하거나 커밋하지
않으므로 실제 에이전트의 수정·커밋 성공을 검증한 것으로 해석하면 안 됩니다.
