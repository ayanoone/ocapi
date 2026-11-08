# OpenCode 2 HTTP API notes

These are the parts that were not obvious from the generated docs, verified against
OpenCode 2.2.6.

## Discovery and auth

- The background service writes `~/.local/state/opencode/service.json` containing
  `{id, version, url, pid, password}`. `~/.config/opencode/service.json` holds the
  password only.
- `GET /api/info` returns the version, pid, urls and capabilities. It is a cheap
  liveness check.
- Auth is HTTP basic with the fixed username `opencode`. There is no other
  username, and no bearer token for local use.
- `GET /openapi.json` returns the full spec (about 445 KB on 2.2.6). It is the
  fastest way to explore the surface:

  ```
  curl -s -u "opencode:$PW" http://127.0.0.1:PORT/openapi.json | jq '.paths | keys'
  ```

## Locations

Almost every read accepts a `location` query object, serialized as a deep object:

```
GET /api/agent?location[directory]=/abs/project
```

Session creation takes `location: {directory}` in the body instead. A session's
`location.directory` is the working directory its agent runs in, so always set it.

## Sessions and prompt turns

`POST /api/session` takes `agent`, `model`, `title`, `location`, `parentID`,
`metadata`, `permissions` and returns the session with an id.

`POST /api/session/{id}/prompt` takes `{text}` plus optional `files`, `skills`,
`agents`, `metadata`, `delivery`, `resume`. It returns immediately after queueing;
the turn then runs on the server.

Waiting for a turn:

| Route | Behaviour |
| --- | --- |
| `POST /api/experimental/session/{id}/wait` | Does not return on turn completion. Do not use it as a completion signal. |
| `GET /api/session/{id}` | Returns `time.{created,updated,busy,idle}`. Settled when `idle >= updated` and `busy <= idle`. |
| `GET /api/session/{id}/message` | Newest-first message list, including `type: "idle"` markers with the turn outcome. |

The settle check used here is the `time` comparison, with a short grace period so a
second tool call after `idle` is not cut off.

## Messages

Each message has `type` (`user`, `assistant`, `idle`, `shell`, `system`,
`compaction`, ...). Assistant messages carry `content`, an array of parts:

- `{"type": "text", "text": "..."}` - the answer
- `{"type": "reasoning", ...}` - thinking
- `{"type": "tool", ...}` - tool call and its state
- `{"type": "file", ...}` and friends

Take the assistant message with the greatest `time.completed`, then join its `text`
parts. The list is newest-first, so `messages[-1]` is the oldest message.

Token and cost accounting live on the session: `tokens.{input,output,reasoning,
cache.read,cache.write}` and `cost`.

## Permissions

`GET /api/permission/request?location[directory]=...` lists pending requests.
Reply with:

```
POST /api/session/{sessionID}/permission/{requestID}/reply
{"decision": "once" | "always" | "reject", "message": "..."}
```

A request carries its own `sessionID`; use that rather than assuming the session you
are polling. Headless runs must answer these or the turn stalls forever.

## Diffs

`GET /api/session/{id}/diff` returns the files the session touched, each with
`file`, `status`, `additions`, `deletions` and a `patch` string. It is a session
diff, not a git diff, and it includes build noise such as `__pycache__`.

## Other useful routes

| Route | Use |
| --- | --- |
| `GET /api/model` | Models the server can currently use |
| `POST /api/session/{id}/agent` | Switch the session's agent |
| `POST /api/session/{id}/model` | Switch the session's model |
| `POST /api/session/{id}/interrupt` | Stop a running turn |
| `GET /api/session/active` | Sessions the process is currently draining |
| `GET /api/session/{id}/context` | Context accounting |
| `GET /api/mcp` | MCP servers OpenCode itself is connected to |
| `GET /api/vcs/status`, `GET /api/vcs/diff` | Repo state |
| `POST /api/location/reload` | Reload config for every loaded location |
| `POST /api/session/{id}/compact` | Force compaction |
| `POST /api/session/{id}/fork` | Fork a session |

## Errors

Failures come back as `{"error": {"code, message, details, retryable,
request_id"}}` with a matching HTTP status. `AgentNotFoundError` lists the available
ids in `details.available`, which is how you discover that a bridge only knows part
of the roster.
