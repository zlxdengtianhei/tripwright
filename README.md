# tripwright

**把旅行计划、偏好和改动记在一起，通过 MCP 接进你的 AI 助手。** tripwright 保存消息与决定，每问返回该考虑的记忆，查询交通和天气，并记录计划为什么改变。它提供远程 HTTP MCP、本机 stdio MCP 和命令行。对话由宿主模型组织；服务端提供事实、检查和记录，当前远程服务没有自带核验模型。

**English.** tripwright is an open-source trip-planning service over the Model Context Protocol (MCP). It brings relevant preferences and decisions into each turn, returns sourced transport facts, and records plan changes with reasons. Connect over remote HTTP or local stdio, or use the CLI. The host model writes the answer; a server-side model is not included. Transport is based on timetables unless a result explicitly says it is live.

**当前状态（2026-11-02）：** 每个人自己部署一份、只给自己用：没有公共服务，也不开放注册。部署用 Docker（自己的电脑或任意机器）或 Railway。作者自己的那份在 VPS 上跑，用网址密钥登录、查真实交通数据。路上回答经过 7 次没参与开发的 AI 判官按固定标准判，最近一次（第 7 次）仍不合格；它点名的错已修，修后只做了作者自己的重放，没有再判。已知限制见下面“数据与限制”。

## 能给到什么

- **每问带出记忆。** 偏好、同行人、决定、想去的地方、本趟来源与须知由程序取出，附上给宿主的考虑与提问指引。宿主仍需实际使用这些信息。
- **两段回答的工作法。** 先按存的计划给下一步，明确待核时刻；查完再给完整安排，先说明和第一段哪里不同。宿主是否能连续发出两段、是否在后台继续工作，取决于客户端。
- **交通事实写清范围。** 工具逐程写出车次、站点、换乘、查询窗口和覆盖限制。宿主应照抄原句；一次没查到不等于没有车。能查到的稳定事实由 agent 查好，拿不到的实时信息在末尾写去哪个 App、搜什么、怎么查。
- **计划可调整，改动可追溯。** 普通选择可带理由调整；你明确说“必须”的地方与时间才作为必须项保护。排计划前先问必须项。每次接受的计划改动记录改了什么、谁改的、为什么；修改你先前的选择时，回答应说明。已买票与已订时刻还需要实况或确认过的更正作为来源。
- **版本与导出。** 旧版本上的结果不能覆盖新计划。仓库模式把正本放在你配置的 Git 仓，服务端保留镜像与运行库；本地也能只用 SQLite。可导出文件、查看每天安排与历史。

这些规则由服务端工具、MCP 提示词与本机角色说明共同实现。它们不能保证模型永不出错，也不能代替运营方的实时通告。Claude Code 的本机钩子提供额外检查；其他宿主不依赖这些钩子。

## 连接与自己部署

**第一次部署：把 [deploy/SETUP.md](deploy/SETUP.md) 交给你的 agent（Claude Code、Codex 等）照做；你只需要注册 Railway 或装 Docker，再在 App 里加连接器。**

| 入口 | 怎样开始 | 当前边界 |
| --- | --- | --- |
| Claude 网页／手机 | 在网页的自定义连接器填部署者给的地址：默认是带网址密钥的 `/mcp/<密钥>`，不用登录；开了可选的 GitHub 登录时填 `/mcp` 并完成 OAuth。手机使用同一连接器 | 网址密钥即凭据；GitHub 登录需部署者先配置并放行账号；真实 app 流程未实测 |
| Claude Code | 配置 HTTP MCP 并登录，或使用本机 stdio | 本机 stdio 有一次历史实连；本项目远程流程尚未实连 |
| ChatGPT | 在支持自定义 MCP 的账号与工作区中填部署者给的地址（默认带网址密钥，不用认证；可选 GitHub 登录时选 OAuth） | 套餐、读写权限与手机支持由宿主决定；完整记账链未实测 |
| 其他 MCP 宿主 | 标准远程 OAuth，或在支持请求头时使用部署者分配的 Bearer 令牌 | 未逐家验证；标准协议实现不等于所有 app 已兼容 |

各家应用自己的限制（2026-11-02 按官方文档核，没逐家实测）：ChatGPT 只能在网页版加自定义 MCP，手机上加不了；能用写入类工具的完整 MCP 只给 Business、Enterprise、Edu，Pro 只能用读取类工具，所以在这些套餐的聊天里改行程会被平台拒绝。Claude 聊天与 ChatGPT 自带的 GitHub 连接只能读，改行程要经你自己部署的服务。Claude Code 云端会话默认网络连不到交通数据，用账号里加的连接器可以绕开。加连接器这一步，两家都只能由账号主人在界面里点。

完整步骤见[怎样连接](docs/CONNECT.md)。部署者从 [Docker 与 HTTPS 部署](deploy/README.md)开始；登录默认用网址密钥，要可选的 GitHub 登录时再按 [GitHub 登录与私有仓配置](deploy/REMOTE_AUTH.md)开启 OAuth。登录只放行配置中的 GitHub 账号；写回行程仓使用独立的部署钥匙。令牌由部署者配置与分发，目前没有自助管理入口。

## 本地试用

从本项目根目录运行，需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)：

```bash
uv sync --extra dev
uv run tripwright demo
uv run tripwright serve --stdio
```

`demo` 使用临时库与虚构数据。最后一条启动本机 MCP 服务；将同一命令配置到本机宿主。默认数据库是 `~/.local/share/tripwright/tripwright.sqlite3`，可用全局参数 `--db` 指定别处。

要让 MCP 查询使用真实数据，在启动服务时设置：

```bash
TRIPWRIGHT_PROVIDERS=live uv run tripwright serve --stdio
```

MCP 服务未设置时使用 `synthetic` 数据；`tripwright query` 命令默认使用 `live`，也可显式加 `--providers synthetic`。结果会标明来源；虚构数据不能用于真实出行。

需要 Git 仓作为正本时：

```bash
uv run tripwright init ./my-trips --trip 2030-05-trip --timezone Europe/Stockholm
TRIPWRIGHT_PROVIDERS=live uv run tripwright --repo ./my-trips serve --stdio
```

`init` 会建立行程目录与本机 agent 配置。添加你自己的远程 Git 仓、同步与备份按[仓库格式说明](docs/FORMAT.md)操作。导出与恢复命令见 `uv run tripwright --help`。

## 数据与限制

默认真实数据适配器是 [Transitous](https://transitous.org/sources/)（交通）、[Open-Meteo](https://open-meteo.com/)（天气）和 [Nominatim / OpenStreetMap](https://www.openstreetmap.org/copyright)（地名解析）。服务端默认把 Transitous 路线请求限制在滚动 24 小时内 720 次，真实网络客户端通过共用计数文件计数；多用户或自动化部署应先核对上游条款。

交通主要是时刻表。结果如有明确实时标记，会按该标记说明；否则不承诺晚点、站台与临时停运信息。覆盖不是全球保证：当前已知奥地利有效期与克恩顿本地巴士缺口，其他地区的缺口和有效期识别尚不完整。数据未覆盖时会说明“查不到”，不推断为没车。

Wiener Linien 与 VAO 的额外实时适配器尚未接入。VAO 如需接入，要用户本人向服务商确认合同并申请凭据；当前没有一键开通入口。营业时间核查依赖保存的地点资料或宿主读取网页，订位与余位也没有通用实时接口。更多说明与署名见[数据来源](docs/DATA_SOURCES.md)。

## 数据与隐私

你配置的行程仓和运行库会保存消息、偏好、实况、决定与证据。宿主 app、Git 托管方、部署者和被调用的数据源各有自己的数据处理范围；这不是“只有你本机能看到”的承诺。

代码将 OAuth 状态与私有仓部署钥匙分开存储。运行时错误、反向代理和宿主仍可能产生诊断日志，目前没有覆盖全部日志路径的正文脱敏保证。部署者应限制日志和数据访问，避免记录请求正文、授权回调或凭据；不要把令牌、私钥或未处理的个人行程放进公开仓。

## 展示、开发与许可

[examples/trip-2026-10-bud-wien/](examples/trip-2026-10-bud-wien/) 是一趟真实走过的行程（2026-10 布达佩斯—维也纳），人名都换成了代号；这趟给朋友看的网页在 <https://zlxdengtianhei.github.io/trip-map-2026/>（默认英文，可切中文）。行程仓里的页面：每一天的安排、要去的地方、偏好与记忆、计划为什么改，都是 tripwright 生成的页面。也可以运行 `tripwright demo` 看虚构行程的程序输出。

本地核验：`uv run pytest -q`。MCP 冒烟：`uv run python scripts/smoke_mcp.py`。本项目代码采用 [MIT 许可](LICENSE)；上游数据与服务条款单独适用，MIT 不会授予 Transitous 公共 API 的商业使用权。
