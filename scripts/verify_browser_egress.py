"""Live check of browser egress against real AWS (read-only): run where the company's network rules apply.

Start the app first with the server's own AWS access blocked, so any call it made itself would fail:

    HTTPS_PROXY=http://127.0.0.1:9 NO_PROXY=127.0.0.1,localhost \\
      diagram-bot serve --aws-egress browser --env-file .env --port 8796

then `python scripts/verify_browser_egress.py --url http://127.0.0.1:8796 --region us-east-1`. The script signs in with the
server's credential chain (with Identity Center, sign in through the dialog first and pass --skip-sign-in), collects one
region through the app UI, sends one chat turn, and reports which AWS hosts the browser called. Nothing is changed in AWS.
"""
import argparse
import json
import sys
import time

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8796")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--chromium", help="Optional preinstalled Chromium executable")
    parser.add_argument("--skip-chat", action="store_true", help="Do not call Bedrock")
    args = parser.parse_args()
    hosts = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chromium) if args.chromium else p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("request", lambda r: hosts.append(r.url.split("/")[2]) if ".amazonaws.com" in r.url else None)
        page.goto(args.url)
        boot = page.evaluate("fetch('/api/bootstrap').then(r => r.json())")
        if boot.get("egress") != "browser":
            sys.exit("The app is not running with --aws-egress browser.")
        page.locator("#profile-button").click()
        page.locator("#signin-button").click()
        expect(page.locator("#egress-note")).to_be_visible()
        page.locator("#use-profile").click()
        expect(page.locator("#profile-label")).not_to_have_text("로그인", timeout=60000)
        identity = page.locator("#profile-label").inner_text()
        page.locator("#profile-button").click()
        page.locator("#collection-button").click()
        page.locator("#aws-region").fill(args.region)
        started = time.time()
        page.locator("#start-collection").click()
        page.wait_for_function("!document.querySelector('#collection-dialog').open", timeout=300000)
        snapshot = page.evaluate(f"fetch('/api/snapshots/{page.locator('#snapshot-select').input_value()}').then(r => r.json())")
        coverage = snapshot["coverage"]
        report = {"identity": identity, "collection_seconds": round(time.time() - started, 1), "status": snapshot["metadata"]["status"],
                  "calls": len(coverage), "incomplete": [c["operation"] for c in coverage if c["status"] != "complete"]}
        if not args.skip_chat:
            text = page.evaluate("""async () => { const token = (await (await fetch('/api/bootstrap')).json()).token;
              const r = await fetch('/api/agent/chat/stream', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Diagram-Bot': token},
                body: JSON.stringify({message: '2 AZ VPC 하나만 그려줘. 짧게 답해.'})}); return r.text(); }""")
            done = json.loads(text.splitlines()[-1])
            report["chat"] = {"result": done["type"], "model": done.get("model_label"), "tools": done.get("tools_used"),
                              "error": done.get("detail")}
        report["browser_aws_requests"] = len(hosts)
        report["browser_aws_hosts"] = sorted(set(hosts))
        browser.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "complete" and report["browser_aws_requests"] and report.get("chat", {}).get("result", "done") == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
