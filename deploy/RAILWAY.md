# Deploy Tripwright on Railway

Use Railway when the user has no computer that stays on. Railway runs one container with a persistent volume, and the settings are variables: no configuration file. An agent doing a first deployment follows [SETUP.md](SETUP.md), which has the tested CLI sequence. This page is the reference: the dashboard way, every variable, sleep, cost and troubleshooting. With a computer that stays on, Docker is simpler ([README.md](README.md)).

## Summary

- **Attach a volume at `/data` and run one instance.** Do not deploy without the volume: a restart would lose unpushed writes and the key.
- **Sleep is off by default.** Idle, the service used about $0.95 a month on Railway, so the Free plan's $1 a month lasts about a month ([measurements below](#how-long-the-free-credit-lasts)).
- **Git pushes go over port 443** (`ssh.github.com:443`), not 22. The service fetches GitHub's SSH host keys at start from GitHub's API over verified HTTPS; it does not trust the first SSH contact.
- **The connector address with its key is the only sign-in.** Set only `TRIPWRIGHT_URL_KEY_GENERATE=1`; the service makes the key.
- **GitHub is optional.** Without a repository, the trips are a local Git repository on the volume.
- **Trial accounts** ([free trial](https://docs.railway.com/pricing/free-trial)): an unverified account gets a "limited trial" with restricted outbound network access, so transport lookups and GitHub pushes can fail. Linking a GitHub account at sign-up can verify it. Volumes of trial accounts are deleted 30 days after the credit runs out, so upgrade or store the trips on GitHub.

## Only the user can do

- Create a Railway account (signing in with GitHub works). A new account may get only the limited trial.
- Add a card or upgrade the plan (Free: $1 credit a month; Hobby: $5 a month).
- For a private GitHub repository: sign in to `gh`, or add the deploy key on GitHub themselves ([SETUP.md step 2](SETUP.md#step-2-connect-a-private-github-repository)).

## Deploy from the dashboard

The agent's way is the CLI sequence in [SETUP.md step 1](SETUP.md#railway). The same in the dashboard:

1. **Create the service.** New Project → Deploy from GitHub repo, and choose the Tripwright repository or the user's fork. In the service's Variables, add `RAILWAY_DOCKERFILE_PATH=deploy/Dockerfile`.
2. **Attach a volume.** Right-click the service → Attach Volume, mount path `/data`.
3. **Public domain.** Settings → Networking → Generate Domain. The service reads its address from `RAILWAY_PUBLIC_DOMAIN`, which Railway sets, and listens on Railway's `PORT`.
4. **Health check.** Settings → Deploy → Healthcheck Path: `/healthz`. Railway's probe comes from `healthcheck.railway.app`, which the service accepts.
5. **Variables** ([below](#variables)). The minimum is `TRIPWRIGHT_URL_KEY_GENERATE=1`. It is a switch, not a key, so it is safe to show.
6. **Deploy,** then open `https://<your-domain>/readyz`. It is done when it shows `"ready":true`; otherwise `kind` and `hint` give the reason ([troubleshooting](#troubleshooting)).
7. **Read the key and add the connector** as [SETUP.md step 3](SETUP.md#step-3-add-the-address-to-each-client) describes. The deploy log has the line `generated a URL key in /data/remote/url-key`, which gives only the location; neither the log nor `/healthz` and `/readyz` show the key.

Never run `railway variables` without arguments: it prints every value, including the deploy key.

## Variables

Changes take effect after a redeploy (`railway up --detach`, or Redeploy in the dashboard).

| Variable | Needed | What it does |
| --- | --- | --- |
| `RAILWAY_DOCKERFILE_PATH` | yes | `deploy/Dockerfile` (the Dockerfile is not at the repository root) |
| `TRIPWRIGHT_URL_KEY_GENERATE` | yes, unless you set the next one | `1`: at its first start the service makes a random 43-character key and writes it to `/data/remote/url-key` (mode 600); later starts read it. New key: `railway ssh -- rm /data/remote/url-key`, restart, read the new key, and replace the address in each client |
| `TRIPWRIGHT_URL_KEY` | no | A key you choose: at least 32 characters, only letters, digits, `-` and `_`. `${{ secret(48) }}` can generate one; the service refuses to start and says why when the value does not qualify. It wins over the generated file, and the log says so |
| `TRIPWRIGHT_REPO_URL` | no | The trip repository. For GitHub, `git@github.com:OWNER/REPO.git` or the web address; the service switches to `ssh.github.com:443`. Without it, the trips are a local Git repository on the volume and the user backs it up. If the service already ran without it, move the empty mirror first ([SETUP.md step 2](SETUP.md#step-2-connect-a-private-github-repository)) |
| `TRIPWRIGHT_DEPLOY_KEY` | with a repository | The deploy key's private key file, all lines. Where a form keeps one line, write each line break as `\n`. The service writes it to the volume with mode 600 |
| `TRIPWRIGHT_BRANCH` | no | The branch, `main` by default |
| `TRIPWRIGHT_KNOWN_HOSTS` | no | SSH host keys of a host other than GitHub (GitLab, Gitea). For GitHub, the service fetches them |
| `TRIPWRIGHT_PUBLIC_URL` | no | Only for a custom domain: `https://<your-domain>`. Without it, the service uses the Railway domain |
| `TRIPWRIGHT_ACTIVE_WINDOW` | no | Seconds after the last request during which the service pulls the repository every 45 seconds; 600 by default. After that it sends nothing |
| `TRIPWRIGHT_CHECK_PUSH` | no | At start, `git push --dry-run` checks that the key can write; on by default, `0` turns it off |
| `RAILWAY_RUN_UID` | no | Set `0` only when the log says "cannot give /data to UID 10001"; the service then runs as root. Normally the entrypoint gives the volume to UID 10001 as root and then drops to that user |
| `TRIPWRIGHT_PROVIDERS` | do not set | Real transport data by default; `synthetic` is for demonstrations only |

The code also has GitHub sign-in (`TRIPWRIGHT_GITHUB_LOGINS` and related variables) and header tokens (`TRIPWRIGHT_TOKENS`). Both are off by default, and we do not recommend them for a personal deployment: the key in the address is the one gate. [REMOTE_AUTH.md](REMOTE_AUTH.md) and the source describe them.

## Sleep (optional, off by default)

- Without sleep, nothing needs setting. Idle, the service sends nothing and uses almost no CPU.
- Ask the user before turning it on, and say: "When the service is unused for a long time, it sleeps. The first call after it wakes can fail (Railway answers 502) and must be repeated. It saves credit."
- If the user agrees: the service's Settings → Serverless. About 10 minutes after the last request (`TRIPWRIGHT_ACTIVE_WINDOW`), the service stops sending; 5 to 10 minutes later Railway puts it to sleep. The next request wakes it, and that first call can fail.

## How long the free credit lasts

**Measured on Railway** (2026-11-02, 17:30 to 21:05 CET; the author's test project with a local repository, the URL key, no sleep and no requests; one sample every 15 minutes): in 3 hours 35 minutes, the container used 27.3 CPU seconds, on average **0.0021 vCPU**; memory (cgroup `memory.current`) stayed at **about 90 MB**; the volume held 1.1 MB; the network byte counters did not change (**no outbound traffic while idle**). At the prices below, that is about $0.91 for memory and $0.04 for CPU: **about $0.95 a month, so the Free plan's $1 lasts about 31 days.** The trial credit ($5 once, 30 days) covers the first month anyway. Real use adds a little for each lookup; for heavy use, see the cautious row below. The figures come from the container's own counters, not from Railway's bill.

**Measured on a local computer** (2026-11-02, the same image run the Railway way: started as root, volume owned by root, settings only from variables, a local repository): after the start and one write, 301 idle seconds used 0.65 CPU seconds, on average **0.0022 vCPU**; memory peaked at **72 MiB** (anonymous memory 68 MiB on average); **no outbound packets** in those 5 minutes.

At Railway's prices (memory about $10 per GB a month, vCPU about $20 per vCPU a month, volume $0.15 per GB a month):

| Case | Memory | CPU | Volume | A month | $1 lasts |
| --- | --- | --- | --- | --- | --- |
| Idle, always on (local measurement) | 0.07 GB → $0.70 | 0.0022 vCPU → $0.04 | 0.1 GB → $0.02 | about $0.76 | about 39 days, so the whole month |
| Heavy use (cautious assumption) | 0.15 GB → $1.46 | 0.01 vCPU → $0.20 | 0.5 GB → $0.08 | about $1.74 | about 17 days |

- Unused credit does not carry over; "39 days" means that $1 is not used up in a month.
- The cautious row uses measurements from a robustness review: about 87 MB after 8 concurrent writes, and a few tens of MB more during a scan once the outbox has grown over long use.
- Railway measured more memory (about 90 MB) than the local run (72 MiB); count with 90 MB. To check the bill, open Railway's Usage page.
- With sleep, only the waking time counts, so it costs less; the price is a first call that can fail after waking.

## Troubleshooting

- **The deploy or the health check fails:** read the Deploy Logs. The usual causes are a missing volume or a missing `RAILWAY_DOCKERFILE_PATH`.
- **`/readyz` is not ready:** `kind` and `hint` give the reason for each tenant, and the log has one line with details ([SETUP.md troubleshooting](SETUP.md#troubleshooting)).
- **A write receipt or `state(view="sync")` says `blocked`:** the write is saved on the service, not lost. After you fix what `hint` says, the service pushes it.
- **The log shows `data directory … is new and empty` after the first start:** the volume was not attached or was replaced, and the writes that were not pushed are lost.
