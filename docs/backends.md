# Choose an agent backend

Ebi Agent Chat Relay separates the **frontend** where a person talks from the **backend** that
does the work. Discord and Microsoft Teams use the same session ledger, coordination layer, and
backend factory. Selecting a backend therefore does not require a different bot or a different
Teams app.

## Supported combinations

| Backend | Transport | Authentication | Best fit |
|---|---|---|---|
| Claude Code | local `claude` CLI | the CLI's existing login | Claude-native coding workflows |
| OpenAI Codex | local `codex` CLI | the CLI's existing login | Codex coding and review workflows |
| Local | local `codex` CLI to an OpenAI-compatible `/v1/responses` endpoint | none by default | data that should stay on a controlled network |
| DSH | the DeepSeek Harness runtime, driven over JSON-RPC by its official SDK | per-route API key | one harness for many providers: DeepSeek, Z.ai/GLM, more to come |
| AG-UI | HTTP request plus JSON server-sent events | optional bearer token | custom and hosted agents that implement AG-UI |

All five work from both Discord and Microsoft Teams. The frontend controls message rendering,
buttons, files, and rate limits; the backend controls model execution and streamed events.

## Select a backend

Set the default before startup:

```dotenv
CCDB_BACKEND=codex
```

On Discord, switch an individual conversation without restarting:

```text
/backend claude
/backend codex
/backend local
/backend dsh
/backend agui
```

These are Discord slash commands, and a conversation override is persisted in SQLite so it survives
a process restart. The normal Teams queue integration in v4 does not dispatch the text-command
router yet; Teams conversations use the configured/global backend. Set `CCDB_BACKEND` before
startup or change the global setting from the Discord administration surface when both frontends
run together.

Use `/model` and `/effort` to inspect or change backend-specific choices. Each backend remembers
its own model and reasoning setting.

## Claude Code and Codex

Install and authenticate the official CLI on the private session host before starting ccdb. The
relay reuses that CLI login; it does not copy a subscription token into Discord or Teams.

```bash
claude --version
codex --version
ccdb start
```

The default backend remains `claude`, so upgrading an existing deployment does not change which
agent receives the next turn.

## Local

The local backend drives an OpenAI-compatible `/v1/responses` endpoint through a ccdb-owned Codex
configuration. It disables the measured startup update check and analytics rather than assuming
that “logged out” means “offline.”

```dotenv
CCDB_LOCAL_BASE_URL=http://127.0.0.1:11434/v1
```

The model is not an environment setting: choose it at runtime with `/ollama use` (or `/model`), and
`/ollama list` marks the selection with `▶`.

Read [Local-model backend](local-backend.md) before using this for sensitive data. The guard is a
configuration control, not an operating-system egress firewall, and should be re-measured after
Codex CLI upgrades.

## DSH

The `dsh` backend runs models on **DeepSeek Harness (DSH)** — DeepSeek's own harness, whose agent
loop is composed from plugins and whose model seam is pluggable. It is the backend that grows: one
harness serves several providers under one tool set, one session model, and one configuration file.

```bash
uv sync --extra deepseek
```

```dotenv
DEEPSEEK_API_KEY=sk-...
ZAI_API_KEY=...        # only needed for the zai route (GLM)
```

Then set `CCDB_BACKEND=dsh`, or enter `/backend dsh` in Discord.

### Routes are chosen by the model name

| Model you select | Provider route |
|---|---|
| `deepseek-v4-flash`, `deepseek-v4-pro` | `deepseek-official` — the adapter DSH ships |
| `glm-5.2`, `glm-5-turbo` | `zai` — served by DSH's `llm-pi-ai` adapter |
| `zai/glm-5.2` | any route written explicitly as `route/model` |

`/model` lists what every configured route actually serves, read from each vendor's own
`GET /models`; the built-in list is the offline fallback.

### Adding another provider

Routes are configuration, not code. The backend applies
`~/.config/ccdb/dsh/providers.patch.yml` (override the path with `CCDB_DSH_PATCH`) over DSH's
bundled composition at boot. It is created on first use:

```yaml
- id: llm-pi-ai
  config:
    providers:
      zai:
        apiKeyEnv: ZAI_API_KEY
```

`zai` is a provider DSH's `llm-pi-ai` adapter already ships a catalog for, so naming the route
supplies its endpoint, wire protocol, and model list; only the credential reference is ours. A
provider the catalog does not ship is declared outright with `api`, `baseURL`, and a `models` list.
Naming an id the bundled composition already mounts is an *override*; inserting a second copy of
the same id is refused at boot, so a mistake is loud rather than silent.

Two consequences of the runtime's design are worth knowing before you rely on them:

- **Threads continue within a bot run, not across restarts.** A DSH session lives as long as the
  runtime that owns it, and DSH refuses to adopt a persisted log written by an earlier process
  (`already has a persisted log on disk that does not match this lineage`). ccdb therefore keeps
  one runtime alive per (route, model, working directory) and starts a *fresh* DSH session whenever
  it is asked to continue one this process has never run. After a restart a thread begins with a
  clean context instead of failing — a different trade from Claude Code and Codex, which resume.
- **Each runtime is a process of its own, and they do not exit.** Measured at 382 MB RSS per
  runtime, and a runtime is keyed by (route, model, working directory) — so a thread with its
  own git worktree gets its own process, and so does every model you switch to. Ten concurrent
  dsh threads in ten worktrees is therefore ~3.8 GB resident. The trade is deliberate: a
  runtime is what keeps a thread's context alive. Evicting idle runtimes would cap the
  footprint at the cost of exactly what a bot restart already costs — a fresh session.
- **Stop ends the Discord turn, not the agent's work.** The bundled SDK runtime implements only
  `initialize`, `session/prompt`, and `shutdown` — there is no cancel method on the wire. The
  Stop button therefore closes the turn immediately (the thread reports "Stopped by the user")
  while the shared runtime finishes the agent's work in the background; a later turn on that
  session waits for it to quiesce. The same gap means image attachments are not supported: the
  runner warns that the image was not sent rather than silently dropping it.

The runtime is a subprocess, and it inherits the bot's environment. ccdb scrubs the credentials it
strips from every other backend (`DISCORD_BOT_TOKEN` and friends) for the moment the runtime
starts; provider keys such as `DEEPSEEK_API_KEY` and `ZAI_API_KEY` deliberately survive, because a
route's `apiKeyEnv` names them.


## AG-UI

Install the optional HTTP dependency and configure the exact run endpoint:

```bash
uv sync --extra agui
```

```dotenv
CCDB_AGUI_URL=https://agent.example.com/run
CCDB_AGUI_TOKEN=replace-with-a-dedicated-token
```

Then set `CCDB_BACKEND=agui`, or enter `/backend agui` in Discord. Whichever frontend supplies the
turn also supplies a stable AG-UI `threadId`, so the remote endpoint can preserve its own
conversation state.

See [AG-UI backend](agui-backend.md) for the event mapping, security boundary, and intentionally
unsupported protocol features.

## Mixing frontends and backends

Backend resolution belongs to the shared session layer, not the platform implementation. In v4,
Discord can set per-conversation overrides; Teams consumes the configured/global choice. A
deployment can therefore run, for example:

- a Discord thread on Claude Code;
- another Discord thread on a local model;
- Teams conversations on the configured Codex default; or
- Teams conversations on an internal AG-UI agent when AG-UI is the configured/global backend.

The same AI Lounge, claims, collision detection, worktree rules, and session persistence cover all
of them. Frontend identity is stored with each session, so a Teams result cannot accidentally be
posted into a Discord thread with a numerically similar key.

## Security boundary

Every selected backend receives that conversation's prompts and attachments. Treat a remote AG-UI
endpoint or local model host as part of the data-processing path. A customer-tenant Teams app does
not by itself keep data inside that tenant: messages still travel through Bot Framework, the public
receiver, the queue, the private session host, and the selected backend. Deploy the complete stack
inside the required boundary when the contract requires that literal property.
