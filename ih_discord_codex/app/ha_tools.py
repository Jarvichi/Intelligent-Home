"""Narrow Home Assistant MCP gateway for the isolated Codex worker.

The add-on process holds Supervisor's API token. The Codex subprocess does not.
This server binds loopback only and exposes filtered reads plus action proposals;
physical service calls can only be released by an authenticated Discord command.
"""
import datetime
import json
import math
import re
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SAFE_DOMAINS = {
    "binary_sensor", "climate", "fan", "humidifier", "light",
    "number", "sensor", "switch", "water_heater",
}
ACTION_DOMAINS = {"light", "switch"}
ACTION_SERVICES = {"turn_on", "turn_off", "toggle"}
ENTITY_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
PRIVATE_HINTS = ("person", "tracker", "location", "gps", "phone", "calendar", "contact",
                 "health", "weight", "blood", "heart_rate", "sleep", "steps", "private",
                 "password", "token", "address")
ATTRS = (
    "device_class", "unit_of_measurement", "battery_level",
    "battery", "last_seen", "last_triggered", "current_temperature",
    "temperature", "target_temp", "target_temperature", "current_humidity",
    "humidity", "brightness", "color_temp_kelvin", "percentage", "hvac_mode",
    "hvac_action", "supported_features", "icon",
)
TOOLS = [
    {"name": "ha_search_entities", "description": "Search Home Assistant entities by name or ID. Returns filtered state and safe diagnostic attributes for ordinary device domains.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "domain": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]}},
    {"name": "ha_read_entity", "description": "Read one ordinary Home Assistant entity's current state and filtered diagnostic attributes. Private domains such as people, calendars, locks and alarm panels are unavailable.",
     "inputSchema": {"type": "object", "properties": {"entity_id": {"type": "string"}}, "required": ["entity_id"]}},
    {"name": "ha_entity_history", "description": "Read state-change times for one ordinary Home Assistant entity, up to seven days. Attribute payloads and private domains are excluded.",
     "inputSchema": {"type": "object", "properties": {"entity_id": {"type": "string"}, "hours": {"type": "integer"}}, "required": ["entity_id"]}},
    {"name": "ha_propose_device_action", "description": "Prepare one light or switch action for the authorized owner to review. This does not operate the device. The service call happens only after the owner sends the exact /approve code in the authorized Discord channel.",
     "inputSchema": {"type": "object", "properties": {"entity_id": {"type": "string"}, "service": {"type": "string", "enum": ["turn_on", "turn_off", "toggle"]}}, "required": ["entity_id", "service"]}},
]


class HATools:
    def __init__(self, bridge, token=None, base="http://supervisor/core/api/"):
        self.bridge = bridge
        self.token = token if token is not None else __import__("os").environ.get("SUPERVISOR_TOKEN", "")
        self.base = base.rstrip("/") + "/"
        self.server = None
        self.thread = None

    def _request(self, method, path, payload=None, timeout=8):
        if not self.token:
            raise RuntimeError("Home Assistant API is not configured")
        body = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(self.base + path.lstrip("/"), data=body, method=method,
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read(8_000_001)
        if len(raw) > 8_000_000:
            raise RuntimeError("Home Assistant response exceeded the safe limit")
        return json.loads(raw) if raw else None

    @staticmethod
    def _entity_id(value):
        if not isinstance(value, str) or len(value) > 255 or not ENTITY_RE.fullmatch(value):
            raise ValueError("invalid entity ID")
        domain = value.split(".", 1)[0]
        if domain not in SAFE_DOMAINS:
            raise ValueError("this entity domain is outside Codex's Home Assistant read scope")
        return value

    @staticmethod
    def _private(item):
        entity_id = str(item.get("entity_id", "")).casefold()
        attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        name = str(attrs.get("friendly_name", "")).casefold()
        return any(hint in entity_id or hint in name for hint in PRIVATE_HINTS)

    @staticmethod
    def _safe_state(item):
        attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        safe_attrs = {}
        for key in ATTRS:
            value = attrs.get(key)
            if ((isinstance(value, (str, int, bool)) and not isinstance(value, str))
                or (isinstance(value, float) and math.isfinite(value))
                or (isinstance(value, str) and len(value) <= 120)):
                if value is not None:
                    safe_attrs[key] = value
        state = item.get("state")
        if isinstance(state, float) and not math.isfinite(state):state = None
        if isinstance(state, str) and len(state) > 120:state = state[:120]
        return {"entity_id": item.get("entity_id"), "state": state,
                "last_changed": item.get("last_changed"), "last_updated": item.get("last_updated"),
                "attributes": safe_attrs}

    def t_ha_search_entities(self, args):
        query = args.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > 80:
            raise ValueError("query must be a short string")
        query = query.casefold().strip()
        domain = args.get("domain")
        if domain is not None and domain not in SAFE_DOMAINS:
            raise ValueError("domain is outside the read scope")
        limit = args.get("limit", 20)
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("limit must be an integer")
        limit = max(1, min(limit, 30))
        states = self._request("GET", "states")
        matches = []
        for item in states if isinstance(states, list) else []:
            entity_id = item.get("entity_id", "")
            item_domain = entity_id.split(".", 1)[0]
            if item_domain not in SAFE_DOMAINS or (domain and item_domain != domain) or self._private(item):
                continue
            attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
            name = str(attrs.get("friendly_name", ""))
            if query and query not in entity_id.casefold() and query not in name.casefold():
                continue
            matches.append(self._safe_state(item))
            if len(matches) >= limit:
                break
        return json.dumps({"matches": matches, "count": len(matches)}, ensure_ascii=False)

    def t_ha_read_entity(self, args):
        entity = self._entity_id(args.get("entity_id"))
        item = self._request("GET", "states/" + urllib.parse.quote(entity, safe="."))
        if self._private(item):
            raise ValueError("this entity is outside Codex's Home Assistant read scope")
        return json.dumps(self._safe_state(item), ensure_ascii=False)

    def t_ha_entity_history(self, args):
        entity = self._entity_id(args.get("entity_id"))
        current = self._request("GET", "states/" + urllib.parse.quote(entity, safe="."))
        if self._private(current):
            raise ValueError("this entity is outside Codex's Home Assistant read scope")
        hours = args.get("hours", 24)
        if isinstance(hours, bool) or not isinstance(hours, int):
            raise ValueError("hours must be an integer")
        hours = max(1, min(hours, 168))
        start = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)).isoformat()
        query = urllib.parse.urlencode({"filter_entity_id": entity, "significant_changes_only": "0"})
        path = "history/period/" + urllib.parse.quote(start, safe="") + "?" + query
        rows = self._request("GET", path, timeout=20)
        history = []
        source = rows[0] if isinstance(rows, list) and rows else []
        for row in source[-100:] if isinstance(source, list) else []:
            history.append({"state": row.get("state"), "last_changed": row.get("last_changed"),
                            "last_updated": row.get("last_updated")})
        return json.dumps({"entity_id": entity, "hours": hours, "history": history}, ensure_ascii=False)

    def t_ha_propose_device_action(self, args):
        entity = args.get("entity_id")
        if not isinstance(entity, str) or not ENTITY_RE.fullmatch(entity):
            raise ValueError("invalid entity ID")
        domain = entity.split(".", 1)[0]
        service = args.get("service")
        if domain not in ACTION_DOMAINS or service not in ACTION_SERVICES:
            raise ValueError("only basic light and switch actions can be proposed")
        current = self._request("GET", "states/" + urllib.parse.quote(entity, safe="."))
        if self._private(current):
            raise ValueError("this entity is outside Codex's device-action scope")
        context = self.bridge.active_context()
        if not context:
            raise RuntimeError("action proposals are available only during an authorized Discord task")
        plan = self.bridge.create_action_plan(context, entity, service)
        return json.dumps({"proposal_id": plan["id"], "entity_id": entity, "service": domain + "." + service,
                           "expires_in_seconds": 300, "physical_action_taken": False})

    def call(self, message):
        if isinstance(message, list):
            return [item for item in (self.call(x) for x in message) if item is not None] or None
        if not isinstance(message, dict):
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
        mid, method = message.get("id"), message.get("method")
        params = message.get("params") or {}
        if mid is None:
            return None
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": params.get("protocolVersion", "2025-03-26"),
                "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "ih-codex-home-assistant", "version": "0.1.0"},
                "instructions": "Filtered Home Assistant reads are available. Device actions are proposals only; they require the authorized owner's separate /approve command in Discord."}}
        if method == "notifications/initialized" or method == "ping":
            return {"jsonrpc": "2.0", "id": mid, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
        if method == "tools/call":
            name = params.get("name")
            fn = getattr(self, "t_" + str(name), None)
            if not fn or name not in {x["name"] for x in TOOLS}:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "unknown tool"}}
            try:
                output = fn(params.get("arguments") or {})
                result = {"content": [{"type": "text", "text": output}]}
            except Exception as exc:
                result = {"content": [{"type": "text", "text": "Home Assistant tool failed: " + type(exc).__name__}], "isError": True}
            return {"jsonrpc": "2.0", "id": mid, "result": result}
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found"}}

    def start(self, host="127.0.0.1", port=8811):
        tools = self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path.rstrip("/") != "/mcp" or self.client_address[0] not in ("127.0.0.1", "::1"):
                    self.send_error(404); return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length < 1 or length > 1_000_000:
                        self.send_error(413); return
                    message = json.loads(self.rfile.read(length))
                    response = tools.call(message)
                    if response is None:
                        self.send_response(202); self.send_header("Content-Length", "0"); self.end_headers(); return
                    body = json.dumps(response, ensure_ascii=False).encode()
                    self.send_response(200); self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store")
                    self.end_headers(); self.wfile.write(body)
                except (ValueError, TypeError):
                    self.send_error(400)
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer((host, port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, name="ha-mcp", daemon=True)
        self.thread.start()

    def close(self):
        if self.server:
            self.server.shutdown(); self.server.server_close()

    def approve(self, context, proposal_id):
        plan = self.bridge.claim_action_plan(context, proposal_id)
        if not plan:
            return "No matching unexpired action proposal was found. Nothing was changed."
        try:
            self._request("POST", "services/%s/%s" % (plan["domain"], plan["service"]),
                          {"entity_id": plan["entity_id"]}, timeout=10)
        except Exception as exc:
            self.bridge.finish_action_plan(plan["id"], "uncertain")
            return "Home Assistant did not confirm the request (%s). Check the device state; this action will not be retried automatically." % type(exc).__name__
        self.bridge.finish_action_plan(plan["id"], "done")
        return "Home Assistant accepted %s for %s. Check its current state with a normal question." % (plan["domain"] + "." + plan["service"], plan["entity_id"])

    def deny(self, context, proposal_id):
        if self.bridge.reject_action_plan(context, proposal_id):
            return "Action proposal cancelled. Nothing was changed."
        return "No matching unexpired action proposal was found. Nothing was changed."

    def pending_for(self, message_id):
        return self.bridge.action_plans_for(message_id)

