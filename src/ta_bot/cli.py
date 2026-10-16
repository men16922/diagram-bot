import argparse
import json
import sys
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError

from .collector import collect
from .demo import demo_snapshot
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
    args = parser.parse_args()
    try:
        if args.command == "collect":
            if len(args.expected_account) != 12 or not args.expected_account.isdigit():
                parser.error("--expected-account는 12자리 AWS 계정 ID여야 합니다.")
            snapshot = collect(args.region, args.profile, args.expected_account)
        elif args.command == "demo":
            snapshot = demo_snapshot()
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
