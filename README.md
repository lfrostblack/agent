# Warpspeed Slack Support Agent

An internal-first Slack support agent powered by the **Claude Agent SDK (Python)**.
It answers teammates' questions from our knowledge base (Nominal) and files a
ticket when it can't.

## Architecture

Two independent cloud services that talk over HTTP, deployed to **Railway** with
**managed Postgres**:

| Service | What it is | Deploy shape |
|---|---|---|
| **`agent-service/`** | Claude Agent SDK + a Slack Socket Mode listener. Registers the Nominal MCP server (knowledge) and an in-process `create_ticket` tool (escalation). Routing lives in the system prompt. | **Always-on worker** — no inbound port (Socket Mode is an outbound websocket). |
| **`ticket-backend/`** | Thin FastAPI service over Postgres: create/read/list/update tickets + `/health`. Kept separate so the future ERP integration stays off the agent side. | **Web service** with a `/health` check. |

## Build status

This repo is being built service by service. Current state:

- [x] **Step 1 — Scaffold** both services (requirements, `.env.example`, Dockerfiles, `docker-compose.yml`).
- [x] **Step 2 — Production-grade minimal agent**: warm per-thread `ClaudeSDKClient` pool, Slack Socket Mode listener (`app_mention` + `message.im`), 👀 ack, post-then-edit, `AGENT_MODEL` configurable (default `claude-sonnet-5`). No tools yet.
- [ ] **Step 3 — Escalation**: in-process `create_ticket` tool + `PreToolUse` audit hook + `allowed_tools` lock (stub sink first).
- [ ] **Step 4 — ticket-backend**: FastAPI + Postgres; switch the tool from stub to real backend.
- [ ] **Step 5 — Nominal knowledge path**: register Nominal HTTP MCP server + bearer-token auth module + routing system prompt.
- [ ] **Step 6 — Deploy to Railway**: managed Postgres, ticket-backend web service, agent-service always-on worker, secrets, smoke test.

## Performance design (why it's fast)

The prototype felt slow because it used one-shot `query()` (fresh CLI subprocess
per message). This build instead keeps a **warm, persistent `ClaudeSDKClient`
per Slack thread** (`channel:thread_ts`):

- Stays warm across messages in a thread and carries that thread's conversation context.
- Per-thread lock serialises messages; LRU cap bounds live subprocesses.
- Background sweeper evicts idle sessions and force-recycles aged ones (so we
  stay ahead of the Nominal token expiry once it's wired in).
- **Post-then-edit**: a placeholder is posted immediately and edited with the
  final answer, instead of per-token edits (keeps us under Slack's `chat.update`
  rate limits). True token streaming is an easy later upgrade.
- Model is right-sized and configurable via `AGENT_MODEL`.

## Governance / safety (from day one)

- `allowed_tools` is locked to an explicit list — no Bash/Read/Write/filesystem.
  In the current minimal phase that list is empty (pure Q&A). `setting_sources`
  is intentionally unset so no filesystem settings / project tools load.
- A `PreToolUse` audit hook (added in step 3) logs every `create_ticket` write as
  structured JSON. The same hook becomes the enforcement point (return a `deny`
  decision) when this goes client-facing.

## Local development

```bash
# agent-service
cp agent-service/.env.example agent-service/.env    # fill in tokens
cp ticket-backend/.env.example ticket-backend/.env

# Whole stack (postgres + ticket-backend + agent-service):
docker compose up --build

# Or just the agent, on the host:
cd agent-service
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.main
```

Required env for the agent: `ANTHROPIC_API_KEY`, `SLACK_BOT_TOKEN` (`xoxb-`),
`SLACK_APP_TOKEN` (`xapp-`). See `agent-service/.env.example` for the full,
annotated list (model, session-pool tuning, and later-phase placeholders).

## Slack app (already configured — "Warpspeed")

- Socket Mode **on**; app-level token (`xapp-…`, `connections:write`).
- Bot scopes: `app_mentions:read`, `chat:write`, `im:read`, `im:history`,
  `reactions:write`.
- Event subscriptions: `app_mention`, `message.im`.
- For DMs: App Home → Messages Tab → "Allow users to send messages" must be on.
  Channel @mentions work without it.

## SDK notes (verified against current docs)

- Package `claude-agent-sdk` (import `claude_agent_sdk`), pinned to **0.2.110**, Python 3.10+.
- The SDK drives a bundled Node-based Claude Code CLI — the Docker image ships Node 20 as insurance.
- Options class is `ClaudeAgentOptions` (renamed from `ClaudeCodeOptions`).
- `allowed_tools` **wildcards have a known bug** — tools are always listed explicitly.
- `PreToolUse` hook matcher for an MCP tool must be the **full** name
  (`mcp__tickets__create_ticket`), not the bare tool name.
