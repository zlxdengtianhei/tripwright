# Remote GitHub sign-in and write-back to the user's repository

中文：[REMOTE_AUTH.zh-CN.md](REMOTE_AUTH.zh-CN.md)

> **Not recommended for a personal deployment.** The default and recommended sign-in is the key in the connector address ([SETUP.md](SETUP.md)). This page documents the optional GitHub sign-in (OAuth) and header tokens that the code keeps, off by default.

This page is for an existing Docker and Caddy deployment (see [README.md](README.md#a-server-with-a-domain-docker-and-caddy)). The public entry point is `https://<your-domain>/mcp`. With this mode on, hosts such as Claude sign in with standard OAuth. The existing `TRIPWRIGHT_TOKENS` header tokens can also connect to the same user repository. Inside, the app still uses the same seven tools, the Git outbox, the committer and the operation replay.

Remote mode has been run on the author's own server, and the header-token path has been verified. The GitHub App is still to be created by the user. While the user is present, `deploy/setup_github_app.py` makes a setup link. After the app is registered, OAuth is turned on.

## Configuration and directories

Copy `remote.example.yaml` to `/opt/tripwright/data/remote.yaml` on the server. Fill in the user's GitHub login name and the SSH address of the existing private trip repository. The repository should already hold `tripwright.yaml` and the other Tripwright files. GitHub login names in the configuration are case-insensitive. An account that is not on the list gets no token for this service.

| Setting | Meaning |
| --- | --- |
| `public_url` | Public HTTPS origin, with no path and no query string |
| `data_dir` | Run directory inside the container, for example `/data/remote` |
| `secrets_file` | Dedicated env file inside the container, for example `/data/remote-secrets.env`, permission 600 |
| `oauth` | Whether GitHub OAuth is on. When false, header tokens still work |
| `accounts` | GitHub login name → tenant ID. List only the approved accounts here |
| `tenants.<id>.repo_url` | SSH address of the tenant's private Git repository |
| `tenants.<id>.owner` | Identity in the repository index. For an existing personal repository this is usually `local` |
| `tenants.<id>.branch` | Source-of-truth branch, default `main` |
| `tenants.<id>.ssh_key` | Path of the private key, held only on the server |
| `tenants.<id>.known_hosts` | SSH host key file whose origin has been checked |
| `token_tenants` | Identity from the old token mapping → tenant ID, for example `local: personal` |

Do not put secrets in the YAML. `data_dir/tenants/<id>/mirror/` is a mirror clone, and the working database is `.tripwright/state.sqlite` inside the mirror. Each tenant has its own directory, database and service object. A caller cannot choose the tenant through tool parameters. If the mirror's origin or current branch differs from the configuration, the service refuses to open it. This stops a push to the wrong repository or the wrong branch.

Every `/data/...` path on the server is `/opt/tripwright/data/...` on the host. The existing Compose file already binds the whole data directory, so no extra mount is needed. Remote mode does not import the old single database `/data/tripwright.sqlite3`. It rebuilds the index from the configured user repository.

## The server makes the deploy key; your agent adds the public key to the private repository

Run the first block below on your computer. Replace `<server>` with the SSH alias of your server. The private key stays on the server, and `ssh-keygen -q` does not print it. If a key with the same name exists, the command stops, so it never overwrites.

```bash
ssh <server> 'install -d -m 700 -o 10001 -g 10001 /opt/tripwright/data/ssh && test ! -e /opt/tripwright/data/ssh/personal_ed25519 && ssh-keygen -q -t ed25519 -N "" -C tripwright-personal -f /opt/tripwright/data/ssh/personal_ed25519 && chown 10001:10001 /opt/tripwright/data/ssh/personal_ed25519 /opt/tripwright/data/ssh/personal_ed25519.pub && chmod 600 /opt/tripwright/data/ssh/personal_ed25519'
```

Your agent uses the `gh` that is already signed in on your computer. Replace `OWNER/PRIVATE_REPO` with the real trip repository. Only the public key goes through the pipe:

```bash
ssh <server> 'ssh-keygen -y -f /opt/tripwright/data/ssh/personal_ed25519' \
  | gh repo deploy-key add /dev/stdin --repo OWNER/PRIVATE_REPO --allow-write --title tripwright-server
```

The GitHub host keys come from the official metadata API, over verified HTTPS. After they are saved, SSH uses `StrictHostKeyChecking=yes`, so it does not blindly trust a key that the network returns on the first SSH connection:

```bash
ssh <server> 'python3 -' <<'PY'
import json
import os
import urllib.request
from pathlib import Path
request = urllib.request.Request('https://api.github.com/meta', headers={'User-Agent': 'tripwright-setup'})
with urllib.request.urlopen(request, timeout=15) as response:
    keys = json.load(response)['ssh_keys']
path = Path('/opt/tripwright/data/ssh/known_hosts')
with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as sink:
    # github.com for port 22, [ssh.github.com]:443 for port 443 (where outbound 22 is blocked, e.g. Railway trials)
    sink.write(''.join(host + ' ' + key + '\n' for key in keys for host in ('github.com', '[ssh.github.com]:443')))
os.chown(path, 10001, 10001)
os.chmod(path, 0o600)
PY
```

If you write `repo_url` as `ssh://git@ssh.github.com:443/OWNER/PRIVATE_REPO.git`, Git uses port 443 and does not need outbound port 22. Use this form for new deployments. (`git@github.com:…` uses port 22 and still works.) For an existing mirror, a changed `repo_url` is refused ("different origin"). First move `data_dir/tenants/<id>/mirror` aside, and the service clones it again. Push any writes that have not been pushed before you do this (`unsynced` in `/readyz` is 0).

The key grants write access to this one trip repository only. Changes the user makes on the GitHub website or in another clone are still picked up by the existing 45-second poll. Pushes are never forced. When a push is rejected, the operations are replayed on the new head.

## Turn on the remote configuration

Make the config file readable by the container user, for example `chown 10001:10001 /opt/tripwright/data/remote.yaml` and `chmod 600 ...`. Add one line to the existing `/opt/tripwright/tripwright.env`:

```dotenv
TRIPWRIGHT_REMOTE_CONFIG=/data/remote.yaml
```

Keep the existing `TRIPWRIGHT_TOKENS` as it is, and map its identities in `token_tenants` in the YAML. The `env_file` in Compose passes the config path to the CLI. You do not need to change the Caddyfile or the Compose routes. The new Dockerfile adds `openssh-client` for Git write-back, so you must rebuild the app image:

```bash
ssh <server> 'cd /opt/tripwright/app/deploy && docker-compose up -d --build --force-recreate'
```

The equivalent when you run the CLI directly:

```bash
tripwright serve --http --host 0.0.0.0 --port 8000 --remote-config /data/remote.yaml
```

Without `--remote-config` or `TRIPWRIGHT_REMOTE_CONFIG`, the original local single-database, `--repo` and Bearer behavior stays. stdio uses the original path.

## GitHub App: the user creates it, the credentials go straight to the server

Use the GitHub App manifest flow. The app is only for sign-in. It does not ask for any permission on the trip repository. Trip write-back uses the deploy key above. The app must be created in the user's own GitHub account, and GitHub requires the account owner to confirm in the browser. When the host has a browser tool and the user is already signed in to GitHub and agrees, the agent may open the link and click confirm for the user (see "Who can connect" in [README.md](README.md#who-can-connect)).

When the user is present and ready to create the GitHub App, run this in the project directory on your computer:

```bash
.venv/bin/python -B deploy/setup_github_app.py --url https://<your-domain> --ssh <SSH alias of the server>
```

If there is no SSH, put one header token in the environment variable `TRIPWRIGHT_CHECK_TOKEN` and run the command without `--ssh`.

The program uses an existing token to make a setup link that is valid for ten minutes and is void after one use. It prints only this link. Your agent gives the link to the user. When the user opens it, the page goes to GitHub by itself, and the user clicks "Create GitHub App" there. If the browser has scripts disabled, the page keeps a "Continue to GitHub" button. The user can change the display name. The login name should match an account in the YAML list.

GitHub calls back to `/setup/github-app/callback`. The server checks the one-time state, the HttpOnly cookie from the same browser and the account that owns the App, and then exchanges them with GitHub for the credentials. The client ID, client secret, App ID, PEM private key and webhook secret are written straight to `/opt/tripwright/data/remote-secrets.env`, saved atomically with permission 600. The page only shows that setup is done. The credentials never appear on the page, in chat, in command arguments or in the source. The dedicated env file is in the writable data volume and is owned by container UID 10001. The container does not need to change the outer env file, which root owns.

After a successful save, OAuth turns on at the next request. You do not need to restart the container. When the setup link has expired, make a new one. When app credentials already exist, registration is no longer open. On failure the page shows only a status, never GitHub's credential response.

Default callback address: `https://<your-domain>/auth/callback`. Keep the default expiry and refresh behavior of the GitHub App user token. The access token that the proxy issues is valid for one hour. The refresh token lasts at most thirty days and rotates on each refresh. The validity of the upstream GitHub token is still a limit.

Creating an OAuth App by hand is the fallback. The user creates it in GitHub Developer settings → OAuth Apps. For Homepage, enter the public origin. For Authorization callback URL, enter the `/auth/callback` address above. The user fills in `TRIPWRIGHT_GITHUB_CLIENT_ID` and `TRIPWRIGHT_GITHUB_CLIENT_SECRET` in the server's dedicated env file with permission 600, owned by 10001:10001. Do not send the secret in chat. A plain OAuth App often gives no refresh token. In that case the user must sign in again after the access token expires, one hour later. Prefer a GitHub App created with the manifest.

The optional `TRIPWRIGHT_OAUTH_SIGNING_KEY` is likewise read only from the process environment or the dedicated 600 env file. When it is not set, the key is derived from the upstream client secret. The OAuth clients, authorization codes, upstream tokens and refresh state are kept in `/data/remote/oauth/`, encrypted with Fernet, so that a container restart does not invalidate every connection. Only one app process writes this state. Rotating the signing material invalidates old connections, and users must sign in again.

## Public paths and checks

The existing Caddy `handle { reverse_proxy tripwright:8000 }` already covers the paths below. All of them must go to the app, with Authorization, Cookie and Set-Cookie kept:

- `/mcp`. An unauthenticated request gets 401, and `WWW-Authenticate` points to the resource metadata.
- `/.well-known/oauth-protected-resource/mcp` and `/.well-known/oauth-authorization-server`.
- `/register`, `/authorize`, `/consent`, `/token` and `/auth/callback`.
- `/setup/github-app/session`, `/setup/github-app/callback` and `/setup/github-app/<one-time ID>`.

Do not turn on access logs: they would record authorization codes, temporary links or cookies. The health endpoints are `/healthz` (alive) and `/readyz` (ready: the tenant is open, the remote is reachable, no write is stuck). See the README.

After deployment you can run:

```bash
curl --fail --silent --show-error https://<your-domain>/.well-known/oauth-authorization-server
curl --fail --silent --show-error https://<your-domain>/.well-known/oauth-protected-resource/mcp
.venv/bin/python -B deploy/check_https.py --url https://<your-domain> --ssh <SSH alias of the server>
```

The discovery documents should contain `S256`, `registration_endpoint`, `client_id_metadata_document_supported=true`, `none` in `token_endpoint_auth_methods_supported`, and `authorization_response_iss_parameter_supported=true`. The resource is the full `/mcp` address. The issuer, `authorization_servers` and the `iss` in the callback use the same public origin. Reusing a refresh token returns `invalid_grant`.

In Claude on the web, add the full `/mcp` address under Connectors and finish the GitHub sign-in. The same connector works on the phone. Claude Code can use the same connector, or you can add an HTTP MCP server yourself and use OAuth. Hosts that send a fixed header can keep using tokens. The ChatGPT connector can use the same OAuth, but its read and write tools and phone support depend on the plan and the host. The OAuth flow has been self-tested against a fake GitHub upstream. The author's own instance has been checked with a header token over HTTPS and the official MCP SDK: the seven tools and the read-only `state`. The GitHub App is still to be created by the user. Real GitHub OAuth, and the Claude, phone and ChatGPT connections, are still to be verified.
