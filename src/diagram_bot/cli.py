import argparse
import json
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
    commands = parser.add_subparsers(dest="command", required=True)
    live = commands.add_parser("collect", help="기존 SSO/SDK 인증 경로로 읽기 전용 조회")
    live.add_argument("--profile", help="AWS SSO 프로파일; 생략하면 표준 SDK 인증 체인")
    live.add_argument("--region", required=True)
    live.add_argument("--expected-account", required=True, help="조회할 12자리 AWS 계정 ID")
    live.add_argument("--output", required=True)
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
            if len(args.expected_account) != 12 or not args.expected_account.isdigit():
                parser.error("--expected-account는 12자리 AWS 계정 ID여야 합니다.")
            snapshot = collect(args.region, args.profile, args.expected_account)
        elif args.command == "demo":
            snapshot = SAMPLES[args.sample]() if args.sample else demo_snapshot()
        else:
            snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
            if snapshot.get("schema_version") != 1:
                raise ValueError("지원하지 않는 snapshot schema_version")
        path = write_report(snapshot, args.output)
        print(f"보고서: {path.resolve()}")
        print(f"수집 상태: {snapshot['metadata']['status']}")
        return 2 if args.command == "collect" and snapshot["metadata"]["status"] != "complete" else 0
    except (BotoCoreError, ClientError) as exc:
        code = exc.response["Error"]["Code"] if isinstance(exc, ClientError) else type(exc).__name__
        print(f"인증 또는 AWS 조회 시작 실패: {code}. 기존 SSO 로그인·프로파일·접속 경로를 확인하세요.", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"실패: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
