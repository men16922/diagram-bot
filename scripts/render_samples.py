"""Regenerate examples/samples: CLI report artifacts, PNGs exported by the app's own editor and README sections.

Run against a fresh `diagram-bot serve` (a new data directory, so no saved edits change the pictures):

    diagram-bot serve --data-dir reports/render-01
    python scripts/render_samples.py [--url http://127.0.0.1:8765] [--sample ai-platform]
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright

from diagram_bot.graph import build_graph
from diagram_bot.samples import SAMPLES

ROOT = Path(__file__).resolve().parent.parent
# The offline report must not pick up the developer's AWS credentials (see docs/LESSONS.md).
CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("AWS_")}
# Resource counts in each README section, in this order (collected nodes only).
COUNTS = [("vpc", "VPC"), ("subnet", "서브넷"), ("ec2", "EC2"), ("load_balancer", "로드밸런서"), ("target_group", "대상 그룹"),
          ("nat_gateway", "NAT"), ("db_instance", "RDS/Aurora/DocumentDB"), ("cache_node", "ElastiCache"), ("msk_broker", "MSK 브로커 그룹"),
          ("search_node", "OpenSearch 노드 그룹"), ("eks_cluster", "EKS"), ("lambda_function", "Lambda"), ("ecs_service", "ECS 서비스"),
          ("ecs_task", "ECS 태스크"), ("efs_mount_target", "EFS 마운트 타깃"), ("redshift_cluster", "Redshift"),
          ("auto_scaling_group", "Auto Scaling 그룹"), ("vpc_endpoint", "VPC 엔드포인트"), ("tgw_attachment", "TGW 연결"),
          ("vpc_peering", "VPC 피어링"), ("vpn_gateway", "VPN 게이트웨이"), ("customer_gateway", "고객 게이트웨이"),
          ("fsx_file_system", "FSx"), ("sagemaker_endpoint", "SageMaker 엔드포인트"), ("sagemaker_cluster", "HyperPod")]


def write_readme(output):
    """Keep the hand-written header; rebuild one section per sample from the sample itself."""
    readme = output / "README.md"
    header = readme.read_text(encoding="utf-8").split("\n## ", 1)[0].rstrip() + "\n"
    sections = []
    for key, build in SAMPLES.items():
        snapshot = build()
        nodes = [n for n in build_graph(snapshot)["nodes"] if not n["reference_only"]]
        counts = " · ".join(f"{label} {count}" for kind, label in COUNTS if (count := sum(n["kind"] == kind for n in nodes)))
        practices = "\n".join(f"- {line}" for line in snapshot["metadata"]["practices"])
        images = "\n".join(f"![{key} {png.name}]({key}/{png.name})" for png in sorted((output / key).glob("*.png")) if png.name != "overview.png")
        sections.append(f"## {snapshot['metadata']['title']} — `{key}`\n\n{counts}\n\n{practices}\n\n{images}\n")
    readme.write_text(header + "\n" + "\n".join(sections), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--output", type=Path, default=ROOT / "examples" / "samples")
    parser.add_argument("--sample", action="append", choices=sorted(SAMPLES), help="Only these samples (default: all)")
    args = parser.parse_args()
    keys = args.sample or list(SAMPLES)
    for key in keys:
        target = args.output / key
        subprocess.run([sys.executable, "-m", "diagram_bot.cli", "demo", "--sample", key, "--output", str(target)],
                       check=True, env=CLEAN_ENV, stdout=subprocess.DEVNULL)
        for old in target.glob("*.png"):
            old.unlink()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1100}, device_scale_factor=1)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        ready = lambda: expect(page.locator("#save-button")).to_be_enabled(timeout=60000)
        page.goto(args.url)
        ready()

        def export(path):
            page.locator("#export-button").click()
            page.locator("#png-scale").select_option("1")
            page.locator("#png-transparent").set_checked(False)
            with page.expect_download(timeout=60000) as event:
                page.locator("#download-png").click()
            event.value.save_as(path)
            page.locator('#export-dialog [aria-label="닫기"]').click()

        for key in keys:
            snapshot = json.loads(urlopen(f"{args.url}/api/snapshots/sample-{key}").read())
            vpcs = [(n["resource_id"], n["name"]) for n in snapshot["graph"]["nodes"] if n["kind"] == "vpc"]
            page.locator("#snapshot-select").select_option(f"sample-{key}")
            ready()
            page.locator("#view-overview").click()
            ready()
            export(args.output / key / "overview.png")
            for vpc, name in vpcs:
                page.locator(f'[data-vpc="{vpc}"]').click()
                ready()
                export(args.output / key / f"{name}.png")
            print(f"{key}: overview + {len(vpcs)} VPC")
        browser.close()
    if errors:
        raise SystemExit(f"page errors: {errors}")
    write_readme(args.output)


if __name__ == "__main__":
    main()
