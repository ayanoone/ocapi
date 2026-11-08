#!/usr/bin/env python3
"""MCP server for OpenCode 2, speaking its HTTP API directly.

No `opencode` CLI process is used. Full capability: any registered agent
(including omo-slim's orchestrator/oracle/fixer), any model, sessions,
continuations, diffs, interrupts, permission auto-answer.

Run over stdio:  python3 ocmcp.py
"""

from __future__ import annotations

import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ocapi  # noqa: E402

PROTOCOL = "2025-06-18"


def _json(x):
    return json.dumps(x, indent=1, default=str)


TOOLS = [
    {
        "name": "oc_run",
        "description": (
            "Run one task in a new OpenCode session and block until it settles. "
            "Any registered agent works, including the omo-slim orchestrator "
            "(which plans and dispatches its own subagents). Returns assistant "
            "text, outcome, tokens, cost and changed files."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Self-contained instruction."},
                "directory": {"type": "string", "description": "Absolute project root."},
                "agent": {
                    "type": "string",
                    "description": "Agent id, e.g. orchestrator, plan, build, explorer, oracle, fixer. Default orchestrator.",
                },
                "model": {
                    "type": "string",
                    "description": "Override model as provider/model#variant. Omit to use the agent's configured model.",
                },
                "title": {"type": "string"},
                "timeout_s": {"type": "integer", "description": "Wall clock budget, default 900."},
            },
            "required": ["task", "directory"],
        },
    },
    {
        "name": "oc_start",
        "description": "Start a task without waiting. Returns session_id for oc_poll.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "directory": {"type": "string"},
                "agent": {"type": "string"},
                "model": {"type": "string"},
                "title": {"type": "string"},
            },
            "required": ["task", "directory"],
        },
    },
    {
        "name": "oc_poll",
        "description": "Check a session started with oc_start. Reports settled state and the latest assistant text.",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
    },
    {
        "name": "oc_continue",
        "description": "Send another message to an existing session, keeping its context, and wait.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "text": {"type": "string"},
                "timeout_s": {"type": "integer"},
            },
            "required": ["session_id", "text"],
        },
    },
    {
        "name": "oc_agents",
        "description": "List agents registered for a directory, with the model each one resolves to.",
        "inputSchema": {
            "type": "object",
            "properties": {"directory": {"type": "string"}},
            "required": ["directory"],
        },
    },
    {
        "name": "oc_sessions",
        "description": "List recent sessions for a directory.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "directory": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["directory"],
        },
    },
    {
        "name": "oc_diff",
        "description": "File changes a session made.",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
    },
    {
        "name": "oc_interrupt",
        "description": "Stop a running session.",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
    },
    {
        "name": "oc_models",
        "description": "List available provider/model ids.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def call_tool(name, args):
    s = ocapi.connect()
    if name == "oc_run":
        return s.run(
            args["task"],
            directory=args["directory"],
            agent=args.get("agent") or "orchestrator",
            model=args.get("model"),
            title=args.get("title"),
            timeout_s=int(args.get("timeout_s") or 900),
        )
    if name == "oc_start":
        info = s.create_session(
            args["directory"],
            agent=args.get("agent") or "orchestrator",
            model=args.get("model"),
            title=args.get("title"),
        )
        s.prompt(info["id"], args["task"])
        return {"session_id": info["id"], "agent": info.get("agent"), "started": True}
    if name == "oc_poll":
        rep = s.report(args["session_id"])
        info = s.session(args["session_id"]) or {}
        rep["settled"] = s._settled(info)
        return rep
    if name == "oc_continue":
        return s.follow(
            args["session_id"],
            args["text"],
            timeout_s=int(args.get("timeout_s") or 900),
        )
    if name == "oc_agents":
        out = []
        for a in s.agents(args["directory"]):
            m = a.get("model") or {}
            out.append(
                {
                    "id": a.get("id"),
                    "mode": a.get("mode"),
                    "model": (
                        f"{m.get('providerID')}/{m.get('id')}"
                        + (f"#{m['variant']}" if m.get("variant") else "")
                        if m
                        else "inherit"
                    ),
                    "description": (a.get("description") or "")[:120],
                }
            )
        return out
    if name == "oc_sessions":
        return s.sessions(args["directory"], limit=int(args.get("limit") or 10))
    if name == "oc_diff":
        return s.diff(args["session_id"])
    if name == "oc_interrupt":
        s.interrupt(args["session_id"])
        return {"interrupted": True}
    if name == "oc_models":
        out = []
        for m in s.models():
            if isinstance(m, dict):
                out.append(m.get("id") or m.get("modelID") or _json(m)[:80])
            else:
                out.append(str(m))
        return out
    raise ValueError(f"unknown tool: {name}")


def respond(msg_id, result=None, error=None):
    out = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        out["error"] = {"code": -32000, "message": str(error)}
    else:
        out["result"] = result
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method")
        mid = msg.get("id")
        try:
            if method == "initialize":
                respond(
                    mid,
                    {
                        "protocolVersion": PROTOCOL,
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "opencode-bridge", "version": "1.0.0"},
                    },
                )
            elif method in ("notifications/initialized", "notifications/cancelled"):
                continue
            elif method == "ping":
                respond(mid, {})
            elif method == "tools/list":
                respond(mid, {"tools": TOOLS})
            elif method == "tools/call":
                params = msg.get("params") or {}
                name = params.get("name")
                args = params.get("arguments") or {}
                try:
                    result = call_tool(name, args)
                    respond(
                        mid,
                        {
                            "content": [{"type": "text", "text": _json(result)}],
                            "isError": False,
                        },
                    )
                except Exception as e:  # surface the real error to the model
                    respond(
                        mid,
                        {
                            "content": [
                                {
                                    "type": "text",
                                    "text": f"{type(e).__name__}: {e}\n{traceback.format_exc()[-800:]}",
                                }
                            ],
                            "isError": True,
                        },
                    )
            elif mid is not None:
                respond(mid, error=f"unsupported method: {method}")
        except Exception as e:
            if mid is not None:
                respond(mid, error=f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
