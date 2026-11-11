# Deploy Tripwright: reference

A first deployment belongs in [SETUP.md](SETUP.md), a short runbook for the user's agent. Add clients with [CLIENTS.md](CLIENTS.md). This page is the Docker and configuration reference; [RAILWAY.md](RAILWAY.md) covers Railway-specific settings and costs.

## Data and repository

Run one instance with one persistent volume at `/data`. Tripwright keeps its state under `/data/remote`:

- `url-key`: the generated connector credential, mode 600;
- `tenants/personal/mirror`: the local trip repository;
- `ssh/deploy_key`: the private deploy key, mode 600 (its `.pub` pair is safe to print);
- `repository.json`: the private-trip connection saved by `tripwright repo connect`;
- `share.json`, `share/ssh/`: the public-page connection and its separate deploy key;
- `share/public/`, `share/build/`: disposable public clone and scanned builds;
- the volume marker, routing counter and writes not pushed yet.

Public-page settings live in each trip's committed `trips/<trip>/share.yaml`, not on this volume. Effective plan commits debounce for 20 seconds, then build, privacy-scan and update only that trip's random subdirectory. The public root has `robots.txt` and `.nojekyll`, never a trip index. Publication failures do not block plan commits; `tripwright share status` shows categories or a safe Git error, never a found value or connector credential.

Do not run two instances on one volume. `repo connect` pushes local history to an empty remote, adopts a remote trip when local has none, and refuses to merge two trip histories. It checks another remote the same way; no manual mirror move is needed.

## Settings

No variable is required by the three Docker commands in SETUP. Set a variable only for the corresponding deployment need.

| Variable | Default and purpose |
| --- | --- |
| `TRIPWRIGHT_PUBLIC_URL` | Origin used by `tripwright address`; Railway domain, else `http://localhost:$PORT`. It is not a Host allowlist. |
| `PORT` | `8000`; the HTTP listening port. |
| `TRIPWRIGHT_DATA_DIR` | `/data/remote`; the persistent state directory. |
| `TRIPWRIGHT_URL_KEY_GENERATE` | `1`; set `0` only to disable automatic key generation. |
| `TRIPWRIGHT_URL_KEY` | Optional explicit URL key (at least 32 ASCII letters, digits, `-` or `_`); overrides the generated file. |
| `TRIPWRIGHT_REPO_URL` | Optional repository address; on startup it is equivalent to `repo connect`. The saved connection is used otherwise. |
| `TRIPWRIGHT_BRANCH` | `main`, or the branch saved by `repo connect`. |
| `TRIPWRIGHT_KNOWN_HOSTS` | Optional SSH host keys for a non-GitHub host. GitHub host keys are fetched over HTTPS. |
| `TRIPWRIGHT_DEPLOY_KEY` | Ignored with a warning. Use `tripwright repo key`; existing on-volume keys are retained. |
| `TRIPWRIGHT_GITHUB_PORT_22` | Set `1` to keep GitHub on port 22 instead of the default 443. |
| `TRIPWRIGHT_CHECK_PUSH` | Set `0` to skip the startup `git push --dry-run`. |
| `TRIPWRIGHT_ACTIVE_WINDOW` | `600` seconds of repository polling after the last request. |
| `TRIPWRIGHT_PROVIDERS` | `live`; `synthetic` is only for demonstrations. |
| `TRIPWRIGHT_VOLUME` | `/data`; the volume whose ownership the container entrypoint fixes before dropping to UID 10001. |
| `RAILWAY_RUN_UID` | Set `0` only when a platform prevents that ownership fix and root execution is intentional. |

The image starts as root so its entrypoint can give a named or platform volume to UID 10001, then drops privileges. `/data` already belongs to 10001 in the image, so a deployment that starts with `user: "10001:10001"` and other hardening options needs no manual ownership step.

## Who can connect

Only the complete `/mcp/<key>` address opens MCP. Missing and wrong keys both return 404. Host headers are not compared with `TRIPWRIGHT_PUBLIC_URL`, so localhost and tunnel addresses work together. The key never appears in `/healthz`, `/readyz` or the service log; `tripwright address` is the command that deliberately prints it.

Cloudflare documents no SSE support on Quick Tunnels. Tripwright returns JSON, and an MCP `initialize` returned 200 through a Quick Tunnel in the maintainer's test; their changing addresses still make Funnel or Railway the stable choices.

The address is a password. Paste it only into the user's own AI apps. Replace a leaked key with the one-line rotation command in SETUP, then update each client.

## A server with a domain: Docker and Caddy

`deploy/compose.yaml` and `deploy/Caddyfile` are reserved for this case. Their service names, container names and mounts are stable for existing installations. Caddy publishes ports 80 and 443; the application is only on the private `tripwright-backend` network.

The established layout is:

| Path | Content |
| --- | --- |
| `/opt/tripwright/app/` | one source commit, including the root `Dockerfile` and `.dockerignore` |
| `/opt/tripwright/data/` | persistent data, UID/GID 10001, mode 700 |
| `/opt/tripwright/tripwright.env` | optional application settings, root-owned, mode 600 |
| `/opt/tripwright/compose.env` | `TRIPWRIGHT_DOMAIN=<your-domain>` |
| `/opt/tripwright/caddy-data/`, `/opt/tripwright/caddy-config/` | Caddy state, mode 700 |
| `/opt/tripwright/SOURCE_REVISION` | deployed commit |

First deployment (use `docker compose` where Compose is a plugin):

```bash
ssh <server> 'install -d -m 755 /opt/tripwright /opt/tripwright/app && install -d -m 700 /opt/tripwright/caddy-data /opt/tripwright/caddy-config && install -d -m 700 -o 10001 -g 10001 /opt/tripwright/data'
ssh <server> 'umask 077 && printf "TRIPWRIGHT_PUBLIC_URL=https://%s\n" "<your-domain>" > /opt/tripwright/tripwright.env'
ssh <server> 'echo "TRIPWRIGHT_DOMAIN=<your-domain>" > /opt/tripwright/compose.env'
git archive HEAD | ssh <server> 'tar -xf - -C /opt/tripwright/app'
git rev-parse HEAD | ssh <server> 'tee /opt/tripwright/SOURCE_REVISION'
ssh <server> 'cd /opt/tripwright/app/deploy && docker-compose --env-file /opt/tripwright/compose.env up -d --build'
ssh <server> 'docker exec tripwright-app tripwright address'
```

Wait for `https://<your-domain>/readyz` to show `"ready":true`. The last command prints the full credential; use it only in the user's clients.

Connect a private repository through the application container rather than editing the mirror or placing a private key in `tripwright.env`:

```bash
ssh <server> 'docker exec tripwright-app tripwright repo key' | gh repo deploy-key add - --allow-write -t tripwright -R <owner>/tripwright-trips
ssh <server> 'docker exec tripwright-app tripwright repo connect git@github.com:<owner>/tripwright-trips.git'
```

The running service applies the saved connection on its next request or health check; no restart is needed.

Upgrade from the source root without changing the data or settings:

```bash
ssh <server> 'test ! -e /opt/tripwright/app.next && mkdir /opt/tripwright/app.next'
git archive HEAD | ssh <server> 'tar -xf - -C /opt/tripwright/app.next'
ssh <server> 'mv /opt/tripwright/app /opt/tripwright/app.previous && mv /opt/tripwright/app.next /opt/tripwright/app && cd /opt/tripwright/app/deploy && docker-compose --env-file /opt/tripwright/compose.env up -d --build --force-recreate'
```

Keep `app.previous` until the new service is ready. Roll back by swapping the source directories and rebuilding; never roll back the data automatically.

## Health, readiness and logs

- `GET /healthz` returns 200 while the process runs and keeps `status`, `ready`, and `tenants.personal` in its body.
- `GET /readyz` returns 200 only when the repository is open, its remote answered and no write is blocked. It keeps `ready`, `tenants`, `started_at`, and `volume`; the personal entry includes `status`, `kind`, `hint`, `unsynced`, and `oldest_unsynced_age_seconds`.
- At startup the service opens the personal repository and checks `git ls-remote` and, by default, `git push --dry-run`. Pending writes retry after a restart or recovered connection.
- A write receipt and `state(view="sync")` show `blocked: {kind, since, hint}` when a push is stuck.

| `kind` | Action |
| --- | --- |
| `auth` | Check the address and that the public deploy key has write access; inspect the service log for the actual Git failure. |
| `branch` | Select the branch that exists, or connect an empty remote and let the service push it. |
| `network` | Check outbound access. GitHub uses port 443 by default. |
| `key_file` | Keep `ssh/deploy_key` private and mode 600; use `repo key` rather than a private-key variable. |
| `config` | Check that the remote is empty or a Tripwright repository. `repo connect` protects two nonempty trip histories. |
| `protected` | Let the deploy key push the branch. |
| `behind`, `commit`, `other` | Read `hint` and the log; writes stay pending while replay or local commit recovery continues. |
