# ocapi

A dependency-free Python client for **OpenCode 2's HTTP API**, plus an MCP server
that exposes it as tools.

It exists because the alternatives are worse. The `opencode run` CLI is awkward to
drive from another agent: no structured results, no reliable completion signal, and
a run that waits forever on background jobs. The existing MCP wrappers are stuck on
the V1 server contract, or ship their own agent roster instead of the one you
configured. This talks to the server OpenCode already runs and returns what the CLI
can do, structured.

- `ocapi.py` - stdlib-only client. Sessions, prompts, agents, models, diffs,
  interrupts, permission auto-answer, settlement polling.
- `ocmcp.py` - MCP server (stdio, no dependencies) wrapping the client.

## Requirements

- Python 3.9+, nothing else. No `pip install`.
- OpenCode 2.x with its background service running (`opencode` starts it, or
  `opencode service start`). Tested against 2.2.6.

## How it finds the server

1. `OPENCODE_URL`, else the `url` in `~/.local/state/opencode/service.json`
   (written by the background service).
2. Auth is HTTP basic: user `opencode`, password from `OPENCODE_PASSWORD` or the
   `password` in that same file.

## Using the library

```python
import ocapi

s = ocapi.connect()

for a in s.agents("/abs/project"):
    print(a["id"], a.get("model"))

result = s.run(
    "Add retry logic to the HTTP client and run the tests.",
    directory="/abs/project",
    agent="orchestrator",          # any registered agent
    timeout_s=900,
)
print(result["outcome"], result["tokens"]["input"], result["tokens"]["output"])
print(result["text"])
for f in result["changed_files"]:
    print(f["status"], f["file"])
```

`run()` creates a session, sends one prompt, polls until the session records idle,
auto-answers permission requests, and returns `session_id`, `agent`, `model`,
`outcome`, `cost`, `tokens`, `text` and `changed_files` (with patches).

For long jobs, split it: `create_session()` + `prompt()` + `follow()`, or call
`report(session_id)` to poll without blocking.

## Using the MCP server

Register it with any MCP client. Hermes example:

```
hermes mcp add oc --command python3 --args /abs/path/to/ocmcp.py
```

Claude Desktop or any `mcpServers` config:

```json
{
  "mcpServers": {
    "oc": {
      "command": "python3",
      "args": ["/abs/path/to/ocmcp.py"]
    }
  }
}
```

| Tool | Purpose |
| --- | --- |
| `oc_run` | New session, one task, block until settled. Returns text, outcome, tokens, cost, changed files. |
| `oc_start` | Start a task without waiting; returns `session_id`. |
| `oc_poll` | Check a started session: settled flag plus latest assistant text. |
| `oc_continue` | Send another message to an existing session, keeping context. |
| `oc_agents` | Agents registered for a directory, with the model each resolves to. |
| `oc_sessions` | Recent sessions for a directory. |
| `oc_diff` | File changes a session made. |
| `oc_interrupt` | Stop a running session. |
| `oc_models` | Available provider/model ids. |

Every tool takes an absolute `directory`. `agent` accepts any id the server
reports, so orchestration plugins' agents work too; `model` overrides per call as
`provider/model#variant`.

## Why not the existing wrappers

- `opencode-mcp` (npm): the popular one, but it pins `@opencode-ai/sdk` 1.x and its
  README says OpenCode 2 support is planned for 4.0. It cannot talk to a V2 server.
- `claude-opencode-mcp-v2` (npm, alpha): does target the V2 `/api/*` surface, but it
  ships its own four agents (`researcher`, `reviewer`, `coder`, `tester`) and rejects
  everything else with `AGENT_NOT_FOUND`. Agent-orchestration plugins become
  invisible through it, and its own model choices ignore your config.

## Things that cost time to learn

- **Settlement** is `time.idle >= time.updated` with `time.busy <= time.idle`. The
  `POST /api/experimental/session/{id}/wait` route exists but does not return when
  the turn ends, so poll the session instead.
- **Message order is newest-first.** The final answer is the assistant message with
  the greatest `time.completed`, not the last element of the list. Concatenate the
  parts where `type == "text"`.
- **Permission decisions** are `once`, `always`, `reject` on
  `POST /api/session/{id}/permission/{requestID}/reply`.
- **Model refs** are `{providerID, id, variant}`; the CLI spelling is
  `provider/model#variant`.
- **Diffs** include `__pycache__` and build noise. Filter it.
- The full route list is served at `GET /openapi.json` on the service port (139
  routes on 2.2.6).

More detail in [docs/http-api.md](docs/http-api.md) and
[docs/mcp-tools.md](docs/mcp-tools.md).

## Licence

MIT. See [LICENSE](LICENSE).
