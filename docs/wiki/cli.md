---
title: CLI와 검증 스크립트
summary: diagram-bot CLI(cli.py)의 모든 하위 명령·옵션·종료 코드, --env-file 동작, Makefile 게이트(check·lint·test·smoke-local)와 scripts/의 검증·렌더링 스크립트.
sources: [src/diagram_bot/cli.py, pyproject.toml, Makefile, scripts/verify_offline.py, scripts/verify_demo.py, scripts/verify_team.py, scripts/verify_browser_egress.py, scripts/render_samples.py, src/diagram_bot/store.py, src/diagram_bot/editor.py, src/diagram_bot/agent.py]
related: [review-rules, change-review, multi-account, cloudformation, browser-egress, collection, chat-agent, team-mode-security, http-api]
updated: 2026-10-18
---
# CLI와 검증 스크립트

> 실행 파일 `diagram-bot`은 `pyproject.toml`의 `[project.scripts]`에서 `diagram_bot.cli:main`을 가리킨다. `python -m diagram_bot.cli`로도 같다. 모든 하위 명령은 `main()`의 `argparse` 하위 파서로 정의된다.

## 핵심
- 전역 옵션: `--version`(`diagram-bot <패키지 버전>`, 설치 안 됐으면 `dev`).
- `--env-file`은 `collect`, `chat`, `serve`에만 있다(공용 부모 파서 `env`).
- 보고서를 쓰는 명령(`demo`, `render`, `combine`, `collect`)은 `report.write_report`로 `index.html`, `snapshot.json`, `topology.drawio`, `inventory.csv`, `review.md`를 만든다.
- 예외 처리: `BotoCoreError`·`ClientError`는 "인증 또는 AWS 조회 시작 실패" 메시지와 종료 코드 1, `OSError`·`ValueError`·`KeyError`·`TypeError`는 `실패: ...`와 1.

## 하위 명령
| 명령 | 인자·옵션 | 동작 |
| --- | --- | --- |
| `demo` | `--output`(기본 `reports/demo`), `--sample {SAMPLES 키}` | 인증 없이 데모 또는 샘플 스냅샷으로 보고서 생성 |
| `collect` | `--profile`, `--region`(필수, 반복·쉼표), `--expected-account`(12자리, `--role-arn`이 없으면 필수), `--role-arn`(반복), `--external-id`, `--output`(필수), `--env-file` | 읽기 전용 수집. (역할 × 리전) 대상마다 보고서. 대상이 하나면 `--output`에, 여럿이면 `<output>/<계정>-<리전>/` |
| `render` | `SNAPSHOT`, `--output`(필수) | 저장된 `snapshot.json`으로 보고서 재생성(`schema_version`이 1이어야 함) |
| `review` | `SNAPSHOT`, `--format markdown\|json`, `--fail-on high\|medium\|low` | 아키텍처 검토 출력([[review-rules]]) |
| `diff` | `BEFORE AFTER`, `--format markdown\|json`, `--fail-on-change` | 변경 비교 출력([[change-review]]) |
| `combine` | `SNAPSHOTS...`(2개 이상), `--output`(필수), `--title` | 계정·리전 스냅샷을 합친 보고서([[multi-account]]) |
| `export-cfn` | `SNAPSHOT`, `--output` | CloudFormation 초안. 합친 스냅샷은 `--output` 필수([[cloudformation]]) |
| `align-xml` | `XML`, `--output`(생략 시 stdout), `--mode preserve\|rebuild`, `--spec`, `--spec-from`, `--normalize-styles` | 기존 `drawio.xml` 인플레이스 정렬·직교 배선 및 회사 규격 적용([[drawio-format-and-alignment]]) |
| `import-xml` | `XML`, `--output`(생략 시 stdout), `--title`, `--region`, `--render`, `--spec` | 기존 `drawio.xml`을 `Snapshot` JSON 및 렌더링된 XML로 변환([[drawio-format-and-alignment]]) |
| `adopt-template` | `XML`, `-o/--output`(필수), `--name`, `--title`, `--region`, `--save-spec` | 회사 표준 `drawio.xml` 기준으로 포맷 규격(`DrawioSpec`) 추출·원본 정렬(`aligned.drawio`)·사내 표준 아키텍처 보고서 생성([[drawio-format-and-alignment]]) |
| `setup-editor` | `--archive`(오프라인 설치용 `draw.war`) | 고정 버전 draw.io 설치(`editor.install_editor`, 기본 위치 `$XDG_CACHE_HOME/diagram-bot/drawio/<VERSION>`) |
| `chat` | `--model`, `--output`(기본 `reports/chat`), `-m/--message`(반복), `--from`, `--env-file` | Strands + Bedrock 채팅 |
| `serve` | 아래 절 | 로컬 앱(FastAPI + uvicorn) |
| `user` | `add\|remove\|list`, `NAME`, `--file`(필수), `--role viewer\|editor\|admin`(기본 viewer), `--password-stdin` | 팀 모드 사용자 파일 관리 |

### collect 세부
- 대상: `--role-arn`이 있으면 (역할, 역할 ARN의 계정, 리전) 조합 전부, 없으면 (`--expected-account`, 리전) 조합. `--role-arn` 없이 `--expected-account`가 12자리 숫자가 아니면 `parser.error`.
- 대상 하나가 실패해도 나머지는 계속한다. 대상별로 `계정/리전: 보고서 ... · 수집 상태 ...`를 출력한다.

### chat 세부
- `--model`: `agent.MODELS`의 `nova-2-lite`(기본, `DEFAULT_MODEL`), `gpt-6-luna`, `claude-sonnet-4.5`. 다른 모델이 실패하면 `nova-2-lite`로 전환(`FALLBACK_MODEL`).
- `-m`을 주면 그 메시지들을 차례로 보내고 끝나며, 없으면 `> ` 프롬프트 대화형(`exit`·`quit`·EOF·Ctrl-C로 종료, 종료 코드 0).
- `--from SNAPSHOT`: 스냅샷 JSON을 읽어 `build_graph`로 그래프를 붙이고 `Workspace.collected`에 넣는다. 에이전트 도구 `start_from_current`는 `snapshot_id`를 비우면 앱에서 보는 스냅샷, 없으면 마지막 수집 결과(여기서는 이 스냅샷)를 설계 출발점으로 쓴다.
- 설계·수집 결과는 `<output>/NN-design` 또는 `<output>/NN-<계정>-<리전>`에 보고서로 저장된다. 응답 머리에 모델 이름, 대체 모델 여부, 도구 호출 횟수를 붙인다.

### serve 세부
- `--port`(기본 8765), `--data-dir`(기본 `$XDG_DATA_HOME/diagram-bot`, 없으면 `~/.local/share/diagram-bot`), `--host`(기본 `127.0.0.1`).
- 팀 모드: `--host`가 `127.0.0.1`·`localhost`·`::1`이 아니면 `--users` 또는 `--oidc-config`, 그리고 `--certfile`, `--keyfile`, `--allowed-host`(반복)가 모두 있어야 한다. 아니면 `parser.error`(인증 없는 네트워크 노출 금지). `--certfile`과 `--keyfile`은 함께만.
- `--users`: `user add`로 만든 사용자 파일. `--oidc-config`: OpenID Connect(SSO) 설정 JSON.
- `--aws-egress server|browser`(기본은 환경 변수 `DIAGRAM_BOT_AWS_EGRESS`, 없으면 `server`): `browser`면 서버는 서명만 하고 사용자 브라우저가 AWS로 보낸다([[browser-egress]]).
- `--sso-start-url`(기본 `DIAGRAM_BOT_SSO_START_URL`), `--sso-region`(기본 `DIAGRAM_BOT_SSO_REGION`, 없으면 `us-east-1`): 로그인 화면에 먼저 보일 IAM Identity Center 시작 URL과 리전. 시작 URL이 있을 때만 `create_app(sso_start=...)`에 넘긴다.
- uvicorn은 `proxy_headers=False`, `server_header=False`로 실행한다.

### user 세부
- `list`: 파일이 없으면 빈 목록. `add`: 비밀번호를 `getpass`로 두 번 받거나 `--password-stdin`이면 표준 입력 첫 줄. 12자 미만이면 `parser.error`. `remove`·`add`는 `NAME`이 필요하다.

## --env-file 동작
- `load_env_file(path)`: 빈 줄·`#` 주석·`=` 없는 줄은 건너뛰고, `export ` 접두사와 같은 따옴표 쌍을 벗긴다. 키가 식별자 형식이면 `os.environ`에 넣는다(파일 값이 기존 환경 변수를 덮어씀).
- 파일에 `AWS_ACCESS_KEY_ID`가 있으면 환경 변수 `AWS_PROFILE`·`AWS_DEFAULT_PROFILE`을 지운다. 파일의 키가 인증 출처가 되게 하기 위함이다.
- 읽은 키 이름(값 아님)을 표준 오류에 `환경 파일 <경로>: KEY1, KEY2`로 출력한다. 명령 분기보다 먼저 실행된다.

## 종료 코드
| 코드 | 의미 |
| --- | --- |
| 0 | 성공 (검토·비교 게이트 미해당 포함) |
| 1 | 인증·AWS 조회 시작 실패, 파일·형식 오류, `collect`에서 대상 하나 이상 실패 |
| 2 | `collect`에서 실패는 없고 하나 이상이 `partial`(완료 아님). `argparse` 오류·`parser.error`도 2 |
| 3 | `review --fail-on`: 그 심각도 이상 결과 있음 |
| 4 | `diff --fail-on-change`: 변경 있음 |

## Makefile
- 기본 목표 `check` = `lint` + `test` + `smoke-local`. `PYTHON`은 `.venv/bin/python`이 있으면 그것, 없으면 `python3`.
- 오프라인 보장: `AWS_EC2_METADATA_DISABLED=true`, `AWS_CONFIG_FILE`·`AWS_SHARED_CREDENTIALS_FILE=/dev/null`을 export하고 `AWS_PROFILE`·`AWS_DEFAULT_PROFILE`·키·세션 토큰을 unexport한다.
- `lint`: `ruff check src tests scripts`. `test`: `pytest -q`. `smoke-local`: `scripts/verify_offline.py`.
- 나머지 `overnight-*` 목표는 overnight-harness 플러그인 실행용이며 앱 동작과 무관하다.

## scripts/
| 스크립트 | 용도 | 실행 조건 |
| --- | --- | --- |
| `verify_offline.py` | 설치된 CLI로 `demo` → `render` 왕복, 산출물 5개가 바이트 단위로 같은지, `demo` 플래그, `inventory.csv` 행 수 = 그래프 노드 수, draw.io 루트 존재 확인 | 오프라인, `make smoke-local` |
| `verify_demo.py` | 브라우저 인수 테스트 (`--url` 기본 `http://127.0.0.1:8765`, `--output` 기본 `artifacts/browser`, `--chromium`) | 새 데이터 디렉터리의 `serve`, Playwright(`browser` extra) |
| `verify_team.py` | 팀 모드: 임시 사용자·자체 서명 인증서로 비루프백 TLS `serve`를 띄워 로그인·역할·감사 로그 확인 (`--port` 기본 8443, `--chromium`) | Playwright |
| `verify_browser_egress.py` | 실제 AWS(읽기 전용)로 브라우저 egress 확인: 앱 UI로 한 리전 수집, 채팅 한 번, 브라우저가 부른 AWS 호스트 보고 (`--url` 기본 `http://127.0.0.1:8796`, `--region` 기본 `us-east-1`, `--chromium`, `--skip-chat`). 자체 Chromium으로 로컬 자격 증명 로그인을 쓰므로 Identity Center 경로는 앱에서 직접 확인 | 서버 자체 AWS 접근을 막은 `serve --aws-egress browser`, 회사 네트워크 |
| `render_samples.py` | `examples/samples` 재생성: CLI 보고서, 앱 편집기로 내보낸 PNG, README 절 (`--url`, `--output`, `--sample` 반복) | 새 `serve`, Playwright, `AWS_` 환경 변수를 뺀 환경 |

`make check`에는 `verify_offline.py`만 들어간다. 나머지는 수동 실행이다.

## 바꿀 때 주의
- 종료 코드 3·4는 CI 게이트 계약이다(`tests/test_findings.py::test_cli_review_exit_code_gates_on_severity`, `tests/test_diff.py::test_cli_diff_exit_code_detects_drift`).
- 새 하위 명령이 AWS 인증을 쓰면 `parents=[env]`로 `--env-file`을 받게 한다.
- 팀 모드 필수 옵션 검사를 약하게 하면 인증 없는 네트워크 노출이 가능해진다.

## 관련
- [[review-rules]] — `review` 출력과 `--fail-on`.
- [[change-review]] — `diff` 출력과 `--fail-on-change`.
- [[multi-account]] — `collect` 다중 대상과 `combine`.
- [[cloudformation]] — `export-cfn`.
- [[browser-egress]] — `serve --aws-egress browser`.
- [[collection]] — `collect`가 부르는 수집기.
- [[chat-agent]] — `chat` 하위 명령의 에이전트.
- [[team-mode-security]] — `serve` 팀 모드 옵션의 보안 경계.
- [[http-api]] — `serve`가 여는 API.
