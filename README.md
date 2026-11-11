# tripwright

English | [简体中文](README.zh-CN.md) | [Project page](https://zlxdengtianhei.github.io/tripwright/)

**tripwright is an open-source trip-planning server for AI assistants. It keeps your plan, your preferences and every change in one record, and gives your assistant the parts that apply each time you ask.** It works with clients that support the Model Context Protocol (MCP), the open standard that lets AI apps call outside tools: for example Claude, ChatGPT, Codex and Claude Code (each has limits; see [Known limits](#known-limits)). Your assistant still writes the answer. tripwright supplies the memory, transport and weather facts with their sources, and a log of what changed and why.

**Status (2026-11-03):** you deploy your own copy, for yourself, with Docker or on Railway. There is no public service and no signup. The author uses a copy on a rented server with real transport data. Answer quality is still being evaluated; see [Known limits](#known-limits).

**To start:** to use it from a chat app, follow [Connect and self-host](#connect-and-self-host); a coding agent can do most of the steps for you. To see what it does first, without any account, see [Try locally](#try-locally): three commands with fictional data.

## What you get

- **Memory in every answer.** For each message, tripwright returns what applies now: preferences, travel companions, decisions, places you want to see, and notes for this trip. It also returns what the assistant should consider and what it should ask. The assistant still has to use this.
- **Answers in two parts.** Part 1 gives the next step from the stored plan and marks the times that are not checked yet. Part 2 comes after the checks and starts with what differs from Part 1. Some clients cannot send a second message on their own. In those clients, Part 1 ends by asking you to reply “go on”.
- **Transport facts with their limits.** For each leg, the tools return the departures, stops, transfers, the time window searched and the coverage limits. The assistant should quote these as given. One empty search does not mean there is no service. The assistant checks what it can check. For live information it cannot get, the answer ends with the app to open, what to search and how.
- **Plans that can change, with a record.** Ordinary choices can change if there is a reason. Only places and times that you call a must are protected, and the assistant asks for your musts before it plans. Each accepted change records what changed, who changed it and why. When a change revises a choice you made, the answer says so. Bought tickets and booked times change only with a live source or a confirmed correction.
- **Versions and export.** A result computed on an old version cannot overwrite a newer plan. In repository mode, your trips live in a Git repository that you configure; this is the master copy, and the server keeps its own copy and a working database. On your own computer you can also use a single SQLite database instead. You can export files and see each day's schedule and the history.

The server's tools, MCP prompts and local role instructions put these rules into practice. They do not guarantee that a model never makes mistakes. They do not replace live notices from transport operators. In Claude Code, local hooks add extra checks; other clients work without them.

## Connect and self-host

1. **Start the service** with Docker (on your own computer or any server) or on Railway. With Docker at home, Claude and ChatGPT reach it through a tunnel; SETUP.md lists options. The service creates a secret key, and your connector address is `https://<your-host>/mcp/<key>`. This address is your login: anyone who has it can read and change your trips.
2. **Connect a private GitHub repository** to hold your trips. This step is optional. Without it, the trips stay in a Git repository inside your deployment, and you make the backups yourself.
3. **Add the address to your client.** In Claude and ChatGPT, only the account owner can add a connector, in the app's settings.

For the first deployment, give [deploy/SETUP.md](deploy/SETUP.md) to your coding agent (Claude Code, Codex or similar) and let it follow the steps. The agent installs and configures Docker or Railway; you only confirm account access, a system password or payment when needed, and click Add in your app. Docker starts with three commands, and connecting a private repository takes three more. SETUP.md also covers Railway costs, and [deploy/CLIENTS.md](deploy/CLIENTS.md) has the steps for each client (Claude Code, Codex, Claude, ChatGPT, Cursor and others).

## Try locally

Run these commands from the project root. You need Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
uv run tripwright demo
uv run tripwright serve --stdio
```

`demo` uses a temporary database and fictional data. The last command starts a local MCP server over stdio; put the same command in your local client's MCP settings. The default database is `~/.local/share/tripwright/tripwright.sqlite3`. To use another path, add the global option `--db`.

To use real data for MCP lookups, set the providers when you start the server:

```bash
TRIPWRIGHT_PROVIDERS=live uv run tripwright serve --stdio
```

Without this setting, the MCP server uses `synthetic` (fictional) data. The `tripwright query` command uses `live` data by default; add `--providers synthetic` for fictional data. Each result states its source. Do not use fictional data for real travel.

To keep the master copy in a Git repository:

```bash
uv run tripwright init ./my-trips --trip 2030-05-trip --timezone Europe/Stockholm
TRIPWRIGHT_PROVIDERS=live uv run tripwright --repo ./my-trips serve --stdio
```

`init` creates the trip directory and the local agent files. To add your own remote Git repository, sync and back up, see the [repository format](docs/FORMAT.md). For export and restore commands, run `uv run tripwright --help`.

## Known limits

**Answers.**

- Answer quality is still being evaluated. Independent reviewers check answers against a fixed standard, and the answers have not passed yet.
- In Claude Code (checked on 2026-11-04 with replays of real questions, judged on content by reviewers who did not build it): two errors in the service's own ready-made sentences were found and fixed. A ride to the planned train named only the first bus, so the assistant wrote it as a direct ride; and "trains all night" hid a two-hour gap. The first part arrives within half a minute.
- The assistant still sometimes combines correct facts wrongly: for example it puts an event at the wrong venue, or gives a "be back by" time without the walk to the platform. It may also add one status line, such as "checking the restaurants now". The service gives the facts and tells it not to do this, but cannot stop it.
- Other clients (Codex, the command line without the service) are usable but less tested: Codex sometimes sends both parts in one reply.
- Your assistant's model writes the answer. The service has no checking model of its own.

**Transport and other data.**

- The default real-data sources are [Transitous](https://transitous.org/sources/) (transport), [Open-Meteo](https://open-meteo.com/) (weather) and [Nominatim / OpenStreetMap](https://www.openstreetmap.org/copyright) (place names).
- Transport is mostly timetables. A result that is marked live says so. Otherwise there is no promise about delays, platforms or cancellations.
- Coverage is not worldwide. tripwright knows when the timetable data runs out only for Austria (at the yearly timetable change in December). The only mapped gap is local buses in Carinthia. Other regions can have gaps or expired data that are not detected yet. When data is missing, the answer says "not found", not "no service".
- Extra live sources for Wiener Linien and VAO are not connected. VAO access needs you to arrange a contract and credentials with the provider yourself; there is no one-click setup.
- Opening hours come from saved place notes or from web pages that your client reads. There is no general live source for bookings or free seats.
- By default, the server sends at most 720 Transitous route requests per rolling 24 hours, counted across processes. Check the upstream terms before you run it for many users or for automation.
- Details and attribution: [data sources](docs/DATA_SOURCES.md) (in Chinese, with an English summary).

**Clients** (checked against official documentation on 2026-11-02; not every client is tested end to end):

- Claude: a free account can add only one custom connector. On Team and Enterprise accounts, an organization owner adds the connector first, and everyone in the organization then shares the same secret key; this is not recommended.
- ChatGPT: you can add a custom MCP server on the web only, not on mobile. Tools that write need a Business, Enterprise or Edu account. Pro accounts can use only read-only tools. In tripwright, only `state` and `explain` are read-only, so on Pro you can read the trip but cannot check transport or change the plan. (`query` counts as a write, because each lookup is stored as evidence in the trip repository.)
- The built-in GitHub connectors of Claude chat and ChatGPT can only read. To change a trip, use your own deployed service.
- Claude Code cloud sessions cannot reach the transport sources by default. A connector added to your Claude account works around this.
- Whether a client can send both parts in a row, or keep working in the background, depends on the client.

## Data and privacy

Your trip repository and runtime database store your messages, preferences, facts, decisions and evidence. The client app, the Git host, the person who runs the deployment and each data source handle data under their own terms. This is not a promise that only your computer can see the data.

The code stores the URL key apart from the deploy key of the private repository. Runtime errors, the reverse proxy and the client can still write diagnostic logs. There is no guarantee yet that message bodies are removed from every log. If you run a deployment, limit who can read the logs and the data, and do not log request bodies or credentials. Do not put keys, private keys or unreviewed personal trips in a public repository.

## Example, development and license

- [examples/trip-2026-10-bud-wien/](examples/trip-2026-10-bud-wien/) is a trip that really happened (Budapest and Vienna, October 2026). All names are replaced with codes. tripwright generated its pages: each day's plan, the places to visit, preferences and memory, and why the plan changed. These pages are in Chinese.
- The trip page shared with friends: <https://zlxdengtianhei.github.io/trip-map-2026/> (English by default, with a Chinese version).
- `uv run tripwright demo` shows the program's output for a fictional trip.

Tests: `uv run pytest -q`. MCP smoke test: `uv run python scripts/smoke_mcp.py`.

The code is under the [MIT license](LICENSE). Upstream data and service terms apply separately; the MIT license does not give commercial rights to the Transitous public API.
