# MCP tools

`ocmcp.py` speaks MCP over stdio. Protocol version `2025-06-18`, tools only (no
resources or prompts), no external dependencies.

Every tool returns a single `text` content block containing JSON. Errors come back
as `isError: true` with the exception type and message, so the calling model can
read what went wrong instead of getting a transport failure.

## oc_run

Create a session, send one prompt, block until the turn settles.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `task` | string | yes | Self-contained brief. The agent cannot see your conversation. |
| `directory` | string | yes | Absolute project root, used as the session's working directory. |
| `agent` | string | no | Any id from `oc_agents`. Default `orchestrator` if it exists, otherwise the server default. |
| `model` | string | no | `provider/model#variant`. Omit to use the agent's configured model. |
| `title` | string | no | Session title. |
| `timeout_s` | integer | no | Wall-clock budget, default 900. |

Returns `session_id`, `agent`, `model`, `outcome`, `cost`, `tokens` (input, output,
reasoning, cache), `text` (the final assistant answer), `changed_files` (with
patches, build noise filtered) and `timed_out`.

Blocking on a long task is fine for an MCP client that tolerates a slow tool call.
When it does not, use `oc_start` plus `oc_poll`.

## oc_start

Same inputs minus `timeout_s`. Creates the session and queues the prompt, then
returns `{session_id, agent, started}` immediately.

## oc_poll

`session_id` required. Returns the same report as `oc_run` plus `settled`, so you
can distinguish "still working" from "finished with an empty answer".
`settled` is the `time.idle >= time.updated` check.

## oc_continue

`session_id` and `text` required, plus optional `timeout_s`. Sends another message to
an existing session and waits. Context is preserved, so this is the cheap way to run
a multi-step conversation without re-sending the repo state.

## oc_agents

`directory` required. Lists agents registered for that directory with id, mode,
resolved model and a truncated description. This is how you discover orchestration
plugin agents, and how you confirm a model profile is actually in effect.

## oc_sessions

`directory` required, `limit` optional. Recent sessions with id, agent, title,
outcome, timestamps and location.

## oc_diff

`session_id` required. The session's file changes: `file`, `status`, `additions`,
`deletions`, `patch`.

## oc_interrupt

`session_id` required. Stops the running turn.

## oc_models

No inputs. Provider/model ids the server can currently use.

## Behaviour worth knowing

- Permissions are auto-answered with `once` while a run is in flight. If the server
  asks for something that requires a human, the run will fail rather than hang.
- Sessions are real server sessions, visible in the OpenCode TUI and desktop app.
  `oc_sessions` finds them again after a restart.
- Nothing is cached between calls. Each tool call resolves the server from the state
  file, so a restarted service is picked up without touching the MCP config.
- Two editing calls against the same directory at once will interleave in the same
  working tree. There is no lock; use separate git worktrees if you need parallel
  edits.
