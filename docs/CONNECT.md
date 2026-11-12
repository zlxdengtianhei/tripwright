# How to connect to Tripwright

There are two ways:

- **Your own service (remote).** Deploy it with [deploy/SETUP.md](../deploy/SETUP.md), then add its connector address, `https://<your-domain>/mcp/<key>`, to each app with [deploy/CLIENTS.md](../deploy/CLIENTS.md). The service speaks MCP over Streamable HTTP, and the key in the address is the only sign-in. Every app that connects to the same service sees the same trips. The trips are kept on the service's volume, and also in your private GitHub repository once the setup agent connects one (SETUP.md, section 3). There is no public Tripwright service and no sign-up.
- **Local mode (stdio), without a service.** A client on your own computer starts Tripwright itself. This works only in clients that can start a local MCP server, such as Claude Code, Codex or Cursor, and only on that computer.

## Local mode

Run once in the Tripwright source root (Python 3.11+ and [uv](https://docs.astral.sh/uv/)):

```bash
uv sync --extra dev
uv run tripwright init ./my-trips --timezone <IANA zone, e.g. Europe/Vienna>
```

Then give the client this command to start the server:

```bash
TRIPWRIGHT_PROVIDERS=live uv run tripwright --repo ./my-trips serve --stdio
```

- `./my-trips` is the trip repository that `tripwright init` made. The command must start in the Tripwright source root; where the client cannot set a working folder, use `uv run --directory <source path> …`.
- `init` also writes the role files and skills for the local clients it supports.
- Local mode keeps its own copy of the data and does not share the trips of a deployed service.
- Without `TRIPWRIGHT_PROVIDERS=live`, the MCP server uses synthetic data, which must not guide real travel.

## What still depends on the app

The service returns, for each message, the memories to consider, the questions about musts, the transport facts as sentences to quote, check guidance and the change record. Local clients also find the matching skill.

The client itself calls `turn`, uses the memories it gets, quotes the transport sentences and sends the two parts of the answer. One reply or two: for a quick question on the road, the default is two messages. The first part (from the stored plan) arrives first and ends with a line that asks you to reply; the checked part follows your next message. In Claude Code a background agent brings the second message without your reply. Setting `TRIPWRIGHT_REPLY_FLOW=one` at deployment (see [deploy/README.md](../deploy/README.md)) puts both parts in one reply; this suits GPT models, while Claude often leaves the first part out. Other questions get one direct answer. The remote service has no verifying model of its own.

**So that the app uses Tripwright at all:** name it in your message (“Use tripwright: when must we leave tonight?”), or add one line to the app's custom instructions: “For my trips, use the tripwright tools.” Some clients keep a connector's tools out of the model's sight until the model looks for them. There, a question that does not name Tripwright can be answered from a web search instead. What we measured on 2026-11-11 with one on-the-road question: in the Codex CLI, the model used Tripwright in 2 of 10 runs without the name (5 of 10 in a second round on 2026-11-12), and in 5 of 5 runs with the name, and in 5 of 5 runs with the `AGENTS.md` that `tripwright init` writes. In a test host that gives the model the tool definitions directly, next to a web-search tool, GPT, GLM and Kimi each called `turn` first in 12 of 12 runs. Through Anthropic's API, with Tripwright added as a remote MCP server and no word about it in the system prompt, Claude Sonnet 5.5 called `turn` first in 10 of 10 runs of an on-the-road question that did not name it (2026-11-12). In a second round the same day, over four kinds of request that did not name it (a question on the road, a plan change, saving screenshots, a planning request), Claude Sonnet 5.5 called `turn` first in 42 of 42 runs and Claude Opus 5.5 in 6 of 6. The ChatGPT and Claude apps have not been measured.

`turn` records the message, and `propose` and `commit` change the plan. A client that allows only read-only tools (ChatGPT Pro, for example) cannot complete this chain: reading the plan with `state` does not record the request.

## Connection problems

- `/mcp` without a key and a wrong key both return 404. After a new key, replace the address in each client.
- Host headers are not compared with `TRIPWRIGHT_PUBLIC_URL`; localhost and tunnel addresses work together.
- Check the service first: `/healthz` returns 200 while the process runs; `/readyz` returns 200 only when the trip repository is open, its remote answered (when one is connected; without one, `storage` reads `local`) and no write is stuck. Otherwise `/readyz` returns 503 with a `kind` and a `hint` ([troubleshooting](../deploy/SETUP.md#troubleshooting)). A healthy `/healthz` does not prove that the trips can be read and written; `/readyz` does.
- When the client shows only read tools, check its plan and permissions ([deploy/CLIENTS.md](../deploy/CLIENTS.md)); Tripwright does not report a request as saved when it was not.
