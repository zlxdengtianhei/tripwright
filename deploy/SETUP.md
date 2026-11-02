# 第一次部署 Tripwright：给 agent 的引导

这页写给替用户部署 Tripwright 的 agent：用户自己的编码 agent（Claude Code、Codex 等，用户说“帮我把 tripwright 部署起来”），或在聊天 App 里帮用户的 agent。人也能照着做。按顺序执行；细节在 [README.md](README.md)（Docker）和 [RAILWAY.md](RAILWAY.md)（Railway）。

三条规矩，全程适用：

- **用户只做本人才能做的事**（第 2 节），其余由 agent 做完。
- **网址密钥不进聊天记录、日志、命令行和仓库。** 服务自己生成密钥，agent 只在最后交给用户一次（第 5 节）。部署钥匙的私钥同理。
- **出错时把原文告诉用户。** `/readyz` 的 `kind` 和 `hint` 原样转述，再照 [README.md 的“出了问题”](README.md#出了问题) 修。不要猜。

## 1. 先问两句，再分路

1. “你有没有一台一直开着、装了 Docker（或能装）的电脑？”
2. “你有没有 GitHub？要不要把行程存到 GitHub 的私有仓里？”

| 第 1 问 | 走哪条路 |
| --- | --- |
| 有 | **Docker**（第 4 节）。数据在自己电脑上，不花钱；电脑睡眠或关机时服务就不在 |
| 没有 | **Railway**（第 3 节）。平台替你开着，有免费额度（第 9 节） |

| 第 2 问 | 存在哪 |
| --- | --- |
| 不要，或没有 GitHub | 默认：卷或数据目录里的本地 Git 仓，备份自己做（第 7 节） |
| 要 | GitHub 私有仓 + 部署钥匙（第 7 节）；别的 Git 托管同理 |

登录默认用**网址密钥**：连接器地址里带一串密钥，不用建 GitHub App。GitHub 登录是可选项（第 10 节）。

## 2. 只能用户本人做的事

把这几件单独列给用户，等用户说做完了再继续。agent 不代填密码、不代绑卡。

- **Railway 路**：注册 Railway；运行 `railway login` 时在浏览器里点确认。
- **Docker 路**：安装 Docker Desktop（Mac、Windows）或 Docker Engine（Linux）。安装时要用户自己的管理员密码。
- **两条路都要**：在 Claude、ChatGPT 等 App 里加连接器（第 6 节）。地址由 agent 给，用户自己粘贴。
- **花钱的事**：绑卡、升级套餐，由用户本人决定、本人操作。
- **要 GitHub 时**：
  - 用户的 `gh` 还没登录，就请用户自己运行 `gh auth login`；
  - 或者请用户在仓库网页上粘贴部署公钥（第 7 节）。

## 3. Railway 路

前提：本机装好 [Railway CLI](https://docs.railway.com/cli)，用户已 `railway login`。先取得 Tripwright 源码：`git clone` 本仓库，或用户 fork 的仓库。以下命令都在**仓库根目录**运行。这串命令主会话 2026-11-02 在真实账号上走通过，大约 1 分钟服务就起来：

```bash
railway init --name tripwright                 # 新建项目（名字可改）
railway add --service tripwright               # 新建服务
railway variables --set RAILWAY_DOCKERFILE_PATH=deploy/Dockerfile \
                  --set TRIPWRIGHT_URL_KEY_GENERATE=1 --skip-deploys
railway volume add --mount-path /data          # 持久卷：没有它，重启就丢数据
railway domain                                 # 生成公网域名，记下它输出的 xxx.up.railway.app
railway up --detach                            # 上传当前目录并构建、部署
```

**等就绪**：每隔约 10 秒运行一次 `curl -s https://<域名>/readyz`，直到看到 `"ready":true`，一般在 1 分钟内。不是 ready 时，把 `kind`、`hint` 原样告诉用户，再照 README 修。

**读出密钥**：用 `railway ssh -- cat /data/remote/url-key`，交付方法见第 5 节。Railway 官方 CLI 文档里，`railway ssh -- <命令>` 是执行单条命令的写法。

注意几件事：

- **`TRIPWRIGHT_URL_KEY_GENERATE=1` 只是开关，不是密钥**，可以写在命令里。不要自己生成密钥，再用 `railway variables --set TRIPWRIGHT_URL_KEY=…` 填进去。
- **不要运行不带参数的 `railway variables`**：它会列出所有变量的值，部署钥匙也在里面。
- 其他变量（存到 GitHub、休眠等）见 [RAILWAY.md 的变量表](RAILWAY.md#变量)。改了变量要重新部署才生效：`railway up --detach`，或在网页上点 Redeploy。

## 4. Docker 路（自己的电脑）

照 [README.md 的“一、个人电脑上的 Docker”](README.md#一个人电脑上的-docker推荐) 做：

```bash
cd deploy
mkdir -p data
cp .env.example .env                       # 已含 TRIPWRIGHT_URL_KEY_GENERATE=1
# Linux：sudo chown 10001:10001 data
docker-compose -f compose.personal.yaml up -d --build    # 新版 Docker 写 docker compose
curl -s http://127.0.0.1:8000/readyz      # 等 "ready":true
```

**密钥在哪**：`deploy/data/remote/url-key`。在 Linux 上这个文件归 UID 10001，要用 `sudo cat`；也可以用 `docker-compose -f compose.personal.yaml exec tripwright cat /data/remote/url-key`。

**谁能直接连 `http://localhost:8000`**：只有从这台电脑本身发起连接的宿主，例如 Claude Code、Cursor、Codex CLI。

**谁需要公网 HTTPS 地址**：

- Claude 的自定义连接器，**包括 Claude Desktop**：Claude 从 Anthropic 的云端去连你的服务器，不从你的电脑连（[官方说明](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)）。
- ChatGPT。
- 手机上的 App。

这些都要一条隧道。把隧道给的 `https://…` 地址（不带路径）填进 `.env` 的 `TRIPWRIGHT_PUBLIC_URL`，然后运行 `docker-compose -f compose.personal.yaml up -d`。

| 隧道 | 要不要账号 | 地址 | 出处 |
| --- | --- | --- | --- |
| Cloudflare Quick Tunnel：`cloudflared tunnel --url http://localhost:8000` | 不要账号，也不要域名 | 每次启动都换一个 `….trycloudflare.com`；换了就要改 `TRIPWRIGHT_PUBLIC_URL`、重启、重加连接器 | [Cloudflare 文档](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/) |
| Cloudflare 命名隧道 | 要 Cloudflare 账号，还要一个托管在 Cloudflare 的域名 | 固定 | [Cloudflare Tunnel 文档](https://developers.cloudflare.com/tunnel/get-started/) |
| Tailscale Funnel：`tailscale funnel 8000` | 要 Tailscale 账号 | 固定，形如 `https://<机器名>.<tailnet>.ts.net` | [Tailscale 文档](https://tailscale.com/docs/features/tailscale-funnel) |

电脑睡眠、关机或 Docker 退出时，服务就不在了。隧道服务商能看到经过的请求，网址里的密钥也在其中。

## 5. 把连接器地址交给用户

连接器地址是 `https://<域名>/mcp/<密钥>`。Docker 只在本机用时，地址是 `http://localhost:8000/mcp/<密钥>`。

- **能用剪贴板就放剪贴板，不在输出里显示**。下面是 macOS + Railway 的例子；Linux 把 `pbcopy` 换成 `wl-copy` 或 `xclip -selection clipboard`：

  ```bash
  key=$(railway ssh -- cat /data/remote/url-key | tr -d '\r\n')
  printf 'https://%s/mcp/%s' "<域名>" "$key" | pbcopy && unset key && echo "已复制到剪贴板"
  ```

  然后告诉用户：“连接器地址已经在你的剪贴板里，直接粘贴到 App 里。”
- **在聊天 App 里、没有剪贴板时**：只在给用户的那一条回复里写一次完整地址。不要贴进共享的日志、群聊、issue 或截图。
- **同时提醒用户**：
  - **这个网址就是钥匙**：拿到它的人能读写你的行程。只填进自己的 AI App，不要转发。
  - **怎样换钥匙**：
    - Railway：运行 `railway ssh -- rm /data/remote/url-key`，再重启服务（`railway redeploy`，或网页上点 Restart）；
    - Docker：删掉 `deploy/data/remote/url-key`，再运行 `docker-compose -f compose.personal.yaml restart`；
    - 然后读出新地址，在 App 里删掉旧连接器、用新地址重加。

## 6. 在 App 里加连接器（用户本人操作）

界面以各家官方文档为准。下面写到的按钮名都来自所附的官方页面；官方页面没写的，标为“按当前界面”。

**Claude（网页、Desktop、手机共用）**，见[官方说明](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)：

1. Pro、Max 用户：打开 **Customize > Connectors**，点 “+ Add”，再点 “Add custom connector”。
2. 填名字（例如 Tripwright）和第 5 节的地址，点 “Continue”。
3. 在 **Authentication** 里选 “No sign in”：网址密钥不需要 OAuth。点 “Add” 完成。
4. 在对话里，点输入框左下的 “+” → “Connectors”，打开它。

还要知道：

- **免费账号只能加一个自定义连接器。**
- Team、Enterprise 账号要由组织的 Owner 先在 **Organization settings > Connectors** 里添加。网址密钥是共享的，同一组织里所有人用的是同一把钥匙；不建议在组织里这样用。
- 在网页上加好的连接器，手机 App 上也能用。

**ChatGPT**，见[官方说明](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt)：

1. 打开 **Plugins**，点加号，再点 **Add custom MCP server**。官方说明：在当前的 Plugins 界面里，不需要先打开开发者模式（Developer Mode）。
2. 填名字；在 **Connection** 里填第 5 节的地址。
3. 认证方式选“不需要认证”的那一项。官方没写这一项的名字，按当前界面选。
4. 勾选 **I understand and want to continue**，点 **Create as a plugin**。
5. 在对话里输入 `@`，选这个插件。

还要知道：

- **哪些账号能用**：官方写明 Business、Enterprise、Edu 账号可以在网页版连接自定义 MCP；Pro 账号只能用读取类工具，Tripwright 的记录、提交是写入工具，用不了；Plus 和免费账号官方没写，按当前界面看有没有这个入口。
- **只能在网页版添加**，手机上不能添加。
- 你的界面如果还没有 Plugins，旧版界面是在设置里打开开发者模式之后再创建，按当前界面操作。

**Claude Code**（编码 agent 本身就能连）：`claude mcp add --transport http tripwright "<地址>"`，见[官方文档](https://code.claude.com/docs/en/mcp)。地址会存进 Claude Code 的本机配置文件；那个文件和密钥一样，要保管好。

## 7. 行程存在哪

**默认：本地 Git 仓。** 不填仓地址时，行程存在卷里（Railway 的 `/data`）或数据目录里（Docker 的 `deploy/data`），是一个本地 Git 仓，照常提交。`state(view="sync")` 会写明“文件只在这里”。**备份要用户自己做**：

- **Docker**：定期复制整个 `deploy/data`。
- **Railway**：卷的备份看当前界面有没有提供，按当前界面操作。也可以导出：`railway ssh -- tar czf - -C /data remote > tripwright-backup.tgz`。导出的文件里有网址密钥，要像密钥一样保管。
- 想省掉备份的事，就改存 GitHub（下面）。

**存到 GitHub 私有仓**（用户的 `gh` 已经登录时，下面全由 agent 做）：

```bash
gh repo create <用户>/<仓名> --private                       # 建空仓：不要勾 README，否则推不上去
uv run tripwright init ./trip-repo --timezone Europe/Vienna --remote git@github.com:<用户>/<仓名>.git
git -C ./trip-repo push origin main                          # 服务要的是 tripwright init 建好的仓
ssh-keygen -t ed25519 -N "" -C tripwright -f ./tripwright-deploy-key
gh repo deploy-key add ./tripwright-deploy-key.pub --allow-write -t tripwright -R <用户>/<仓名>
```

`gh repo deploy-key add` 的用法见 [gh 手册](https://cli.github.com/manual/gh_repo_deploy-key_add)。

几点说明：

- **为什么要先 init 再推送**：只有 README 的仓，服务打不开（`/readyz` 会报 `config`）；完全空的仓也打不开（会报 `branch`）。
- **用户的 `gh` 没登录时**：把 `tripwright-deploy-key.pub` 的内容给用户，请用户在仓库的 **Settings → Deploy keys → Add deploy key** 里粘贴，并勾上 **Allow write access**。公钥可以给，私钥不给。
- **把私钥交给服务**：
  - Railway：`railway variables --set "TRIPWRIGHT_REPO_URL=git@github.com:<用户>/<仓名>.git" --set "TRIPWRIGHT_DEPLOY_KEY=$(cat ./tripwright-deploy-key)"`，再 `railway up --detach`。用 `$(cat …)` 写，私钥就不会出现在你的命令文字里。GitHub 的地址会自动改走 443 端口。
  - Docker：按 `.env` 里的注释，填 `TRIPWRIGHT_REPO_URL` 和 `TRIPWRIGHT_DEPLOY_KEY`（换行写成 `\n`）。
  - 交给服务以后，删掉本机的私钥文件：`rm ./tripwright-deploy-key`。服务已经把它存在卷上，权限 600。
- **别的 Git 托管**（GitLab、Gitea 等）：做法一样，在托管的仓库设置里加一把有写权限的部署钥匙，按当前界面操作；`TRIPWRIGHT_REPO_URL` 写 SSH 地址。非 GitHub 的主机还要填 `TRIPWRIGHT_KNOWN_HOSTS`，见 [RAILWAY.md 的变量表](RAILWAY.md#变量)。
- 更多细节见 [REMOTE_AUTH.md](REMOTE_AUTH.md)。

## 8. 休眠（只在 Railway 上有）

默认不开。agent 先问用户：“要不要开休眠？开了更省额度；但很久不用以后，醒来后的第一次调用可能失败，要再调一次。”用户同意了才开，做法见 [RAILWAY.md 的“休眠”](RAILWAY.md#休眠可选默认关)。

## 9. 费用（Railway）

- **试用**：新账号先进入试用：一次性给 $5 额度，30 天有效，**不用绑卡**。之后转为 Free 计划，每月 $1 额度，用不完不结转。出处：[Railway 定价页](https://railway.com/pricing)（“Can I try Railway without a credit card? Yes.”）、[Free Trial 文档](https://docs.railway.com/pricing/free-trial)。
- **Free 计划的限制**：每个服务最多 0.5 GB 内存，卷 0.5 GB（同见定价页）。Tripwright 空闲时内存约 70 MB，够用。
- **空闲时每月大约花多少、能撑几天**：〔主会话实测后填〕。本机估算见 [RAILWAY.md](RAILWAY.md#免费额度能撑多久估算待实测)。
- **要提醒用户的两点**（出处：Free Trial 文档）：
  - **没验证的账号是“受限试用”，出站网络受限**。Tripwright 要上网查交通、推 GitHub，可能因此失败。注册时连上 GitHub 账号可以申请验证；没通过的，可以到 [railway.com/verify](https://railway.com/verify) 再试，或升级套餐。
  - **试用账号建的卷，在额度到期 30 天后会被删除**；要保留数据得升级。所以用 Railway 又不升级的话，更应该把行程存到 GitHub（第 7 节），或定期导出备份。

## 10. GitHub 登录（可选）

- **什么时候值得开**：不想让“网址就是钥匙”。比如网址可能被别人看到，或者要让几个人各用自己的 GitHub 账号登录。开了以后，连接器地址是 `https://<域名>/mcp`（不带密钥），App 会跳到 GitHub 登录，只放行名单里的账号。
- **为什么要建一个 GitHub App**：
  - 它只管“你是谁”，不申请任何仓库权限；行程仓的读写走另一把部署钥匙。
  - 在 Claude、ChatGPT 里连 GitHub，只是让那个 App 能读你的 GitHub，**不能向 Tripwright 证明连进来的是你**，所以不能代替它。
- **怎样建**：
  1. 先给服务设 `TRIPWRIGHT_GITHUB_LOGINS=<允许的 GitHub 账号>` 和一个请求头令牌 `TRIPWRIGHT_TOKENS`。
  2. 部署后，在本仓运行 `deploy/setup_github_app.py --url https://<域名>`。令牌从环境变量 `TRIPWRIGHT_CHECK_TOKEN` 读；自己的服务器也可以加 `--ssh <主机>`，让脚本经 SSH 读服务器上的令牌，令牌不进命令行。
  3. 脚本会给出一个 10 分钟内有效、只能用一次的链接。请用户在浏览器里打开，点 “Create GitHub App”。凭据直接回到服务器，不经过聊天。
  4. 详见 [RAILWAY.md 的“加 GitHub 登录”](RAILWAY.md#加-github-登录) 和 [REMOTE_AUTH.md](REMOTE_AUTH.md)。
- **两种可以同时开**：网址密钥可以留着，也可以去掉：Railway 把 `TRIPWRIGHT_URL_KEY_GENERATE` 设成 `0`，并删掉 `/data/remote/url-key`。

## 做完时告诉用户的三件事

1. 连接器地址已经交给你（第 5 节）：网址就是钥匙，以及怎样换。
2. 行程存在哪、备份谁来做（第 7 节）。
3. Railway 的话：额度大约能用多久（第 9 节）、休眠开没开（第 8 节）。
