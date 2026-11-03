# 远程 GitHub 登录与用户仓写回

English: [REMOTE_AUTH.md](REMOTE_AUTH.md)

> **Not recommended for a personal deployment.** The default and recommended sign-in is the key in the connector address ([SETUP.md](SETUP.md)). This page documents the optional GitHub sign-in (OAuth) and header tokens that the code keeps, off by default.

这份说明用于已有的 Docker＋Caddy 部署（见 [README.md](README.md#a-server-with-a-domain-docker-and-caddy)）。公网入口是 `https://<你的域名>/mcp`。开启本模式后，Claude 等宿主使用标准 OAuth 登录；现有 `TRIPWRIGHT_TOKENS` 请求头令牌也能连接同一个用户仓。应用内部仍使用现有七个工具和 Git 发件箱、提交器、操作重放。

远程模式已在作者自己的服务器上跑过，请求头令牌路已验证可用。GitHub App 待用户创建；用户在场时通过 `deploy/setup_github_app.py` 生成设置链接，完成应用登记后启用 OAuth。

## 配置和目录

将 `remote.example.yaml` 复制为服务器 `/opt/tripwright/data/remote.yaml`。填写用户的 GitHub 登录名与已有私有行程仓的 SSH 地址，仓中应已有 `tripwright.yaml` 等 Tripwright 文件。配置的 GitHub 登录名大小写不敏感，名单外的账号拿不到本服务令牌。

| 配置 | 含义 |
| --- | --- |
| `public_url` | 无路径、无查询参数的 HTTPS 公开源地址 |
| `data_dir` | 容器内运行目录；例 `/data/remote` |
| `secrets_file` | 容器内专用 env 文件；例 `/data/remote-secrets.env`，权限 600 |
| `oauth` | 是否开启 GitHub OAuth；false 时仍可使用请求头令牌 |
| `accounts` | GitHub 登录名 → 租户 ID；这里只列获准账号 |
| `tenants.<id>.repo_url` | 对应的私有 Git 仓 SSH 地址 |
| `tenants.<id>.owner` | 仓索引中的身份；现有个人仓一般为 `local` |
| `tenants.<id>.branch` | 正本分支，默认 `main` |
| `tenants.<id>.ssh_key` | 仅服务器持有的私钥路径 |
| `tenants.<id>.known_hosts` | 已核过来源的 SSH 主机公钥文件 |
| `token_tenants` | 旧令牌映射中的身份 → 租户 ID；例 `local: personal` |

YAML 中不放密钥。`data_dir/tenants/<id>/mirror/` 是镜像克隆，运行库在镜像的 `.tripwright/state.sqlite`。每个租户独立目录、库和服务对象；调用方不能通过工具参数指定租户。镜像 origin 或当前分支与配置不同会拒绝打开，避免推错仓或推错分支。

服务器上所有 `/data/...` 路径对应宿主 `/opt/tripwright/data/...`。现有 Compose 已绑定整个数据目录，无需额外挂载。远程模式不会导入旧的单库 `/data/tripwright.sqlite3`，而是从配置的用户仓重建索引。

## 服务器生成部署钥匙；你的 agent 将公钥加到私有仓

下面第一段在你的电脑上执行，把 `<server>` 换成服务器的 SSH 别名。私钥留在服务器上，`ssh-keygen -q` 不显示私钥。已有同名钥匙时停止，避免覆盖。

```bash
ssh <server> 'install -d -m 700 -o 10001 -g 10001 /opt/tripwright/data/ssh && test ! -e /opt/tripwright/data/ssh/personal_ed25519 && ssh-keygen -q -t ed25519 -N "" -C tripwright-personal -f /opt/tripwright/data/ssh/personal_ed25519 && chown 10001:10001 /opt/tripwright/data/ssh/personal_ed25519 /opt/tripwright/data/ssh/personal_ed25519.pub && chmod 600 /opt/tripwright/data/ssh/personal_ed25519'
```

你的 agent 使用电脑上已经登录的 `gh`，把 `OWNER/PRIVATE_REPO` 换成实际行程仓。管道里只有公钥：

```bash
ssh <server> 'ssh-keygen -y -f /opt/tripwright/data/ssh/personal_ed25519' \
  | gh repo deploy-key add /dev/stdin --repo OWNER/PRIVATE_REPO --allow-write --title tripwright-server
```

GitHub 主机钥匙从经 HTTPS 校验的官方 metadata API 取得，保存后 SSH 使用 `StrictHostKeyChecking=yes`，不会在第一次 SSH 连接时盲信网络返回的钥匙：

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

`repo_url` 写成 `ssh://git@ssh.github.com:443/OWNER/PRIVATE_REPO.git` 就走 443 端口，不依赖出站 22 端口；新部署建议用这个写法（`git@github.com:…` 走 22，仍可用）。已有镜像改 `repo_url` 会被拒绝打开（“different origin”）：先把 `data_dir/tenants/<id>/mirror` 挪开，服务会重新克隆，没推上去的写入要先推完（`/readyz` 的 `unsynced` 为 0）。

钥匙只授予这一个行程仓的写权限。用户在 GitHub 网页或其他克隆里的改动，沿用现有 45 秒轮询收编；推送不强推，被拒后按操作在新 head 上重放。

## 开启远程配置

让容器用户能够读取配置文件；例如 `chown 10001:10001 /opt/tripwright/data/remote.yaml`、`chmod 600 ...`。在既有 `/opt/tripwright/tripwright.env` 中新增一行：

```dotenv
TRIPWRIGHT_REMOTE_CONFIG=/data/remote.yaml
```

现有 `TRIPWRIGHT_TOKENS` 原样保留，并在 YAML 的 `token_tenants` 中映射其身份。Compose 的 `env_file` 会把配置路径传给 CLI；不需要更改 Caddyfile 或 Compose 路由。新 Dockerfile 为 Git 写回加入了 `openssh-client`，因此需要重建应用镜像：

```bash
ssh <server> 'cd /opt/tripwright/app/deploy && docker-compose up -d --build --force-recreate'
```

直接运行 CLI 时的等价入口：

```bash
tripwright serve --http --host 0.0.0.0 --port 8000 --remote-config /data/remote.yaml
```

没有 `--remote-config` 或 `TRIPWRIGHT_REMOTE_CONFIG` 时，原有本地单库、`--repo` 与 Bearer 行为保留。stdio 使用原有路径。

## GitHub 应用：用户创建，凭据直接回服务器

采用 GitHub App manifest。它只用于登录，不申请行程仓权限；行程写回单独使用上面的部署钥匙。应用要建在用户自己的 GitHub 账号里，GitHub 要求账号主人在浏览器里确认；宿主有浏览器工具、用户已在浏览器登录 GitHub 并同意时，agent 可以替用户打开链接、点确认（见 [README.md](README.md#who-can-connect) 的“Who can connect”一节）。

用户在场准备创建 GitHub App 时，在电脑上的项目目录运行：

```bash
.venv/bin/python -B deploy/setup_github_app.py --url https://<你的域名> --ssh <服务器的 SSH 别名>
```

没有 SSH 时把一个请求头令牌放进环境变量 `TRIPWRIGHT_CHECK_TOKEN` 再运行，不给 `--ssh`。

程序用已有令牌创建一个十分钟内有效、使用后作废的设置链接，只输出这个链接。你的 agent 把链接给用户。用户打开后会自动转到 GitHub，在 GitHub 点“Create GitHub App”即可；浏览器禁用脚本时，页面保留“Continue to GitHub”按钮。用户可以修改显示名称。登录名应与 YAML 名单中的账号一致。

GitHub 回调到 `/setup/github-app/callback`。服务器核对一次性 state、同一浏览器的 HttpOnly cookie 和 App 所属账号，再向 GitHub 换取凭据。客户端号、客户端密钥、App ID、PEM 私钥和 webhook 密钥直接写入 `/opt/tripwright/data/remote-secrets.env`，以权限 600 原子保存。页面只显示设置完成。凭据不会出现在页面、聊天、命令参数或源码中。专用 env 位于可写数据卷内，文件归容器 UID 10001；不要求容器修改 root 所有的外层 env 文件。

保存成功后下一次请求自动启用 OAuth，无需再重启容器。设置链接过期后重新生成；已有应用凭据时不再开放登记。失败时页面只给出状态，不显示 GitHub 的凭据响应。

默认回调地址：`https://<你的域名>/auth/callback`。GitHub App 的用户令牌应保持默认的到期与刷新功能；代理签发的访问令牌有效期为一小时，刷新令牌上限为三十天并随刷新轮换，GitHub 上游的有效性仍是约束。

手建 OAuth App 是退路：用户在 GitHub Developer settings → OAuth Apps 创建，Homepage 填公开源地址，Authorization callback URL 填上面的 `/auth/callback`；用户自己在服务器的 600 专用 env 文件中填 `TRIPWRIGHT_GITHUB_CLIENT_ID` 和 `TRIPWRIGHT_GITHUB_CLIENT_SECRET`，文件归 10001:10001。不要将密钥发到聊天。普通 OAuth App 往往不提供刷新令牌；此时访问令牌一小时后需要重新登录。优先使用 manifest 创建的 GitHub App。

可选 `TRIPWRIGHT_OAUTH_SIGNING_KEY` 同样只能从进程环境或专用 600 env 文件读取；未配置时由上游客户端密钥派生。OAuth 客户端、授权码、上游令牌与刷新状态保存在 `/data/remote/oauth/`，以 Fernet 加密，避免容器重启后所有连接失效。只有一个应用进程写这份状态。轮换签名材料会让旧连接失效，需要重新登录。

## 公网路径与核验

Caddy 现有 `handle { reverse_proxy tripwright:8000 }` 已覆盖以下路径，应全部转给应用并保留 Authorization、Cookie 和 Set-Cookie：

- `/mcp`；未认证响应 401，`WWW-Authenticate` 指向资源元数据。
- `/.well-known/oauth-protected-resource/mcp`、`/.well-known/oauth-authorization-server`。
- `/register`、`/authorize`、`/consent`、`/token`、`/auth/callback`。
- `/setup/github-app/session`、`/setup/github-app/callback`、`/setup/github-app/<一次性号>`。

不启用访问日志来记录授权码、临时链接或 cookie。健康入口是 `/healthz`（存活）与 `/readyz`（就绪：租户已打开、远端可达、没有卡住的写入），见 README。

部署后可运行：

```bash
curl --fail --silent --show-error https://<你的域名>/.well-known/oauth-authorization-server
curl --fail --silent --show-error https://<你的域名>/.well-known/oauth-protected-resource/mcp
.venv/bin/python -B deploy/check_https.py --url https://<你的域名> --ssh <服务器的 SSH 别名>
```

发现文档应包含 `S256`、`registration_endpoint`、`client_id_metadata_document_supported=true`、`token_endpoint_auth_methods_supported` 中的 `none`，以及 `authorization_response_iss_parameter_supported=true`。资源是完整 `/mcp` 地址；issuer、authorization_servers 与回调的 `iss` 使用同一个公开源地址。刷新令牌重复使用返回 `invalid_grant`。

Claude 网页在 Connectors 加完整 `/mcp` 地址，完成 GitHub 登录；同一连接器供手机使用。Claude Code 可以用同一连接器，或自己添加 HTTP MCP 并走 OAuth；固定请求头宿主也可继续用令牌。ChatGPT 的连接器能走相同 OAuth，但读写工具及手机可用性受套餐和宿主能力限制。OAuth 流程已在假的 GitHub 上游自验；作者自己的实例已用请求头令牌经 HTTPS 和官方 MCP SDK 验证七个工具与只读 `state`。GitHub App 待用户创建，真实 GitHub OAuth、Claude、手机和 ChatGPT 连接仍待验证。
