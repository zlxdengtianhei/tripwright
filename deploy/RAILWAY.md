# Deploy Tripwright on Railway

Use Railway when no computer stays on. The agent's short CLI runbook is [SETUP.md](SETUP.md); this page is the dashboard and settings reference. With an always-on computer, [Docker](README.md) is simpler.

## Required pieces

- One service built from the repository's **root `Dockerfile`**, detected automatically.
- One persistent volume mounted at **`/data`**.
- One public domain. `RAILWAY_PUBLIC_DOMAIN` supplies the default origin to `tripwright address`; `PORT` supplies the listening port.
- Healthcheck path **`/healthz`**.

No Dockerfile-path variable, URL-key switch, application configuration file or manual data-directory ownership step is needed. The container gives the volume to UID 10001 and then drops to that user.

Only the user creates the Railway account, confirms `railway login`, verifies or pays for an account, and clicks **Add** in chat apps. Never accept payment or account terms for them.

## Dashboard sequence

1. **New Project → Deploy from GitHub repo** and select Tripwright or the user's fork.
2. **Attach Volume** with mount path `/data`.
3. **Settings → Networking → Generate Domain**.
4. Set **Settings → Deploy → Healthcheck Path** to `/healthz`.
5. Deploy and wait for `https://<your-domain>/readyz` to show `"ready":true`.
6. Run `railway ssh -- tripwright address` and add the printed address with [CLIENTS.md](CLIENTS.md).

The CLI equivalent is:

```bash
railway init --name tripwright
railway add --service tripwright
railway volume add --mount-path /data
railway domain
railway up --detach
railway ssh -- tripwright address
```

The URL key is generated on first start and stored at `/data/remote/url-key`. The address is the only login credential. Missing and wrong keys return 404; arbitrary Host headers and a changed public domain do not require a restart.

## Optional private repository

Create an empty private repository and let the service generate its key:

```bash
gh repo create <owner>/tripwright-trips --private
railway ssh -- tripwright repo key | gh repo deploy-key add - --allow-write -t tripwright -R <owner>/tripwright-trips
railway ssh -- tripwright repo connect git@github.com:<owner>/tripwright-trips.git
```

`repo connect` preserves existing local trips when pushing an empty remote and adopts a remote trip for a fresh service. The running service applies either on its next request or health check, with no redeploy. It refuses to merge when both sides have trips. GitHub SSH uses port 443 and host keys fetched over verified HTTPS. Without GitHub, trips stay in the local Git repository on the volume and the user makes backups.

## Variables

Redeploy after changing a variable. All application variables are optional.

| Variable | Purpose |
| --- | --- |
| `TRIPWRIGHT_PUBLIC_URL` | Overrides the Railway domain only as the default origin printed by `tripwright address`; it does not restrict Host headers. |
| `TRIPWRIGHT_DATA_DIR` | Defaults to `/data/remote`. |
| `TRIPWRIGHT_URL_KEY` | Explicit key, overriding the generated file. Minimum 32 ASCII letters, digits, `-` or `_`. |
| `TRIPWRIGHT_URL_KEY_GENERATE` | Defaults to `1`; set `0` only to disable generation. |
| `TRIPWRIGHT_REPO_URL`, `TRIPWRIGHT_BRANCH` | Legacy/Railway-friendly alternative to `repo connect`; the connection is checked and saved on startup. |
| `TRIPWRIGHT_KNOWN_HOSTS` | SSH host keys for a host other than GitHub. |
| `TRIPWRIGHT_DEPLOY_KEY` | Ignored with a warning. Use `repo key`; an existing key file on the volume remains valid. |
| `TRIPWRIGHT_GITHUB_PORT_22` | Set `1` to use port 22 instead of 443. |
| `TRIPWRIGHT_ACTIVE_WINDOW` | Seconds of repository polling after the last request; default 600. |
| `TRIPWRIGHT_CHECK_PUSH` | Set `0` to skip startup's write probe. |
| `RAILWAY_RUN_UID` | Set `0` only when the volume ownership fix is impossible and root execution is intentional. |
| `TRIPWRIGHT_PROVIDERS` | Leave unset for live sources; `synthetic` is demonstration data. |

Avoid printing all variables or a container inspection in shared output. They are configuration, not a place for the deploy private key.

## Sleep and cost

Serverless/sleep is **off by default**. Without it, an idle service sends no repository traffic after the activity window and uses little CPU. Ask the user before enabling sleep and explain: after a long pause, Railway can answer 502 to the first waking request, which must be retried.

[Current pricing](https://railway.com/pricing) and account limits are authoritative. A limited trial can restrict outbound transport and GitHub requests, and trial volumes can be deleted after the credit period. Verification, plan upgrades and payment belong to the user.

A previous measurement (2026-11-02, one idle personal service with a local repository and no sleep) used about 90 MB memory and 0.0021 vCPU, approximately $0.95 for a month at that date's prices. It is not a current-price guarantee. Real lookups add usage; heavy use costs more.

## Troubleshooting

- **Build or healthcheck fails:** read Deploy Logs; first verify the `/data` volume and root Dockerfile build.
- **`/readyz` is not ready:** read `kind` and `hint` under `tenants.personal`. `auth` means address/key/host-key trouble; `network` means outbound access; `branch` means the selected branch is missing; `config` means an incompatible repository.
- **`repo connect` reports two trip histories:** it changed neither side. Choose an empty remote for local history or a fresh data volume for the remote history; do not merge by hand.
- **A write is `blocked`:** it remains on the volume and retries after the deploy key, branch protection or network problem is fixed.
- **The log says the data directory is new and empty after the first start:** the volume was not attached or was replaced. Restore the backup before making more writes.
- **Back up without GitHub:** `railway ssh -- tar czf - -C /data remote > tripwright-backup.tgz`. The backup includes trips and the URL key; protect both.
