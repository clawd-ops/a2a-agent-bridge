# a2a-mcp-bridge

MCP server that lets an agent client talk to an A2A endpoint.

This is intended for Codex, OpenClaw, Clawd, and other agents that can load MCP
servers but do not natively poll or call A2A endpoints.

## What It Does

- Reads an A2A agent card.
- Sends an A2A `message/send` JSON-RPC request.
- Sends structured handoff notes between agents.
- Fetches an A2A task with `tasks/get` when the target supports it.

## Configure

Set the target A2A agent URL:

```sh
export A2A_AGENT_URL="https://a2a-agent.example.com"
```

If unset, the server defaults to `http://a2a-agent-bridge.ai.svc.cluster.local:8080`.

## Run

```sh
uv run a2a-mcp-bridge
```

For stdio MCP clients:

```json
{
  "mcpServers": {
    "a2a": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/a2a-mcp-bridge",
        "run",
        "a2a-mcp-bridge"
      ],
      "env": {
        "A2A_AGENT_URL": "https://a2a-agent.example.com"
      }
    }
  }
}
```

## Tools

- `get_agent_card`: Fetch the configured or provided A2A agent card.
- `send_a2a_message`: Send a text message to an A2A agent.
- `send_handoff`: Send a structured handoff note from one agent to another.
- `get_a2a_task`: Fetch task state by task ID when supported by the A2A server.

## Notes

This server intentionally logs to stderr only. Stdio MCP servers must keep
stdout reserved for MCP JSON-RPC traffic.
