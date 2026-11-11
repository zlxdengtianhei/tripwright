# Deploy Tripwright

Tripwright is an MCP service: sessions in Claude, ChatGPT and other AI apps use it to look up transport and keep the trip records. Each person deploys their own copy, used only by them (and the people they travel with). **An agent doing a first deployment follows [SETUP.md](SETUP.md); [CLIENTS.md](CLIENTS.md) adds the address to each app.** This page is the reference for Docker deployments.

## Choose a deployment

| You have | Use | Runs while |
| --- | --- | --- |
| A computer that stays on (macOS, Windows, Linux) | Docker on that computer ([SETUP.md step 1](SETUP.md#docker-a-computer-that-stays-on), settings [below](#docker-on-your-own-computer)) | the computer is on and Docker runs |
| A server with a public IP address and a domain | Docker with Caddy ([below](#a-server-with-a-domain-docker-and-caddy)) | the server runs |
| No such computer | Railway ([RAILWAY.md](RAILWAY.md)) | always; the Free plan gives $1 a month, see that page |

Every way supports **one instance with one persistent data folder** (`/data`). The folder holds the copy of the trip repository, writes that are not pushed yet, and the key. If it is lost, unpushed writes are lost. Do not run two instances on the same folder or the same trip repository. At each start the log says whether the data folder is new or kept; `data directory … is new and empty` should appear only at the first start.

## Where the trips are stored

- **A private GitHub repository:** the service pushes with a deploy key that works for this one repository only. Steps: [SETUP.md step 2](SETUP.md#step-2-connect-a-private-github-repository).
- **Another Git host** (GitLab, Gitea, your own): the same, with the repository's SSH address and the host's SSH keys in `TRIPWRIGHT_KNOWN_HOSTS`.
- **A repository on the same machine:** `TRIPWRIGHT_REPO_URL` can be the absolute path of a Git repository inside the container (mounted in, readable and writable by UID 10001). A path needs no deploy key.
- **No host:** without `TRIPWRIGHT_REPO_URL`, the service keeps a local Git repository in the data folder and commits as usual, without pushing. `state(view="sync")` says that the files are only in this folder. The user backs it up by copying the whole data folder.

The service refuses to point an existing copy (its "mirror") at a different repository. To add a repository to a service that already ran without one, move the empty mirror aside first, as SETUP.md step 2 shows.

## Who can connect

Only someone who has the connector address `https://<your-domain>/mcp/<key>`. The address is the key: anyone who has it can read and change the trips, so it does not go into chat, screenshots or public documents. The service compares keys in constant time, and its log never records the address.

- **Let the service make the key (recommended):** `TRIPWRIGHT_URL_KEY_GENERATE=1`. This is a switch, not a key. At its first start the service writes a random key to `remote/url-key` in the data folder (mode 600) and reads it at every later start. The log says only where the key is; `/healthz` and `/readyz` never show it.
- **Choose the key yourself:** `TRIPWRIGHT_URL_KEY`, at least 32 characters, only letters, digits, `-` and `_`. When both are set, this one wins and the log says so.
- **New key:** delete `remote/url-key` (or change `TRIPWRIGHT_URL_KEY`) and restart; then replace the address in each client.

## Docker on your own computer

The commands are in [SETUP.md step 1](SETUP.md#docker-a-computer-that-stays-on). You need Docker Desktop (macOS, Windows) or Docker Engine with Compose (Linux), and the source. Reference:

- **All settings live in `deploy/.env`,** copied from `.env.example`, which already sets `TRIPWRIGHT_URL_KEY_GENERATE=1` and `TRIPWRIGHT_PUBLIC_URL=http://localhost:8000`.
- **The data folder is `deploy/data`.** The key is `deploy/data/remote/url-key`. On Linux the container runs as UID 10001, so first run `sudo chown 10001:10001 data`; Docker Desktop on macOS and Windows does not need it.
- **`TRIPWRIGHT_PUBLIC_URL`:** keep `http://localhost:8000` when only clients on this computer connect (Claude Code, Codex, Cursor). The host and port in every client's address must match it exactly (`localhost`, not `127.0.0.1`), or the service answers 421. Claude (also Claude Desktop) and ChatGPT connect from their providers' cloud, so they need a tunnel; put the tunnel's `https://…` address here, and use it in the clients on this computer too. The tunnels are listed in SETUP.md step 1.
- **Another port** (when 8000 is taken): set `TRIPWRIGHT_PORT=<port>` in `.env` and use the same port in `TRIPWRIGHT_PUBLIC_URL`.
- **After a change to `.env`,** run `docker compose -f compose.personal.yaml up -d`.
- **Sleep or shutdown** of the computer, or a stopped Docker, takes the service away, and the apps report that they cannot connect. Docker starts the container again after a restart (`restart: unless-stopped`).
- **Update:** `git pull`, then `docker compose -f compose.personal.yaml up -d --build`. The data folder stays.
- **Without Docker,** a client on the same computer can run Tripwright directly over stdio ([../docs/CONNECT.md](../docs/CONNECT.md)). That is separate data, not shared with the Docker service.

## A server with a domain: Docker and Caddy

`compose.yaml` adds a Caddy container, which gets the certificate and passes HTTPS to the application. TCP ports 80 and 443 must be free and reachable from the internet, and the domain must point to the server. The application container `tripwright-app` runs as UID and GID `10001:10001` and listens on port 8000 only inside the `tripwright-backend` Docker network; `tripwright-caddy` publishes 80 and 443.

`compose.yaml` expects this layout; the example uses `/opt/tripwright` on the server, which these commands reach as `<server>` over SSH:

| Path | Content |
| --- | --- |
| `/opt/tripwright/app/` | the source of one Git commit, with `deploy/` |
| `/opt/tripwright/data/` | the data folder; UID and GID 10001, mode 700 |
| `/opt/tripwright/tripwright.env` | the service's settings; owned by root, mode 600 |
| `/opt/tripwright/compose.env` | `TRIPWRIGHT_DOMAIN=<your-domain>`, for Caddy; compose refuses to start without it, so Caddy never asks for a certificate for someone else's name |
| `/opt/tripwright/caddy-data/`, `/opt/tripwright/caddy-config/` | Caddy's certificates, keys and configuration; mode 700 |
| `/opt/tripwright/SOURCE_REVISION` | the commit of the deployed source |

The commands use `docker-compose`; use `docker compose` where Compose is a plugin. Run the local commands from the Tripwright source root.

**First deployment.** Make the folders and the two settings files. The commands do not overwrite an existing settings file.

```bash
ssh <server> 'install -d -m 755 /opt/tripwright /opt/tripwright/app && install -d -m 700 /opt/tripwright/caddy-data /opt/tripwright/caddy-config && install -d -m 700 -o 10001 -g 10001 /opt/tripwright/data'
ssh <server> 'test ! -e /opt/tripwright/tripwright.env && umask 077 && printf "TRIPWRIGHT_PUBLIC_URL=https://%s\nTRIPWRIGHT_URL_KEY_GENERATE=1\n" "<your-domain>" > /opt/tripwright/tripwright.env'
ssh <server> 'echo "TRIPWRIGHT_DOMAIN=<your-domain>" > /opt/tripwright/compose.env'
```

Then copy the source of the current commit and build. The commands also work when Tripwright sits in a subfolder of a larger repository; only the Tripwright folder goes to the server. `deploy/` is copied from the working tree, so check its changes first. The build runs `uv sync --locked --no-dev --no-editable` and does not change the lock file. The Docker build context is only `app/`, and `Dockerfile.dockerignore` narrows it further; the settings and the data stay outside it.

```bash
set -euo pipefail
deploy_revision=$(git rev-parse HEAD)
deploy_repo_root=$(git rev-parse --show-toplevel)
deploy_prefix=$(git rev-parse --show-prefix)
deploy_tree=$deploy_revision
if [ -n "$deploy_prefix" ]; then deploy_tree="$deploy_revision:${deploy_prefix%/}"; fi
git -C "$deploy_repo_root" archive "$deploy_tree" | ssh <server> 'tar -xf - -C /opt/tripwright/app'
COPYFILE_DISABLE=1 tar -cf - deploy | ssh <server> 'tar -xf - -C /opt/tripwright/app'
printf '%s\n' "$deploy_revision" | ssh <server> 'tee /opt/tripwright/SOURCE_REVISION'
ssh <server> 'cd /opt/tripwright/app/deploy && docker-compose --env-file /opt/tripwright/compose.env config --quiet && docker-compose --env-file /opt/tripwright/compose.env up -d --build'
```

`COPYFILE_DISABLE=1` keeps macOS tar from adding AppleDouble files; the same command works on Linux.

**Check,** after Caddy has its certificate: `curl -s https://<your-domain>/readyz` shows `"ready":true`. Then put the address on the clipboard without showing it, and check it with the command in [SETUP.md step 3](SETUP.md#step-3-add-the-address-to-each-client):

```bash
key=$(ssh <server> 'cat /opt/tripwright/data/remote/url-key' | tr -d '\r\n')
printf 'https://%s/mcp/%s' "<your-domain>" "$key" | pbcopy && unset key && echo "copied"
```

If Let's Encrypt finally refuses the certificate, keep Caddy's error and stop to find the cause; do not switch to another public entry. During the first start, while the certificate is being issued, TLS connections can fail for a short time.

**A trip repository** works as in [SETUP.md step 2](SETUP.md#step-2-connect-a-private-github-repository), with these differences: append `TRIPWRIGHT_REPO_URL` and `TRIPWRIGHT_DEPLOY_KEY` to `/opt/tripwright/tripwright.env`; the empty mirror to move aside is `/opt/tripwright/data/remote/tenants/personal/mirror`; then recreate the application with `docker-compose --env-file /opt/tripwright/compose.env up -d --force-recreate tripwright`.

**Update and roll back.** Keep the settings, the data and the Caddy folders. An update builds a fresh `app.next/`, so that files removed in the new commit do not stay behind, and switches only after the build succeeds. The commands require that `app.next` and `app.previous` do not exist yet; if they do, check what happened at the last update first. `deploy/` again comes from the working tree.

```bash
set -euo pipefail
ssh <server> 'test ! -e /opt/tripwright/app.next && test ! -e /opt/tripwright/app.previous && mkdir /opt/tripwright/app.next'
deploy_revision=$(git rev-parse HEAD)
deploy_repo_root=$(git rev-parse --show-toplevel)
deploy_prefix=$(git rev-parse --show-prefix)
deploy_tree=$deploy_revision
if [ -n "$deploy_prefix" ]; then deploy_tree="$deploy_revision:${deploy_prefix%/}"; fi
git -C "$deploy_repo_root" archive "$deploy_tree" | ssh <server> 'tar -xf - -C /opt/tripwright/app.next'
COPYFILE_DISABLE=1 tar -cf - deploy | ssh <server> 'tar -xf - -C /opt/tripwright/app.next'
printf '%s\n' "$deploy_revision" | ssh <server> 'tee /opt/tripwright/SOURCE_REVISION.next'
ssh <server> 'cd /opt/tripwright/app.next/deploy && docker-compose --env-file /opt/tripwright/compose.env config --quiet && docker-compose --env-file /opt/tripwright/compose.env build tripwright'
ssh <server> 'mv /opt/tripwright/app /opt/tripwright/app.previous && mv /opt/tripwright/app.next /opt/tripwright/app && mv /opt/tripwright/SOURCE_REVISION /opt/tripwright/SOURCE_REVISION.previous && mv /opt/tripwright/SOURCE_REVISION.next /opt/tripwright/SOURCE_REVISION && cd /opt/tripwright/app/deploy && docker-compose --env-file /opt/tripwright/compose.env up -d --force-recreate'
```

`--force-recreate` also mounts the Caddyfile again; a configuration reload alone may not read a file at a replaced path. The update causes a short interruption. After it passes the check, you can delete `app.previous` and `SOURCE_REVISION.previous`, which hold only source; back up the data separately.

To go back to the previous source, stop the project, swap the source folders and rebuild. The data does not go back with the source: before an upgrade that changes the data format, back up the data and follow that upgrade's migration notes.

```bash
ssh <server> 'cd /opt/tripwright/app/deploy && docker-compose --env-file /opt/tripwright/compose.env down && mv /opt/tripwright/app /opt/tripwright/app.failed && mv /opt/tripwright/app.previous /opt/tripwright/app && mv /opt/tripwright/SOURCE_REVISION /opt/tripwright/SOURCE_REVISION.failed && mv /opt/tripwright/SOURCE_REVISION.previous /opt/tripwright/SOURCE_REVISION && cd /opt/tripwright/app/deploy && docker-compose --env-file /opt/tripwright/compose.env up -d --build --force-recreate'
```

**Logs, stop and start:**

```bash
ssh <server> 'cd /opt/tripwright/app/deploy && docker-compose --env-file /opt/tripwright/compose.env ps'
ssh <server> 'cd /opt/tripwright/app/deploy && docker-compose --env-file /opt/tripwright/compose.env logs --tail 100'
ssh <server> 'cd /opt/tripwright/app/deploy && docker-compose --env-file /opt/tripwright/compose.env logs --tail 100 --follow caddy'
ssh <server> 'cd /opt/tripwright/app/deploy && docker-compose --env-file /opt/tripwright/compose.env stop'
ssh <server> 'cd /opt/tripwright/app/deploy && docker-compose --env-file /opt/tripwright/compose.env start'
```

`down` instead of `stop` removes the containers and the project network and keeps the data and the key. Do not paste the settings file, a container inspection with its environment, or the expanded Compose configuration into logs, reports or repositories. After a change to `tripwright.env`, run `docker-compose --env-file /opt/tripwright/compose.env up -d --force-recreate tripwright`.

**Remove everything.** This deletes the data, the key, the certificates and the source of this deployment for good; run it only after a backup, or when nothing is needed any more. It does not prune Docker globally and does not touch other projects.

```bash
ssh <server> 'cd /opt/tripwright/app/deploy && docker-compose --env-file /opt/tripwright/compose.env down && docker image rm tripwright-app:local && rm -rf -- /opt/tripwright'
```

## Health, readiness and logs

- `GET /healthz` returns 200 while the process runs; the body lists the personal repository's status under `tenants.personal`. Container and platform health checks use it.
- `GET /readyz` returns 200 only when the personal repository is open, its remote answered and no write is stuck. Otherwise it returns 503, and `tenants.personal` has `status` (`ok`, `starting`, `error`, `blocked`), `kind` (the problem), `hint` (how to fix it), `unsynced` and `oldest_unsynced_age_seconds` (how long the oldest unpushed write has waited). With a repository, it shows `"storage":"remote"`; without one, `"storage":"local"`.
- At start the service opens the personal repository in the background and runs `git ls-remote` and `git push --dry-run`, which finds a read-only key (`TRIPWRIGHT_CHECK_PUSH=0` turns that off). Writes left from before a restart are then pushed without waiting for a request.
- When a write cannot be pushed, the write receipt and `state(view="sync")` carry `blocked: {kind, since, hint}`.
- The log (`docker compose logs tripwright`) has one line per event: the data folder's state, a tenant that failed to open and why, a write that is stuck or recovered, committer errors, and unexpected errors in a tool. It never shows tokens, keys or connector addresses.

## Troubleshooting

| `kind` in `/readyz` or the log | Meaning | What to do |
| --- | --- | --- |
| `auth` | The key was refused, the repository does not exist or its address is wrong, or the host keys do not match | Check the repository address; the deploy key must be on this repository with write access; for GitHub, the service fetches the host keys itself. Then read the detail on the log line: `hint` speaks of SSH keys even when the cause is another, such as a repository path that does not exist |
| `branch` | The repository has no such branch (an empty repository counts) | Push the repository that `tripwright init` made, or change `TRIPWRIGHT_BRANCH` |
| `network` | The service cannot reach the Git host | Check the outbound network; GitHub goes over port 443 already |
| `key_file` | The private key or the host key file is missing, or the key is not mode 600 | Give the key again; files owned by 10001, mode 600 |
| `config` | The settings do not match the files: an existing mirror with another repository, or a repository without `tripwright.yaml` | Read the detail on the log line; for a new repository, move the empty mirror aside ([SETUP.md step 2](SETUP.md#step-2-connect-a-private-github-repository)) |
| `protected` | Branch protection refuses the service's push (only a real push shows this, not the start check) | Allow the deploy key to push to the branch, or use another branch |
| `behind`, `commit`, `other` | The remote has new commits and the replay has not succeeded yet; a local commit failed (for example a lock left by a crash; a restart clears it); something else | Read `hint` and the log line |

## Sleep

Docker deployments do not sleep: the service is there while the computer or server runs. Railway has an optional sleep, off by default ([RAILWAY.md](RAILWAY.md#sleep-optional-off-by-default)). Without sleep, the idle service uses almost no CPU and sends nothing: 10 minutes after the last request (`TRIPWRIGHT_ACTIVE_WINDOW`, in seconds) it stops pulling the repository every 45 seconds, and the next request pulls once before it answers.
