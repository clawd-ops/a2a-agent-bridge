import logging
import os

import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from a2a.utils import get_message_text, new_agent_text_message
from starlette.responses import JSONResponse
from starlette.routing import Route


logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("a2a-agent-bridge")

AGENT_NAME = os.environ.get("A2A_AGENT_NAME", "Home Ops Agent Bridge")
BASE_URL = os.environ.get(
    "A2A_BASE_URL",
    "http://a2a-agent-bridge.ai.svc.cluster.local:8080",
).rstrip("/")


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

        logger.info(
            "received_a2a_message task_id=%s context_id=%s text=%r",
            context.task_id,
            context.context_id,
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


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
