# GitHub 개발 안내

저장소: https://github.com/men16922/diagram-bot

기본 브랜치는 `main`입니다. Python 패키지와 CLI 명령 이름은 `diagram-bot`입니다.

## 개발 환경 구성

```bash
git clone https://github.com/men16922/diagram-bot.git
cd diagram-bot
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]' build
pytest -q
python -m build
diagram-bot demo --output reports/demo
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
- `.github/workflows/ci.yml`: Python 3.11/3.12/3.13에서 패키지 빌드, wheel 설치, pytest, 샘플 보고서 생성·재렌더링.
- `.github/pull_request_template.md`: 변경 내용과 검증 결과 기록.
- `.gitignore`: 가상환경, 빌드 결과, `.env`, `.aws`, 키 파일, 수집 산출물 제외.
- `examples/demo`: 실제 AWS 정보가 아닌 합성 샘플만 예외적으로 포함.

CI는 모의 AWS 응답과 샘플 데이터로 실행하며 AWS/GitHub 추가 시크릿을 요구하지 않습니다. 실행 결과는 저장소의 Actions에서 확인합니다.

실제 수집은 `--output reports/current`처럼 `reports/` 아래로 출력하면 HTML을 포함한 산출물 전체가 Git 추적에서 제외됩니다.
