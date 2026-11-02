# 怎样连接 tripwright

**连接地址是部署者提供的完整 `https://<你的域名>/mcp`。** 远程 MCP 使用 Streamable HTTP。配置好 GitHub 应用后，支持 OAuth 的宿主通过 MCP 标准 OAuth 登录；服务只放行部署配置中的 GitHub 账号。支持自定义请求头的客户端也能使用部署者分配的 Bearer 令牌。部署者开了网址密钥时，连接器地址是 `https://<你的域名>/mcp/<密钥>`，不用 GitHub 登录；网址就是钥匙，拿到它的人能读写你的行程，不要外传。普通用户不需要在自己的电脑运行 Tripwright。

**English.** Use the full `/mcp` URL supplied by your operator. Remote MCP uses Streamable HTTP with standard OAuth backed by GitHub, restricted to configured accounts. Clients with custom-header support can use an operator-provided Bearer token instead. Server implementation, a reachable endpoint, and a tested app connection are separate states; see the table below.

## 先确认服务已准备好

部署者先按 [HTTPS 部署](../deploy/README.md)与 [GitHub 登录、账号名单和私有仓写回](../deploy/REMOTE_AUTH.md)配置服务器。GitHub 应用创建由用户本人完成；临时设置链接用于服务器登记 GitHub 应用，**不是**给聊天 app 安装连接器的链接。

作者自己的部署地址为 `https://159-195-240-39.sslip.io/mcp`，不是开放注册的公共实例。**2026-11-02 01:48 CET 的无认证检查：** `/healthz` 返回 200，`/mcp` 返回 401 和 Bearer challenge；两个 OAuth 发现地址仍返回 503。健康页正常不能证明 OAuth 已配置，也不能证明行程仓已可读写。部署者完成 GitHub 应用设置后，才适合开始下列 OAuth 连接步骤。

不要把 GitHub 客户端密钥、服务端私钥或 Bearer 令牌发进聊天。没有令牌自助管理页或 `tripwright admin token create` 命令；请求头令牌由部署者在受限环境中配置与分发。

## 各入口核到哪一层

| 入口 | 代码与协议层 | 真实 app 的验证状态 |
| --- | --- | --- |
| 本机 stdio | 提供七个 MCP 工具、角色提示词与本机 agent 文件 | Claude Code 有一次历史实连（2026-10-31，一条消息）；其他本机宿主未逐个验证 |
| 远程 HTTP、请求头令牌 | 有认证与用户仓隔离；MCP SDK 自验覆盖工具调用和身份边界 | 作者地址可达且要求认证；这次只做无认证检查 |
| 远程 GitHub OAuth | 实现发现、客户端注册、PKCE、同意页、账号名单与刷新；测试中的 GitHub 上游为替身 | 真 GitHub 授权与刷新、Claude 网页／手机、ChatGPT 均未在本次核验中完成 |
| 其他 MCP 宿主 | 服务端不依赖厂商来源或固定 IP | 具体菜单、回调、套餐及手机能力未逐家确认 |

## Claude 网页与手机聊天

前提：你的账号和工作区允许添加自定义 MCP 连接器，且部署者已启用 OAuth、放行你的 GitHub 账号。

1. 在 Claude 网页打开连接器设置，选择添加自定义连接器。
2. 名称填 `tripwright`，服务器地址填部署者给的完整 `/mcp` URL。
3. 按宿主提示连接；在浏览器完成 GitHub 登录与授权，再在服务的同意页确认。
4. 返回对话，启用该连接器的工具权限。可以先问“当前存了哪趟行程”，检查读到的是否是你的数据。
5. 手机使用同一 Claude 账号与连接器；若看不到，先回网页检查连接器、工作区权限和登录状态。

界面名称、可用套餐和手机同步能力可能变化；本项目没有逐个实测，不能据此承诺所有 Claude 账号都能添加。这里不要求关闭每次写入确认，也不承诺在手机端直接新增连接器。

## Claude Code

远程 HTTP MCP 可以显式添加：

```bash
claude mcp add --transport http tripwright https://trip.example/mcp
```

把 `trip.example` 换成部署者的域名。随后在 Claude Code 的 `/mcp` 中打开该服务器并完成 OAuth 登录。命令格式可对照 [Claude Code MCP 文档](https://code.claude.com/docs/en/mcp)。本项目的真实远程登录流程未实测；不要假设 claude.ai 的连接器会自动出现在每一种本机或云端会话里。

本机 stdio 无需远程 OAuth。先在代码仓根目录完成 `uv sync --extra dev`，再将以下启动命令配置到宿主：

```bash
TRIPWRIGHT_PROVIDERS=live uv run tripwright --repo ./my-trips serve --stdio
```

`./my-trips` 是已用 `tripwright init` 建好的行程仓。宿主启动命令的工作目录应是 Tripwright 代码仓根目录；不在该目录时，用该宿主支持的工作目录选项或 `uv run --directory <代码仓路径>`。`init` 会为支持的本机宿主写入角色文件与 skills。Claude Code 另有两个本机钩子加强第一段顺序和第二段草稿一致性；这些钩子不随远程连接器运行。

## ChatGPT

前提：你的套餐和工作区允许添加自定义 MCP app／连接器，并允许本服务需要的读写工具。

1. 在 ChatGPT 提供的自定义 MCP 配置入口添加完整 `/mcp` URL。
2. 选择 OAuth，按提示完成 GitHub 登录与服务同意页。
3. 在对话中启用该 app；先读当前行程，再确认宿主是否允许记录消息和更改计划。

账号、工作区、网页与手机的工具能力由 ChatGPT 决定。本项目没有完成真实连接，**不在这里断言某个套餐一定有写工具，或手机一定能用**。如果你的界面没有自定义 MCP 入口，或只允许读取，这份指南不能把它变成完整的旅行规划入口。

`turn` 会记录消息，`propose`／`commit` 会写计划。只读宿主不能完成这条链；只用 `state` 看当前行程不等于本次要求已入账。当前也没有 `query(kind="brief")` 的只读替代入口。

## 其他 MCP 宿主与请求头令牌

Mistral、Cursor、VS Code、Codex、Gemini CLI、Kimi Code 等产品，各自有不同的 MCP 配置入口。只有在该宿主明确支持远程 Streamable HTTP 与 OAuth 时，才尝试填写 `/mcp` 并完成登录；本项目不提供未经验证的套餐、管理员权限或命令行参数保证。

支持自定义请求头的宿主，可在其 MCP 配置中使用部署者分配的令牌。下面仅示意常见的 JSON 形状，实际键名以宿主为准：

```json
{
  "mcpServers": {
    "tripwright": {
      "url": "https://trip.example/mcp",
      "headers": {"Authorization": "Bearer REPLACE_ON_THIS_DEVICE"}
    }
  }
}
```

令牌仅保存在该客户端的私有配置或密钥存储中，不提交到仓库。没有自定义请求头支持时使用 OAuth；不要把令牌塞进 URL。普通聊天产品如果没有可配置的 MCP 连接器，目前没有本项目确认过的接法，不把它们列为已兼容。

## 连上之后，回答还受什么影响

服务端会返回本问该考虑的记忆、必须项提问、事实原句、核查引导与变更记录。完整工作法在 MCP prompt `tripwright-main`；能选择提示词的宿主可加载它，本机宿主也可发现对应 skill。提示词可用不代表宿主一定自动加载或照做。

宿主负责实际调用 `turn`、使用取出的记忆、照抄交通句，并把两段回答发给用户。没有终端或后台继续执行能力的宿主，时钟刷新与第二段接续仍需客户端配合；必要时用户下一条回复“继续”。远程模式没有内置独立核验 agent，也没有接入旧的 `/r` 结果页作为手机后备入口。

## 排查连接

- `/mcp` 无令牌返回 401 是正常的认证要求。
- 先看服务本身：`/healthz` 是存活（进程在就回 200）；`/readyz` 是就绪（租户已打开、远端可达、没有卡住的写入才回 200，否则 503 并按租户给出 `kind` 与 `hint`）。`/healthz` 正常不代表能读写行程仓，以 `/readyz` 为准。
- 网址密钥的地址写错一位会回 404；换过密钥后要删掉旧连接器、用新网址重加。
- OAuth 发现地址返回 503 时，先请部署者检查 GitHub 应用配置；重复新增连接器不能完成服务器设置。
- 登录后仍被拒绝时，检查 GitHub 账号是否在部署名单中，不是检查宿主厂商或来源 IP。
- 认证后出现“用户仓不可用”时，由部署者检查仓地址、分支、部署钥匙与主机公钥；不要把私钥发给宿主模型。
- 工具只显示读操作时，先核对宿主权限；本项目不把未记录的要求说成已经保存。
