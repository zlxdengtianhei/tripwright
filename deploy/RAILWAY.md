# 在 Railway 上部署 Tripwright

没有常开的电脑时用 Railway：它替你开着一个容器和一块持久卷，你在网页上填变量（或由 agent 用 CLI 填），不用写配置文件。这页给部署者看，也给帮部署者配置的 agent 看；agent 第一次部署先照 [SETUP.md](SETUP.md) 的顺序做，命令序列见下面“用 CLI 部署”。有电脑的，用 [README.md](README.md) 的 Docker 个人电脑方式更省事。

## 先说结论

- **必须挂一块卷到 `/data`，只开一个实例。** 不挂卷就不要部署：一重启，没推上去的写入、登录状态和钥匙都会丢。
- **默认不休眠。** 按本机实测的空闲开销和 Railway 单价，空闲常开时每月约 $0.8，Free 计划每月 $1 的额度**估算能撑一整个月（约 39 天的量）；按用得多时的保守假设也约 17 天**。**这是估算，待在 Railway 账号上实测**（算法见文末）。
- **休眠可选，默认关。** 要开时 agent 先问用户，说明“醒来后第一次调用可能失败，要再调一次”。
- **Git 写回走 443 端口**（`ssh.github.com:443`），不用 22 端口；GitHub 的主机钥匙在启动时从 GitHub 官方接口（HTTPS 校验）取，不盲信第一次 SSH 连接。
- **登录默认用网址密钥，GitHub 登录可选。** 只设 `TRIPWRIGHT_URL_KEY_GENERATE=1`，服务自己生成密钥；不用建 GitHub App。
- **没有 GitHub 也能用**：不填仓地址时行程存在卷里的本地 Git 仓。
- **试用账号要注意两点**（[Free Trial 文档](https://docs.railway.com/pricing/free-trial)）：没验证的账号是“受限试用”，出站网络受限，查交通、推 GitHub 可能失败（注册时连 GitHub 账号可申请验证）；试用账号的卷在额度到期 30 天后会被删除，要保留数据得升级，或把行程存到 GitHub。

## 只能你本人做的事

- 注册 Railway（可用 GitHub 账号登录）。新账号可能只拿到受限试用，出站网络受限。
- 绑卡、升级套餐（Free 计划每月 $1 额度；Hobby $5/月）。agent 不代做。
- 要 GitHub 登录时：在 GitHub 确认创建登录应用（宿主有浏览器工具且你同意时 agent 可代点）。
- 要存到 GitHub 私有仓时：把部署公钥加到仓的 Deploy keys（勾 Allow write access），这一步要你的 GitHub 登录。

## 部署顺序

1. **建服务。** Railway 里 New Project → Deploy from GitHub repo，选 Tripwright 的仓（或你 fork 的）。在服务的 Variables 里加 `RAILWAY_DOCKERFILE_PATH=deploy/Dockerfile`。
2. **挂卷。** 右键服务 → Attach Volume，挂载路径 `/data`。
3. **公网域名。** Settings → Networking → Generate Domain。服务从 Railway 注入的 `RAILWAY_PUBLIC_DOMAIN` 自动得到自己的地址，不用另填。端口由 Railway 的 `PORT` 决定，服务会监听它。
4. **健康检查。** Settings → Deploy → Healthcheck Path 填 `/healthz`。Railway 的探针从 `healthcheck.railway.app` 发出，服务放行它。
5. **填变量**（下表）。最少只要一个：`TRIPWRIGHT_URL_KEY_GENERATE=1`，让服务第一次启动时自己生成网址密钥。这个值只是开关，不是密钥，可以写在命令行或聊天里。
6. **部署**，然后打开 `https://<域名>/readyz`，看到 `"ready":true` 才算好；不是的话，`kind` 和 `hint` 说明了原因（对照 README 的“出了问题”）。
7. **读出密钥，加连接器**：在本机装好 Railway CLI 并 `railway link` 到这个项目，然后运行 `railway ssh -- cat /data/remote/url-key`（项目里有几个服务时加 `--service <服务名>`）。连接器地址是 `https://<域名>/mcp/<读出的密钥>`，在 AI app 里加成自定义连接器。部署日志里有一行 `generated a URL key in /data/remote/url-key`，只写了位置，没有写密钥本身；`/healthz`、`/readyz` 也不带它。自己设了 `TRIPWRIGHT_URL_KEY` 的，地址里用那个值。
8. **（可选）GitHub 登录**：要等第 3 步有了域名才能做，因为 GitHub 应用的回调地址要填 `https://<域名>/auth/callback`。见下面“加 GitHub 登录”。

## 用 CLI 部署（agent 用）

用户已装 Railway CLI 并 `railway login`（本人在浏览器点确认）。在 Tripwright 仓库根目录运行（主会话 2026-11-02 在真实账号上走通，约 1 分钟起来）：

```bash
railway init --name tripwright
railway add --service tripwright
railway variables --set RAILWAY_DOCKERFILE_PATH=deploy/Dockerfile --set TRIPWRIGHT_URL_KEY_GENERATE=1 --skip-deploys
railway volume add --mount-path /data
railway domain                       # 记下输出的域名
railway up --detach
curl -s https://<域名>/readyz        # 重复到 "ready":true
railway ssh -- cat /data/remote/url-key    # 读出密钥：不要回显到共享的地方，交付方法见 SETUP.md 第 5 节
```

不要运行不带参数的 `railway variables`：它会列出所有变量的值（含部署私钥）。健康检查路径（`/healthz`）可在网页 Settings → Deploy 里补上。

## 变量

| 变量 | 必填 | 用途 |
| --- | --- | --- |
| `RAILWAY_DOCKERFILE_PATH` | 是 | 填 `deploy/Dockerfile`（Dockerfile 不在仓库根目录） |
| `TRIPWRIGHT_URL_KEY_GENERATE` | 用网址密钥登录时（推荐） | 填 `1`：服务第一次启动时生成一个 43 个字符的随机密钥，写进卷上的 `/data/remote/url-key`（权限 600），以后每次启动读它，不会重新生成。读法见第 7 步。换钥匙：`railway ssh -- rm /data/remote/url-key`，重新部署或重启，读出新密钥，在 AI app 里删掉旧连接器、用新网址重加 |
| `TRIPWRIGHT_URL_KEY` | 不用上一项时 | 自己指定连接器网址里的密钥，≥32 个字符，只含字母、数字、`-`、`_`。可用 `${{ secret(48) }}` 生成；生成的字符不合要求时服务拒绝启动并说明原因。**网址就是钥匙**：拿到网址的人能读写你的行程。换钥匙：改这个变量、重新部署、在 AI app 里删掉旧连接器用新网址重加。和 `TRIPWRIGHT_URL_KEY_GENERATE` 同时设时用这个值，不读生成的文件，日志会说明 |
| `TRIPWRIGHT_REPO_URL` | 否 | 行程仓的地址。GitHub 写 `git@github.com:OWNER/REPO.git` 或网页地址都行，服务自动改走 `ssh.github.com:443`。不填：行程存在卷里的本地 Git 仓，备份要自己做 |
| `TRIPWRIGHT_DEPLOY_KEY` | 填了仓地址时 | 部署钥匙的私钥全文（多行）。网页表单只能填一行时，把换行写成 `\n`。服务把它写到卷上，权限 600 |
| `TRIPWRIGHT_BRANCH` | 否 | 正本分支，默认 `main` |
| `TRIPWRIGHT_KNOWN_HOSTS` | 否 | 非 GitHub 托管（GitLab、Gitea）的 SSH 主机钥匙全文。GitHub 的不用填，服务从官方接口取 |
| `TRIPWRIGHT_GITHUB_LOGINS` | GitHub 登录时（可选） | 允许登录的 GitHub 账号，逗号分隔。不填就不开 GitHub 登录（默认） |
| `TRIPWRIGHT_GITHUB_CLIENT_ID`、`TRIPWRIGHT_GITHUB_CLIENT_SECRET` | GitHub 登录时 | GitHub 登录应用的客户端号与密钥 |
| `TRIPWRIGHT_OAUTH_SIGNING_KEY` | 否 | 登录令牌的签名材料，建议 `${{ secret(64) }}`；不填时由客户端密钥派生。改它会让所有人重新登录 |
| `TRIPWRIGHT_TOKENS` | 否 | 请求头令牌（JSON：令牌 → `local`），给能自定义请求头的客户端和下面的 GitHub 应用设置链接用 |
| `TRIPWRIGHT_PUBLIC_URL` | 否 | 用自定义域名时填 `https://<域名>`；不填用 Railway 分配的域名 |
| `TRIPWRIGHT_ACTIVE_WINDOW` | 否 | 最后一个请求后继续每 45 秒拉远端的秒数，默认 600。之后不再往外发包 |
| `TRIPWRIGHT_CHECK_PUSH` | 否 | 启动时用 `git push --dry-run` 查钥匙有没有写权限，默认开；`0` 关 |
| `RAILWAY_RUN_UID` | 否 | 只在日志说 “cannot give /data to UID 10001” 时设 `0`，服务改以 root 运行。正常情况下入口先以 root 把卷交给 UID 10001，再降权运行 |
| `TRIPWRIGHT_PROVIDERS` | 不要设 | 默认查真实交通数据；只有演示时才设 `synthetic` |

## 给 agent：网址密钥怎么处理

- 只设开关 `TRIPWRIGHT_URL_KEY_GENERATE=1`。不要自己生成密钥，也不要把密钥写进 `railway variables` 命令、脚本或聊天记录。
- 部署好、`/readyz` 显示 ready 之后，用 `railway ssh -- cat /data/remote/url-key` 读出密钥，拼成 `https://<域名>/mcp/<密钥>`。
- 把完整网址交给用户时，提醒一句：**这个网址就是钥匙**，拿到它的人能读写行程；只填进自己的 AI app，不要转发、截图或贴到公开的地方。泄露了就删掉文件、重启、重加连接器（见上表）。
- 除了交给用户这一次，不要在别处复述密钥。

## 加 GitHub 登录

可选，默认不开：网址密钥已经够用；想让网址不再是钥匙时再加。GitHub 登录只管“你是谁”，不碰仓库（为什么要它，见 README 的“GitHub 登录是什么、谁来点”）。两种做法，都要先有域名：

- **自动登记（推荐）**：先给服务设一个 `TRIPWRIGHT_TOKENS`，并设 `TRIPWRIGHT_GITHUB_LOGINS`；部署后在本仓运行 `TRIPWRIGHT_CHECK_TOKEN=<那个令牌> .venv/bin/python deploy/setup_github_app.py --url https://<域名>`，得到一个十分钟内有效的链接。账号主人在浏览器打开它，GitHub 页面上点 “Create GitHub App”。凭据直接回到服务器，存在卷上的 `/data/remote-secrets.env`（600），不经过聊天。
- **手建 OAuth App**：GitHub → Settings → Developer settings → OAuth Apps → New。Homepage 填 `https://<域名>`，Authorization callback URL 填 `https://<域名>/auth/callback`。把客户端号与密钥填进上表两个变量，重新部署。普通 OAuth App 往往不给刷新令牌，一小时后要重新登录。

## 休眠（可选，默认关）

- 默认不休眠，什么都不用配。空闲时服务不往外发包，几乎不占 CPU。
- agent 要先问用户要不要开，说明：“开了以后，长时间不用服务会睡下；醒来后的第一次调用可能失败（Railway 会回 502），要再调一次。好处是更省额度。”
- 用户同意后：服务的 Settings → Serverless 打开。服务在最后一个请求约 10 分钟（`TRIPWRIGHT_ACTIVE_WINDOW`）后停止出站，Railway 再过 5–10 分钟让它睡下；下一个请求把它叫醒，第一次可能失败。

## 免费额度能撑多久（估算，待实测）

**本机实测（2026-11-02，同一个镜像，按 Railway 的跑法：以 root 启动、卷归 root、只用环境变量配置、本地仓租户）**：启动并写入一次后空闲 301 秒，容器 CPU 共用 0.65 秒，平均 **0.0022 vCPU**；内存（cgroup `memory.current`）峰值 **72 MiB**，匿名内存平均 68 MiB；这 5 分钟里网卡**出站 0 个包**。

按 Railway 单价（内存约 $10/GB·月，vCPU 约 $20/vCPU·月，卷 $0.15/GB·月）：

| 情形 | 内存 | CPU | 卷 | 每月 | $1 能撑 |
| --- | --- | --- | --- | --- | --- |
| 空闲常开（实测值） | 0.07 GB → $0.70 | 0.0022 vCPU → $0.04 | 0.1 GB → $0.02 | 约 $0.76 | 约 39 天，即整月 |
| 用得多（保守假设） | 0.15 GB → $1.46 | 0.01 vCPU → $0.20 | 0.5 GB → $0.08 | 约 $1.74 | 约 17 天 |

- 每月额度不结转；“39 天”的意思是一个月用不完 $1。
- 保守假设参考了健壮性审查的实测：8 个并发写入后约 87 MB，长期使用、发件箱变大时一次扫描临时多几十 MB。
- **这是估算，待实测**：Railway 计内存的口径、平台自身的开销、Python 在它那里的常驻内存，都可能和本机不同。部署后在 Railway 的 Metrics 页看一两天的内存和 CPU，再按上面的单价重算。
- 开休眠时只算醒着的时间，会更省；代价是醒来第一次调用可能失败。

## 出了问题

- 部署失败、健康检查不过：看 Deploy Logs。最常见是没挂卷或 `RAILWAY_DOCKERFILE_PATH` 没填。
- `/readyz` 不是 ready：每个租户的 `kind` 与 `hint` 给出原因；日志里同一件事有一行带详情。对照 README 的“出了问题”。
- 写入回执或 `state(view="sync")` 出现 `blocked`：写入还在服务器上，没丢；按 `hint` 修好后会自动推上去。
- 日志里 `data directory … is new and empty` 出现在第一次之外：卷没挂上或换了卷，之前没推上去的写入已经丢了。
