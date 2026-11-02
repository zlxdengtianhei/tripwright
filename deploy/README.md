# 部署 Tripwright

Tripwright 是一个 MCP 服务：Claude、ChatGPT 等 AI app 的会话经它查交通、记行程。每个人部署自己的一份，只给自己（和同行的人）用。这页给部署者看，也给帮部署者配置的 agent 看；**agent 第一次替用户部署，先照 [SETUP.md](SETUP.md) 的顺序做**。

## 先选一种部署

| 你有什么 | 用哪种 | 要不要一直开着 |
| --- | --- | --- |
| 一台常开的电脑（Mac、Windows、Linux 都行） | **Docker，个人电脑**（下面第一节，推荐） | 电脑开着、Docker 在跑，服务才在 |
| 一台有公网 IP 和域名的服务器 | Docker + Caddy（下面第二节） | 服务器常开 |
| 没有电脑 | Railway，见 [RAILWAY.md](RAILWAY.md) | 平台替你开着；免费档每月 $1，估算见那页 |

所有方式都**只支持一个实例、一个持久的数据目录**（`/data`）。数据目录里有：行程仓的副本、还没推上去的写入、登录状态、钥匙。它丢了，没推上去的写入就丢了，所有人要重新登录。不要开多个实例共用或各自写同一个行程仓。每次启动，日志里会有一行说明数据目录是新的还是保留下来的（`data directory … is new and empty` 只应在第一次启动出现）。

行程文件存在哪，三选一（配置写在 `remote.yaml` 的 `tenants` 里）：

- **GitHub 私有仓**：服务器用一把只属于这个仓的部署钥匙推送。见 [REMOTE_AUTH.md](REMOTE_AUTH.md)。
- **其他 Git 托管**（GitLab、Gitea、自建）：只要它支持 SSH 部署钥匙，配法和 GitHub 一样，`repo_url` 写 SSH 地址。
- **不用任何托管**：`repo_url` 不写。服务在数据目录里建一个本地 Git 仓作正本，照常提交，不推送。`state(view="sync")` 会写明“文件只在这个卷（或这台电脑的这个目录）里”。**备份要你自己做**：定期复制整个数据目录。

谁能连上，三种方式（可以同时开）。**默认用网址密钥，GitHub 登录可选**：

- **网址密钥**（默认）：在部署者设了 `TRIPWRIGHT_URL_KEY_GENERATE=1` 或 `TRIPWRIGHT_URL_KEY` 时生效。连接器地址是 `https://<域名>/mcp/<密钥>`，不需要 GitHub。代价：**网址就是钥匙**，谁拿到网址谁就能读写你的行程；不要贴进聊天、截图或公开文档。服务端比较用常数时间，日志不记录网址。
  - **让服务自己生成（推荐）**：设 `TRIPWRIGHT_URL_KEY_GENERATE=1`（只是开关，不是密钥）。第一次启动时服务生成一个随机密钥，写进数据目录的 `remote/url-key`（权限 600），以后每次启动读它。日志只写一行它在哪、怎么读，不写密钥本身；`/healthz`、`/readyz` 也不带它。读法：Docker 用 `docker exec <容器> cat /data/remote/url-key`（个人电脑方式也可以直接打开 `deploy/data/remote/url-key`），Railway 用 `railway ssh -- cat /data/remote/url-key`。
  - **自己指定**：设 `TRIPWRIGHT_URL_KEY`（≥32 个字符，只含字母、数字、`-`、`_`）。两个都设时用自己指定的，不读生成的文件，日志会说明。
  - **换钥匙**：生成的，删掉 `remote/url-key` 再重启；自己指定的，改 `TRIPWRIGHT_URL_KEY` 再重启。换完都要在 AI app 里删掉旧连接器、用新网址重加。
- **GitHub 登录**（可选）：AI app 走标准 OAuth，跳到 GitHub 登录，只放行 `accounts` 里的账号；连接器地址是不带密钥的 `https://<域名>/mcp`。要在 GitHub 建一个 App（只管登录，不碰仓库），见下面“GitHub 登录是什么、谁来点”和 [REMOTE_AUTH.md](REMOTE_AUTH.md)。
- **请求头令牌**：`TRIPWRIGHT_TOKENS`（令牌 → 身份的 JSON）。只适合能自定义请求头的客户端（如 Claude Code、脚本）。

## 一、个人电脑上的 Docker（推荐）

要的东西：Docker Desktop（Mac、Windows）或 Docker Engine + Compose（Linux）；本仓源码。

1. 准备配置（在仓库的 `deploy/` 目录里）。所有设置都写在 `deploy/.env` 一个文件里：

   ```bash
   cd deploy
   mkdir -p data
   cp .env.example .env
   ```

   `.env` 里已经设了 `TRIPWRIGHT_URL_KEY_GENERATE=1`：第一次启动时服务自己生成网址密钥，存在 `data/remote/url-key`。

   不写 `TRIPWRIGHT_REPO_URL` 时，行程存在 `deploy/data` 里的本地 Git 仓，不需要 GitHub。要存到 GitHub 等托管，按 `.env` 里的注释填仓地址和部署私钥（钥匙怎么生成、公钥加到哪，见 [REMOTE_AUTH.md](REMOTE_AUTH.md)）。习惯写 YAML 的，在 `.env` 里设 `TRIPWRIGHT_REMOTE_CONFIG=/data/remote.yaml`，照 `personal.example.yaml` 或 `remote.example.yaml` 写 `data/remote.yaml`。

   Linux 上容器以 UID 10001 运行，要先让它能写数据目录：`sudo chown 10001:10001 data`。Mac 和 Windows 的 Docker Desktop 不用。

2. 决定 `.env` 里的 `TRIPWRIGHT_PUBLIC_URL`：
   - 只给**从这台电脑本身发起连接**的宿主用（Claude Code、Cursor、Codex CLI 等）：保持 `http://localhost:8000`。
   - 要用 **Claude 的自定义连接器（网页、手机，也包括 Claude Desktop）或 ChatGPT**：它们从自己的云端连你的服务器，不从你的电脑连（[Claude 官方说明](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)），所以要一条隧道（见下面）。把隧道给的 `https://…` 地址（不带路径）填进去；这台电脑上的其他宿主也改用这个地址。

3. 启动：

   ```bash
   docker-compose -f compose.personal.yaml up -d --build      # 新版 Docker 写 docker compose
   curl -s http://127.0.0.1:8000/healthz                      # {"status":"ok",...}
   curl -s http://127.0.0.1:8000/readyz                       # {"ready":true,...}；不是 true 时看下面“出了问题”
   ```

4. 读出密钥：`cat data/remote/url-key`（Linux 上文件归 UID 10001，用 `sudo cat`，或 `docker-compose -f compose.personal.yaml exec tripwright cat /data/remote/url-key`）。在 AI app 里加自定义连接器，地址填 `<TRIPWRIGHT_PUBLIC_URL>/mcp/<密钥>`。

改了 `.env` 后，用 `docker-compose -f compose.personal.yaml up -d` 让容器重新读取。

电脑睡眠、关机或 Docker 退出时，服务就不在了；AI app 会报连不上。再开机后 Docker 会自动拉起容器（`restart: unless-stopped`）。

**隧道（手机、网页版要用）**，任选其一：

| 隧道 | 要不要账号 | 地址稳不稳 | 命令 |
| --- | --- | --- | --- |
| Cloudflare 快速隧道 | 不要 | 每次启动换一个 `https://….trycloudflare.com`；换了就要改 `TRIPWRIGHT_PUBLIC_URL`、重启容器、重加连接器 | `cloudflared tunnel --url http://localhost:8000` |
| Cloudflare 命名隧道 | 要 Cloudflare 账号和一个托管在 Cloudflare 的域名 | 稳定 | 见 Cloudflare 文档 “Create a tunnel” |
| Tailscale Funnel | 要 Tailscale 账号（免费） | 稳定，`https://<机器名>.<tailnet>.ts.net` | `tailscale funnel 8000` |

隧道服务商能看到经过的请求，包括网址里的密钥。介意的话用 GitHub 登录代替网址密钥。

**同一台电脑上的桌面 app 也可以不经 Docker**：本地模式直接用 stdio 运行 Tripwright，见 [../docs/CONNECT.md](../docs/CONNECT.md)。那是另一份数据，和 Docker 里的不共享。

更新：`git pull` 后再执行一次第 3 步的 `up -d --build`。数据目录不动。

## 二、有域名的服务器：Docker + Caddy

`compose.yaml` 带一个 Caddy 容器，自动申请证书、把 HTTPS 转给应用。要求宿主 TCP 80、443 空闲且公网可达，域名已解析到这台服务器。在 compose 读得到的 env 文件里写 `TRIPWRIGHT_DOMAIN=<你的域名>`（`deploy/.env`，或用 `--env-file` 指定）；没写时 compose 拒绝启动，不会替别人的域名申请证书。目录约定和命令见下面的“作者实例运维示例”；核验脚本带参数：`deploy/check_https.py --url https://<你的域名> --ssh <SSH 目标>`（或用 `TRIPWRIGHT_CHECK_TOKEN` 直接给令牌）。

## 健康、就绪和日志

- `GET /healthz`：进程在就回 200，正文列出每个租户的状态。容器健康检查和平台健康检查用它。
- `GET /readyz`：每个租户都打开了、远端答应了、没有卡住的写入才回 200；否则回 503，每个租户给出 `status`（`ok`、`starting`、`error`、`blocked`）、`kind`（问题类别）、`hint`（怎么修）、`unsynced` 和 `oldest_unsynced_age_seconds`（最老一条没推上去的写入等了多久）。
- 启动时，服务在后台为每个租户打开仓、跑 `git ls-remote` 和 `git push --dry-run`（查出只读钥匙；设 `TRIPWRIGHT_CHECK_PUSH=0` 可关）。这样重启前没推完的写入不用等第一个请求就会推送。
- 写入推不上去时，写入回执和 `state(view="sync")` 带 `blocked: {kind, since, hint}`。`kind` 是 `auth`（钥匙没有写权限、钥匙被删、仓地址错）、`protected`（分支受保护）、`network`（连不上 Git 主机）、`behind`（远端有新提交，重放还没成功）、`other`、`commit`（本机提交失败，例如崩溃留下的锁；重启会清掉残留锁）。受保护分支只有真推的时候才看得出来，启动检查查不到。
- 日志（`docker-compose logs tripwright`）每件事一行：数据目录状态、租户打开失败及类别、写入卡住与恢复、提交器错误、工具里未预期的异常。日志里不会出现令牌、密钥或连接器网址。

## 出了问题

| `/readyz` 或日志里的 `kind` | 意思 | 怎么做 |
| --- | --- | --- |
| `auth` | 钥匙被拒、仓不存在或地址错、known_hosts 不对 | 核对仓地址；部署钥匙要加到这个仓并勾写权限；known_hosts 按 REMOTE_AUTH.md 从 GitHub 官方接口取 |
| `branch` | 仓里没有这个分支（空仓也算） | 先往仓里推一个提交，或改 `branch` |
| `network` | 连不上 Git 主机 | 查出站网络；22 端口不通时改用 `ssh.github.com:443`（见 REMOTE_AUTH.md） |
| `key_file` | 私钥或 known_hosts 文件缺失，或私钥权限不是 600 | `chmod 600`，属主 10001 |
| `config` | 配置和镜像对不上（例如改了 `repo_url`，旧镜像还在；仓里缺 `tripwright.yaml`） | 看日志那一行的详情 |
| `protected` | 分支受保护，拒绝服务器推送 | 允许部署钥匙推这个分支，或换分支 |

## 休眠：默认不休眠

Docker 部署没有休眠，电脑或服务器开着就在。Railway 有可选的休眠（Serverless），默认关。

- **不休眠（默认）**：什么都不用配。空闲时服务几乎不占 CPU，也不往外发包：最后一个请求 10 分钟后（`TRIPWRIGHT_ACTIVE_WINDOW`，秒）停止每 45 秒拉一次远端；下一个请求进来先拉一次再答。
- **要开休眠**：**agent 先问用户要不要开**，并说明：“开了以后，长时间不用服务会睡下；醒来后的第一次调用可能失败，要再调一次。好处是更省额度。”用户同意后，在 Railway 服务的 Settings 里打开 Serverless。服务在最后一个请求约 10 分钟后停止出站，再过 5–10 分钟 Railway 让它睡下。
- 只在 Railway 上有意义；Docker 版不用问。

## GitHub 登录是什么、谁来点

- **可选，默认不开**：默认的网址密钥已经能让手机和网页版连上。GitHub 登录的用处是让网址不再是钥匙：谁拿到网址都还要用名单里的 GitHub 账号登录。Tripwright 自己不存密码，把“你是谁”交给 GitHub 判断：AI app 跳到 GitHub，你登录，GitHub 告诉服务“这是某某账号”，服务只放行配置里的账号。
- **为什么不能用 App 里的 GitHub 连接代替**：在 Claude、ChatGPT 里连 GitHub，只让那个 App 能读你的 GitHub，不能向 Tripwright 证明“连进来的是你”。
- **它是什么**：在你自己的 GitHub 账号里登记的一个“用 GitHub 登录”的应用（GitHub App 或 OAuth App）。**只管登录，不碰任何仓库**：它不申请仓库权限；行程仓的读写用另一把只属于那个仓的部署钥匙。
- **谁来点**：GitHub 要求账号主人在浏览器里确认创建，不能由服务器或别人的账号替你建。宿主有浏览器工具、你已在浏览器登录 GitHub 并同意时，agent 可以替你打开设置链接、点“Create GitHub App”；否则 agent 把链接给你，你点一下。步骤见 [REMOTE_AUTH.md](REMOTE_AUTH.md)（GitHub 应用一节）；Railway 上的顺序见 [RAILWAY.md](RAILWAY.md)。
- **不想要 GitHub**：用网址密钥，见上面“谁能连上”。

## 给帮用户配置的 agent

完整的首次部署顺序（先问什么、命令、交付连接器地址、费用）见 [SETUP.md](SETUP.md)。要点：

1. 问用户有没有常开的电脑：有 → Docker 个人电脑；没有 → Railway。
2. 问用户有没有 GitHub、想不想用：不用 → 本地仓 + 网址密钥（最少步骤）；用 → 私有仓 + 部署钥匙，登录照旧用网址密钥，GitHub 登录只在用户想要时再加。
3. 只在用户要手机或网页版时配隧道或 Railway 域名。
4. 部署后看 `/readyz`：`ready: true` 才算好；不是就按“出了问题”的表修，`kind` 和 `hint` 原样告诉用户。
5. 网址密钥只设开关 `TRIPWRIGHT_URL_KEY_GENERATE=1`，不要自己生成，也不要把密钥写进命令行、脚本或聊天。部署好后读出 `/data/remote/url-key`（Railway：`railway ssh -- cat /data/remote/url-key`；Docker：`docker exec <容器> cat /data/remote/url-key`），拼成 `https://<域名>/mcp/<密钥>` 交给用户，同时提醒：**网址就是钥匙**，只填进自己的 AI app，不要转发或截图。
6. Railway 上问用户要不要开休眠（见上），默认不开。
7. 只能用户本人做的事，直接请用户做，不代填：注册 Railway 或 GitHub 账号、绑卡或升级套餐、在 GitHub 确认创建应用（有浏览器工具且用户同意时可代点）、把部署公钥加到仓时的 GitHub 登录确认。

## 作者实例运维示例

下面是作者自己的维也纳实例的运维记录：MCP 地址 `https://159-195-240-39.sslip.io/mcp`，健康地址 `https://159-195-240-39.sslip.io/healthz`。这不是开放注册的公共服务。Caddy 的站点地址来自 `TRIPWRIGHT_DOMAIN`；作者实例把它放在源码目录之外的 `/opt/tripwright/compose.env`（一次性：`ssh vienna 'echo TRIPWRIGHT_DOMAIN=159-195-240-39.sslip.io > /opt/tripwright/compose.env'`），每条 compose 命令用 `--env-file` 读它，这样更新时替换源码目录不会丢掉它。应用容器 `tripwright-app` 以 UID/GID `10001:10001` 运行，只在 `tripwright-backend` Docker 网络监听 8000；`tripwright-caddy` 发布宿主 TCP 80、443。

### 服务器目录

| 路径 | 内容 |
| --- | --- |
| `/opt/tripwright/app/` | 某次 Git 提交的源码快照及 `deploy/` |
| `/opt/tripwright/data/` | SQLite 数据，含 WAL/SHM；UID/GID 10001，目录权限 700 |
| `/opt/tripwright/tripwright.env` | 令牌到用户的 JSON 映射及以后需要的 API 密钥；root 所有，权限 600 |
| `/opt/tripwright/caddy-data/` | Caddy 证书、私钥及自动续期状态；目录权限 700 |
| `/opt/tripwright/caddy-config/` | Caddy 运行配置；目录权限 700 |
| `/opt/tripwright/SOURCE_REVISION` | 当前部署源码的仓库提交号 |

作者实例使用 `docker-compose`（v2）。以下客户端命令从 **Tripwright 项目根目录**运行，目标 SSH 别名写成 `vienna`；自己部署时换成自己的服务器，并按环境使用 `docker compose` 或 `docker-compose`。示例目标账号需要管理 `/opt/tripwright/` 与 Docker 的权限。部署使用 `/opt/tripwright/` 和 `tripwright-*` 容器，不占用宿主 8000 端口。自选域名时同步修改 Caddy 的站点地址及远程配置的 `public_url`；所有 OAuth 元数据与回调使用同一个 HTTPS 源地址。

### 首次部署

先确认 `159-195-240-39.sslip.io` 解析到 `159.195.240.39`，宿主 TCP 80、443 空闲且公网可达；维也纳已有 Docker 和 Compose。初始化目录，并在服务器内生成随机令牌。程序不会覆盖已有 env 文件，也不会显示令牌。

```bash
ssh vienna 'python3 -' <<'PY'
import json
import os
import secrets
from pathlib import Path

base = Path('/opt/tripwright')
base.mkdir(mode=0o755, exist_ok=True)
(base / 'app').mkdir(mode=0o755, exist_ok=True)
for name in ('data', 'caddy-data', 'caddy-config'):
    folder = base / name
    folder.mkdir(mode=0o700, exist_ok=True)
    folder.chmod(0o700)
    if name == 'data':
        os.chown(folder, 10001, 10001)
with os.fdopen(os.open(base / 'tripwright.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as sink:
    sink.write("TRIPWRIGHT_TOKENS='" + json.dumps({secrets.token_urlsafe(48): 'local'}, separators=(',', ':')) + "'\n")
PY
```

从 Tripwright 项目根目录执行以下命令。应用代码来自确定的 Git 提交；命令也支持项目位于大仓库的子目录中，导出时不会把整个大仓库传给服务器。`deploy/` 另外复制当前工作树版本，执行前应核对其差量。构建用 `uv 0.9.27` 执行 `uv sync --locked --no-dev --no-editable`，不修改锁文件。Docker 构建上下文只包括 `app/`，env 和数据位于其外；`Dockerfile.dockerignore` 再缩小发送范围。

```bash
set -euo pipefail
deploy_revision=$(git rev-parse HEAD)
deploy_repo_root=$(git rev-parse --show-toplevel)
deploy_prefix=$(git rev-parse --show-prefix)
deploy_tree=$deploy_revision
if [ -n "$deploy_prefix" ]; then deploy_tree="$deploy_revision:${deploy_prefix%/}"; fi
git -C "$deploy_repo_root" archive "$deploy_tree" \
  | ssh vienna 'tar -xf - -C /opt/tripwright/app'
COPYFILE_DISABLE=1 tar -cf - deploy | ssh vienna 'tar -xf - -C /opt/tripwright/app'
printf '%s\n' "$deploy_revision" | ssh vienna 'tee /opt/tripwright/SOURCE_REVISION'
ssh vienna 'cd /opt/tripwright/app/deploy && docker-compose --env-file /opt/tripwright/compose.env config --quiet && docker-compose --env-file /opt/tripwright/compose.env up -d --build'
```

`COPYFILE_DISABLE=1` 让 macOS 的 tar 不写入 AppleDouble 元数据；同一命令也可在常见 Linux 环境执行，不需要 macOS 专用的 `--no-xattrs` 参数。

等 Caddy 成功取得证书后核验：

```bash
curl --noproxy '*' --connect-timeout 10 --max-time 20 -sS -D - \
  -w '\nhttp_code=%{http_code} ssl_verify_result=%{ssl_verify_result}\n' \
  https://159-195-240-39.sslip.io/healthz
curl --noproxy '*' --connect-timeout 10 --max-time 20 -sS -i \
  https://159-195-240-39.sslip.io/mcp
.venv/bin/python -B deploy/check_https.py --url https://159-195-240-39.sslip.io --ssh vienna
```

预期依次为：200、JSON 里 `"status":"ok"`、`ssl_verify_result=0`；401 且有 `WWW-Authenticate: Bearer`；SDK 报 `https_verified=true`、七个工具和 `state_ok=true`。SDK 检查程序通过 SSH 私有管道将服务器 env 中的令牌读入 Mac 进程内存，不写本地文件、不显示令牌、不把它放在命令行参数中。它只调用初始化、工具列表和只读 `state`，不会写入测试对话。

Caddy 把 `/healthz`、`/readyz` 直接转给应用（见上面“健康、就绪和日志”）。应用连不上时返回 502。`/readyz` 证明租户已打开、远端可达；数据服务能否执行工具由 SDK 的 `state` 检查补充。

若 Let's Encrypt 最终申请失败，保留 Caddy 的原始报错并停下排障；不要改用其他公网入口。第一次启动、证书尚在签发时，TLS 连接可能短暂失败。

### 更新与回退

保留 env、data 和 Caddy 状态目录。更新使用全新的 `app.next/`，避免已从新提交删除的源码残留。先构建，成功后再切换。以下命令要求 `app.next` 和 `app.previous` 尚不存在；若存在，先核对上次更新的状态。`deploy/` 也会复制为当前工作树中的版本。

```bash
set -euo pipefail
ssh vienna 'test ! -e /opt/tripwright/app.next && test ! -e /opt/tripwright/app.previous && mkdir /opt/tripwright/app.next'
deploy_revision=$(git rev-parse HEAD)
deploy_repo_root=$(git rev-parse --show-toplevel)
deploy_prefix=$(git rev-parse --show-prefix)
deploy_tree=$deploy_revision
if [ -n "$deploy_prefix" ]; then deploy_tree="$deploy_revision:${deploy_prefix%/}"; fi
git -C "$deploy_repo_root" archive "$deploy_tree" \
  | ssh vienna 'tar -xf - -C /opt/tripwright/app.next'
COPYFILE_DISABLE=1 tar -cf - deploy | ssh vienna 'tar -xf - -C /opt/tripwright/app.next'
printf '%s\n' "$deploy_revision" | ssh vienna 'tee /opt/tripwright/SOURCE_REVISION.next'
ssh vienna 'cd /opt/tripwright/app.next/deploy && docker-compose --env-file /opt/tripwright/compose.env config --quiet && docker-compose --env-file /opt/tripwright/compose.env build tripwright'
ssh vienna 'mv /opt/tripwright/app /opt/tripwright/app.previous && mv /opt/tripwright/app.next /opt/tripwright/app && mv /opt/tripwright/SOURCE_REVISION /opt/tripwright/SOURCE_REVISION.previous && mv /opt/tripwright/SOURCE_REVISION.next /opt/tripwright/SOURCE_REVISION && cd /opt/tripwright/app/deploy && docker-compose --env-file /opt/tripwright/compose.env up -d --force-recreate'
.venv/bin/python -B deploy/check_https.py --url https://159-195-240-39.sslip.io --ssh vienna
```

`--force-recreate` 也会重新挂载 Caddyfile；仅重载配置无法保证读到被替换路径的新文件。更新会有短暂中断。确认更新通过后可以删除纯源码备份 `app.previous` 和 `SOURCE_REVISION.previous`；数据备份另行保存。

若需要回退旧源码，先停本项目，再交换源码目录并重建。共享数据库不随源码回退；升级涉及数据格式时，先备份数据，再按该升级的迁移说明操作。

```bash
ssh vienna 'cd /opt/tripwright/app/deploy && docker-compose --env-file /opt/tripwright/compose.env down && mv /opt/tripwright/app /opt/tripwright/app.failed && mv /opt/tripwright/app.previous /opt/tripwright/app && mv /opt/tripwright/SOURCE_REVISION /opt/tripwright/SOURCE_REVISION.failed && mv /opt/tripwright/SOURCE_REVISION.previous /opt/tripwright/SOURCE_REVISION && cd /opt/tripwright/app/deploy && docker-compose --env-file /opt/tripwright/compose.env up -d --build --force-recreate'
```

### 日志与停止

```bash
ssh vienna 'cd /opt/tripwright/app/deploy && docker-compose --env-file /opt/tripwright/compose.env ps'
ssh vienna 'cd /opt/tripwright/app/deploy && docker-compose --env-file /opt/tripwright/compose.env logs --tail 100'
ssh vienna 'cd /opt/tripwright/app/deploy && docker-compose --env-file /opt/tripwright/compose.env logs --tail 100 --follow caddy'
ssh vienna 'cd /opt/tripwright/app/deploy && docker-compose --env-file /opt/tripwright/compose.env stop'
ssh vienna 'cd /opt/tripwright/app/deploy && docker-compose --env-file /opt/tripwright/compose.env start'
```

需要移除容器和本项目网络、保留数据与密钥时：

```bash
ssh vienna 'cd /opt/tripwright/app/deploy && docker-compose --env-file /opt/tripwright/compose.env down'
```

不要把 env 文件、含完整环境变量的容器检查结果或展开后的 Compose 配置贴进日志、报告或仓库。修改 env 后，用 `docker-compose --env-file /opt/tripwright/compose.env up -d --force-recreate tripwright` 让应用重新读取。

### 完全撤除

下列命令会永久删除本部署的数据、令牌、证书和源码；只在确认已备份或不再需要时执行。

```bash
ssh vienna 'cd /opt/tripwright/app/deploy && docker-compose --env-file /opt/tripwright/compose.env down && docker image rm tripwright-app:local && rm -rf -- /opt/tripwright'
```

这里不执行 Docker 全局清理，不删除其他项目的镜像、容器或数据。默认部署继续接受现有 Bearer 令牌。

### 开启 GitHub OAuth 与用户 Git 仓

远程模式使用标准 OAuth（CIMD／动态注册、GitHub 登录），并把登录身份映射到用户私有行程仓。配置与应用登记步骤见 [REMOTE_AUTH.md](REMOTE_AUTH.md)。先准备账号名单、仓地址与部署钥匙，再设置 `TRIPWRIGHT_REMOTE_CONFIG=/data/remote.yaml`，并由用户完成 GitHub 应用登记；只有客户端号与密钥都就绪，OAuth 才启用。原有请求头令牌仍可使用，通过 `token_tenants` 映射到同一用户仓。

当前代码已包含远程模式；是否在线可用以部署配置和发现端点为准。可以无认证读取下面两个地址验证发现服务已就绪，但这仍不代替真实登录与用户仓读写核验：

```bash
curl --fail --silent --show-error https://159-195-240-39.sslip.io/.well-known/oauth-authorization-server
curl --fail --silent --show-error https://159-195-240-39.sslip.io/.well-known/oauth-protected-resource/mcp
```

首次部署时没有 GitHub 凭据，返回 503 表示尚未配置，不能靠重复添加聊天连接器解决。远程模式不挂载旧的 `/r` 结果页，也没有自助注册或令牌管理页。
