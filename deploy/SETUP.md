# Set up Tripwright: instructions for the user's agent

You are the agent setting up a personal Tripwright service. Do the work for the user; do not make them edit configuration files or move repositories by hand. The service has one login credential: the key in its MCP address.

## 1. Ask two questions and choose

1. “Which AI apps will you use?”
2. “Do you have a computer that stays on?”

- **Docker** is simplest for coding clients on that computer. Claude, ChatGPT and phone apps also need an HTTPS tunnel.
- **Railway** needs no always-on computer and gives a stable HTTPS address. It can cost money.
- GitHub is optional. Without it, Tripwright keeps a local Git repository on its data volume; make backups.

Only the user may do these things; wait when one is needed. Everything else is the agent's job.

- Create a Railway account and confirm the browser page opened by `railway login`; pay or verify the account if needed.
- Enter the system password once while Docker is installed. The agent does the installation steps below.
- Run `gh auth login` when a private GitHub repository is wanted.
- Click **Add** or **Create** in Claude and ChatGPT, and approve any paid plan.
- Accept Docker Desktop's license personally if choosing it; the agent must not accept it for the user.

## 2A. Docker: three commands to start

If Docker is absent, install it as the agent:

- **Linux:** `curl -fsSL https://get.docker.com -o install-docker.sh`; inspect with `sh install-docker.sh --dry-run`; then ask the user for the password and run `sudo sh install-docker.sh`.
- **macOS:** prefer `brew install colima docker`, then `colima start`. This needs neither a Docker account nor Docker Desktop's license agreement.

Run from any folder; no environment variables, source checkout, data-directory setup or `sudo` is needed:
```bash
docker build -t tripwright https://github.com/zlxdengtianhei/tripwright.git
docker run -d --name tripwright --restart unless-stopped -p 127.0.0.1:8000:8000 -v tripwright-data:/data tripwright
docker exec tripwright tripwright address     # http://localhost:8000/mcp/<key>
```

The first command uses the Dockerfile at the repository root. The named volume keeps `/data/remote`, including the generated URL key and local trips. Check `curl -s http://localhost:8000/readyz` for `"ready":true`. If you publish a different host port, print the address with `docker exec tripwright tripwright address --base http://localhost:<port>`.
Confirm MCP itself (the variable hides the key from the command's output):
```bash
address=$(docker exec tripwright tripwright address)
curl -s -o /dev/null -w '%{http_code}\n' "$address" -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"setup-check","version":"0"}}}'
```
`200` means MCP is connected.
For Claude, ChatGPT or a phone, use one of these and then `docker exec tripwright tripwright address --base <https://public-origin>`:

- **Tailscale Funnel:** `tailscale funnel 8000`; stable address, account required.
- **Cloudflare Quick Tunnel:** `cloudflared tunnel --url http://localhost:8000`; no account, but its address changes every time. Use Funnel or Railway for a stable address.

Localhost and tunnel addresses work at the same time; a changed tunnel origin needs no restart or variable change.

## 2B. Railway: the same short setup

Install the Railway CLI, ask the user to complete `railway login`, clone the public source, then run:

```bash
railway init --name tripwright
railway add --service tripwright
railway volume add --mount-path /data
railway domain
railway up --detach
railway ssh -- tripwright address
```

The root Dockerfile is detected automatically; set neither a Dockerfile variable nor a key-generation switch. Keep the volume at `/data`. Check `https://<your-domain>/readyz` for `"ready":true`, then run the MCP initialize check above with `address=$(railway ssh -- tripwright address)`.
Railway offers a trial and paid plans; show the user [current pricing](https://railway.com/pricing) before any payment. A limited trial can restrict outbound network access.

## 3. Optional: three commands to connect a private repository

Ask for the GitHub owner and use `tripwright-trips` as the repository name. If needed, ask the user to run `gh auth login`. Create an **empty** private repository:

```bash
gh repo create <owner>/tripwright-trips --private
docker exec tripwright tripwright repo key | gh repo deploy-key add - --allow-write -t tripwright -R <owner>/tripwright-trips
docker exec tripwright tripwright repo connect git@github.com:<owner>/tripwright-trips.git
```

On Railway, replace the two `docker exec tripwright` prefixes with `railway ssh --`:

```bash
railway ssh -- tripwright repo key | gh repo deploy-key add - --allow-write -t tripwright -R <owner>/tripwright-trips
railway ssh -- tripwright repo connect git@github.com:<owner>/tripwright-trips.git
```

`repo key` prints only the public key and reuses it on later calls. `repo connect` pushes existing local trips to an empty remote, adopts a remote trip when local has none, and a running service applies either connection on its next request or health check—no restart. If both have trips, it refuses and changes neither. Switching remotes uses the same checks; never move the mirror manually.

Check `/readyz` for `"storage":"remote"`. Other SSH Git hosts work too; set `TRIPWRIGHT_KNOWN_HOSTS` when the service cannot obtain their host keys itself.

## 4. Add the address to each AI app

The printed address is a password: anyone holding it can read and change the trips. It may be printed in the user's own terminal; paste it only into the user's own AI apps, never into issues, shared chats or a repository.

Follow [CLIENTS.md](CLIENTS.md) for each app named in step 1. The agent configures coding clients. The user clicks **Add** or **Create** in Claude and ChatGPT. Authentication is **none**; the key is already in the address.

Ask each client: “Use tripwright: which trips are stored?” Confirm that it calls a Tripwright tool.

## 5. Later

- **Replace a leaked key, Docker:** `docker exec tripwright rm /data/remote/url-key && docker restart tripwright && docker exec tripwright tripwright address`
- **Replace a leaked key, Railway:** `railway ssh -- rm /data/remote/url-key && railway up --detach && railway ssh -- tripwright address`
- Replace the old address in every client after either command.
- **Upgrade Docker:** build again, stop and remove the container, then run the same `docker run` command; the named volume and key remain.
- **Upgrade Railway:** `railway up --detach` after pulling the source.
- **Back up without GitHub:** `docker exec tripwright tar czf - -C /data remote > tripwright-backup.tgz` (or `railway ssh -- tar czf - -C /data remote > tripwright-backup.tgz`). The backup contains the URL key and trips; protect both.

## 6. Troubleshooting

| Symptom | What the agent does |
| --- | --- |
| `/mcp` or a wrong-key URL returns 404 | Print a fresh address with `tripwright address`; do not add headers or change Host settings. |
| `/readyz` is not ready | Read `kind` and `hint`; show them to the user and apply the matching row below. |
| `auth` or `key_file` | Check the repository address and that the **public** key from `repo key` has write access. Never paste the private key into a variable. |
| `branch` or `config` | Check the branch. Use an empty remote or a Tripwright repository; `repo connect` refuses destructive conflicts. |
| `network` | Check outbound networking; GitHub SSH uses port 443. A limited Railway trial may require user verification. |
| `protected` or `blocked` | Allow the deploy key to push the branch; saved writes retry after the fix. |
| A changed tunnel address stops working elsewhere | Run `address --base <new-origin>` and update clients; the service itself needs no restart. |
| A later start says the data directory is new and empty | The volume is missing or replaced; restore the backup before writing new trips. |
