"""Team mode sign-in to the app itself: local accounts, OpenID Connect SSO, sign-out and the audit log.

(Signing in to AWS is separate: routes/aws.py and aws_access.py.)"""
import html

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from ..auth import SESSION_SECONDS

COOKIE, OIDC_COOKIE = "diagram_bot_session", "diagram_bot_oidc"
PUBLIC = {"/login", "/api/login", "/auth/oidc/start", "/auth/oidc/callback"}
LOGIN_PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Diagram Bot 로그인</title><style>body{font-family:-apple-system,"Segoe UI","Noto Sans KR",Arial,sans-serif;background:#F3F5F8;display:grid;
place-items:center;min-height:100vh;margin:0;color:#233247}form{background:#fff;border:1px solid #DFE6EF;border-radius:12px;padding:32px;width:320px}
h1{font-size:20px;margin:0 0 20px}label{display:block;font-size:12px;color:#5A6C86;margin:14px 0 6px}input{width:100%;box-sizing:border-box;padding:10px;
border:1px solid #DCE5F0;border-radius:6px;font-size:14px}button{margin-top:20px;width:100%;padding:11px;border:0;border-radius:6px;background:#4773DD;
color:#fff;font-size:14px}p{color:#B4403F;font-size:12px;min-height:1em}a.sso{display:block;text-align:center;padding:11px;border-radius:6px;
background:#232F3E;color:#fff;text-decoration:none;font-size:14px;margin-bottom:8px}</style></head><body><form id="f"><h1>Diagram Bot</h1>__SSO__
<div __LOCAL__><label for="n">사용자</label><input id="n" autocomplete="username" required><label for="p">비밀번호</label>
<input id="p" type="password" autocomplete="current-password" required><button>로그인</button></div><p id="e" role="alert">__ERROR__</p></form>
<script>document.getElementById('f').onsubmit=async e=>{e.preventDefault();const r=await fetch('/api/login',{method:'POST',
headers:{'Content-Type':'application/json'},body:JSON.stringify({name:document.getElementById('n').value,password:document.getElementById('p').value})});
if(r.ok)location.href='/';else document.getElementById('e').textContent=(await r.json()).detail;};</script></body></html>"""


class LoginRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


def client_of(request):
    return request.client.host if request.client else None


def router(context):
    api = APIRouter()
    auth, oidc, audit, secure = context.auth, context.oidc, context.audit, context.secure

    def login_html(error="", status=200):
        page = (LOGIN_PAGE.replace("__SSO__", '<a class="sso" href="/auth/oidc/start">회사 계정(SSO)으로 로그인</a>' if oidc else "")
                .replace("__LOCAL__", "" if auth.users else "hidden").replace("__ERROR__", html.escape(error)))
        return HTMLResponse(page, status_code=status, headers={"X-Frame-Options": "DENY", "Cache-Control": "no-store"})

    @api.get("/login", response_class=HTMLResponse)
    def login_page():
        if not auth:
            return RedirectResponse("/", status_code=303)
        return login_html()

    @api.get("/auth/oidc/start")
    def oidc_start():
        if not oidc:
            raise HTTPException(404, "SSO가 설정되지 않았습니다.")
        try:
            url, state = oidc.start()
        except (OSError, ValueError) as exc:
            return login_html(f"SSO 제공자에 연결하지 못했습니다: {exc}", 502)
        response = RedirectResponse(url, status_code=303)
        # Lax: the provider sends the browser back with a top-level cross-site GET.
        response.set_cookie(OIDC_COOKIE, state, max_age=600, httponly=True, secure=secure, samesite="lax", path="/auth/oidc")
        return response

    @api.get("/auth/oidc/callback")
    def oidc_callback(request: Request, code: str = "", state: str = "", error: str = ""):
        client = client_of(request)
        if not oidc:
            raise HTTPException(404, "SSO가 설정되지 않았습니다.")
        try:
            if error or not code:
                raise ValueError(f"SSO 제공자가 로그인을 거부했습니다: {error or 'code 없음'}")
            name, role, _ = oidc.finish(code, state, request.cookies.get(OIDC_COOKIE))
        except (OSError, ValueError, KeyError) as exc:
            audit.write(None, "login", result="failed", client=client, method="oidc", reason=str(exc)[:200])
            return login_html(str(exc), 403)
        token = auth.start_session(name, role)
        audit.write(name, "login", result="ok", client=client, method="oidc", role=role)
        # A same-site page load, so the Strict session cookie is sent on the next request.
        response = HTMLResponse('<!doctype html><meta http-equiv="refresh" content="0;url=/"><a href="/">계속</a>',
                                headers={"Cache-Control": "no-store"})
        response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, secure=secure, samesite="strict", path="/")
        response.delete_cookie(OIDC_COOKIE, path="/auth/oidc")
        return response

    @api.post("/api/login")
    def login(credentials: LoginRequest, request: Request):
        client = client_of(request)
        if not auth:
            raise HTTPException(404, "팀 모드가 아닙니다.")
        if auth.throttled(client):
            audit.write(credentials.name, "login", result="throttled", client=client)
            raise HTTPException(429, "로그인 실패가 많습니다. 15분 후 다시 시도하세요.")
        session = auth.login(credentials.name, credentials.password, client)
        audit.write(credentials.name, "login", result="ok" if session else "failed", client=client)
        if not session:
            raise HTTPException(401, "사용자 이름 또는 비밀번호가 올바르지 않습니다.")
        response = JSONResponse({"name": credentials.name})
        response.set_cookie(COOKIE, session, max_age=SESSION_SECONDS, httponly=True, secure=secure, samesite="strict", path="/")
        return response

    @api.post("/api/logout")
    def logout(request: Request):
        if auth:
            auth.logout(request.cookies.get(COOKIE))
            audit.write(request.state.user, "logout", client=client_of(request))
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/")
        return response

    @api.get("/api/audit")
    def read_audit(request: Request, limit: int = 200):
        if auth and request.state.role != "admin":
            raise HTTPException(403, "감사 로그는 admin만 볼 수 있습니다.")
        return audit.tail(max(1, min(limit, 1000)))

    return api
