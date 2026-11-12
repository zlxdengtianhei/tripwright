# Deploy and Host Tripwright with Railway

Tripwright is an open-source trip-planning memory service for AI assistants. It keeps your trip, your requirements and the exact words they came from, looks up transport and weather, and saves a plan change only after the change passes its checks. It speaks MCP (the Model Context Protocol, the open standard that lets AI apps call outside tools), so it works with Claude, ChatGPT, Claude Code, Codex and other MCP clients.

## About Hosting Tripwright

There is no hosted Tripwright service and no signup: each person runs their own copy. This template deploys that copy as one service built from the project's Dockerfile. A volume at `/data` keeps your trips in a local Git repository, Railway gives the service a public domain, and the health check at `/healthz` decides when a deploy is live.

When the deploy finishes, Railway has made a login key for you (the `TRIPWRIGHT_URL_KEY` variable) and put your complete connection address, `https://<your-domain>/mcp/<key>`, in the `TRIPWRIGHT_ADDRESS` variable. Open the service's **Variables** tab, copy that address, and add it to your assistant. The address is your only login: anyone who has it can read and change your trips, so keep it private.

## Common Use Cases

- Plan a trip with an assistant that remembers every requirement and where it came from, across chats and apps.
- Ask quick questions on the road, such as when you must leave to catch the last train.
- Save screenshots of tips and tickets, and ask about them later in another chat.
- Optionally publish a privacy-scanned trip page to a public GitHub repository that you choose.

## Dependencies for Tripwright Hosting

- An MCP client: Claude, ChatGPT, Claude Code, Codex, Cursor or another client that supports remote MCP servers.
- Optional: a GitHub account, for a private repository that keeps the master copy of your trips and a public one for trip pages.

### Deployment Dependencies

- This template needs no extra Railway service and no API key.
- Source code and documentation: https://github.com/zlxdengtianhei/tripwright
- Connecting each app: https://github.com/zlxdengtianhei/tripwright/blob/main/deploy/CLIENTS.md
- Railway details, variables and troubleshooting: https://github.com/zlxdengtianhei/tripwright/blob/main/deploy/RAILWAY.md
- Private repository and trip pages: https://github.com/zlxdengtianhei/tripwright/blob/main/deploy/SETUP.md
- Transport data comes from Transitous, Open-Meteo and Nominatim / OpenStreetMap: https://github.com/zlxdengtianhei/tripwright/blob/main/docs/DATA_SOURCES.md

### Implementation Details

| Variable | Set by the template |
| --- | --- |
| `TRIPWRIGHT_URL_KEY` | `${{secret(43, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")}}` |
| `TRIPWRIGHT_ADDRESS` | `https://${{RAILWAY_PUBLIC_DOMAIN}}/mcp/${{TRIPWRIGHT_URL_KEY}}` (for you to copy; the service does not read it) |

The service needs no other variable. It uses live transport sources by default.

## Why Deploy Tripwright on Railway

Railway gives you an always-on service, a volume and a public HTTPS address in one click, so your assistant can reach Tripwright from any chat app without a computer that stays on. Railway is a singular platform to deploy your infrastructure stack: it hosts your infrastructure so you don't have to deal with configuration, while allowing you to scale it vertically and horizontally.
