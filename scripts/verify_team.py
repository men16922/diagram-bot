"""Team-mode acceptance: TLS server on a non-loopback address, sign-in, roles and audit, in Chromium.

Creates users and a self-signed certificate in a temporary directory, starts `diagram-bot serve`,
and checks what a viewer and an admin can do. Usage: python scripts/verify_team.py [--chromium PATH]
"""
import argparse
import base64
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.error import URLError
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright

PASSWORD = "team-mode-check-1"


def fake_idp(port, app_port):
    """A minimal OpenID provider on loopback: signs in a fixed user from the cloud-architects group."""
    issuer = f"http://localhost:{port}"
    grants = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, payload):
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path == "/.well-known/openid-configuration":
                self.reply({"issuer": issuer, "authorization_endpoint": issuer + "/authorize", "token_endpoint": issuer + "/token",
                            "userinfo_endpoint": issuer + "/userinfo"})
            elif url.path == "/authorize":
                grants["code-1"] = query["nonce"]
                self.send_response(302)
                self.send_header("Location", query["redirect_uri"] + "?" + urlencode({"code": "code-1", "state": query["state"]}))
                self.end_headers()
            elif url.path == "/userinfo":
                self.reply({"sub": "sso-1", "email": "architect@corp.example", "groups": ["cloud-architects"]})

        def do_POST(self):  # noqa: N802
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode()).items()}
            claims = {"iss": issuer, "aud": "diagram-bot", "sub": "sso-1", "nonce": grants.pop(form["code"]), "exp": time.time() + 300}
            token = ".".join(base64.urlsafe_b64encode(json.dumps(part).encode()).decode().rstrip("=") for part in ({"alg": "none"}, claims)) + ".x"
            self.reply({"access_token": "at", "id_token": token})

    server = HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return issuer, server


def run(*args, stdin=None):
    subprocess.run(args, input=stdin, text=True, check=True, capture_output=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--chromium")
    parser.add_argument("--port", type=int, default=8443)
    args = parser.parse_args()
    work = Path(tempfile.mkdtemp(prefix="diagram-bot-team-"))
    users = work / "users.json"
    for name, role in (("viewer1", "viewer"), ("admin1", "admin")):
        run("diagram-bot", "user", "add", name, "--role", role, "--file", str(users), "--password-stdin", stdin=PASSWORD + "\n")
    run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=localhost",
        "-keyout", str(work / "key.pem"), "-out", str(work / "cert.pem"))
    issuer, idp = fake_idp(args.port + 1, args.port)
    (work / "oidc.json").write_text(json.dumps({"issuer": issuer, "client_id": "diagram-bot", "client_secret": "s3cret",
                                                "redirect_uri": f"https://localhost:{args.port}/auth/oidc/callback",
                                                "role_claim": "groups", "role_map": {"cloud-architects": "admin"}}))
    # A non-loopback bind address without credentials must be refused.
    refused = subprocess.run(["diagram-bot", "serve", "--host", "0.0.0.0", "--data-dir", str(work / "x")], capture_output=True, text=True)
    assert refused.returncode != 0 and "인증 없는 네트워크 노출 금지" in refused.stderr, refused.stderr
    host = socket.gethostname()
    server = subprocess.Popen(["diagram-bot", "serve", "--host", "0.0.0.0", "--port", str(args.port), "--data-dir", str(work / "data"),
                               "--users", str(users), "--oidc-config", str(work / "oidc.json"), "--certfile", str(work / "cert.pem"), "--keyfile", str(work / "key.pem"),
                               "--allowed-host", "localhost", "--allowed-host", host],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"https://localhost:{args.port}"
    try:
        for _ in range(60):
            try:
                urlopen(f"http://localhost:{args.port}/login", timeout=1)  # noqa: S310 - plain HTTP must not be served
            except URLError:
                pass
            except Exception:  # noqa: BLE001 - the TLS listener rejects plain HTTP; any answer means it is up
                break
            time.sleep(0.5)
        with sync_playwright() as p:
            browser = p.chromium.launch(**({"executable_path": args.chromium} if args.chromium else {}))
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()
            page.goto(base + "/")
            expect(page).to_have_url(base + "/login")
            page.fill("#n", "viewer1")
            page.fill("#p", "wrong-password-x")
            page.click("button")
            expect(page.locator("#e")).to_contain_text("올바르지 않습니다")
            page.fill("#p", PASSWORD)
            page.click("button")
            expect(page.locator("#user-role")).to_contain_text("viewer1 · 보기 전용")
            expect(page.locator("#collection-button")).to_be_disabled()
            expect(page.locator("#import-button")).to_be_disabled()
            cookie = next(c for c in context.cookies() if c["name"] == "diagram_bot_session")
            assert cookie["httpOnly"] and cookie["secure"] and cookie["sameSite"] == "Strict", cookie
            status = page.evaluate("fetch('/api/snapshots', {method: 'POST', headers: {'Content-Type': 'application/json', "
                                   "'X-Diagram-Bot': 'x'}, body: '{}'}).then(r => r.status)")
            assert status == 403, status
            page.click("#logout-button")
            expect(page).to_have_url(base + "/login")
            page.fill("#n", "admin1")
            page.fill("#p", PASSWORD)
            page.click("button")
            expect(page.locator("#user-role")).to_contain_text("admin1 · 관리자")
            expect(page.locator("#collection-button")).to_be_enabled()
            page.click("#logout-button")
            expect(page).to_have_url(base + "/login")
            page.click("a.sso")  # app -> provider -> callback -> app
            expect(page.locator("#user-role")).to_contain_text("architect@corp.example · 관리자")
            audit = page.evaluate("fetch('/api/audit').then(r => r.json())")
            actions = [(e["user"], e["action"], e["result"]) for e in audit]
            assert ("viewer1", "login", "failed") in actions and ("viewer1", "logout", "ok") in actions, actions
            assert ("admin1", "login", "ok") in actions and ("architect@corp.example", "login", "ok") in actions, actions
            browser.close()
        print(json.dumps({"team_mode": "ok", "checks": ["non-loopback refused without credentials", "TLS", "login redirect",
                                                        "failed login message", "viewer controls disabled", "cookie HttpOnly/Secure/Strict",
                                                        "viewer mutation 403", "logout", "admin controls", "OIDC SSO sign-in (PKCE)",
                                                        "audit log"]}, ensure_ascii=False))
    finally:
        idp.shutdown()
        server.terminate()
        server.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main())
