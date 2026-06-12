from __future__ import annotations

import logging
import os
import sys
import uuid
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP


DEFAULT_A2A_AGENT_URL = "http://a2a-agent-bridge.ai.svc.cluster.local:8080"
WELL_KNOWN_AGENT_CARD = "/.well-known/agent-card.json"

mcp = FastMCP("a2a-mcp-bridge")

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def _target_url(agent_url: str | None = None) -> str:
    return (agent_url or os.environ.get("A2A_AGENT_URL") or DEFAULT_A2A_AGENT_URL).rstrip("/")


def _rpc_url(agent_url: str | None = None) -> str:
    return f"{_target_url(agent_url)}/"


def _card_url(agent_url: str | None = None) -> str:
    return f"{_target_url(agent_url)}{WELL_KNOWN_AGENT_CARD}"


async def _post_jsonrpc(agent_url: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(_rpc_url(agent_url), json=payload)
        response.raise_for_status()
        data = response.json()

    if "error" in data and data["error"]:
        raise RuntimeError(f"A2A JSON-RPC error: {data['error']}")

    return data


@mcp.tool()
async def get_agent_card(agent_url: str | None = None) -> dict[str, Any]:
    """Fetch an A2A agent card.

    Args:
        agent_url: Optional A2A agent base URL. Defaults to A2A_AGENT_URL.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(_card_url(agent_url))
        response.raise_for_status()
        return response.json()


@mcp.tool()
async def send_a2a_message(
    text: str,
    agent_url: str | None = None,
    context_id: str | None = None,
    task_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send a text message to an A2A agent with message/send.

    Args:
        text: Message body to send.
        agent_url: Optional A2A agent base URL. Defaults to A2A_AGENT_URL.
        context_id: Optional A2A context ID for continuing a conversation.
        task_id: Optional A2A task ID for continuing task work.
        metadata: Optional JSON metadata to attach to the message send params.
    """
    message: dict[str, Any] = {
        "kind": "message",
        "messageId": str(uuid.uuid4()),
        "role": "user",
        "parts": [{"kind": "text", "text": text}],
    }

    if context_id:
        message["contextId"] = context_id
    if task_id:
        message["taskId"] = task_id

    params: dict[str, Any] = {"message": message}
    if metadata:
        params["metadata"] = metadata

    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/send",
        "params": params,
    }
    return await _post_jsonrpc(agent_url, payload)


@mcp.tool()
async def send_handoff(
    to_agent: str,
    summary: str,
    from_agent: str = "codex",
    current_state: str | None = None,
    next_steps: list[str] | None = None,
    agent_url: str | None = None,
) -> dict[str, Any]:
    """Send a structured handoff note to another agent through A2A.

    Args:
        to_agent: Agent/persona that should receive the handoff.
        summary: Main handoff summary.
        from_agent: Agent/persona sending the handoff.
        current_state: Optional current system or task state.
        next_steps: Optional ordered next steps.
        agent_url: Optional A2A agent base URL. Defaults to A2A_AGENT_URL.
    """
    lines = [
        f"Handoff from: {from_agent}",
        f"Handoff to: {to_agent}",
        "",
        "Summary:",
        summary,
    ]
    if current_state:
        lines.extend(["", "Current state:", current_state])
    if next_steps:
        lines.extend(["", "Next steps:"])
        lines.extend(f"{index}. {step}" for index, step in enumerate(next_steps, start=1))

    return await send_a2a_message(
        text="\n".join(lines),
        agent_url=agent_url,
        metadata={
            "kind": "handoff",
            "fromAgent": from_agent,
            "toAgent": to_agent,
        },
    )


@mcp.tool()
async def get_a2a_task(task_id: str, agent_url: str | None = None) -> dict[str, Any]:
    """Fetch an A2A task with tasks/get when the target server supports it.

    Args:
        task_id: A2A task ID to fetch.
        agent_url: Optional A2A agent base URL. Defaults to A2A_AGENT_URL.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tasks/get",
        "params": {"id": task_id},
    }
    return await _post_jsonrpc(agent_url, payload)


def main() -> None:
    print("Starting a2a-mcp-bridge on stdio", file=sys.stderr)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
