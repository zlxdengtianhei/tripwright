# Delivery levels

Read this from `SKILL.md` when you need to know which level the user has, what to set up, or what a level lacks. Both levels share one core and one repository format. One private Git repository holds a person's trips, one folder per trip under `trips/`, and is the source of truth at every level. Long-term content (stable preferences, companions, standing rules, saved material) lives in `profile/`; when a new trip starts, the agent asks in the conversation which of it still applies. The repository format belongs to the core; this page only says what each level reads and writes.

Evidence for host support is graded: "connected" means a real connection was made; "documented" means the host's documentation says so and nobody has tried it here.

## L1 — trip repository and a coding agent

- For: the user; people who use Claude Code, Codex, Gemini CLI or Kimi Code.
- Set up: the trip repository that `tripwright init` creates, with its agent files (`AGENTS.md`, `CLAUDE.md`, `.mcp.json`, `.claude/agents/`, `.claude/skills/`, `.agents/skills/`, `.codex/agents/`); `.mcp.json` registers the local server under the name `tripwright`. No server to run, no key.
- Multi-agent: the host's subagents. The main agent answers and dispatches whom `flow.dispatch` names: `tripwright-requirements` for new requirements while planning, `tripwright-researcher` for facts, `tripwright-verifier` for a changed plan before commit.
- Lacks, compared with L2: it is not inside the chat app; only hosts that dispatch subagents get multi-agent; a background researcher can stop if a cloud session is reclaimed.

Claude Code on your computer (connected once, one message):
1. Install Tripwright so that the `tripwright` command is on your PATH.
2. Create the trip repository: `tripwright init <folder> --trip 2031-05-wien`, with `--remote <url>` for a GitHub copy; or clone an existing one.
3. Start `claude` in the folder. `.mcp.json` starts `tripwright --repo . serve --stdio`; the main agent follows the skill `tripwright-main`, and the subagents load from `.claude/agents/`.

Codex on your computer (documented):
1. Install Tripwright and create or clone the trip repository, as above.
2. Register the server in `.codex/config.toml` of the trip repository, or in `~/.codex/config.toml`: `[mcp_servers.tripwright]` with `command = "tripwright"` and `args = ["--repo", ".", "serve", "--stdio"]`. Codex does not read `.mcp.json`, and it reads a project's config only after you trust the project.
3. Start Codex there. Skills load from `.agents/skills/`. Custom agents load from `.codex/agents/` (named `tripwright_verifier` and so on). Codex starts a subagent after a direct request or an applicable project or skill instruction; `AGENTS.md` and the skill give that instruction.

The Code page of the Claude phone app (文档说可用，未实测):
1. Keep the trip repository on GitHub and connect GitHub to Claude.
2. In the cloud environment, set the network to Custom and allow the domains of the transport, weather and geocoding sources; a setup script installs uv and Tripwright.
3. Open the Code page, choose the trip repository and start a session. The documentation says subagents in `.claude/agents/` load automatically.
4. Keep the session active until the second response arrives: the virtual machine pauses after a few idle minutes, and a reclaimed machine stops background subagents.

## L2 — remote connector

- For: people who use Claude or ChatGPT in the browser or phone chat and never a coding interface.
- Set up: add the connector address in the chat app and authorize one trip repository. The operator runs the server.
- Multi-agent: none on the server; a few hosts have their own.
- Lacks, compared with L1: no background researcher on the server; the second response comes on the user's next message or the result page.
