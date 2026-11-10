"""JSON-RPC 2.0 / MCP handling for ih_brain (same behaviour as the House Brain component: tool errors are results with isError)."""
import logging

from brainlib import PROTOCOLS, TOOLS

LOG = logging.getLogger("ih_brain")
INSTRUCTIONS = "Call brain_help first, then brain_index. Write small linked notes with the reason for every decision."
CODEX_TOOLS = [
    {"name": "brain_search", "description": "Search House Brain notes explicitly marked share:true. Results exclude inbox and private notes.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "area": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]}},
    {"name": "brain_read", "description": "Read one markdown note explicitly marked share:true. Use a path returned by brain_search.",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
]
CODEX_INSTRUCTIONS = "Read-only House Brain search. Only notes marked share:true are available; inbox, private notes, files and attachments are excluded."


def _ok(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def handle_rpc(brain, msg, version="0", tools=None, server_name="house-brain", instructions=None):
    available_tools = TOOLS if tools is None else tools
    if isinstance(msg, list):
        res = [r for r in (handle_rpc(brain, m, version, tools, server_name, instructions) for m in msg) if r is not None]
        return res or None
    if not isinstance(msg, dict):
        return _err(None, -32600, "invalid request")
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:           # notification
        return None
    try:
        if method == "initialize":
            want = params.get("protocolVersion")
            instructions = instructions or (CODEX_INSTRUCTIONS if tools is not None else INSTRUCTIONS)
            return _ok(mid, {"protocolVersion": want if want in PROTOCOLS else PROTOCOLS[0],
                             "capabilities": {"tools": {"listChanged": False}},
                             "serverInfo": {"name": server_name, "version": version},
                             "instructions": instructions})
        if method == "ping":
            return _ok(mid, {})
        if method == "tools/list":
            return _ok(mid, {"tools": available_tools})
        if method == "tools/call":
            name = params.get("name")
            fn = getattr(brain, "t_" + str(name), None)
            if not fn or name not in [t["name"] for t in available_tools]:
                return _err(mid, -32602, "unknown tool %s" % name)
            try:
                text = fn(params.get("arguments") or {})
                return _ok(mid, {"content": [{"type": "text", "text": text}]})
            except Exception as e:     # tool errors are results, not protocol errors
                return _ok(mid, {"content": [{"type": "text", "text": "Error: %s" % e}], "isError": True})
        return _err(mid, -32601, "method not found: %s" % method)
    except Exception as e:
        LOG.exception("rpc failed")
        return _err(mid, -32603, str(e))
