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
    merge = commands.add_parser("combine", help="계정·리전별 snapshot.json을 합쳐 계정 간 네트워크 연결도 보고서 생성")
    merge.add_argument("snapshots", nargs="+", help="collect가 만든 snapshot.json (2개 이상)")
    merge.add_argument("--output", required=True)
    merge.add_argument("--title", help="통합 보기 이름")
    cfn = commands.add_parser("export-cfn", help="snapshot.json(설계·샘플·수집)을 CloudFormation 템플릿 초안(JSON)으로 내보내기")
    cfn.add_argument("snapshot")
    cfn.add_argument("--output", help="템플릿 파일 경로 (생략하면 표준 출력; 여러 계정을 합친 스냅샷은 <이름>-<계정>-<리전>.json으로 나눠 저장)")
    setup = commands.add_parser("setup-editor", help="고정 버전 draw.io 편집기 설치 (최초 한 번)")
    setup.add_argument("--archive", help="오프라인 설치용 공식 draw.war 경로")
    talk = commands.add_parser("chat", parents=[env], help="채팅으로 표준 아키텍처 설계·AWS 읽기 전용 조회 (Strands + Bedrock)")
    talk.add_argument("--model", help="nova-2-lite(기본), gpt-6-luna, claude-sonnet-4.5 (다른 모델이 실패하면 nova-2-lite로 전환)")
    talk.add_argument("--output", default="reports/chat", help="설계·수집 결과 보고서 디렉터리")
    talk.add_argument("-m", "--message", action="append", default=[], help="대화형 대신 보낼 메시지 (여러 번 지정 가능)")
    talk.add_argument("--from", dest="start", help="설계 출발점으로 쓸 스냅샷 JSON (수집 결과 등; 에이전트의 start_from_current)")
    serve = commands.add_parser("serve", parents=[env], help="draw.io 기반 로컬 시연 앱 실행")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--data-dir", help="스냅샷과 편집본 저장 디렉터리")
    serve.add_argument("--host", default="127.0.0.1", help="루프백이 아니면 --users·--certfile·--keyfile·--allowed-host 필수 (팀 모드)")
    serve.add_argument("--users", help="팀 모드 사용자 파일 (diagram-bot user add로 생성)")
    serve.add_argument("--oidc-config", help="팀 모드 OpenID Connect(SSO) 설정 JSON")
    serve.add_argument("--certfile", help="TLS 인증서 (PEM)")
    serve.add_argument("--keyfile", help="TLS 개인 키 (PEM)")
    serve.add_argument("--allowed-host", action="append", default=[], help="접속에 쓰는 호스트 이름 (여러 번 지정 가능)")
    serve.add_argument("--aws-egress", choices=("server", "browser"), default=os.environ.get("DIAGRAM_BOT_AWS_EGRESS", "server"),
                       help="AWS API 호출 경로: server(서버가 직접) 또는 browser(서버가 서명, 사용자 브라우저가 PC IP에서 전송; "
                            "서버 방화벽·aws:SourceIp 조건이 있는 환경)")
    serve.add_argument("--sso-start-url", default=os.environ.get("DIAGRAM_BOT_SSO_START_URL"), help="로그인 화면에 먼저 보일 IAM Identity Center 시작 URL")
    serve.add_argument("--sso-region", default=os.environ.get("DIAGRAM_BOT_SSO_REGION", "us-east-1"), help="Identity Center 리전 (기본 us-east-1)")
    account = commands.add_parser("user", help="팀 모드 사용자 관리")
    account.add_argument("action", choices=("add", "remove", "list"))
    account.add_argument("name", nargs="?")
    account.add_argument("--file", required=True, help="사용자 파일 경로")
    account.add_argument("--role", choices=("viewer", "editor", "admin"), default="viewer")
    account.add_argument("--password-stdin", action="store_true", help="비밀번호를 표준 입력 첫 줄에서 읽기 (자동화용)")
    align_cmd = commands.add_parser("align-xml", help="기존 draw.io XML 파일을 규칙에 맞게 자동 정렬·컨테이너 크기 맞춤·직교 배선")
    align_cmd.add_argument("input", help="정렬할 draw.io XML 파일 경로")
    align_cmd.add_argument("-o", "--output", help="저장할 draw.io XML 경로 (생략하면 표준 출력)")
    align_cmd.add_argument("--mode", choices=("preserve", "rebuild"), default="preserve",
                           help="preserve(기존 셀 ID·구조 유지 정렬) 또는 rebuild(Snapshot 변환 후 전체 재배치)")
    align_cmd.add_argument("--spec", help="적용할 규격 프리셋(default, clean, mxcell) 또는 규격 JSON 파일 경로")
    align_cmd.add_argument("--spec-from", help="스타일·규격을 추출할 회사 표준 draw.io XML 샘플 파일 경로")
    align_cmd.add_argument("--normalize-styles", action="store_true", help="컨테이너·화살표 스타일을 규격에 맞게 통일")
    imp_cmd = commands.add_parser("import-xml", help="기존 AWS draw.io XML 파일을 Diagram Bot snapshot.json으로 변환")
    imp_cmd.add_argument("input", help="변환할 draw.io XML 파일 경로")
    imp_cmd.add_argument("-o", "--output", help="저장할 snapshot.json 경로 (생략하면 표준 출력)")
    imp_cmd.add_argument("--title", help="스냅샷 제목")
    imp_cmd.add_argument("--region", default="ap-northeast-2", help="기본 리전 (기본 ap-northeast-2)")
    imp_cmd.add_argument("--render", help="변환된 스냅샷을 다시 렌더링해 저장할 draw.io XML 경로")
    imp_cmd.add_argument("--spec", help="--render 시 적용할 규격 프리셋(default, clean, mxcell)")
    adopt_cmd = commands.add_parser("adopt-template", help="회사 표준 draw.io XML을 기준으로 포맷 규격(DrawioSpec) 추출·자동 정렬·표준 아키텍처 보고서 생성")
    adopt_cmd.add_argument("input", help="회사 표준 아키텍처 draw.io XML 파일 경로")
    adopt_cmd.add_argument("-o", "--output", required=True, help="생성할 보고서 및 표준화된 산출물 디렉터리")
    adopt_cmd.add_argument("--name", default="company-standard", help="등록할 사내 표준 아키텍처 이름 (기본 company-standard)")
    adopt_cmd.add_argument("--title", help="아키텍처 제목")
    adopt_cmd.add_argument("--region", default="ap-northeast-2", help="기본 리전 (기본 ap-northeast-2)")
    adopt_cmd.add_argument("--save-spec", help="추출한 DrawioSpec JSON을 별도 저장할 경로")
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
        if args.command == "align-xml":
            return run_align_xml(args)
        if args.command == "import-xml":
            return run_import_xml(args)
        if args.command == "adopt-template":
            return run_adopt_template(args)
        if args.command == "diff":
            load = lambda path: json.loads(Path(path).read_text(encoding="utf-8"))
            result = diff(load(args.before), load(args.after))
            print(json.dumps(result, ensure_ascii=False, indent=2) if args.format == "json" else diff_markdown(result))
            return 4 if args.fail_on_change and any(result["summary"].values()) else 0
        if args.command == "export-cfn":
            return export_cfn(parser, args)
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
        elif args.command == "combine":
            from .combine import combine_snapshots
            snapshot = combine_snapshots([json.loads(Path(path).read_text(encoding="utf-8")) for path in args.snapshots], args.title)
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

    workspace = Workspace(save)
    if args.start:
        snapshot = json.loads(Path(args.start).read_text(encoding="utf-8"))
        snapshot["graph"] = build_graph(snapshot)
        workspace.collected = snapshot
    chat = Chat(workspace, args.model or DEFAULT_MODEL)

    def progress(event):
        if event["type"] == "tool" and event["status"] != "start":
            print(f"  · {event['name']} {'완료' if event['status'] == 'success' else '오류'}", file=sys.stderr)

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
        result = chat.ask(message, on_event=progress)
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
    app = create_app(args.data_dir, users=users, allowed_hosts=args.allowed_host, secure=secure, oidc=oidc, egress=args.aws_egress,
                     sso_start=(args.sso_start_url, args.sso_region) if args.sso_start_url else None)
    shown = args.allowed_host[0] if args.allowed_host else args.host
    print(f"Diagram Bot: {'https' if secure else 'http'}://{shown}:{args.port}" + (f" · 팀 모드 사용자 {len(users)}명" if users else "")
          + (" · SSO" if oidc else "") + (" · AWS 호출은 브라우저에서 (서버는 서명만)" if args.aws_egress == "browser" else ""), flush=True)
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


def export_cfn(parser, args):
    """CloudFormation draft per account × region; unsupported items and assumptions are summarized on stderr."""
    from .iac import dumps, templates
    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    if snapshot.get("schema_version") != 1:
        raise ValueError("지원하지 않는 snapshot schema_version")
    results = templates(snapshot)
    if len(results) > 1 and not args.output:
        parser.error("여러 계정·리전을 합친 스냅샷은 --output이 필요합니다 (계정·리전마다 템플릿 파일을 따로 씁니다).")
    for label, template in results:
        meta = template["Metadata"]["DiagramBot"]
        if args.output:
            path = Path(args.output)
            if len(results) > 1:
                path = path.with_name(f"{path.stem}-{label}{path.suffix or '.json'}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(dumps(template), encoding="utf-8")
            where = f"CloudFormation 템플릿: {path.resolve()}"
        else:
            sys.stdout.write(dumps(template))
            where = f"CloudFormation 템플릿 ({label})"
        print(f"{where} · 리소스 {len(template['Resources'])}개 · 매개변수 {len(template.get('Parameters', {}))}개 · "
              f"미지원 {len(meta['Unsupported'])}개 · 가정 {len(meta['Assumptions'])}개 (Metadata.DiagramBot에서 검토)", file=sys.stderr)
    return 0


def run_align_xml(args):
    from .drawio_spec import DrawioSpec, resolve_spec, spec_from_xml
    from .xml_align import align_drawio_xml, inspect_drawio_xml
    xml_text = Path(args.input).read_text(encoding="utf-8")
    spec = None
    if args.spec_from:
        spec = spec_from_xml(Path(args.spec_from).read_text(encoding="utf-8"))
    elif args.spec:
        spec = DrawioSpec.from_dict(json.loads(Path(args.spec).read_text(encoding="utf-8"))) if Path(args.spec).is_file() else resolve_spec(args.spec)
    before = inspect_drawio_xml(xml_text)
    aligned = align_drawio_xml(xml_text, mode=args.mode, spec=spec, normalize_styles=args.normalize_styles or spec is not None)
    after = inspect_drawio_xml(aligned)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(aligned, encoding="utf-8")
        print(f"정렬된 draw.io XML: {out_path.resolve()} · 겹침 {len(before['overlaps'])}→{len(after['overlaps'])} · "
              f"범위 이탈 {len(before['out_of_bounds'])}→{len(after['out_of_bounds'])} · "
              f"미배선 {len(before['unrouted_edges'])}→{len(after['unrouted_edges'])}", file=sys.stderr)
    else:
        sys.stdout.write(aligned)
    return 0


def run_import_xml(args):
    from .drawio import generate_document
    from .xml_align import import_drawio_xml
    xml_text = Path(args.input).read_text(encoding="utf-8")
    snapshot = import_drawio_xml(xml_text, title=args.title, region=args.region)
    text = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        print(f"가져온 스냅샷: {out_path.resolve()} · 노드 {len(snapshot['graph']['nodes'])}개 · 관계 {len(snapshot['graph']['edges'])}개", file=sys.stderr)
    else:
        sys.stdout.write(text)
    if args.render:
        render_path = Path(args.render)
        render_path.parent.mkdir(parents=True, exist_ok=True)
        render_path.write_text(generate_document(snapshot, spec=args.spec), encoding="utf-8")
        print(f"렌더링된 draw.io XML: {render_path.resolve()}", file=sys.stderr)
    return 0


def run_adopt_template(args):
    from .drawio_spec import set_default_spec
    from .xml_align import adopt_company_xml
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    spec_file = Path(args.save_spec) if args.save_spec else (out_dir / "company-spec.json")
    try:
        result = adopt_company_xml(
            args.input,
            name=args.name,
            title=args.title,
            region=args.region,
            set_default=True,
            save_spec=spec_file,
        )
        (out_dir / "aligned.drawio").write_text(result["aligned_xml"], encoding="utf-8")
        path = write_report(result["snapshot"], out_dir)
        spec = result["spec"]
        print(f"보고서: {path.resolve()}")
        print(f"사내 규격(DrawioSpec): {spec_file.resolve()} (래퍼={spec.cell_wrapper}, Cloud상자={spec.use_cloud_box}, "
              f"Region상자={spec.use_region_box}, AZ상자={spec.use_az_box}, 서브넷라벨={spec.subnet_label_style})")
    finally:
        set_default_spec(None)
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
