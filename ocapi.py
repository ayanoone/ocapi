"""Minimal client for OpenCode 2's HTTP API.

Talks to the shared background server directly, so no `opencode` CLI process is
involved. Used by ocmcp.py (MCP server) and as a library.

Server discovery: OPENCODE_URL env var, else the url in
~/.local/state/opencode/service.json (written by the background service).
Auth: HTTP basic, user "opencode", password from that file or OPENCODE_PASSWORD.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

STATE = os.path.expanduser("~/.local/state/opencode/service.json")


class ApiError(RuntimeError):
    def __init__(self, status, body, path):
        self.status = status
        self.body = body
        self.path = path
        super().__init__(f"{status} on {path}: {body[:400]}")


def _state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {}


class Server:
    def __init__(self, url=None, password=None):
        st = _state()
        self.url = (url or os.environ.get("OPENCODE_URL") or st.get("url") or "").rstrip("/")
        if not self.url:
            raise RuntimeError(
                "no OpenCode server found: start one with `opencode serve` or "
                "`opencode service start`, or set OPENCODE_URL"
            )
        self.password = password or os.environ.get("OPENCODE_PASSWORD") or st.get("password") or ""

    # -- plumbing ---------------------------------------------------------
    def _auth_header(self):
        raw = f"opencode:{self.password}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def request(self, method, path, body=None, query=None, timeout=60, raw=False):
        url = self.url + path
        if query:
            pairs = []
            for k, v in query.items():
                if v is None:
                    continue
                if isinstance(v, dict):
                    for k2, v2 in v.items():
                        if v2 is not None:
                            pairs.append((f"{k}[{k2}]", v2))
                else:
                    pairs.append((k, v))
            if pairs:
                url += "?" + urllib.parse.urlencode(pairs)
        data = None
        headers = {"Authorization": self._auth_header(), "Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = r.read()
                if raw:
                    return payload
                if not payload:
                    return None
                return json.loads(payload)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            raise ApiError(e.code, detail, path) from None
        except (socket.timeout, TimeoutError):
            raise TimeoutError(f"{method} {path} timed out after {timeout}s") from None

    # -- reads ------------------------------------------------------------
    def info(self):
        return self.request("GET", "/api/info", timeout=10)

    def agents(self, directory=None):
        q = {"location": {"directory": directory}} if directory else None
        return (self.request("GET", "/api/agent", query=q) or {}).get("data", [])

    def models(self):
        data = (self.request("GET", "/api/model") or {}).get("data", [])
        return data

    def sessions(self, directory=None, limit=20, search=None):
        q = {"limit": limit, "search": search}
        if directory:
            q["directory"] = directory
        return (self.request("GET", "/api/session", query=q) or {}).get("data", [])

    def session(self, session_id):
        return (self.request("GET", f"/api/session/{session_id}") or {}).get("data")

    def messages(self, session_id, limit=200):
        data = self.request("GET", f"/api/session/{session_id}/message", query={"limit": limit})
        if isinstance(data, dict):
            return data.get("data", []) or data.get("messages", [])
        return data or []

    def diff(self, session_id):
        return (self.request("GET", f"/api/session/{session_id}/diff") or {}).get("data")

    def permissions(self, directory=None):
        q = {"location": {"directory": directory}} if directory else None
        return (self.request("GET", "/api/permission/request", query=q) or {}).get("data", [])

    # -- writes -----------------------------------------------------------
    def create_session(self, directory, agent=None, model=None, title=None):
        body = {"location": {"directory": os.path.abspath(os.path.expanduser(directory))}}
        if agent:
            body["agent"] = agent
        if model:
            body["model"] = parse_model(model)
        if title:
            body["title"] = title
        return (self.request("POST", "/api/session", body=body) or {}).get("data")

    def prompt(self, session_id, text, files=None):
        body = {"text": text}
        if files:
            body["files"] = files
        return self.request("POST", f"/api/session/{session_id}/prompt", body=body)

    def interrupt(self, session_id):
        return self.request("POST", f"/api/session/{session_id}/interrupt", body={})

    def switch_agent(self, session_id, agent):
        return self.request("POST", f"/api/session/{session_id}/agent", body={"agent": agent})

    def switch_model(self, session_id, model):
        return self.request("POST", f"/api/session/{session_id}/model", body={"model": parse_model(model)})

    def reply_permission(self, session_id, request_id, decision="once"):
        return self.request(
            "POST",
            f"/api/session/{session_id}/permission/{request_id}/reply",
            body={"decision": decision},
        )

    def _settled(self, info):
        """A turn is settled once the session recorded idle after its last change."""
        t = (info or {}).get("time") or {}
        idle = t.get("idle")
        busy = t.get("busy")
        updated = t.get("updated") or 0
        if idle is None:
            return False
        if busy is not None and busy > idle:
            return False
        return idle >= updated

    # -- high level -------------------------------------------------------
    def run(self, prompt_text, directory, agent=None, model=None, title=None,
            timeout_s=900, auto_permissions=True, on_event=None):
        """Create a session, send one prompt, and block until the turn settles."""
        info = self.create_session(directory, agent=agent, model=model, title=title)
        sid = info["id"]
        return self.follow(sid, prompt_text, timeout_s=timeout_s,
                           auto_permissions=auto_permissions, on_event=on_event)

    def follow(self, session_id, prompt_text=None, timeout_s=900,
               auto_permissions=True, on_event=None, poll_s=0.8):
        if prompt_text is not None:
            self.prompt(session_id, prompt_text)
        deadline = time.time() + timeout_s
        answered = set()
        settled_at = None
        while time.time() < deadline:
            if auto_permissions:
                try:
                    for req in self.permissions():
                        rid = req.get("id")
                        if rid and rid not in answered:
                            target = req.get("sessionID") or session_id
                            self.reply_permission(target, rid, "once")
                            answered.add(rid)
                            if on_event:
                                on_event(f"permission {rid} auto-allowed")
                except ApiError:
                    pass
            info = self.session(session_id) or {}
            if self._settled(info) and self._last_assistant(session_id):
                if settled_at is None:
                    settled_at = time.time()
                if time.time() - settled_at > 1.5:
                    break
            else:
                settled_at = None
            if on_event and settled_at is None:
                on_event(f"state outcome={info.get('outcome')} idle={(info.get('time') or {}).get('idle')}")
            time.sleep(poll_s)
        rep = self.report(session_id)
        rep["timed_out"] = time.time() >= deadline
        return rep

    def _last_assistant(self, session_id):
        best = None
        best_t = -1
        for m in self.messages(session_id):
            if m.get("type") != "assistant":
                continue
            t = (m.get("time") or {}).get("completed")
            if t and t > best_t:
                best, best_t = m, t
        return best

    def text_of(self, message):
        out = []
        for part in message.get("content") or []:
            if part.get("type") == "text" and part.get("text"):
                out.append(part["text"])
        return "\n".join(out).strip()

    def report(self, session_id):
        info = self.session(session_id) or {}
        last = self._last_assistant(session_id)
        model = info.get("model")
        if not model:
            try:
                for a in self.agents((info.get("location") or {}).get("directory")):
                    if a.get("id") == info.get("agent"):
                        model = a.get("model")
                        break
            except ApiError:
                pass
        changed = []
        try:
            d = self.diff(session_id) or {}
            if isinstance(d, dict):
                changed = d.get("files") or d.get("data") or []
            elif isinstance(d, list):
                changed = d
        except ApiError:
            pass
        noise = ("__pycache__/", ".pyc", "node_modules/", ".tsbuildinfo")
        changed = [c for c in changed if not any(n in (c.get("file") or "") for n in noise)]
        return {
            "session_id": session_id,
            "agent": info.get("agent"),
            "model": model,
            "outcome": info.get("outcome"),
            "cost": info.get("cost"),
            "tokens": info.get("tokens"),
            "text": self.text_of(last) if last else "",
            "changed_files": changed,
        }


def parse_model(model):
    """'provider/model#variant' or 'provider/model' -> Model.Ref."""
    if isinstance(model, dict):
        return model
    variant = None
    if "#" in model:
        model, variant = model.split("#", 1)
    provider, _, name = model.partition("/")
    ref = {"providerID": provider, "id": name or provider}
    if not name:
        ref = {"providerID": provider, "id": provider}
    if variant:
        ref["variant"] = variant
    return ref


def connect():
    return Server()
