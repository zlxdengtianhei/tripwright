# Deploy and Host Tripwright with Railway

Tripwright is a travel-planning memory service for AI assistants. Your chat app or coding agent connects to it over MCP. It keeps your trip, your requirements and the exact words they came from, looks up transport and weather, and saves a plan change only after the change passes its checks.

## About Hosting Tripwright

The template runs one service, built from the Tripwright GitHub repository with its Dockerfile. A volume at `/data` keeps your trips in a local Git repository. Railway gives the service a public domain, and the health check at `/healthz` decides when a deploy is live. At deploy time Railway generates a random URL key. The service variable `TRIPWRIGHT_ADDRESS` then shows your complete connection address, `https://<your-domain>/mcp/<key>`. Add that address to Claude, ChatGPT, Claude Code or Codex. The address is the only login, so keep it private. Optionally, connect a private GitHub repository as the master copy of your trips.

## Common Use Cases

- Plan a trip with an assistant that remembers every requirement and where it came from.
- Ask quick questions on the road, such as when you must leave to catch the last train.
- Save screenshots of tips and tickets, and ask about them later in another chat.
- Share a scanned public trip page with the people you travel with.

## Dependencies for Tripwright Hosting

- An MCP client: Claude, ChatGPT, Claude Code, Codex, Cursor or another client that supports remote MCP servers.
- Optional: a private GitHub repository for the master copy, and a public one for trip pages.

### Deployment Dependencies

- Source and documentation: https://github.com/zlxdengtianhei/tripwright
- Connecting each app: https://github.com/zlxdengtianhei/tripwright/blob/main/deploy/CLIENTS.md
- Railway details, variables and troubleshooting: https://github.com/zlxdengtianhei/tripwright/blob/main/deploy/RAILWAY.md
- Transport data comes from Transitous, Open-Meteo and Nominatim / OpenStreetMap: https://github.com/zlxdengtianhei/tripwright/blob/main/docs/DATA_SOURCES.md

### Implementation Details

| Variable | Set by the template |
| --- | --- |
| `TRIPWRIGHT_URL_KEY` | `${{secret(43, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")}}` |
| `TRIPWRIGHT_ADDRESS` | `https://${{RAILWAY_PUBLIC_DOMAIN}}/mcp/${{TRIPWRIGHT_URL_KEY}}` (for you to copy; the service does not read it) |

The service needs no other variable. It uses live transport sources by default.

### Why Deploy Tripwright on Railway?

Railway is a singular platform to deploy your infrastructure stack. Railway will host your infrastructure so you don't have to deal with configuration, while allowing you to vertically and horizontally scale it.

By deploying Tripwright on Railway, you are one step closer to supporting a complete full-stack application with minimal burden. Host your servers, databases, AI agents, and more on Railway.
