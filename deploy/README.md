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

Public-page settings live in each trip's committed `trips/<trip>/share.yaml`, not on this volume. Effective plan commits debounce for 20 seconds, then build, privacy-scan and update only that trip's random subdirectory; `share connect` publishes every existing trip once when it connects. The public root has `robots.txt` and `.nojekyll`, never a trip index. Publication failures do not block plan commits; `tripwright share status` shows categories or a safe Git error, never a found value or connector credential.

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
| `TRIPWRIGHT_REPLY_FLOW` | How a quick question on the road is answered. `two` (default): the assistant sends the first part from the stored plan and ends the reply; the checked part comes after the user's next message, or by itself where the host runs a background agent (Claude Code). `one`: both parts in one reply. In our runs GPT, GLM and Kimi models wrote the first part before the lookups with `one`; Claude often left the first part out, so keep `two` for Claude. |
| `TRIPWRIGHT_SAVE_UNVERIFIED` | What happens when a plan change brings in facts about the outside world that are not verified: opening hours, a timetable, the weather, whether a ticket applies, a fixed time. This is only the default for a user who has not chosen: each user chooses proactive or cautious in the conversation (asked once; recorded as the `save_mode` setting, which overrides this variable). Only a setting from the user's own message counts: a person of the trip as the speaker, and a basis other than the agent's inference; a choice the user made is not replaced by the agent's. The service cannot read what a quoted sentence means, so an agent that labels its own entry as the user's, quoting the user's message, is not caught when the user has not chosen yet. For the same reason, a change to a hard node (a fixed time, such as a booked train) is admitted when the quoted words come from the user's own message in that turn; the service cannot check that they state the new value. In a test host on 2026-11-13, GLM quoted “可以，就按这样存吧。” (yes, save it like this) for a train moved from 16:30 to 09:00, and the change was admitted. `ask` (default, cautious): the service saves the change only after `turn` has received another message, with different words, since the change was proposed; from then on the assistant's acceptance counts. The service cannot check what the assistant told you, or whether that message agrees. `agent` (proactive): the assistant may accept such facts itself in the turn that proposed the change, and tell you afterwards. In both modes, unverified facts that the saved plan already carries do not hold a change back and need no acceptance (before 2026-11-12 they needed the assistant's acceptance every time). The same holds for unverified points about the plan itself: a travel time between two blocks that was not given, a requirement the service has no test for, fares not known yet. The service returns them for the assistant to say and saves without waiting, also when the gap between the two blocks is short. |
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

Upgrade from the source root without changing the data or settings. The new image is built while the old service keeps running; then only the application container is recreated (about 3 s without answers; Caddy keeps serving):

```bash
ssh <server> 'test ! -e /opt/tripwright/app.next && mkdir /opt/tripwright/app.next'
git archive HEAD | ssh <server> 'tar -xf - -C /opt/tripwright/app.next'
ssh <server> 'docker tag tripwright-app:local tripwright-app:before-upgrade && cd /opt/tripwright/app.next/deploy && docker-compose --env-file /opt/tripwright/compose.env build tripwright'
ssh <server> 'cd /opt/tripwright && mv app app.before-$(date +%Y%m%d-%H%M) && mv app.next app && cd app/deploy && docker-compose --env-file /opt/tripwright/compose.env up -d --no-deps --no-build --force-recreate tripwright'
```

Keep the `app.before-*` directory and the `tripwright-app:before-upgrade` image until the new service is ready. The directory gets a new name each time because `mv` into an existing directory would put the old source inside it. Naming the `tripwright` service keeps Caddy running; without it compose recreates Caddy too, and Caddy waits for the new container's health check (up to about 30 s). Roll back with `docker tag tripwright-app:before-upgrade tripwright-app:local`, the source directories swapped back and the same `up` command; never roll back the data automatically.

An installation made before November 2026 kept its settings in `data/remote.yaml` and its repository key in `data/ssh/`. Neither is read now: without the steps below the new service waits (`/readyz` shows `kind: config`) and the connector answers 503. Run them after the `build` command and before swapping the directories. The old service keeps running and does not read what they write; the connector address, the trips and the key that the repository already trusts stay the same:

```bash
ssh <server> 'printf "TRIPWRIGHT_GITHUB_PORT_22=1\nTRIPWRIGHT_PUBLIC_URL=https://<your-domain>\n" >> /opt/tripwright/tripwright.env'
ssh <server> 'docker run --rm --user 10001:10001 --env-file /opt/tripwright/tripwright.env -v /opt/tripwright/data:/data --entrypoint sh tripwright-app:local -c "mkdir -p -m 700 /data/remote/ssh && cp -p /data/ssh/personal_ed25519 /data/remote/ssh/deploy_key && cp -p /data/ssh/personal_ed25519.pub /data/remote/ssh/deploy_key.pub && cp -p /data/ssh/known_hosts /data/remote/ssh/known_hosts && tripwright repo connect <the repo_url from data/remote.yaml>"'
```

`repo connect` must answer “Already connected”; if it answers anything else, stop before the swap: nothing has changed yet. `TRIPWRIGHT_GITHUB_PORT_22=1` stays set: the repository was connected as `git@github.com:…`, and the service now writes GitHub addresses for port 443. Under the other address `repo connect` would read the remote and refuse because both sides have trips, and the old `known_hosts` has no key for the port-443 host. `TRIPWRIGHT_PUBLIC_URL` makes `address` print the public address; the key in it does not change. Keep `TRIPWRIGHT_REMOTE_CONFIG` and `TRIPWRIGHT_TOKENS` in `tripwright.env` while a rollback is possible: the new service ignores them, the old one needs them. A compose file of your own must not set `TRIPWRIGHT_DB`: with it the service would leave the repository and open a single database file, so it stops at start instead. Rolling back to the old source needs no change to the data.

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
