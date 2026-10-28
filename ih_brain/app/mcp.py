"""JSON-RPC 2.0 / MCP handling for ih_brain (same behaviour as the House Brain component: tool errors are results with isError)."""
import logging

from brainlib import PROTOCOLS, TOOLS

LOG = logging.getLogger("ih_brain")
INSTRUCTIONS = "Call brain_help first, then brain_index. Write small linked notes with the reason for every decision."


def _ok(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def handle_rpc(brain, msg, version="0"):
    if isinstance(msg, list):
        res = [r for r in (handle_rpc(brain, m, version) for m in msg) if r is not None]
        return res or None
    if not isinstance(msg, dict):
        return _err(None, -32600, "invalid request")
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:           # notification
        return None
    try:
        if method == "initialize":
            want = params.get("protocolVersion")
            return _ok(mid, {"protocolVersion": want if want in PROTOCOLS else PROTOCOLS[0],
                             "capabilities": {"tools": {"listChanged": False}},
                             "serverInfo": {"name": "house-brain", "version": version},
                             "instructions": INSTRUCTIONS})
        if method == "ping":
            return _ok(mid, {})
        if method == "tools/list":
            return _ok(mid, {"tools": TOOLS})
        if method == "tools/call":
            name = params.get("name")
            fn = getattr(brain, "t_" + str(name), None)
            if not fn or name not in [t["name"] for t in TOOLS]:
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
