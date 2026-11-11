<h1 align="center"><img src="docs/assets/logo.svg" alt="tripwright" width="317" height="64"></h1>

[English](README.md) | 简体中文 | [项目主页](https://zlxdengtianhei.github.io/tripwright/)

**tripwright 是给 AI 助手用的开源旅行规划服务。它把你的计划、偏好和每次改动记在一处，每次你提问时，把这次该考虑的那部分交给助手。** 它通过 MCP（模型上下文协议，让 AI App 调用外部工具的开放标准）接进支持 MCP 的客户端，例如 Claude、ChatGPT、Codex 和 Claude Code（各家都有限制，见[已知限制](#已知限制)）。回答仍由你的助手来写；tripwright 提供记忆、带来源的交通和天气事实，并记下计划改了什么、为什么改。

**当前状态（2026-11-03）：** 每人用 Docker 或 Railway 自己部署一份，只给自己用。没有公共服务，也不开放注册。作者自己用的那份跑在租的服务器上，查真实交通数据。回答质量还在评测中，见[已知限制](#已知限制)。

**从哪开始：** 要在聊天 App 里用，看[连接与自己部署](#连接与自己部署)，大部分步骤可以交给编码 agent 做。想先看看它做什么、不注册任何账号，看[本地试用](#本地试用)：三条命令，用虚构数据。

## 能给到什么

- **每问带出记忆。** 每收到一条消息，tripwright 取出眼下适用的偏好、同行人、决定、想去的地方和本趟须知，并附上助手该考虑什么、该问什么。助手仍需实际用上这些信息。
- **回答分两段。** 第一段按存的计划给下一步，标明哪些时刻还没核。第二段在查完之后给，开头先说和第一段哪里不同。有的客户端不能自己接着发第二条消息，这时第一段末尾会请你回一句“继续”。
- **交通事实写清范围。** 工具逐程给出车次、站点、换乘、查询的时间窗和覆盖限制，助手应照原句转述。一次没查到不等于没有车。能查的由助手查好；拿不到的实时信息，回答末尾写明去哪个 App、搜什么、怎么查。
- **计划可调整，改动可追溯。** 普通选择有理由就可以调整；只有你明确说“必须”的地点和时间才作为必须项保护，助手排计划前会先问必须项。每次接受的改动都记下改了什么、谁改的、为什么；改了你先前的选择，回答里会说明。已买的票和已订的时刻，要有实况或确认过的更正才改。
- **版本与导出。** 旧版本上算出的结果不能覆盖新计划。仓库模式下，行程放在你配置的 Git 仓里，这是正本；服务端另存一份副本和一个运行库。在自己电脑上，也可以只用一个 SQLite 数据库。可以导出文件，查看每天的安排和历史。

这些规则由服务端工具、MCP 提示词和本机角色说明共同落实。它们不能保证模型永不出错，也不能代替运营方的实时通告。在 Claude Code 里，本机钩子会再做一层检查；其他客户端没有这些钩子也能用。

## 连接与自己部署

1. **起服务**：用 Docker（自己的电脑或任意一台机器）或 Railway。在自己电脑上用 Docker 时，Claude 和 ChatGPT 要经隧道才连得到，SETUP.md 列了几种隧道。服务会生成一串密钥，连接器地址形如 `https://<你的域名>/mcp/<密钥>`。这个地址就是登录凭据：拿到它的人能读写你的行程。
2. **连 GitHub 私有仓**存行程。这一步可以不做；不做时，行程存在部署里的一个本地 Git 仓，备份要自己做。
3. **把地址加进你的客户端。** 在 Claude 和 ChatGPT 里，只有账号主人能在 App 的设置里加连接器。

第一次部署，把 [deploy/SETUP.md](deploy/SETUP.md) 交给你的编码 agent（Claude Code、Codex 等）照做。Docker 或 Railway 的安装和配置都由 agent 做；你只需在需要时确认账号、输入一次系统密码或处理付费，再在 App 里点添加。Docker 三条命令起服务，连私有仓再用三条。Railway 的费用也写在 SETUP.md 里；各客户端（Claude Code、Codex、Claude、ChatGPT、Cursor 等）的步骤在 [deploy/CLIENTS.md](deploy/CLIENTS.md)。

## 本地试用

在本项目根目录运行。需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync --extra dev
uv run tripwright demo
uv run tripwright serve --stdio
```

`demo` 用临时库和虚构数据。最后一条命令启动本机 stdio MCP 服务；把同一条命令填进本机客户端的 MCP 配置。默认数据库是 `~/.local/share/tripwright/tripwright.sqlite3`，要放别处，加全局参数 `--db`。

要让 MCP 查询用真实数据，启动服务时设置数据来源：

```bash
TRIPWRIGHT_PROVIDERS=live uv run tripwright serve --stdio
```

不设置时，MCP 服务用 `synthetic`（虚构）数据。`tripwright query` 命令默认用 `live`；要虚构数据就加 `--providers synthetic`。每个结果都标明来源。虚构数据不能用于真实出行。

要把正本放进 Git 仓：

```bash
uv run tripwright init ./my-trips --trip 2030-05-trip --timezone Europe/Stockholm
TRIPWRIGHT_PROVIDERS=live uv run tripwright --repo ./my-trips serve --stdio
```

`init` 会建好行程目录和本机 agent 配置。添加你自己的远程 Git 仓、同步和备份，见[仓库格式说明](docs/FORMAT.md)（英文）。导出与恢复命令见 `uv run tripwright --help`。

## 已知限制

**回答**

- 回答质量还在评测中：由没参与开发的评审按固定标准判，目前还没有通过。
- 在 Claude Code 里（2026-11-04 用真实问题重放，由没参与开发的评审按内容判）：查出并修好了服务自己写好的句子里的两处错：去计划车次的走法只写了第一程的车，助手写成了直达；“夜里一直有车”掩盖了两小时的空档。第一段半分钟内出来。
- 助手有时仍会把对的事实拼错：例如把活动写到别的场馆，或让你“几点前回到车站”却没算走到站台的时间；也会多写一句过程话（例如“正在核对吃饭地点”）。服务给了对的事实、也告诉它别这样，但拦不住。
- 其他入口（Codex、服务连不上时只走命令行）能用，测得少：Codex 有时一条回复里就出完两段。
- 回答由你的助手所用的模型来写，服务端没有自带的核验模型。

**交通与其他数据**

- 默认的真实数据来源是 [Transitous](https://transitous.org/sources/)（交通）、[Open-Meteo](https://open-meteo.com/)（天气）和 [Nominatim / OpenStreetMap](https://www.openstreetmap.org/copyright)（地名解析）。
- 交通主要是时刻表。结果标了实时的会写明；没标的，不承诺晚点、站台和临时停运。
- 覆盖不是全球的。tripwright 目前只知道奥地利的时刻表数据到哪天为止（每年 12 月换时刻表）。已画出的地区缺口只有克恩顿的本地巴士。其他地区可能有还没识别出的缺口或过期数据。数据没覆盖时，回答说“查不到”，不说“没车”。
- Wiener Linien 和 VAO 的额外实时来源还没接入。VAO 要接入，得你本人向服务商谈合同、申请凭据；没有一键开通。
- 营业时间靠保存的地点资料，或客户端读到的网页；订位和余位没有通用的实时来源。
- 服务端默认把 Transitous 路线请求限制在滚动 24 小时内 720 次，多个进程共用一个计数。给多人用或做自动化之前，先核对上游条款。
- 详情与署名见[数据来源](docs/DATA_SOURCES.md)。

**客户端**（2026-11-02 按各家官方文档核过，没有逐家完整实测）：

- Claude：免费账号只能加一个自定义连接器。Team、Enterprise 账号要由组织的 Owner 先加连接器，之后组织里所有人共用同一把网址密钥；不建议这样用。
- ChatGPT：自定义 MCP 只能在网页版添加，手机上加不了。会写入的工具要 Business、Enterprise 或 Edu 账号；Pro 账号只能用只读工具。tripwright 只有 `state`、`explain` 是只读的，所以在 Pro 上能读行程，不能查交通，也不能改计划。（`query` 也算写，因为每次查询都作为证据记进行程仓。）
- Claude 聊天和 ChatGPT 自带的 GitHub 连接只能读；要改行程，得经你自己部署的服务。
- Claude Code 云端会话默认连不到交通数据来源；用你 Claude 账号里加的连接器可以绕开。
- 客户端能不能连着发出两段、能不能在后台接着做，取决于客户端本身。

## 数据与隐私

你的行程仓和运行库会保存消息、偏好、实况、决定和证据。客户端 App、Git 托管方、部署者和各数据来源，各按自己的条款处理数据；这不是“只有你的电脑能看到”的承诺。

代码把网址密钥和私有仓的部署钥匙分开存。运行时错误、反向代理和客户端仍可能写诊断日志；目前还不能保证每份日志都去掉了消息正文。自己部署的人要限制谁能读日志和数据，不记录请求正文和凭据。不要把密钥、私钥或没处理过的个人行程放进公开仓。

## 示例、开发与许可

- [examples/trip-2026-10-bud-wien/](examples/trip-2026-10-bud-wien/) 是一趟真实走过的行程（2026 年 10 月，布达佩斯和维也纳），人名都换成了代号。里面的页面都由 tripwright 生成：每天的安排、要去的地方、偏好与记忆、计划为什么改。
- 这趟给朋友看的网页：<https://zlxdengtianhei.github.io/trip-map-2026/>（默认英文，可切中文）。
- `uv run tripwright demo` 显示一趟虚构行程的程序输出。

测试：`uv run pytest -q`。MCP 冒烟测试：`uv run python scripts/smoke_mcp.py`。

代码采用 [MIT 许可](LICENSE)。上游数据和服务条款另行适用；MIT 许可不授予 Transitous 公共 API 的商业使用权。
