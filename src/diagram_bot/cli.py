import argparse
import json
import os
from importlib import metadata
import sys
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError

from .collector import collect
from .demo import demo_snapshot
from .diff import diff, markdown as diff_markdown
from .findings import SEVERITIES, markdown, review, summary
from .graph import build_graph
from .samples import SAMPLES
from .report import write_report


def main():
    parser = argparse.ArgumentParser(description="AWS 현행 구성도·리소스 내역 자동 생성")
    parser.add_argument("--version", action="version", version=f"diagram-bot {app_version()}")
    commands = parser.add_subparsers(dest="command", required=True)
    env = argparse.ArgumentParser(add_help=False)
    env.add_argument("--env-file", help="AWS_ACCESS_KEY_ID·AWS_SECRET_ACCESS_KEY·AWS_REGION 등을 읽을 .env 파일 (Git 추적 제외 경로)")
    live = commands.add_parser("collect", parents=[env], help="기존 SSO/SDK 인증 경로로 읽기 전용 조회")
    live.add_argument("--profile", help="AWS SSO 프로파일; 생략하면 표준 SDK 인증 체인")
    live.add_argument("--region", required=True, action="append", help="리전 (여러 번 또는 쉼표로 여러 리전)")
    live.add_argument("--expected-account", help="조회할 12자리 AWS 계정 ID (역할 ARN이 없으면 필수)")
    live.add_argument("--role-arn", action="append", default=[],
                      help="다른 계정에서 맡을 역할 ARN (여러 번 지정 가능, ReadOnlyAccess 세션 정책으로 제한)")
    live.add_argument("--external-id", help="역할 신뢰 정책의 External ID")
    live.add_argument("--output", required=True, help="출력 디렉터리 (대상이 여럿이면 <계정>-<리전> 하위 디렉터리)")
    render = commands.add_parser("render", help="저장된 snapshot.json으로 보고서 재생성")
    render.add_argument("snapshot")
    render.add_argument("--output", required=True)
    demo = commands.add_parser("demo", help="인증 없이 샘플 보고서 생성")
    demo.add_argument("--output", default="reports/demo")
    demo.add_argument("--sample", choices=sorted(SAMPLES), help="AWS best practice 참고 구성 샘플")
    check = commands.add_parser("review", help="snapshot.json 아키텍처 검토 (CI 게이트용 종료 코드)")
    check.add_argument("snapshot")
    check.add_argument("--format", choices=("markdown", "json"), default="markdown")
    check.add_argument("--fail-on", choices=SEVERITIES, help="이 심각도 이상 결과가 있으면 종료 코드 3")
    compare = commands.add_parser("diff", help="두 snapshot.json의 리소스·설정·관계 변경 비교")
    compare.add_argument("before")
    compare.add_argument("after")
    compare.add_argument("--format", choices=("markdown", "json"), default="markdown")
    compare.add_argument("--fail-on-change", action="store_true", help="변경이 있으면 종료 코드 4 (드리프트 감지용)")
    setup = commands.add_parser("setup-editor", help="고정 버전 draw.io 편집기 설치 (최초 한 번)")
    setup.add_argument("--archive", help="오프라인 설치용 공식 draw.war 경로")
    talk = commands.add_parser("chat", parents=[env], help="채팅으로 표준 아키텍처 설계·AWS 읽기 전용 조회 (Strands + Bedrock)")
    talk.add_argument("--model", help="gpt-6-luna(기본, 실패 시 nova-2-lite), nova-2-lite, claude-sonnet-4.5")
    talk.add_argument("--output", default="reports/chat", help="설계·수집 결과 보고서 디렉터리")
    talk.add_argument("-m", "--message", action="append", default=[], help="대화형 대신 보낼 메시지 (여러 번 지정 가능)")
    serve = commands.add_parser("serve", parents=[env], help="draw.io 기반 로컬 시연 앱 실행")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--data-dir", help="스냅샷과 편집본 저장 디렉터리")
    serve.add_argument("--host", default="127.0.0.1", help="루프백이 아니면 --users·--certfile·--keyfile·--allowed-host 필수 (팀 모드)")
    serve.add_argument("--users", help="팀 모드 사용자 파일 (diagram-bot user add로 생성)")
    serve.add_argument("--oidc-config", help="팀 모드 OpenID Connect(SSO) 설정 JSON")
    serve.add_argument("--certfile", help="TLS 인증서 (PEM)")
    serve.add_argument("--keyfile", help="TLS 개인 키 (PEM)")
    serve.add_argument("--allowed-host", action="append", default=[], help="접속에 쓰는 호스트 이름 (여러 번 지정 가능)")
    account = commands.add_parser("user", help="팀 모드 사용자 관리")
    account.add_argument("action", choices=("add", "remove", "list"))
    account.add_argument("name", nargs="?")
    account.add_argument("--file", required=True, help="사용자 파일 경로")
    account.add_argument("--role", choices=("viewer", "editor", "admin"), default="viewer")
    account.add_argument("--password-stdin", action="store_true", help="비밀번호를 표준 입력 첫 줄에서 읽기 (자동화용)")
    args = parser.parse_args()
    try:
        if getattr(args, "env_file", None):
            loaded = load_env_file(args.env_file)
            print(f"환경 파일 {args.env_file}: {', '.join(loaded) or '설정 없음'}", file=sys.stderr)
        if args.command == "chat":
            return run_chat(args)
        if args.command == "setup-editor":
            from .editor import install_editor
            print(f"draw.io 설치 완료: {install_editor(args.archive)}")
            return 0
        if args.command == "serve":
            return run_server(parser, args)
        if args.command == "user":
            return manage_user(parser, args)
        if args.command == "diff":
            load = lambda path: json.loads(Path(path).read_text(encoding="utf-8"))
            result = diff(load(args.before), load(args.after))
            print(json.dumps(result, ensure_ascii=False, indent=2) if args.format == "json" else diff_markdown(result))
            return 4 if args.fail_on_change and any(result["summary"].values()) else 0
        if args.command == "review":
            snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
            findings = review(build_graph(snapshot), snapshot.get("coverage", []))
            if args.format == "json":
                print(json.dumps({"summary": summary(findings), "findings": findings}, ensure_ascii=False, indent=2))
            else:
                print("\n".join(markdown(findings)))
            threshold = SEVERITIES.index(args.fail_on) if args.fail_on else -1
            return 3 if any(SEVERITIES.index(f["severity"]) <= threshold for f in findings) else 0
        if args.command == "collect":
            return collect_all(parser, args)
        if args.command == "demo":
            snapshot = SAMPLES[args.sample]() if args.sample else demo_snapshot()
        else:
            snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
            if snapshot.get("schema_version") != 1:
                raise ValueError("지원하지 않는 snapshot schema_version")
        path = write_report(snapshot, args.output)
        print(f"보고서: {path.resolve()}")
        print(f"수집 상태: {snapshot['metadata']['status']}")
        return 0
    except (BotoCoreError, ClientError) as exc:
        code = exc.response["Error"]["Code"] if isinstance(exc, ClientError) else type(exc).__name__
        print(f"인증 또는 AWS 조회 시작 실패: {code}. 기존 SSO 로그인·프로파일·접속 경로를 확인하세요.", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"실패: {exc}", file=sys.stderr)
        return 1


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def load_env_file(path):
    """KEY=VALUE lines (comments, `export` and quotes allowed) into the process environment; returns the key names.
    Keys from the file win, and an explicit profile is dropped when the file carries access keys, so the file is the
    credential source the user pointed at."""
    loaded = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key.isidentifier():
            os.environ[key] = value
            loaded.append(key)
    if "AWS_ACCESS_KEY_ID" in loaded:
        for name in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE"):
            os.environ.pop(name, None)
    return loaded


def run_chat(args):
    from .agent import DEFAULT_MODEL, Chat, Workspace
    output, count = Path(args.output), [0]

    def save(snapshot):
        count[0] += 1
        meta = snapshot["metadata"]
        name = f"{count[0]:02d}-{'design' if meta.get('design') else meta['account_id'] + '-' + meta['region']}"
        path = write_report(snapshot, output / name)
        print(f"  ↳ 구성도 {path.resolve()}", file=sys.stderr)
        return name

    chat = Chat(Workspace(save), args.model or DEFAULT_MODEL)
    messages = iter(args.message) if args.message else None
    print("Diagram Bot 채팅 · 표준 아키텍처 설계와 AWS 읽기 전용 조회 · 종료: exit", file=sys.stderr)
    while True:
        try:
            message = next(messages) if messages else input("\n> ").strip()
        except (StopIteration, EOFError, KeyboardInterrupt):
            return 0
        if message in ("exit", "quit"):
            return 0
        if not message:
            continue
        result = chat.ask(message)
        note = " (대체 모델)" if result["fallback"] else ""
        print(f"\n[{result['model_label']}{note} · 도구 {len(result['tools_used'])}회]\n{result['reply']}")


def run_server(parser, args):
    import uvicorn

    from .auth import load_users
    from .server import create_app
    team = args.host not in LOOPBACK_HOSTS
    if team and not ((args.users or args.oidc_config) and args.certfile and args.keyfile and args.allowed_host):
        parser.error("루프백이 아닌 주소로 열려면 --users 또는 --oidc-config, 그리고 --certfile, --keyfile, --allowed-host가 모두 필요합니다 "
                     "(인증 없는 네트워크 노출 금지).")
    if bool(args.certfile) != bool(args.keyfile):
        parser.error("--certfile과 --keyfile은 함께 지정하세요.")
    from .oidc import OidcProvider, load_config
    users = load_users(args.users) if args.users else None
    oidc = OidcProvider(load_config(args.oidc_config)) if args.oidc_config else None
    secure = bool(args.certfile)
    app = create_app(args.data_dir, users=users, allowed_hosts=args.allowed_host, secure=secure, oidc=oidc)
    shown = args.allowed_host[0] if args.allowed_host else args.host
    print(f"Diagram Bot: {'https' if secure else 'http'}://{shown}:{args.port}" + (f" · 팀 모드 사용자 {len(users)}명" if users else "")
          + (" · SSO" if oidc else ""), flush=True)
    uvicorn.run(app, host=args.host, port=args.port, ssl_certfile=args.certfile, ssl_keyfile=args.keyfile,
                proxy_headers=False, server_header=False)
    return 0


def manage_user(parser, args):
    import getpass

    from .auth import ROLES, remove_user, save_user
    if args.action == "list":
        data = json.loads(Path(args.file).read_text(encoding="utf-8")) if Path(args.file).exists() else {"users": []}
        for user in data["users"]:
            print(f"{user['name']}\t{user['role']}")
        return 0
    if not args.name:
        parser.error("사용자 이름이 필요합니다.")
    if args.action == "remove":
        remove_user(args.file, args.name)
        print(f"삭제: {args.name}")
        return 0
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("비밀번호: ")
        if getpass.getpass("비밀번호 확인: ") != password:
            parser.error("비밀번호가 일치하지 않습니다.")
    if len(password) < 12:
        parser.error("비밀번호는 12자 이상이어야 합니다.")
    save_user(args.file, args.name, args.role, password)
    print(f"저장: {args.name} ({args.role}) · 역할: {', '.join(ROLES)}")
    return 0


def app_version():
    try:
        return metadata.version("diagram-bot")
    except metadata.PackageNotFoundError:
        return "dev"


def collect_all(parser, args):
    """Every (role, region) target; one report directory each. Exit 1 if any target failed, 2 if any was partial."""
    regions = [r.strip() for value in args.region for r in value.split(",") if r.strip()]
    if not args.role_arn and not (args.expected_account and len(args.expected_account) == 12 and args.expected_account.isdigit()):
        parser.error("--expected-account는 12자리 AWS 계정 ID여야 합니다 (--role-arn을 쓰면 역할의 계정으로 확인).")
    targets = [(role, role.split(":")[4], region) for role in args.role_arn for region in regions] or \
              [(None, args.expected_account, region) for region in regions]
    single = len(targets) == 1
    worst = 0
    for role, account, region in targets:
        label = f"{account}/{region}"
        try:
            snapshot = collect(region, args.profile, account, role_arn=role, external_id=args.external_id)
        except (BotoCoreError, ClientError) as exc:
            code = exc.response["Error"]["Code"] if isinstance(exc, ClientError) else type(exc).__name__
            print(f"{label}: 인증 또는 AWS 조회 시작 실패 ({code}). 프로파일·역할 신뢰 정책·접속 경로를 확인하세요.", file=sys.stderr)
            worst = 1
            continue
        except ValueError as exc:
            print(f"{label}: {exc}", file=sys.stderr)
            worst = 1
            continue
        path = write_report(snapshot, args.output if single else Path(args.output) / f"{account}-{region}")
        print(f"{label}: 보고서 {path.resolve()} · 수집 상태 {snapshot['metadata']['status']}")
        if snapshot["metadata"]["status"] != "complete" and worst == 0:
            worst = 2
    return worst


if __name__ == "__main__":
    sys.exit(main())
