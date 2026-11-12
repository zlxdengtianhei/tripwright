<h1 align="center"><img src="docs/assets/logo.svg" alt="tripwright" width="317" height="64"></h1>

<p align="center">An open-source trip-planning server for AI assistants: it remembers plans across chats, checks transport facts with sources, and creates shareable pages.</p>

<p align="center"><strong>English</strong> | <a href="README.zh-CN.md">简体中文</a> | <a href="https://zlxdengtianhei.github.io/tripwright/">Project page</a></p>

<p align="center"><img src="docs/assets/hero.png" alt="Three phone screens of a real 8-day trip page made by tripwright: the route and the list of days; one day's key times and places to go; the same day's transport legs with a map" width="100%"></p>
<p align="center"><sub>A real 8-day trip (Budapest, Vienna and the Salzkammergut, October 2026), as the page tripwright makes. Names are replaced with codes, and places to stay show only the district.</sub></p>

You deploy your own copy: [three steps](#get-started), or [look at an example trip first](#see-an-example). It also remembers your preferences across chats and records every change. It works with Claude, ChatGPT, Claude Code, Codex and other apps that support MCP (the Model Context Protocol, the open standard that lets AI apps call outside tools).

There is no hosted service and no signup. Each person runs their own copy, for themselves.

## What you get

- **Your own travel library, from screenshots.** Send a batch of guide screenshots at once, from Xiaohongshu or anywhere. Your assistant transcribes them, stores the places in your trip and the tips in its records, and later answers draw on them. Ask it to research something, and it records what it found with the source. When a question needs a fact that is not stored, it looks it up and records it. The screenshots themselves are not kept; their transcription is, marked as such.
- **One trip, many chats.** Your preferences, travel companions, decisions and places to see are stored once. Each new message brings back the ones that apply, in any app that is connected to your service.
- **Quick answers on the road, then checked ones.** For a quick question, your assistant first answers from the stored plan and marks what is not checked. It then checks and tells you what changed.
- **Transport facts with their limits.** Each leg comes with departures, transfers, the time window searched and where the data stops. No result means “not found”, never “no service”.
- **A record of every change.** Each accepted change records what changed, who changed it and why. A result computed on an old version cannot overwrite a newer plan. Your trips live in a Git repository that you own.
- **A trip page for your friends.** After each plan change, the service builds a day-by-day page, scans it for private data and publishes it to a public repository that you choose. This step is optional.

Your assistant's model still writes the answers. tripwright supplies the memory, the sourced facts and the record.

## How it works

```mermaid
flowchart LR
    A["Chat app or<br/>command-line agent"] -->|MCP| B["tripwright service<br/>Docker or Railway"]
    B <-->|"master copy (optional)"| C[("Your private<br/>Git repository")]
    B -->|"scanned pages (optional)"| D["Public trip page<br/>your public repository"]
```

The service has seven MCP tools. One of them receives each message and returns what applies now; the others look up facts, propose and commit changes, and read the plan and its history. Without a private repository, the trips stay in a local Git repository on the service's volume.

## Get started

Three steps. You need a computer or server that can run Docker, or a Railway account. You do not need a GitHub account for the basic setup.

1. **Give [deploy/SETUP.md](deploy/SETUP.md) to your coding agent** (Claude Code, Codex or similar). It starts the service with Docker (three commands) or on Railway, and prints your connection address, `https://<your-host>/mcp/<key>`. This address is your only login. Anyone who has it can read and change your trips, so treat it like a password.
2. **Recommended: connect GitHub.** These are two separate repositories. A private repository keeps the full record of your trips; without it, they are kept only on the server. A public repository holds only the scanned trip pages that you share, and nothing else; public pages need it. After you connect it, every trip gets a page; you can switch off any trip. The agent runs three commands for each repository, with no restart.
3. **Add the address to your app.** Coding agents add it for you. In Claude and ChatGPT, the account owner clicks **Add** in the app's settings. Then ask: “Use tripwright: which trips are stored?”

The agent asks you only for what only you can do: sign in to Railway, type a system password, pay, or click **Add**.

## Connect your assistant

| Where you use it | How it connects | Replies | Checked by us |
| --- | --- | --- | --- |
| Claude Code | `claude mcp add --transport http tripwright <address>` | Two messages: the first part, then the checked part | Yes, connected and used |
| Claude (web, desktop, mobile) | Settings, add a custom connector, sign-in “none” | The first part, then the checked part after you reply | No real app connection yet |
| ChatGPT | Web only. Business, Enterprise or Edu to use all tools; Pro is read-only | The same; or one message, see below | No real app connection yet |
| Codex | `codex mcp add tripwright --url <address>` | Not measured | Command taken from the docs |
| Cursor and other MCP clients | Streamable HTTP with the same address | Depends on the client | No |

This is a default you can change. Set `TRIPWRIGHT_REPLY_FLOW=one` when you deploy to get both parts in one reply (see [deploy/README.md](deploy/README.md)); that suits GPT models, while Claude often leaves the first part out. Only quick questions on the road have two parts. Account limits, the steps for each app and problems are in [docs/CONNECT.md](docs/CONNECT.md) and [deploy/CLIENTS.md](deploy/CLIENTS.md).

## Getting the best results

**Two ways to use it.** On the road, ask short questions (“I'm at the station, when is the next train?”): a first answer from your stored plan comes within seconds, then the checked one. When planning, send requirements and screenshots: they are recorded before the plan changes, even when the change is held back, and the requirements agent links them to what you already decided; the changed plan is checked before it is saved.

**Use an app that can run sub-agents.** In Claude Code and Codex the service names the helper each step needs, while the main agent keeps answering: a requirements agent records new requirements and screenshots and links them to your earlier decisions; a researcher checks each fact, and ticket prices and discounts when tickets are still to buy; a verifier checks a changed plan before it is saved. The service updates the website itself. Their cloud sessions that work on your GitHub repository, and similar modes in other apps, may give the same; we have not tested them with tripwright.

**A plain chat works, with limits we do not fix.** In ChatGPT or Claude chat one agent does everything. Checked answers come after you reply “continue”, and in a long chat the model can lose constraints you set early (the service sends back the ones that apply with every message, but the model may still drop them). We do not guarantee results there.

**Many screenshots.** Both apps take about 20 images per message (ChatGPT: 20 files per message, 20 MB per image, 3 uploads per day on the free plan; Claude: 20 images per message, 10 MB and 8000 × 8000 pixels per image). Send them in batches of up to 20. Neither app's help pages list ZIP among the formats a chat accepts, so do not zip them for a chat; to make them smaller, save smaller images or crop the empty parts, as long as each stays within the limit. For more, save them to one folder (on iPhone, in Files) and give the folder to Claude Code or Codex.

## See an example

[examples/trip-2026-10-central-europe.yaml](examples/trip-2026-10-central-europe.yaml) is a real 8-day trip, put together from a page its owner already published, plus a trip log of what the owner typed on the road. To build its ten pages on your computer (Python 3.11+ and [uv](https://docs.astral.sh/uv/)):

```bash
uv sync --extra dev
uv run python -m tripwright.site.example examples/trip-2026-10-central-europe.yaml out
```

Open `out/index.html`. The page has an EN / 中文 switch. `out/log.html` is the trip log: what the traveler typed on the road, how it was handled then, and what tripwright does with such a message now. The [project page](https://zlxdengtianhei.github.io/tripwright/) shows more. To see the program's output for a made-up trip, run `uv run tripwright demo`.

## Status and limits

- **Real apps are untested.** The ChatGPT and Claude apps have never been connected. We tested with scripts that simulate a chat host, and with Claude Code.
- **Answer quality is still being evaluated.** The service gives facts and instructions, but the assistant can still combine correct facts wrongly, such as a wrong venue or no walking time to the platform. The service cannot stop this. One case we know: when the assistant passes a question that contains a stored place as your location (“can I get into the airport?”), the quick first reply says you are at that place (“you are at Vienna Airport”) instead of where the plan has you.
- **Deployment.** From the first Docker command to a ready service took about 13 seconds in a run where a low-cost model followed SETUP.md alone. On Railway, a real account was ready 59 seconds after upload. Connecting a private GitHub repository was tested only against a local bare repository; a real GitHub deploy key is not tested yet.
- **Trip pages.** The scan replaces names with codes and shows places to stay by district only. If it finds something like a booking number, it publishes nothing for that build. After you connect a public repository, every trip is published by default; set `off: true` in the trip's `share.yaml` to stop one. This was tested only against a local bare repository, not on real GitHub Pages.
- **Transport data** comes from [Transitous](https://transitous.org/sources/), [Open-Meteo](https://open-meteo.com/) and [Nominatim / OpenStreetMap](https://www.openstreetmap.org/copyright). It is mostly timetables, not live data. Coverage is not worldwide, and the end of timetable data is detected only for Austria. By default the service sends at most 720 Transitous route requests per rolling 24 hours. Details: [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md).
- **Privacy.** Your trips and messages are stored in your deployment. Your Git host, your client app and each data source handle data under their own terms. Logs may still contain message text; limit who can read them. Never put unreviewed personal trips or keys in a public repository.

## Documentation

- [deploy/SETUP.md](deploy/SETUP.md): the runbook for your coding agent. [deploy/README.md](deploy/README.md): settings and Docker reference. [deploy/RAILWAY.md](deploy/RAILWAY.md): Railway.
- [deploy/CLIENTS.md](deploy/CLIENTS.md) and [docs/CONNECT.md](docs/CONNECT.md): connect each app; local mode without a service.
- [docs/FORMAT.md](docs/FORMAT.md): the trip repository format. [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md): data sources.
- Tests: `uv run pytest -q`.

## License

The code is under the [MIT license](LICENSE). Upstream data and service terms apply separately; the MIT license gives no commercial rights to the Transitous public API.
