import logging
import os
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from a2a.utils import get_message_text, new_agent_text_message
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("a2a-agent-bridge")

AGENT_NAME = os.environ.get("A2A_AGENT_NAME", "Home Ops Agent Bridge")
BASE_URL = os.environ.get(
    "A2A_BASE_URL",
    "http://a2a-agent-bridge.ai.svc.cluster.local:8080",
).rstrip("/")
BRIDGE_TOKEN = os.environ.get("A2A_BRIDGE_TOKEN", "")
MAX_INBOX_MESSAGES = int(os.environ.get("A2A_MAX_INBOX_MESSAGES", "500"))
MAX_TOTAL_INBOX_MESSAGES = int(os.environ.get("A2A_MAX_TOTAL_INBOX_MESSAGES", "1000"))
MAX_MESSAGE_CHARS = int(os.environ.get("A2A_MAX_MESSAGE_CHARS", "20000"))

INBOX: dict[str, list[dict[str, Any]]] = {}


def _normalize_agent(value: str | None) -> str:
    normalized = (value or "default").strip().lower()
    return re.sub(r"[^a-z0-9_.-]+", "-", normalized).strip("-") or "default"


def _message_metadata(message: Any) -> dict[str, Any]:
    if message is None:
        return {}
    if hasattr(message, "model_dump"):
        data = message.model_dump(mode="json", exclude_none=True)
    elif isinstance(message, dict):
        data = message
    else:
        return {}
    metadata = data.get("metadata") or {}
    return metadata if isinstance(metadata, dict) else {}


def _metadata_value(metadata: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _infer_handoff_agent(text: str, label: str) -> str | None:
    match = re.search(rf"(?im)^\s*{re.escape(label)}\s*:\s*(.+?)\s*$", text)
    return match.group(1).strip() if match else None


def _bounded_text(text: str) -> str:
    if len(text) <= MAX_MESSAGE_CHARS:
        return text
    omitted = len(text) - MAX_MESSAGE_CHARS
    return f"{text[:MAX_MESSAGE_CHARS]}\n\n[truncated {omitted} chars]"


def _prune_total_inbox() -> None:
    messages = [
        (message["receivedAt"], agent, message["id"])
        for agent, inbox in INBOX.items()
        for message in inbox
    ]
    overflow = len(messages) - MAX_TOTAL_INBOX_MESSAGES
    if overflow <= 0:
        return

    remove_ids = {
        message_id
        for _received_at, _agent, message_id in sorted(messages)[:overflow]
    }
    empty_agents: list[str] = []
    for agent, inbox in INBOX.items():
        INBOX[agent] = [message for message in inbox if message["id"] not in remove_ids]
        if not INBOX[agent]:
            empty_agents.append(agent)

    for agent in empty_agents:
        del INBOX[agent]


def _capture_message(
    *,
    task_id: str,
    context_id: str,
    text: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    text = _bounded_text(text)
    to_agent = _metadata_value(metadata, "toAgent", "to_agent", "to")
    from_agent = _metadata_value(metadata, "fromAgent", "from_agent", "from")
    to_agent = to_agent or _infer_handoff_agent(text, "Handoff to") or "default"
    from_agent = from_agent or _infer_handoff_agent(text, "Handoff from")

    item = {
        "id": str(uuid.uuid4()),
        "receivedAt": datetime.now(UTC).isoformat(),
        "taskId": task_id,
        "contextId": context_id,
        "toAgent": _normalize_agent(to_agent),
        "fromAgent": from_agent,
        "text": text,
        "metadata": metadata,
        "acked": False,
    }

    messages = INBOX.setdefault(item["toAgent"], [])
    messages.append(item)
    if len(messages) > MAX_INBOX_MESSAGES:
        del messages[: len(messages) - MAX_INBOX_MESSAGES]
    _prune_total_inbox()

    return item


def _authorize_bridge_request(request: Request) -> JSONResponse | None:
    if not BRIDGE_TOKEN:
        return None

    authorization = request.headers.get("authorization", "")
    bearer_prefix = "Bearer "
    bearer_token = (
        authorization[len(bearer_prefix) :].strip()
        if authorization.startswith(bearer_prefix)
        else ""
    )
    header_token = request.headers.get("x-a2a-bridge-token", "").strip()
    if BRIDGE_TOKEN in {bearer_token, header_token}:
        return None

    return JSONResponse({"error": "unauthorized"}, status_code=401)


class HomeOpsAgentExecutor(AgentExecutor):
    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        if context.current_task is None:
            await updater.submit()

        await updater.start_work()

        try:
            request_text = get_message_text(context.message).strip()
        except Exception:
            request_text = ""

        metadata = _message_metadata(context.message)
        inbox_item = _capture_message(
            task_id=context.task_id,
            context_id=context.context_id,
            text=request_text,
            metadata=metadata,
        )
        logger.info(
            "received_a2a_message task_id=%s context_id=%s to_agent=%s inbox_id=%s text=%r",
            context.task_id,
            context.context_id,
            inbox_item["toAgent"],
            inbox_item["id"],
            request_text,
        )

        response = (
            "A2A message received by Home Ops Agent Bridge.\n\n"
            "This is the real in-cluster A2A endpoint for agent-to-agent "
            "traffic. Use the inspector to connect to this agent card and "
            "watch protocol traffic. Codex/OpenClaw still need an MCP-facing "
            "tool or polling loop to notice and act on queued A2A work.\n\n"
            f"Received text: {request_text or '(empty message)'}"
        )
        await updater.complete(new_agent_text_message(response))

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.failed(new_agent_text_message("Cancellation is not implemented."))


async def healthz(_request):
    return JSONResponse({"status": "ok", "agent": AGENT_NAME})


async def list_inbox(request: Request):
    unauthorized = _authorize_bridge_request(request)
    if unauthorized:
        return unauthorized

    agent = _normalize_agent(request.path_params["agent"])
    include_acked = request.query_params.get("includeAcked", "false").lower() == "true"
    messages = INBOX.get(agent, [])
    if not include_acked:
        messages = [message for message in messages if not message["acked"]]

    return JSONResponse({"agent": agent, "messages": messages})


async def ack_inbox(request: Request):
    unauthorized = _authorize_bridge_request(request)
    if unauthorized:
        return unauthorized

    agent = _normalize_agent(request.path_params["agent"])
    body = await request.json()
    ids = body.get("ids", [])
    if not isinstance(ids, list):
        return JSONResponse({"error": "ids must be a list"}, status_code=400)

    wanted = {str(message_id) for message_id in ids}
    count = 0
    for message in INBOX.get(agent, []):
        if message["id"] in wanted and not message["acked"]:
            message["acked"] = True
            count += 1

    return JSONResponse({"agent": agent, "acked": count})


agent_card = AgentCard(
    name=AGENT_NAME,
    description=(
        "A minimal A2A endpoint for Home Ops agent conversations. It gives "
        "the inspector and agent clients a real A2A target while MCP-facing "
        "notification tooling is wired in."
    ),
    url=f"{BASE_URL}/",
    version="0.1.0",
    capabilities=AgentCapabilities(
        streaming=False,
        pushNotifications=False,
        stateTransitionHistory=True,
    ),
    defaultInputModes=["text/plain"],
    defaultOutputModes=["text/plain"],
    skills=[
        AgentSkill(
            id="agent-message-relay",
            name="Agent message relay",
            description=(
                "Accepts text messages from A2A peers and returns an "
                "acknowledged task response visible through A2A clients."
            ),
            tags=["a2a", "agents", "home-ops"],
            examples=[
                "Tell Codex that Clawd is working on the HelmRelease.",
                "Record a handoff note for another agent.",
            ],
        )
    ],
)

request_handler = DefaultRequestHandler(
    agent_executor=HomeOpsAgentExecutor(),
    task_store=InMemoryTaskStore(),
)

app = A2AStarletteApplication(
    agent_card=agent_card,
    http_handler=request_handler,
).build()
app.routes.append(Route("/healthz", healthz, methods=["GET"]))
app.routes.append(Route("/bridge/inbox/{agent}", list_inbox, methods=["GET"]))
app.routes.append(Route("/bridge/inbox/{agent}/ack", ack_inbox, methods=["POST"]))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
