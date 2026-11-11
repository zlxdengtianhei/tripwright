# How to connect to Tripwright

There are two ways:

- **Your own service (remote).** Deploy it with [deploy/SETUP.md](../deploy/SETUP.md), then add its connector address, `https://<your-domain>/mcp/<key>`, to each app with [deploy/CLIENTS.md](../deploy/CLIENTS.md). The service speaks MCP over Streamable HTTP, and the key in the address is the only sign-in. Every app that connects to the same service sees the same trips. There is no public Tripwright service and no sign-up.
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
- `init` also writes the role files and skills for the local clients it supports. In Claude Code, two local hooks add checks on the order of the first part and on the second part's draft; these hooks do not run with a remote connector.
- Local mode keeps its own copy of the data and does not share the trips of a deployed service.
- Without `TRIPWRIGHT_PROVIDERS=live`, the MCP server uses synthetic data, which must not guide real travel.

## What still depends on the app

The service returns, for each message, the memories to consider, the questions about musts, the transport facts as sentences to quote, check guidance and the change record. The full way of working is the MCP prompt `tripwright-main`; clients that let you choose prompts can load it, and local clients also find the matching skill. A prompt being available does not mean that the client loads it or follows it.

The client itself calls `turn`, uses the memories it gets, quotes the transport sentences and sends the two parts of the answer. A client that cannot continue in the background may need the user to reply "continue" before the second part. The remote service has no verifying model of its own.

`turn` records the message, and `propose` and `commit` change the plan. A client that allows only read-only tools (ChatGPT Pro, for example) cannot complete this chain: reading the plan with `state` does not record the request.

## Connection problems

- `/mcp` without a key and a wrong key both return 404. After a new key, replace the address in each client.
- Host headers are not compared with `TRIPWRIGHT_PUBLIC_URL`; localhost and tunnel addresses work together.
- Check the service first: `/healthz` returns 200 while the process runs; `/readyz` returns 200 only when the trip repository is open, its remote answered and no write is stuck. Otherwise `/readyz` returns 503 with a `kind` and a `hint` ([troubleshooting](../deploy/SETUP.md#troubleshooting)). A healthy `/healthz` does not prove that the trips can be read and written; `/readyz` does.
- When the client shows only read tools, check its plan and permissions ([deploy/CLIENTS.md](../deploy/CLIENTS.md)); Tripwright does not report a request as saved when it was not.
