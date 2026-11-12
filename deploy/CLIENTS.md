# Connect a client to Tripwright

This page is step 4 of [SETUP.md](SETUP.md), one section per client. Use it again whenever the user adds another app.

## What every client needs

- **Address:** `https://<your-domain>/mcp/<key>`, or `http://localhost:8000/mcp/<key>` for Docker without a tunnel. Localhost and tunnel addresses work together; Host headers are not restricted.
- **Where the commands below get it:** [SETUP.md step 4](SETUP.md#4-add-the-address-to-each-ai-app) prints the address with `tripwright address`. Copy it from your own terminal and paste it into the commands below. The `pbpaste` substitutions are optional conveniences, not a requirement to hide it from the terminal; use the printed address directly where that is simpler.
- **Transport:** Streamable HTTP. Clients often call it "HTTP".
- **Authentication:** none ("No sign-in", "No authentication"). The key is in the address.
- **Name:** `tripwright` in coding clients (Claude Code, Codex, Cursor): the trip repository's settings and subagents expect this name. Chat apps such as Claude and ChatGPT only display the name, so `Tripwright` is fine there.

| Client | Connects from | Who adds it | Limits |
| --- | --- | --- | --- |
| [Claude Code](#claude-code) | the user's computer | agent | no MCP limit in the docs |
| [Codex](#codex-cli-ide-extension-chatgpt-desktop-app) (CLI, IDE extension, ChatGPT desktop app) | the user's computer | agent | no MCP limit in the docs; Codex Cloud not covered |
| [Claude](#claude-web-desktop-mobile) (web, desktop, mobile) | Anthropic's cloud: public HTTPS only | user clicks **Add**; agent fills the form | Free: one custom connector; Team and Enterprise: only an Owner adds |
| [ChatGPT](#chatgpt) | OpenAI's cloud: public HTTPS only | user, on the web only | full use on Business, Enterprise, Edu; Pro: read only; Plus, Go, Free: not documented |
| [Cursor](#cursor) | the user's computer | agent | no MCP limit in the docs |
| [Other clients](#other-clients) | depends on the client | depends | see the section |

**The first question, for every client.** Ask: "Use tripwright: which trips are stored?" It is done when the client shows a call to a Tripwright tool (`state` or `turn`) and answers from it. A new repository has no trip yet, and the answer says so.

## Claude Code

**Needs:** any address the computer can reach. `http://localhost:8000/…` works when Docker runs on the same computer without a tunnel; with a tunnel, use the tunnel address.

**Agent does:**

```bash
cd ~/tripwright-trips && claude mcp add --transport http tripwright "$(pbpaste)"
```

This stores the server for the trip repository folder only (local scope, in `~/.claude.json`). Run it in the trip repository: that repository's `.mcp.json` defines its own `tripwright`, which starts a local copy of Tripwright instead of using the service, and local scope takes precedence over `.mcp.json`. The trip repository also gives Claude Code its instructions, skills and hooks, so open Claude Code there; run `git -C ~/tripwright-trips pull` first so its files match the service.

Optional: to use the service from every other folder too, also run the command below. Without step 2 there is no trip repository; then run only the command below.

```bash
claude mcp add --transport http --scope user tripwright "$(pbpaste)"
```

**User does:** nothing. In the trip repository, its settings already allow the Tripwright tools. In other folders, Claude Code asks before the first call.

**Check:** `claude mcp list | grep -q '^tripwright:.*Connected' && echo connected || echo "not connected"`. In a session, `/mcp` lists `tripwright`.

**Notes:**

- `claude mcp list`, `claude mcp get` and `/mcp` show the full address. Do not run them on a shared screen or paste their output. `~/.claude.json` holds the address; keep it like the key.
- If the user also added Tripwright in Claude (below) and Claude Code is signed in with that claude.ai account, the connector has the same address. Claude Code then uses the `tripwright` entry from the command above and hides the connector.
- In Claude Code on the web, use the connector added in [Claude](#claude-web-desktop-mobile). Connectors reach the service without changes to the cloud environment's network allowlist.

## Codex (CLI, IDE extension, ChatGPT desktop app)

These three share one configuration file: `~/.codex/config.toml`, or `$CODEX_HOME/config.toml` when `CODEX_HOME` is set.

**Needs:** any address the computer can reach (with a Docker tunnel, the tunnel address).

**Agent does:**

```bash
codex mcp add tripwright --url "$(pbpaste)"
```

It may print `Run codex mcp login tripwright to login`. Ignore that: this service has no login. Then add `default_tools_approval_mode = "approve"` to the `[mcp_servers.tripwright]` table. This script does it without printing the file (do not open the file in an editor tool, which would show the address):

```bash
python3 - <<'EOF'
import os, pathlib
p = pathlib.Path(os.environ.get("CODEX_HOME") or pathlib.Path.home() / ".codex") / "config.toml"
lines = p.read_text().splitlines(keepends=True)
start = lines.index("[mcp_servers.tripwright]\n")
end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
if not any(line.split("=")[0].strip() == "default_tools_approval_mode" for line in lines[start + 1:end]):
    lines.insert(start + 1, 'default_tools_approval_mode = "approve"\n')
    p.write_text("".join(lines))
print("approval mode set")
EOF
```

The table then holds `url = "https://<your-domain>/mcp/<key>"` and `default_tools_approval_mode = "approve"`. Add the line by default and tell the user that Codex now calls the Tripwright tools without asking. Without it, Codex asks for approval before tools such as `query`, which is marked as writing and as reaching outside data, and non-interactive runs (`codex exec`) refuse the call: "MCP tool call requires approval, but approval policy is never" (codex-cli 0.162). Leave it out only if the user wants to approve each call in interactive sessions.

**User does:** nothing. In the ChatGPT desktop app, the user can instead open **Settings → MCP servers → Add server**, choose **Streamable HTTP**, paste the address, save and select **Restart**.

**Check:** `codex mcp list | grep -qw tripwright && echo added`. In a Codex session, `/mcp` lists `tripwright` and its tools.

**Notes:**

- **Open Codex in the trip repository** (`~/tripwright-trips`, after `git pull`). Its `AGENTS.md` tells Codex to call `turn` for every message. In an empty folder without it, Codex (model gpt-6-luna, 2026-11-03) received the server's instructions but answered without calling a tool. In any other folder, start the first message with "Use tripwright:".
- `codex mcp list` and `codex mcp get tripwright` print the full address.
- Codex Cloud (tasks on the web): we found no official statement that it can use an MCP server you add. This section is for Codex on the user's computer.

## Claude (web, desktop, mobile)

**Needs:** a public HTTPS address. Claude connects from Anthropic's cloud for every Claude app, including Claude Desktop and Cowork, so `localhost` does not work. Plans: on Free, Pro, Max and Ultra, the user adds the connector to their own account; Free allows one custom connector. On Team and Enterprise, only an Owner can add it (**Organization settings → Connectors**), and every member then shares the same key; do not use Tripwright that way.

**Agent does:** open Claude's prefilled **Add custom connector** dialog in the user's browser:

```bash
link=$(pbpaste | python3 -c 'import sys, urllib.parse as u; print("https://claude.ai/customize/connectors?modal=add-custom-connector&connectorName=Tripwright&connectorUrl=" + u.quote(sys.stdin.read().strip(), safe=""))')
open "$link"; unset link      # Linux: xdg-open
```

The link only fills in the form; nothing is added until the user confirms. The browser history keeps the link, and the link contains the key. Without a browser on this computer, give the user the printed address and the steps below.

**User does:**

1. Check the dialog. Without the link: **Customize → Connectors → Add custom connector**, then paste the address.
2. If the dialog asks for **Authentication**, choose **No sign-in**.
3. Click **Add**.
4. In a chat, click **+ → Connectors** and turn Tripwright on. Allow the first tool call, or choose **Always allow**.

**Check:** **Customize → Connectors** lists Tripwright; then ask the first question.

**Notes:**

- One connector works in Claude on the web, the desktop app and the mobile apps. The documentation does not say that the mobile app can add one, so add it on the web.
- For a new key, edit the connector's URL from its three-dot menu, or remove the connector and add it again.

## ChatGPT

**Needs:** a public HTTPS address. Plans, from OpenAI's help article:

- **Business, Enterprise, Edu:** full MCP support, with tools that read and write, on ChatGPT web. On Business, only admins and owners can add it; on Enterprise and Edu, admins can grant it to members.
- **Pro:** only tools marked read-only. Tripwright marks only `state` and `explain` read-only, so on Pro ChatGPT can read the trip but cannot look up transport or change the plan.
- **Plus, Go, Free:** the article does not say. Check whether **Add custom MCP server** appears.

**Agent does:** give the user the printed address and the steps below. We found no install link or interface that adds it for them.

**User does,** on chatgpt.com in a browser (the mobile app cannot add one):

1. Open **Plugins**, click **+**, then **Add custom MCP server**.
2. Name: Tripwright. Under **Connection**, paste the address.
3. Authentication: **No authentication**.
4. Tick **I understand and want to continue**, then click **Create as a plugin**.
5. Install the plugin. In a new chat, type `@` and choose Tripwright. The choice holds for one message: choose it again when a later message needs Tripwright to look something up or record something.

**Check:** after step 4, ChatGPT lists the tools it found: `state`, `explain`, `turn`, `propose`, `commit`, `result`, `query`. Then ask the first question.

**Notes:**

- ChatGPT treats every tool without the read-only mark as a write and asks before it runs; the user can keep the answer for the rest of one conversation. `query` counts as a write because each lookup is saved as evidence in the trip repository.
- OpenAI's plugin page says that the mobile app can use the plugins of the account in Chat and Work. We have not tried this with Tripwright.

## Cursor

**Needs:** any address the computer can reach.

**Agent does:** add `tripwright` to `~/.cursor/mcp.json` (all projects) and keep the other entries. Use `.cursor/mcp.json` in a project folder for that project only.

```bash
pbpaste | python3 -c 'import json, sys, pathlib
p = pathlib.Path.home() / ".cursor" / "mcp.json"
d = json.loads(p.read_text()) if p.exists() else {}
d.setdefault("mcpServers", {})["tripwright"] = {"url": sys.stdin.read().strip()}
p.parent.mkdir(exist_ok=True); p.write_text(json.dumps(d, indent=2) + "\n")'
```

The result has the shape `{"mcpServers": {"tripwright": {"url": "https://<your-domain>/mcp/<key>"}}}`.

**User does:** approve the tool calls. Cursor asks before each MCP tool call by default; its Run Mode setting changes that.

**Check:** **Customize** in Cursor's sidebar shows `tripwright` turned on, with its tools. Errors are in the Output panel under **MCP Logs**.

## Other clients

Any client that can add a remote MCP server works with the settings in [What every client needs](#what-every-client-needs). Clients that run in their provider's cloud need the public HTTPS address. Known ways, not tried with Tripwright:

- **VS Code with GitHub Copilot:** Command Palette → **MCP: Add Server** → **HTTP** → paste the address → server ID `tripwright` → **Global** → **Copilot Global**.
- **Gemini CLI:** `gemini mcp add --transport http --scope user tripwright "$(pbpaste)"`. `gemini mcp list` shows the status and also prints the address.
- **Doubao Work (豆包工作):** **插件·技能·伙伴 → 插件 → + 添加 → 新建自定义插件**, transport (传输类型) **HTTP**, server URL (服务器 URL) = the address, Headers empty, **保存**. The page does not state plan limits.
- **Kimi Work, QwenWork desktop (千问办公), ZCode:** their documentation describes adding a remote MCP server: Kimi Work's Plugin Builder asks for the MCP address; QwenWork uses **扩展 → 连接器 → + 添加** with type `streamable-http`; ZCode uses its MCP settings with type HTTP. We have not checked these step by step.
- **A client that runs only local (stdio) servers:** set its command to `npx` with the arguments `-y mcp-remote <address>`, a bridge that Linear's MCP documentation uses. Paste the address into that client's settings from the clipboard; it then sits in the client's configuration.

## Helper roles in each app

Tripwright has four roles: the main agent, which answers; `tripwright-requirements`, which records new requirements and screenshots and links them to earlier decisions; `tripwright-researcher`, which checks facts (and ticket prices and discounts on request); `tripwright-verifier`, which checks a changed plan before it is saved. The result of `turn` carries `flow`: `flow.dispatch` names the role a step needs, with one short reminder of the steps; `state`, `query`, `propose`, `commit` and `explain` repeat that reminder at most once in three results. While planning, the service commits what a message asks for before the plan draft, so a plan held back by the verifier does not take the requirements with it.

What each app can hold is taken from the vendors' pages listed under [Sources](#sources) (read 2026-11-12). "Documented, not tested" means the page says it works and we have not run Tripwright's roles there.

| App | What it can hold (vendor docs) | How the roles get there | How they run |
|---|---|---|---|
| Claude Code (CLI, desktop) | Project sub-agents in `.claude/agents/`, project skills in `.claude/skills/<name>/SKILL.md` | `tripwright init` writes them into the trip repository; `tripwright init --upgrade` refreshes them | As sub-agents; tested |
| Claude Code on the web | A cloud session clones the repository with its `.claude/agents/` and `.claude/skills/`; not in a Claude project's cloud threads | Open a session on the trip repository; connect the service with the connector, as in the notes under [Claude Code](#claude-code) | Documented, not tested |
| Codex (CLI, IDE) | Project agents in `.codex/agents/`, skills in `.agents/skills/`; Codex delegates when asked directly or when `AGENTS.md` or a skill asks for it | Written by `tripwright init`; the generated `AGENTS.md` says to dispatch whom `flow.dispatch` names | Documented, not tested |
| Codex cloud | No page says cloud tasks can use a custom MCP server (openai/codex#4226 is open) | — | Not checked |
| Claude (web, desktop, mobile) | Uploaded skills (a ZIP; code execution must be on); project instructions; custom remote connectors. Cowork may coordinate sub-agents itself; no page describes sub-agents a user configures | Upload `skills/tripwright-main` as a ZIP; in the project instructions write “use the tripwright skill and tools for the trip” | One agent follows the main skill's steps; the roles do not start as separate agents, and their own instructions are not in that skill. Not tested |
| ChatGPT | Project instructions; custom MCP apps on Business and Enterprise/Edu, web only; skills on Business, Enterprise, Healthcare and Edu; workspace agents (Business, Enterprise) take skills and custom MCP, with no documented way for one agent to hand work to another | The same sentence in the project instructions; skills, where the plan has them, as for Claude | One agent follows the project instructions and the `flow` lines the service returns; the roles' own instructions are not there. Not tested. Work-type modes that set up separate roles: not checked |

## Sources

Read on 2026-11-03 unless a date is given. "Not tried" means we have not connected Tripwright to that client.

- Helper roles (read 2026-11-12): Claude Code [Subagents](https://code.claude.com/docs/en/sub-agents), [Skills](https://code.claude.com/docs/en/skills), [Cloud environments](https://code.claude.com/docs/en/cloud-environments); Codex [Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents), [Skills](https://learn.chatgpt.com/docs/build-skills), [openai/codex#4226](https://github.com/openai/codex/issues/4226); Claude [Use skills in Claude](https://support.claude.com/en/articles/12512180-use-skills-in-claude), [Custom connectors](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp), [Projects](https://support.claude.com/en/articles/9519177-how-can-i-create-and-manage-projects), [Cowork](https://support.claude.com/en/articles/13345190-get-started-with-claude-cowork); ChatGPT [Projects](https://help.openai.com/en/articles/10169521-projects-in-chatgpt), [Developer mode and MCP apps](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt), [Skills](https://help.openai.com/en/articles/20001066-skills-in-chatgpt), [Workspace agents](https://help.openai.com/en/articles/20001143-chatgpt-workspace-agents-for-enterprise-and-business).
- Claude Code: [Connect Claude Code to tools via MCP](https://code.claude.com/docs/en/mcp) (installation scopes, scope precedence, `claude mcp list` status, connectors from claude.ai); [Cloud environments](https://code.claude.com/docs/en/cloud-environments) (connectors bypass the network allowlist; read on 2026-11-02).
- Codex: [Model Context Protocol](https://learn.chatgpt.com/docs/extend/mcp) (shared `config.toml`, ChatGPT desktop app steps, `default_tools_approval_mode` values `auto`, `prompt`, `writes`, `approve`, server instructions). `codex mcp add --url`, the approval error and the effect of `"approve"`: codex-cli 0.162, run by the Tripwright maintainers on 2026-11-02 and 2026-11-03.
- Claude: [Add a connector that isn't in the directory](https://claude.com/docs/connectors/custom/add-unlisted) (plans, steps, authentication, editing); [Directory and custom connectors](https://claude.com/docs/connectors/building/directory-vs-custom) (install link); [Get started with custom connectors using remote MCP](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp) (connections come from Anthropic's cloud). This support article still says that editing means removing and adding again.
- ChatGPT: [Connecting custom MCP servers in ChatGPT](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt) (plans, steps, web only, Pro read only; marked "Updated: 7 days ago"); [Building MCP servers for ChatGPT](https://developers.openai.com/api/docs/guides/custom-mcp-server) (authentication options, `readOnlyHint`, confirmations); [Connect and test your plugin](https://developers.openai.com/plugins/deploy/connect-chatgpt); [Plugins](https://learn.chatgpt.com/docs/plugins) (mobile use; read on 2026-11-02).
- Cursor: [Model Context Protocol (MCP)](https://cursor.com/docs/mcp) (`mcp.json` locations, remote server shape, tool approval, MCP Logs).
- VS Code: [Add and manage MCP servers](https://code.visualstudio.com/docs/agent-customization/mcp-servers). Gemini CLI: [MCP servers with the Gemini CLI](https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md).
- Doubao Work: [使用插件](https://www.doubao.com/work/docs/zh-cn/articles/705018132598-plugins.md) (page dated 2026-09-28). Kimi Work, QwenWork, ZCode: their help pages, read on 2026-11-02 for the Tripwright research.
- Page structure follows the client-by-client setup pages of the [GitHub MCP server](https://github.com/github/github-mcp-server), [Linear](https://linear.app/docs/mcp), [Notion](https://developers.notion.com/docs/get-started-with-mcp) and [Sentry](https://mcp.sentry.dev/).
