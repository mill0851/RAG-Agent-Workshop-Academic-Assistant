"""A minimal MCP server, so Claude Code and Codex can call our tools.

MCP (Model Context Protocol) is the standard way to plug tools into an agent program.
Under the name it is JSON-RPC: the agent POSTs one JSON message per request, and we
answer. These are the only messages an agent needs:

    -> {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", ...}}
    <- {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, ...}}
    -> {"jsonrpc": "2.0", "method": "notifications/initialized"}            (no id: nothing to answer)
    -> {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    <- {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "search_course", "description": ..., "inputSchema": ...}]}}
    -> {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "find_text", "arguments": {"text": "HW 3"}}}
    <- {"jsonrpc": "2.0", "id": 3, "result": {"content": [{"type": "text", "text": "- HW3 2026.pdf (p. 1) ..."}]}}

The server mounts this at POST /mcp/<session> (serve/agent_routes.py), one session per question.
"""

from __future__ import annotations

from .. import __version__
from .toolbox import Toolbox

PROTOCOL_VERSION = "2025-06-18"


def handle(message: dict, box: Toolbox) -> dict | None:
    """The reply to one JSON-RPC message, or None for a notification (which gets none)."""
    if "id" not in message:
        return None
    method, params = message.get("method"), message.get("params") or {}
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "academic-assistant", "version": __version__}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        # every tool only reads course material; saying so lets agents run them without asking
        result = {"tools": [{"name": s["name"], "description": s["description"], "inputSchema": s["input_schema"],
                             "annotations": {"readOnlyHint": True, "openWorldHint": False}}
                            for s in box.specs()]}
    elif method == "tools/call":
        text = box.call(params.get("name", ""), params.get("arguments") or {})
        result = {"content": [{"type": "text", "text": text}], "isError": False}
    else:
        return {"jsonrpc": "2.0", "id": message["id"],
                "error": {"code": -32601, "message": f"Unknown method: {method}"}}
    return {"jsonrpc": "2.0", "id": message["id"], "result": result}
