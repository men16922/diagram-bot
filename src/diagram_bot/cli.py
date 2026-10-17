import argparse
import json
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
    live = commands.add_parser("collect", help="기존 SSO/SDK 인증 경로로 읽기 전용 조회")
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
    serve = commands.add_parser("serve", help="draw.io 기반 로컬 시연 앱 실행")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--data-dir", help="스냅샷과 편집본 저장 디렉터리")
    args = parser.parse_args()
    try:
        if args.command == "setup-editor":
            from .editor import install_editor
            print(f"draw.io 설치 완료: {install_editor(args.archive)}")
            return 0
        if args.command == "serve":
            import uvicorn
            from .server import create_app
            print(f"Diagram Bot: http://127.0.0.1:{args.port}", flush=True)
            uvicorn.run(create_app(args.data_dir), host="127.0.0.1", port=args.port)
            return 0
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
