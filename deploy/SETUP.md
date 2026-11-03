# Set up Tripwright: a guide for your agent

This page is for the agent that sets up Tripwright for a user: a coding agent such as Claude Code or Codex that the user asked to "set up tripwright". A person can follow it too. At the end the user has:

- their own Tripwright service, on Railway or in Docker, used only by them;
- their trips in a private GitHub repository;
- one connector address, `https://<your-domain>/mcp/<key>`, added to each AI client they use.

Do the steps in order. The agent does every step except the ones listed in [What only the user can do](#what-only-the-user-can-do). Run each code block as one shell command: some blocks set a variable that a later line in the same block uses. Paths are fixed (`~/tripwright` for the source, `~/tripwright-trips` for the trips), so a later command in a new shell still finds them. If `~/tripwright` already exists, run `git -C ~/tripwright pull` instead of cloning.

Three rules apply throughout:

- **The connector address is the only key.** Anyone who has it can read and change the user's trips. The service makes the key itself. Keep it out of chat, logs, issues, screenshots, repositories and the text of the commands you type; the commands on this page read it from a file or the clipboard instead. The one exception: when the user must paste it by hand and neither a clipboard nor a file can reach them, write it once in your reply and tell them not to forward it ([step 3](#step-3-add-the-address-to-each-client)). The service's own log never shows the key, but a platform's request log (such as Railway's HTTP logs) shows request paths, which contain it: do not paste those anywhere. Treat the deploy key's private key file the same way.
- **Report errors as the service states them.** When `/readyz` is not ready, give the user its `kind` and `hint` word for word, then fix it with [Troubleshooting](#troubleshooting). Do not guess.
- **Add no other sign-in.** The key in the address is the one gate. Do not set up GitHub sign-in, header tokens or other permission settings.

## The three steps

| Step | Who | Done when |
| --- | --- | --- |
| [1. Start the service](#step-1-start-the-service) on Railway or Docker | Agent. The user creates the Railway account or installs Docker. | `/readyz` returns `"ready":true` |
| [2. Connect a private GitHub repository](#step-2-connect-a-private-github-repository) | Agent. The user runs `gh auth login` if `gh` is not signed in. | `/readyz` returns `"ready":true` and `"storage":"remote"` |
| [3. Add the address to each client](#step-3-add-the-address-to-each-client) | Agent for coding clients; the user clicks **Add** in Claude and ChatGPT. | The client lists the Tripwright tools and answers a first question with them |

### Before step 1: ask the user two questions

1. "Which AI apps will you use with Tripwright?"
2. "Do you have a computer that stays on, with Docker installed or allowed?"

Then choose the path:

- **Claude (web, desktop or mobile), ChatGPT, or any phone app:** these apps connect from their providers' cloud, not from the user's computer, so the service needs a public HTTPS address. Use **Railway**. With a computer that stays on, **Docker with a tunnel** also works.
- **Only coding clients on the computer that runs the service** (Claude Code, Codex, Cursor and similar): **Docker** on `http://localhost:8000` is enough. Nothing is public.
- **No GitHub account, or the user does not want one:** skip step 2. The trips then stay in a local Git repository on the service's volume, and the user backs it up.

### What only the user can do

Ask the user to do these, and wait until they say they are done. Do not type passwords or payment details for them.

- **Railway:** create an account at [railway.com](https://railway.com); confirm the browser page that `railway login` opens.
- **Docker:** install Docker Desktop (macOS, Windows) or Docker Engine (Linux). On Linux, run `sudo chown 10001:10001 ~/tripwright/deploy/data` (step 1), which needs their password.
- **GitHub:** run `gh auth login` if `gh auth status` says they are not signed in.
- **In Claude, ChatGPT and other chat apps:** click **Add** or **Create** for the connector ([CLIENTS.md](CLIENTS.md)). The agent prepares the address.
- **Money:** a card, a plan upgrade, or Railway account verification.

## Step 1. Start the service

### Railway

Before you start: the [Railway CLI](https://docs.railway.com/cli) is installed and `railway login` is done. If a `railway` command asks a question, such as which workspace to use, ask the user.

```bash
git clone https://github.com/zlxdengtianhei/tripwright.git ~/tripwright && cd ~/tripwright
railway init --name tripwright           # also links ~/tripwright to the new project
railway add --service tripwright
railway variables --set RAILWAY_DOCKERFILE_PATH=deploy/Dockerfile \
                  --set TRIPWRIGHT_URL_KEY_GENERATE=1 --skip-deploys
railway volume add --mount-path /data    # without the volume, a restart loses the trips and the key
railway domain                           # prints <your-domain>, e.g. tripwright-production-xxxx.up.railway.app
railway up --detach
```

This sequence ran on a real Railway account on 2026-11-02. The service was up in about one minute.

**Check:** run `curl -s https://<your-domain>/readyz` every 10 seconds until it shows `"ready":true` (usually within a minute).

- `TRIPWRIGHT_URL_KEY_GENERATE=1` is a switch, not a key. The service makes the key at its first start and keeps it in `/data/remote/url-key`. Do not make a key yourself and set it with `railway variables`.
- Do not set `TRIPWRIGHT_PUBLIC_URL` on Railway: the service takes its address from the Railway domain.
- Never run `railway variables` without arguments: it prints every value, including the deploy key from step 2.
- **Cost.** A new account starts with a one-time trial credit of $5 for 30 days, without a card; after that, the Free plan gives $1 a month ([pricing](https://railway.com/pricing), [free trial](https://docs.railway.com/pricing/free-trial)). Idle, Tripwright used about $0.95 a month on Railway (measured 2026-11-02), so $1 lasts about 31 days; a cautious estimate for heavy use is about 17 days ([RAILWAY.md](RAILWAY.md#how-long-the-free-credit-lasts)).
- **Unverified accounts.** A "limited trial" restricts outbound network access, so transport queries and GitHub pushes can fail. Volumes of trial accounts are deleted 30 days after the credit runs out. Both are reasons to do step 2. Verification and upgrades are for the user.
- **Sleep** (Railway's Serverless setting) is off by default. Turn it on only if the user agrees after you say: "It saves credit, but after a long pause the first call can fail and must be repeated." See [RAILWAY.md](RAILWAY.md#sleep-optional-off-by-default).

### Docker (a computer that stays on)

```bash
git clone https://github.com/zlxdengtianhei/tripwright.git ~/tripwright && cd ~/tripwright/deploy
mkdir -p data
cp .env.example .env       # sets TRIPWRIGHT_PUBLIC_URL=http://localhost:8000 and TRIPWRIGHT_URL_KEY_GENERATE=1
```

On Linux only, stop here and ask the user to run `sudo chown 10001:10001 ~/tripwright/deploy/data` (the container runs as UID 10001). Then:

```bash
cd ~/tripwright/deploy && docker compose -f compose.personal.yaml up -d --build
```

Use `docker-compose` instead of `docker compose` where Docker ships Compose as a separate command. All later Docker commands on this page run in `~/tripwright/deploy`.

**Check:** `curl -s http://localhost:8000/readyz` shows `"ready":true`.

**Another port** (when 8000 is taken): set `TRIPWRIGHT_PORT=<port>` in `.env`, change `TRIPWRIGHT_PUBLIC_URL` to `http://localhost:<port>`, and run `docker compose -f compose.personal.yaml up -d`. Without the second change, the check in step 3 returns `421`.

The service listens only on this computer, at `http://localhost:8000`. Write the address as `TRIPWRIGHT_PUBLIC_URL` has it, with `localhost`, not `127.0.0.1`: the service accepts only that host and port. That is enough for coding clients on this computer. Claude, ChatGPT and phone apps need a public HTTPS address from a tunnel:

- **Cloudflare Quick Tunnel:** `cloudflared tunnel --url http://localhost:8000`. No account. The address changes at every start, and each change means updating the setting below and the connectors ([docs](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)).
- **Tailscale Funnel:** `tailscale funnel 8000`. Needs a Tailscale account; the address stays the same ([docs](https://tailscale.com/docs/features/tailscale-funnel)).
- **Cloudflare named tunnel:** needs a Cloudflare account and a domain on Cloudflare; the address stays the same ([docs](https://developers.cloudflare.com/tunnel/get-started/)).

Put the tunnel's `https://…` address, without a path, in `.env` as `TRIPWRIGHT_PUBLIC_URL`, then run `docker compose -f compose.personal.yaml up -d`. The service accepts only the address in `TRIPWRIGHT_PUBLIC_URL`, so every client on this computer must then use the tunnel address too. The tunnel provider can see the requests, including the key in the address. The service is gone while the computer sleeps or Docker is stopped.

## Step 2. Connect a private GitHub repository

The service writes each change to the repository with a deploy key: an SSH key that GitHub accepts for this one repository only. Run these on the user's computer with `gh` signed in. The trip repository and the key stay outside `~/tripwright`, so that `railway up` and Docker builds never upload them.

Fill in three values first: `<owner>` is the output of `gh api user -q .login`; `<repo>` is a name the user agrees to (suggest `tripwright-trips`); the time zone is the user's home zone as an IANA name, such as `Europe/Vienna` (ask, or read the computer's setting).

```bash
cd ~/tripwright
gh repo create <owner>/<repo> --private        # an empty repository: no README, no licence
uv run tripwright init ~/tripwright-trips --timezone <IANA zone> \
    --remote https://github.com/<owner>/<repo>.git   # a local copy; later you open coding clients here
git -C ~/tripwright-trips push -u origin main
rm -rf ~/.tripwright-key-tmp && mkdir -m 700 ~/.tripwright-key-tmp
ssh-keygen -t ed25519 -N "" -C tripwright -f ~/.tripwright-key-tmp/deploy-key
gh repo deploy-key add ~/.tripwright-key-tmp/deploy-key.pub --allow-write -t tripwright -R <owner>/<repo>
```

`uv run` needs [uv](https://docs.astral.sh/uv/). If `git push` asks for a password, run `gh auth setup-git` once, which makes Git use the `gh` sign-in. The service can start only from the repository that `tripwright init` made: a repository with only a README gives `kind` `config`, and one with no commit gives `kind` `branch`.

**Give the key to the service, then restart it.** The service from step 1 already made an empty local copy of the trips (its "mirror"), and it refuses to point that copy at a new repository (`kind` `config`). So move the mirror aside. First check that it is still empty: `railway ssh -- ls /data/remote/tenants/personal/mirror` (Docker: `docker compose -f compose.personal.yaml exec -T tripwright ls /data/remote/tenants/personal/mirror`) lists only `AGENTS.md`, `CLAUDE.md` and `tripwright.yaml`. If it also lists `trips` or `profile`, the service already holds trips: do not move it, stop and tell the user. (If you do step 2 before the service's first start, there is no mirror yet; skip the move.)

Railway, with the service running:

```bash
cd ~/tripwright
railway variables --set "TRIPWRIGHT_REPO_URL=git@github.com:<owner>/<repo>.git" \
                  --set "TRIPWRIGHT_DEPLOY_KEY=$(cat ~/.tripwright-key-tmp/deploy-key)" --skip-deploys
railway ssh -- mv /data/remote/tenants/personal/mirror /data/remote/tenants/personal/mirror.unused
railway up --detach
```

Docker, with the container running:

```bash
cd ~/tripwright/deploy
{ echo "TRIPWRIGHT_REPO_URL=git@github.com:<owner>/<repo>.git"
  printf 'TRIPWRIGHT_DEPLOY_KEY="%s"\n' "$(awk 'NR>1{printf "\\n"} {printf "%s", $0}' ~/.tripwright-key-tmp/deploy-key)"; } >> .env
docker compose -f compose.personal.yaml exec -T tripwright mv /data/remote/tenants/personal/mirror /data/remote/tenants/personal/mirror.unused
docker compose -f compose.personal.yaml up -d
```

`$(cat …)` and `awk` put the private key into the setting without showing it in your command text; the service adds back the final line break. It stores the key on the volume with mode 600 and sends GitHub traffic over port 443. After the check below passes, delete the local copy: `rm -rf ~/.tripwright-key-tmp`.

**Check:**

- `/readyz` shows `"ready":true` and `"storage":"remote"`. At start the service runs `git ls-remote` and a `git push --dry-run`, so this also proves that the key can write.
- `gh repo view <owner>/<repo> --json visibility -q .visibility` prints `PRIVATE`.
- `gh repo deploy-key list -R <owner>/<repo>` lists `tripwright` with write access.

If the user does not want to sign in to `gh`, they do the two GitHub parts on github.com themselves: create the empty private repository, and add the content of `~/.tripwright-key-tmp/deploy-key.pub` (the public key only) under the repository's **Settings → Deploy keys → Add deploy key**, with **Allow write access** ticked. Other Git hosts (GitLab, Gitea) work the same way with an SSH address; they also need `TRIPWRIGHT_KNOWN_HOSTS` ([RAILWAY.md](RAILWAY.md#variables)).

**Without GitHub,** the trips stay on the volume and the user backs them up: on Docker, copy `~/tripwright/deploy/data`; on Railway, `railway ssh -- tar czf - -C /data remote > tripwright-backup.tgz`. The backup contains the key, so keep it like the key.

## Step 3. Add the address to each client

**Put the address on the clipboard.** Do not print it. On macOS, with Railway:

```bash
cd ~/tripwright
key=$(railway ssh -- cat /data/remote/url-key | tr -d '\r\n')
printf 'https://%s/mcp/%s' "<your-domain>" "$key" | pbcopy && unset key && echo "copied"
```

With Docker; the address starts with `TRIPWRIGHT_PUBLIC_URL` from `.env`:

```bash
cd ~/tripwright/deploy
key=$(docker compose -f compose.personal.yaml exec -T tripwright cat /data/remote/url-key | tr -d '\r\n')
printf '%s/mcp/%s' "$(grep '^TRIPWRIGHT_PUBLIC_URL=' .env | cut -d= -f2-)" "$key" | pbcopy && unset key && echo "copied"
```

On Linux, use `wl-copy` or `xclip -selection clipboard` instead of `pbcopy`, and `wl-paste` or `xclip -o -selection clipboard` wherever this guide uses `pbpaste`.

**Without a clipboard** (for example on a server over SSH), write the address to a file that only this account can read, and use `cat ~/.tripwright-url` wherever this guide and CLIENTS.md use `pbpaste`. With Docker:

```bash
cd ~/tripwright/deploy
key=$(docker compose -f compose.personal.yaml exec -T tripwright cat /data/remote/url-key | tr -d '\r\n')
(umask 077; printf '%s/mcp/%s' "$(grep '^TRIPWRIGHT_PUBLIC_URL=' .env | cut -d= -f2-)" "$key" > ~/.tripwright-url) && unset key && echo "saved"
```

On Railway:

```bash
cd ~/tripwright
key=$(railway ssh -- cat /data/remote/url-key | tr -d '\r\n')
(umask 077; printf 'https://%s/mcp/%s' "<your-domain>" "$key" > ~/.tripwright-url) && unset key && echo "saved"
```

Delete the file with `rm ~/.tripwright-url` after the last client is set up.

**Check the address before you hand it out:**

```bash
printf 'url = "%s"\n' "$(pbpaste)" | curl -s -o /dev/null -w '%{http_code}\n' -K - \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"setup-check","version":"0"}}}'
```

`200` means the address works. `404` means a wrong key. `421` means the host or port in the address differs from `TRIPWRIGHT_PUBLIC_URL` (for example `127.0.0.1` instead of `localhost`, another port, or the local address while a tunnel is set). The user may copy something else in the meantime, so run this check again before you add the address to each further client; copy the address again if it fails.

**Add it to each client** with [CLIENTS.md](CLIENTS.md). Each client has a section of the same shape: what it needs, what you do, what the user does, how to check. In coding clients, name the server `tripwright`.

Only when the user must paste the address by hand (Claude, ChatGPT) and neither the clipboard nor the file can reach them, for example because you run inside a chat app, write the full address once in your reply, and tell the user not to forward it.

**Tell the user, in these words or similar:** "The connector address is the key to your trips. Anyone who has it can read and change them. Paste it only into your own AI apps."

## Later

- **New key** (for example after a leak): Railway, in `~/tripwright`: `railway ssh -- rm /data/remote/url-key`, then `railway redeploy` or **Restart** in the dashboard. Docker, in `~/tripwright/deploy`: `docker compose -f compose.personal.yaml exec -T tripwright rm /data/remote/url-key`, then `docker compose -f compose.personal.yaml restart`. Then copy the new address (step 3), and in each client replace the old address (remove the connector and add it again, or edit its address where the client allows it).
- **Update Tripwright:** `git -C ~/tripwright pull`, then `railway up --detach` in `~/tripwright`, or `docker compose -f compose.personal.yaml up -d --build` in `~/tripwright/deploy`. The data stays.
- **Share page for friends (optional):** after `git -C ~/tripwright-trips pull`, `uv run tripwright --repo ~/tripwright-trips share publish --create` publishes the plan as web pages in a separate public GitHub repository, because GitHub Free serves Pages only from public repositories ([GitHub docs](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)). The program leaves out decisions, memory, raw messages, evidence, fares and bookings, replaces names with codes, and does not push a version in which its scan finds an e-mail address, a booking number, a flight number, a personal phone number, a listed name or a lodging name. Ask the user before you create the public repository, and ask them to look at the preview once (`uv run tripwright --repo ~/tripwright-trips share build --out ./share-preview`): the scan does not catch names it does not know. After each plan change, `share publish` updates the pages; exit code 2 means the scan blocked that version. Run `uv run tripwright share --help` for the options.

## Troubleshooting

| What you see | Meaning | What to do |
| --- | --- | --- |
| `/readyz` `kind` `auth` | GitHub refused the key, or the repository address is wrong | Check `TRIPWRIGHT_REPO_URL`; check that the deploy key is on this repository with write access. Then read the detail on the service's log line (`docker compose -f compose.personal.yaml logs --tail 50 tripwright`, or Railway's Deploy Logs): `hint` speaks of SSH keys even when the cause is another, such as a repository path that does not exist |
| `kind` `branch` | The repository has no commit | Push the `tripwright init` repository (step 2) |
| `kind` `config` | The settings do not match the files, e.g. an old mirror or a repository without `tripwright.yaml` | Read the detail line in the service log; for a changed repository, see the mirror move in step 2 |
| `kind` `network` | The service cannot reach GitHub | Check the outbound network; on a limited Railway trial, the user must verify the account |
| `kind` `protected` | Branch protection refuses the service's push | Allow the deploy key to push to `main`, or remove the protection |
| `kind` `key_file` | Deploy key missing or not mode 600 | Make a new deploy key and give it to the service (the key part of step 2), then restart |
| `blocked` in a write receipt or `state(view="sync")` | The write is saved on the service but not yet pushed | Fix what `hint` says; the service pushes on its own afterwards |
| Deploy fails on Railway | Usually no volume, or `RAILWAY_DOCKERFILE_PATH` missing | Read the Deploy Logs in the dashboard |
| A log line `data directory … is new and empty` after the first start | The volume is not persistent; unpushed writes are lost | Attach the volume at `/data` (Railway) or keep `~/tripwright/deploy/data` (Docker) |

More detail: [README.md](README.md#troubleshooting) (Docker), [RAILWAY.md](RAILWAY.md) (Railway, all settings).

## When you finish, tell the user three things

1. Where the connector address is: in their clipboard, or in your reply if there was no other way (and `~/.tripwright-url` is deleted). It is the key, and the "Later" section says how to replace it.
2. Where the trips are: the private GitHub repository `<owner>/<repo>`, or only on the volume with backups by the user.
3. On Railway: about how long the free credit lasts, and that sleep is off (or on, if they chose it).
