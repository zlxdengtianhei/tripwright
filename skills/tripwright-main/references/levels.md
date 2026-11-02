# Delivery levels

Read this from `SKILL.md` when you need to know which level the user has, what to set up, or what a level lacks. All five levels share one core and one repository format. One private Git repository holds a person's trips, one folder per trip under `trips/`, and is the source of truth at every level. Long-term content (stable preferences, companions, standing rules, saved material) lives in `profile/`; when a new trip starts, the agent asks in the conversation which of it still applies. The repository format belongs to the core; this page only says what each level reads and writes.

Evidence for host support is graded: "connected" means a real connection was made; "documented" means the host's documentation says so and nobody has tried it here.

## L0 — instructions only

- For: people trying Tripwright first; apps that cannot add a custom MCP connector.
- Set up: a chat app and a private trip repository made from the template. Paste the L0 text (`agents/generated/l0/tripwright-main.md` in the package) into the app's project instructions.
- Multi-agent: none. The single-agent steps without tools.
- Lacks, compared with L3: automatic recording and receipts, deterministic checks (time chains, hard nodes, per-person tasks, budget), version protection of user decisions, the background deep check, live transport data, automatic site updates.
- Manual carrying, and how often:
  - Once, and again after each upgrade: paste the L0 text into the project instructions. It is about 22 KB because it carries the writing rules in Chinese and English; some apps limit the length of project instructions (not checked).
  - When a new trip starts: paste the files in `profile/` the agent asks for, and answer which long-term records still apply.
  - At the start of each day: paste `trips/<trip>/views/packs/<date>.md`, so the agent reads the applicable conditions.
  - When the agent asks: paste the file it names by its path.
  - After every reply that ends with a 写回仓 block: commit the block in the GitHub app or website, then reply “已提交”. On a travel day this is about once per message that reports something new. A skipped commit loses that record.

## L1 — trip repository and a coding agent

- For: the user; people who use Claude Code, Codex, Gemini CLI or Kimi Code.
- Set up: the trip repository that `tripwright init` creates, with its agent files (`AGENTS.md`, `CLAUDE.md`, `.mcp.json`, `.claude/agents/`, `.claude/skills/`, `.agents/skills/`, `.codex/agents/`); `.mcp.json` registers the local server under the name `tripwright`. No server to run, no key.
- Multi-agent: the host's subagents. The main agent answers; `tripwright-verifier` checks in the background; `tripwright-researcher` and `tripwright-requirements` when needed.
- Lacks, compared with L3: it is not inside the chat app; only hosts that dispatch subagents get multi-agent; a deep check can stop if a cloud session is reclaimed; no result-page short link.

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
- Lacks, compared with L3: the semantic deep check (only deterministic checks and live re-queries remain); the second response comes on the user's next message or the result page.

## L3 — remote connector with server-side agents

- For: people who want the deep check in phone chat too; the user's own deployment.
- Set up: as L2. The operator also provides a model key or gateway and per-user budget limits.
- Multi-agent: server-side agents in a durable job: requirements, one or two researchers, an independent verifier, built from the same role sources as the L1 subagents.
- Lacks, compared with L4: only cards and directory listing.

## L4 — additions

- For: ordinary users after a public release.
- Adds: interactive cards, push notifications, directory listings. Multi-agent and data are as in L3.
