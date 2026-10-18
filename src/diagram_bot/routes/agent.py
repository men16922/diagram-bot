"""Chat design agent: model list, one turn (whole or streamed as NDJSON progress), reset."""
import json
import os
import queue
import threading

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..egress import RelayError
from ..errors import error_code
from ..sso import SsoError
from . import role, user


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    model: str | None = Field(default=None, max_length=64)
    snapshot: str | None = Field(default=None, max_length=64)  # open in the app: the agent may start a design from it


def default_chat(workspace, model):
    from functools import partial

    from ..agent import Chat, bedrock_model
    if workspace.model_session:
        # Browser egress: Bedrock is called with the user's session through the browser, without response streaming
        # (the relay carries whole responses; the chat still streams tool progress).
        return Chat(workspace, model, model_factory=partial(bedrock_model, session=workspace.model_session(), streaming=False))
    return Chat(workspace, model)


def chat_error(exc):
    if isinstance(exc, (RelayError, SsoError)):
        return str(exc)
    if isinstance(exc, (BotoCoreError, ClientError)):
        return f"Bedrock 호출 실패({error_code(exc)}): 모델 액세스·리전·자격 증명을 확인하세요."
    # The model provider's own errors are surfaced to the user, the chat stays usable.
    return f"에이전트 응답 실패: {type(exc).__name__}: {str(exc)[:300]}"


def router(context):
    api = APIRouter()
    aws, audit, chats = context.aws, context.audit, context.chats
    chat_factory = context.chat_factory or default_chat

    @api.get("/api/agent")
    def agent_info():
        from ..agent import DEFAULT_MODEL, FALLBACK_MODEL, available_models
        try:
            import strands  # noqa: F401
            ready = True
        except ImportError:
            ready = False
        return {"ready": ready, "models": available_models(), "default": DEFAULT_MODEL, "fallback": FALLBACK_MODEL}

    def chat_for(params, request):
        from ..agent import DEFAULT_MODEL, MODELS, Workspace
        who = user(request)
        model = params.model or DEFAULT_MODEL
        if model not in MODELS:
            raise HTTPException(422, f"지원하지 않는 모델입니다: {model}")
        can_collect = not context.auth or role(request) == "admin"

        def publish(snapshot):
            identifier = context.snapshots.save(snapshot)
            audit.write(who, "agent snapshot", target=identifier, design=bool(snapshot["metadata"].get("design")))
            return identifier

        with context.mutex:
            current = chats.get(who)
            if not current or current["model"] != model:
                def session_for(region):
                    # After an Identity Center sign-in the agent reads with that role; otherwise the SDK chain.
                    return aws.session(who, region)
                try:
                    workspace = Workspace(publish, session_factory=session_for, allow_collection=can_collect, load=context.snapshots.get,
                                          default_account=lambda: aws.chosen_account(who) or os.environ.get("AWS_ACCOUNT_ID"))
                    if aws.relay:
                        # Refreshing credentials: the chat outlives one role session, and building it calls no AWS.
                        workspace.model_session = lambda: aws.model_session(who, "us-east-1")
                    current = chats[who] = {"chat": chat_factory(workspace, model), "model": model}
                except ImportError as exc:
                    raise HTTPException(503, "채팅 에이전트를 쓰려면 pip install -e '.[agent]'로 strands-agents를 설치하세요.") from exc
                except SsoError as exc:
                    raise HTTPException(409, str(exc)) from exc
            current["chat"].workspace.current_snapshot = params.snapshot
        return current["chat"]

    @api.post("/api/agent/chat")
    def agent_chat(params: ChatRequest, request: Request):
        chat = chat_for(params, request)
        try:
            return chat.ask(params.message)
        except SsoError as exc:  # the Identity Center sign-in expired: the user signs in again, the chat is kept
            raise HTTPException(409, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - see chat_error
            raise HTTPException(502, chat_error(exc)) from exc

    @api.post("/api/agent/chat/stream")
    def agent_chat_stream(params: ChatRequest, request: Request):
        """The same turn as /api/agent/chat, as NDJSON progress events (text, tool, model, snapshot) while the agent works.
        The last line is {"type": "done", ...the /api/agent/chat result} or {"type": "error", "detail": ...}."""
        chat = chat_for(params, request)
        events = queue.Queue()

        def run():
            try:
                events.put({"type": "done", **chat.ask(params.message, on_event=events.put)})
            except Exception as exc:  # noqa: BLE001 - see chat_error
                events.put({"type": "error", "detail": chat_error(exc)})

        threading.Thread(target=run, daemon=True).start()

        def lines():
            while True:
                event = events.get()
                yield json.dumps(event, ensure_ascii=False, default=str) + "\n"
                if event["type"] in ("done", "error"):
                    return

        return StreamingResponse(lines(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store"})

    @api.post("/api/agent/reset")
    def agent_reset(request: Request):
        with context.mutex:
            chats.pop(user(request), None)
        return {"reset": True}

    return api
