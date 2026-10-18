"""AWS: who the credentials are, read-only collection jobs, Identity Center sign-in and the browser egress relay."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..collector import collect, now
from ..egress import RelayError
from ..sso import SsoError
from . import role, user

REGION = r"^[a-z]{2}(?:-[a-z0-9]+)+-\d+$"


class CollectionRequest(BaseModel):
    profile: str | None = Field(default=None, max_length=128)
    region: str = Field(pattern=REGION, max_length=50)
    expected_account: str = Field(pattern=r"^\d{12}$")
    role_arn: str | None = Field(default=None, max_length=2048, pattern=r"^arn:aws[a-z-]*:iam::\d{12}:role/[\w+=,.@/-]+$")
    external_id: str | None = Field(default=None, min_length=2, max_length=1224, pattern=r"^[\w+=,.@:/-]+$")
    sso_login: str | None = Field(default=None, max_length=64, pattern=r"^[\w-]+$")
    sso_role: str | None = Field(default=None, max_length=64, pattern=r"^[\w+=,.@-]+$")


class SsoStartRequest(BaseModel):
    start_url: str = Field(min_length=10, max_length=512)
    region: str = Field(pattern=REGION, max_length=50)


class SsoUseRequest(BaseModel):
    account_id: str = Field(pattern=r"^\d{12}$")
    role: str = Field(max_length=64, pattern=r"^[\w+=,.@-]+$")


class RelayAnswer(BaseModel):
    status: int = Field(ge=0, le=599)
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = Field(default=None, max_length=12 * 1024 * 1024)
    error: str | None = Field(default=None, max_length=1000)


def router(context, executor=None):
    """executor runs collection jobs (one at a time by default); jobs live in memory and reset on restart."""
    api = APIRouter()
    aws, audit = context.aws, context.audit
    executor = executor or ThreadPoolExecutor(max_workers=1)
    jobs = {}

    @api.get("/api/identity")
    def identity(request: Request, profile: str | None = None):
        """Who the local credentials are (STS GetCallerIdentity, read-only): the profile menu shows it after sign-in."""
        if context.auth and role(request) != "admin":
            raise HTTPException(403, "AWS 자격 증명은 admin만 사용할 수 있습니다.")
        try:
            caller = aws.outbound(boto3.Session(profile_name=profile or None), user(request)).client("sts").get_caller_identity()
        except Exception as exc:  # noqa: BLE001 - shown to the user as the reason sign-in failed
            raise HTTPException(422, f"AWS 자격 증명을 확인하지 못했습니다: {type(exc).__name__}: {exc}") from exc
        return {"account": caller["Account"], "arn": caller["Arn"], "profile": profile or None}

    @api.post("/api/collections", status_code=202)
    def start_collection(params: CollectionRequest, request: Request):
        who = user(request)
        with context.mutex:
            if any(j["status"] in {"queued", "running"} for j in jobs.values()):
                raise HTTPException(409, "현재 수집이 진행 중입니다.")
            identifier = uuid4().hex
            jobs[identifier] = {"id": identifier, "status": "queued", "started_at": now()}

        def work():
            jobs[identifier]["status"] = "running"
            try:
                session = aws.collection_session(who, params.sso_login, params.expected_account, params.sso_role, params.region)
                # prepare: with browser egress every session of the collection (incl. an assumed role's) is relayed.
                snapshot = collect(params.region, None if session else params.profile, params.expected_account, session=session,
                                   role_arn=params.role_arn, external_id=params.external_id, prepare=aws.prepare_for(who))
                jobs[identifier].update(status=snapshot["metadata"]["status"], snapshot_id=context.snapshots.save(snapshot))
            except (BotoCoreError, ClientError) as exc:
                code = exc.response["Error"]["Code"] if isinstance(exc, ClientError) else type(exc).__name__
                jobs[identifier].update(status="failed", error=f"{code}: 기존 SSO 로그인·프로파일·권한을 확인하세요.")
            except (ValueError, RelayError) as exc:
                jobs[identifier].update(status="failed", error=str(exc))
            except Exception:
                jobs[identifier].update(status="failed", error="수집을 완료하지 못했습니다. 마지막 스냅샷은 보존됩니다.")
            jobs[identifier]["finished_at"] = now()
            audit.write(who, "collection", target=f"{params.role_arn or params.expected_account}/{params.region}",
                        result=jobs[identifier]["status"], snapshot=jobs[identifier].get("snapshot_id"),
                        via=aws.via(params.sso_role if params.sso_login else None))

        executor.submit(work)
        return jobs[identifier]

    @api.get("/api/collections/{identifier}")
    def collection_status(identifier: str):
        if identifier not in jobs:
            raise HTTPException(404, "수집 작업을 찾을 수 없습니다. 앱 재시작 시 진행 상태가 초기화됩니다.")
        return jobs[identifier]

    @api.get("/api/sso/defaults")
    def sso_start_defaults():
        return aws.start_urls()

    @api.post("/api/sso/login")
    def sso_login(params: SsoStartRequest, request: Request):
        try:
            result = aws.sso.start(params.start_url, params.region, user(request))
        except SsoError as exc:
            raise HTTPException(422, str(exc)) from exc
        audit.write(user(request), "sso login started", target=params.start_url)
        return result

    @api.get("/api/sso/login/{identifier}")
    def sso_status(identifier: str, request: Request):
        try:
            view = aws.sso.poll(identifier, user(request))
        except SsoError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (BotoCoreError, ClientError) as exc:
            raise HTTPException(502, f"SSO 계정 목록을 받지 못했습니다({type(exc).__name__}).") from exc
        if view.pop("changed", False):
            audit.write(user(request), f"sso login {view['status']}")
        return view

    @api.get("/api/sso/login/{identifier}/roles")
    def sso_roles(identifier: str, account_id: str, request: Request):
        try:
            return {"roles": aws.sso.roles(identifier, user(request), account_id)}
        except SsoError as exc:
            raise HTTPException(409, str(exc)) from exc
        except (BotoCoreError, ClientError) as exc:
            raise HTTPException(502, f"SSO 역할 목록을 받지 못했습니다({type(exc).__name__}).") from exc

    @api.post("/api/sso/login/{identifier}/use")
    def sso_use(identifier: str, params: SsoUseRequest, request: Request):
        """The account and role the user signed in with: the chat agent reads (and, with browser egress, calls Bedrock) with it."""
        who = user(request)
        try:
            aws.sso.authorized_flow(identifier, who)
        except SsoError as exc:
            raise HTTPException(422, str(exc)) from exc
        aws.choose(who, identifier, params.account_id, params.role)
        with context.mutex:
            context.chats.pop(who, None)  # the next chat turn starts with the new identity
        audit.write(who, "sso role selected", target=f"{params.role}@{params.account_id}")
        return {"account": params.account_id, "role": params.role}

    @api.post("/api/sso/login/{identifier}/logout")
    def sso_logout(identifier: str, request: Request):
        aws.forget(user(request), identifier)
        return {"logged_out": aws.sso.logout(identifier, user(request))}

    @api.get("/api/relay/next")
    def relay_next(request: Request):
        """Browser egress: AWS requests the server has signed for this user, for the browser to send (long-poll)."""
        if not aws.relay:
            raise HTTPException(404, "브라우저 중계는 --aws-egress browser로 실행할 때만 씁니다.")
        return {"requests": aws.relay.next(user(request))}

    @api.post("/api/relay/{identifier}")
    def relay_answer(identifier: str, params: RelayAnswer, request: Request):
        if not aws.relay:
            raise HTTPException(404, "브라우저 중계는 --aws-egress browser로 실행할 때만 씁니다.")
        if not aws.relay.answer(user(request), identifier, params.model_dump()):
            raise HTTPException(404, "이미 끝났거나 다른 사용자의 요청입니다.")
        return {"ok": True}

    api.executor = executor  # create_app shuts it down with the app
    return api
