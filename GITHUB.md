# GitHub 업로드 준비

이 프로젝트는 `main` 브랜치를 사용하는 Python 저장소입니다. GitHub 저장소 주소가 정해지면 연결하여 push할 수 있습니다.

## 새 저장소를 만드는 경우

아래 이름은 예시입니다. 실제 owner/name으로 바꿉니다.

```bash
cd /workspace/ta-bot
gh repo create OWNER/ta-bot --private --source=. --remote=origin --push
```

## 이미 만든 빈 저장소를 연결하는 경우

```bash
cd /workspace/ta-bot
git remote add origin https://github.com/OWNER/REPOSITORY.git
git push -u origin main
```

원격 저장소에 기존 커밋이 있다면 먼저 `git fetch origin`으로 이력을 확인하고 통합합니다. 위 명령은 비어 있는 저장소를 전제로 합니다.

## 포함된 준비 사항

- `pyproject.toml`: 설치 가능한 Python 패키지와 `ta-bot` CLI.
- `.github/workflows/ci.yml`: Python 3.11/3.12/3.13에서 패키지 빌드, wheel 설치, pytest, 샘플 보고서 생성·재렌더링.
- `.github/pull_request_template.md`: 변경 내용과 검증 결과 기록.
- `.gitignore`: 가상환경, 빌드 결과, `.env`, `.aws`, 키 파일, 수집 산출물 제외.
- `examples/demo`: 실제 AWS 정보가 아닌 합성 샘플만 예외적으로 포함.

CI는 모의 AWS 응답과 샘플 데이터로 실행하며 AWS/GitHub 추가 시크릿을 요구하지 않습니다. 원격 push 이후 Actions 실행 결과를 확인합니다.

실제 수집은 `--output reports/current`처럼 `reports/` 아래로 출력하면 HTML을 포함한 산출물 전체가 Git 추적에서 제외됩니다.

## 로컬 검증

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]' build
pytest -q
python -m build
ta-bot demo --output reports/demo
git status --short
```
