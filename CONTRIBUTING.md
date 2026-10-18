# 개발 안내

저장소: https://github.com/men16922/diagram-bot · 기본 브랜치 `main` · Python 패키지와 CLI 이름 `diagram-bot`.
설치·실행은 [README](README.md), 테스트·브라우저 검증 명령은 README의 [검증과 현재 한계](README.md#검증과-현재-한계)에 있습니다.

## 개발 환경

```bash
git clone https://github.com/men16922/diagram-bot.git
cd diagram-bot
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test,browser]' build
make check                  # 커밋 게이트: ruff + pytest + CLI 산출물 재생성 검증 (오프라인)
python -m build
diagram-bot setup-editor
diagram-bot serve --data-dir reports/workspace
```

테스트는 bare `pytest` 대신 `make check`로 돌립니다. Makefile이 셸의 `AWS_PROFILE`·키를 지워 에뮬레이터 테스트가 실제 자격 증명을 보지 않게 합니다([LESSONS](docs/LESSONS.md)).

## 변경 사항 올리기

```bash
git switch -c feature/my-change
# 변경 후
make check
git add <변경한-파일>
git commit -m "변경 내용"
git push -u origin feature/my-change
gh pr create --base main      # .github/pull_request_template.md: 변경 내용과 검증 결과
```

## CI와 추적 범위

- `pyproject.toml`: 설치 가능한 Python 패키지와 `diagram-bot` CLI.
- `.github/workflows/ci.yml`: Python 3.11/3.12/3.13에서 wheel을 만들어 `[test]`로 설치한 뒤 lint·pytest·CLI 검증, 그리고 Chromium으로 실제 draw.io 편집·다운로드 검증. 브라우저 산출물은 Actions artifact로 보관합니다.
- CI는 모의 AWS 응답과 샘플 데이터로 실행하며 AWS·GitHub 추가 시크릿을 요구하지 않습니다. 결과는 저장소의 Actions에서 확인합니다.
- `.gitignore`: 가상환경, 빌드 결과, `.env`, `.aws`, 키 파일, 수집 산출물 제외. `examples/demo`, `examples/samples`의 합성 샘플만 예외로 포함합니다.
- 실제 수집은 `--output reports/current`처럼 `reports/` 아래로 출력하면 HTML을 포함한 산출물 전체가 Git 추적에서 제외됩니다.
- 위키(`docs/wiki/`)를 고치면 `python scripts/wiki_index.py`로 색인과 `llms.txt`를 다시 만들고 [docs/wiki/log.md](docs/wiki/log.md)에 한 줄을 남깁니다([작성 규칙](docs/wiki/SCHEMA.md)).

## Overnight Harness (Codex)

프로젝트 설정은 `.claude/harness-config.json`에 있으며 기본 엔진은 Codex입니다. 실행 코드와 스킬은 설치된 플러그인에서 읽고,
프로젝트에는 설정·문서·검증 명령만 둡니다. `CODEX_HOME`을 사용하는 설치 경로도 Makefile이 탐색합니다.

```bash
make overnight-where        # 설치된 플러그인과 runner 경로
make overnight-clean
MAX_ITER=1 make overnight-codex-once
make overnight-status
make overnight-stop
```

문맥은 [AGENT_BRIEF](docs/AGENT_BRIEF.md)의 읽는 순서를 따릅니다. 실행은 [NEXT_PLAN](docs/NEXT_PLAN.md)에 승인된 `[auto]` 작업이 있을 때만 합니다.
자동 실행은 로컬 커밋까지 수행할 수 있으므로 사용자가 남긴 변경 사항을 먼저 정리한 상태에서 시작합니다. 자동 push·실제 AWS 조회·배포는 루프 범위 밖이고,
외부 알림은 기본적으로 꺼져 있습니다. Codex 모델·effort는 기존 CLI 설정을 따릅니다. 초기화 검증 기록은 [COMPLETED_SUMMARY](docs/COMPLETED_SUMMARY.md)의 Harness setup에 있습니다.
