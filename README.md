# A2A Agent Bridge

Small A2A bridge experiments for OpenClaw/Home Ops agent-to-agent messaging.

This repository contains two pieces:

- `a2a-agent-bridge/`: an HTTP A2A server exposing an Agent Card, JSON-RPC endpoint, and `/healthz`.
- `a2a-mcp-bridge/`: a stdio MCP server that lets MCP-capable agents call an A2A endpoint.

The A2A server is intentionally minimal and in-memory. It is useful as a real protocol target for the A2A Inspector and for wiring agent clients, but it is not yet a durable multi-agent queue.

## A2A Server

Build locally:

```sh
docker build -t a2a-agent-bridge .
```

Run locally:

```sh
docker run --rm -p 8080:8080 \
  -e A2A_AGENT_NAME="Home Ops Agent Bridge" \
  -e A2A_BASE_URL="http://localhost:8080" \
  a2a-agent-bridge
```

Endpoints:

- `GET /healthz`
- `GET /.well-known/agent-card.json`
- `POST /` for A2A JSON-RPC

## MCP Bridge

The MCP bridge lives in `a2a-mcp-bridge/` and can be run with:

```sh
cd a2a-mcp-bridge
uv run a2a-mcp-bridge
```

Set `A2A_AGENT_URL` to point at the target A2A server.

## Home Ops

Home Ops should consume this repository via the published GHCR image rather than storing the A2A server source in a ConfigMap.
