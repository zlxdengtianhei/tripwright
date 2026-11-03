# Implementation decisions — core v0.1

- MCP SDK 2.3.0 exposes `MCPServer`, having removed the old `FastMCP` import; use the installed API with explicit structured output and session-independent handles; adapters can change independently of the domain.
- SQLite owns one trip per authenticated identity in v0.1; every record read/write includes that identity and HTTP derives it exclusively from the bearer token; a future trip_id dimension can extend the same storage contract.
- Tables keep versioned JSON documents behind a small storage API, with `BEGIN IMMEDIATE` transactions, an in-process lock, append-only event triggers and primary keys per identity; normalized SQL columns can replace the encoding without changing domain models.
- `turn` only receives text and compiles a durable context; extraction is an explicit `propose` change followed by `commit`, because the default server has no language model; a later optional interpreter can produce the same change schema.
- Contexts bind both trip version and ledger revision; proposals and commits reject either form of staleness; this can later use content hashes instead of revision counts.
- Draft checking previews ledger changes in a rolled-back savepoint; only the committer applies them, creates a version and invalidates old jobs in one transaction; no alternate plan write path is exposed.
- Plan proposals use complete typed Trip documents; the checker reruns every day and every hard condition, so correctness does not rely on explanation edges; a future patch API must still materialize the complete candidate before committing.
- User-decided plan parts use explicit JSON-pointer locks, including whole-block locks when appropriate; semantic choices without such locks cannot be compared deterministically and must remain agent-reviewed; stable block-addressed paths can replace positional paths later.
- Soft conditions preserve quote, reason, optional strength and candidate interpretations; only a source-supported numeric predicate is accepted, and no natural-language threshold is invented; a richer predicate registry can extend the deterministic checks.
- Exact-scope preferences replace earlier preferences; narrower conditions mask broader ones only when the requested scope is wholly covered, while broad packages retain both exception and default; interval subtraction can later provide finer broad-query partitions.
- A same-message contradiction creates pending items and an undecided question; clashes with an existing hard condition preserve the old condition pending clarification; semantic conflict extraction remains the host's responsibility.
- Critical unknowns refuse commit by default; explicit `accept_unverified` permits a disclosed provisional version while each unknown keeps its consequence, fallback and unverified status; deployments may adopt stricter rejection policy.
- Provider lookup results retain source, branch/date context, fetch time and validity but do not automatically certify plan predicates; defaults are loudly marked fictional and can never pass a critical-fact check; real verification can add fact-specific evidence binding.
- Deep jobs currently rerun the complete deterministic check list, including scoped facts; external requery has no configured live verifier in this core slice, so missing facts remain unverified; an injected verifier can extend the durable handler.
- The checker independently adds opening-hours obligations for visits/meals, timetable obligations for fixed transit, ticket-applicability obligations for booked blocks and hard-node evidence checks; omitting the author's critical-fact or pending list cannot make these obligations disappear.
- Queue handlers execute at least once after crashes; idempotency keys deduplicate completed logical jobs and lease tokens fence expired workers from publishing; handlers must therefore be side-effect-free or idempotent, rather than claiming impossible general exactly-once execution.
- Job results move through generated, available and retrieved states, with stale overriding availability after a version change; retrieval explicitly does not imply that a user read the result; future delivery channels share the same inbox contract.
- HTTP uses an ASGI bearer identity boundary, per-user request limiting, loopback by default and stateless Streamable HTTP; OAuth and public deployment are later stages and can wrap the same domain service.
- Directory export writes the specified trip/places/bookings/day files, decisions.md and per-table JSONL history; import requires an empty identity and verifies the visible current plan against the snapshot before a transaction restores it; edited views require a proposal rather than silently rewriting history.
- Dates are full ISO dates, local clock times use the trip's IANA timezone, identities/areas/currencies are data and monetary sums use decimal arithmetic per currency; overnight activities must split at the date boundary in this version.
- Adjacent gaps and latest-departure calculations use UTC instants derived from the configured local date/timezone; ambiguous or nonexistent daylight-saving clock times remain unverified rather than receiving an arbitrary fold; offset-bearing blocks can extend the format later.
- Existing hard nodes and already bought/booked arrangements are preserved independently of explicit decision locks; changing those requires a future explicit user-review operation, so v0.1 refuses silent cancellation or reassignment. 〔B3k (D-53): a field of them changes when the same proposal carries the user's report or a confirmed correction naming that field; anything else is still refused.〕
- The default conversation rules are persisted in each user's profile on first receipt, with conversation-scoped rule records as well as readable text; long-term preferences stay in the ledger and are included in the profile view.
- The migrated core format retains place references, per-person tracks, three alternatives, uncertainty, app actions, fare references, hard nodes and booking deadlines; site-specific images/offers/trails and the old private Markdown tree are outside this core slice and can add separate models later.
- The fictional example and main skill are wheel data as well as repository artifacts, so installed CLI demo and MCP prompt/resource do not depend on a working checkout; deployment can replace examples without touching the domain.

Author validation uses tests under `tests/core`, `tripwright demo` and the SDK client in `scripts/smoke_mcp.py`. The main session owns independent acceptance, real host/mobile connections, publication and commits.

# Implementation decisions — B3a (2026-10-31)

- Usage claims carry stable block IDs; the usage module checks existence, people, date/time/place scope and a small explicit activity-kind registry, while subjective claims become `host_reported` and a noncritical unverified check; expanding that registry does not change ledger storage or the commit boundary.
- Block locks normalize positional legacy pointers to `/blocks/{id}` when recorded or migrated during commit; provenance-only evidence attachments do not change a locked choice; field-level pointers remain supported and a richer object-addressing scheme can replace this module independently.
- A later explicit user decision on overlapping lock paths for the same people/scope supersedes the old decision and retains its history; agent decisions cannot release user locks; partial releases and paid-booking cancellation remain separate future operations.
- A facts-only ledger proposal may commit despite conflicts with the unchanged plan; the resulting version exposes `needs_replan`, warnings and failed checks rather than claiming feasibility; combined plan/decision proposals retain normal refusal rules.
- Same-message items of the same kind with overlapping people/scope and differing readings raise a conservative possible contradiction even across keys; the same key or a shared bounded clock window also makes the items pending. Broad independent preferences remain applicable. One question per pair carries both texts and a neutral default; semantic disambiguation can later replace this guard without silently choosing for the user.
- Ledger previews deep-copy typed changes before annotating relations, preventing preview/commit from duplicating supersession edges; this preserves the existing transactional storage API.
- MCP business errors become SDK `ToolError` with the domain reason; SDK client tests cover real stdio and HTTP rejection messages; this exposes only errors already intended for the authenticated caller.
- `TRIPWRIGHT_PROVIDERS` selects synthetic (default) or live Transitous/Open-Meteo/Nominatim instances; dependency injection remains the offline-test and alternative-provider seam, and provider policy/attribution stays owned by the provider layer.
- Evidence targets identify a block or anchor, branch, date, fact and optional draft; query derives exact endpoints/time from the plan and only matching provider results bind a supported fact, with fetch time, expiry and a target snapshot; stale, synthetic, missing or mismatched evidence cannot pass.
- Deep jobs attach refreshed evidence to their own draft only after rechecking trip version and ledger revision, with external I/O outside SQLite transactions; transit timetables and hard nodes require exact saved endpoints, weather proves forecast availability only, and unsupported opening hours remain unverified with an explicit reason.
- One lazy asynchronous runtime owns provider I/O for queries and jobs so reusable HTTP clients stay on one loop; durable job leases renew while waiting, stale/cancelled work is interrupted, and shutdown cancels requests and closes providers; a separate worker process can replace the runtime later.
- Whole-day solving is transit `query(action=plan_day)` returning a durable, idempotent job with bounded timeout; `result(cancel=true)` persists cancellation and stops active I/O, while solver results never write the committed plan; the seven-tool surface remains unchanged.
- HTTP owns the queue lifecycle once per process and mounts optional `web_app(service,tokens)` at `/r/` outside MCP bearer middleware so the web app can apply its own access boundary; missing web modules are optional, missing dependencies inside an installed module remain visible startup failures.
- Conversation views separate server-checked and host-reported uses, include pending questions immediately, omit empty hard-node headings and deduplicate rendered unknown lines; stored check IDs remain intact for explanation and audit.

## 视图

静态行程站由 `tripwright.site.build_site(service_or_store, owner, out_dir)` 生成，或运行 `uv run python -m tripwright.site <db> <owner> <out>`。只读指定身份的已提交版本；当天按行程的 IANA 时区计算。每个日期一页，另有首页与决定页。页面写版本、生成时刻和核验时刻。中英标签即时切换，默认依次采用显式 language、行程 language、画像 language；均无时，标题含中文则用中文，否则英文。当前 Trip 格式没有 language 字段，因此标题推断只是回落规则，可通过函数参数或模块入口的 `--language` 指定。切换不翻译用户原有文字。

网站先在内存中生成全部页面，替换参与者姓名，隐藏住宿地点名称、坐标与地图入口，再扫描邮箱、长数字、订单样式与可选的私有名单。扫描失败不创建或更改输出目录，错误不回显敏感值。输出目录须不存在或为空，已有站点不被覆盖；调用方可在新目录构建成功后切换发布。`Policy(["私有词", "原名 => 公开别名"])` 可传给 `privacy_policy`，模块入口可用 `--privacy-policy` 读取私有名单。默认扫描不是识别人名和地址的通用脱敏器：发布者仍须提供额外名单并审阅产物。没有来源的外部事实仍标未核，列出后果和替代走法；排程的 fixed 不等于事实已核。

手机页由 `tripwright.adapters.web.web_app(service, tokens)` 提供 `/r/<短链>`；也支持挂载于 `/r` 后使用子应用的 `/<短链>`。推荐传入独立 `ResultLinks()`，由已认证的服务端代码调用 `issue(owner, ttl=...)` 得到 144 位随机短链，以 `revoke(link)` 撤销。注册表只存 SHA-256 摘要、身份与到期时刻，默认有效七天；重启即失效。也可传独立的短链→身份映射，原映射删除键立即撤销；映射的持久化、随机强度与到期管理归调用方。不能把 MCP 的 Bearer 映射传入这里：URL 会出现在历史和访问日志，若放全权 Bearer，泄漏将同时授予写入 MCP 的能力。结果短链只授予当前身份的只读页面；它本身仍应作为私密链接保管，部署时应隐藏 `/r/` 路径日志。

结果页仅以 `state` 和按 owner 分区的 `store.all` 读取材料，不调用会标记取回的 `result`。第一段是最近 context 的回答骨架与条件材料，宿主实际对话没有保存在核心，所以不冒充已写出的回答。第二段保留最近收件箱结果，先展示差异，再展示完整候选与核验；它若不是当前 context 最新作业的结果，明确标为先前任务，当前作业状态另列。旧版本结果显著标过期。当前作业没有结果时显示核验中、失败或过期状态与预计字段；核心未给预计时刻时如实说明，仅提示约八分钟是设计目标。页面另列当前版本今天的行程；旅行尚未开始时附下一天。响应设置 no-store、no-referrer、noindex、禁止嵌入与外部资源的 CSP。页上无提交、撤销或发消息入口。HTTP 主应用的挂载由另一写域负责。

`homepage/` 是独立的项目说明首页，与用户行程产物隔开。原版中英文版式与方法论页保留，运行状态与快速开始改为 README 的本地 stdio/demo 命令。`.github/workflows/pages.yml` 只允许手动 workflow_dispatch，从 homepage 目录发布；本路仅写配置，没有部署。

# Implementation decisions — B3c (2026-10-31)

- A lock covers where a block sits as well as its fields: each block-addressed lock records the block's date, candidate plan name and sorted track people when the decision is proposed (`Decision.placement`, server-owned and hidden from the input schema; a host value is overwritten), and the checker compares both; locks stored earlier are backfilled from the last committed plan, where they last passed. Moving a locked block to another day, into another candidate plan or into a track with other people is refused with the changed part named (`… (date changed)`); inserting or deleting other blocks, editing unlocked fields under a field lock and a later explicit user decision remain allowed. Placement is a value in the same lock record, so a richer object-addressing scheme can replace it without changing the commit boundary.
- The activity-kind registry moved into `checks.py`, which now gives must and forbid items with a recognized kind a deterministic predicate: a banned kind at a block's place or option for those people and that scope is a critical conflict per day and candidate, absent is passed; a required kind is checked once over the scope and is a conflict only when every day in scope has a concrete candidate and none has it. Free text or an unclassified place name that only mentions the activity stays unverified, so the service neither invents a violation nor claims a pass. A banned activity therefore cannot fall back to "unverified" and pass with `accept_unverified`. Limit: the registry matches keywords in the key and quote, so a ban whose quote only mentions an activity it does not ban (for example "no long coach ride after hiking") would also ban hiking places; replay should measure this before the registry grows, and an explicit host-declared activity field could replace the keyword match.
- A usage label cannot contradict the service's own check: when any `item:<id>:…` predicate is a conflict, `used`, `host_reported` and `not_applicable` become `invalid` with the conflicting check IDs as the reason; only an explicit trade-off of a soft condition stands. This holds for every predicate, not only activity kinds.
- Any proposal without a plan is ledger-only: plan conflicts it reveals become `warnings` and `needs_replan` on the unchanged plan instead of a refusal, and it needs no `accept_unverified` because it proposes no new plan. Facts never wait for the rest of a message: if another part of a ledger-only proposal is invalid, the draft keeps only the facts and `not_recorded` lists each left-out part with the validation reason; the other parts stay all-or-nothing so a declared pair is never split. Usages on a ledger-only proposal are refused with a reason. A rejected plan proposal says that its facts were not recorded and how to record them. Drafts stored before B3c keep their `facts_only` flag.
- The server no longer guesses whether two items concern the same object. Contract for hosts: `Item.exclusive_with` lists keys or IDs whose reading excludes this one (two readings of one intention, only one meant); `Item.same_object_as` lists keys or IDs that state the same object under another key. In one message, a declared pair or two items with the same key and overlapping people and scope, whatever their kinds, become `pending` with one question naming both readings and the default "Await clarification; neither new reading is selected". Across messages a declared item is handled exactly like the same key (later replaces earlier, an old hard condition is kept pending clarification, a narrower scope becomes an exception). Undeclared items with different keys stay active; when they share people and an overlapping bounded time window they get `relations.competes_with` and a `crowding` notice in the proposal, so the planner offers options per method §4 rule 3 instead of asking which reading was meant. The trade-off: an undeclared contradiction is no longer caught by the service and depends on the host following the `propose` description and `tripwright-main`; replay should measure how often hosts omit the declaration.
- Validation failures always carry a reason: `Trip` reports an unknown IANA timezone as a `ValueError` naming it, and every model validator is wrapped so a lookup or arithmetic error becomes a validation error with its message instead of an SDK crash whose text stays in the server log. The MCP tool wrapper reports `KeyError` subclasses by their message rather than as a missing argument. The smoke script asserts the bad-timezone reason through stdio and HTTP.
- The `propose` description and the `baggage_holder` field description tell hosts to set the holder of per-person tasks (luggage, tickets); free text is still not checked. The same sentence in `skills/tripwright-main` belongs to the skill owner.

# 接缝约定（2026-10-31，contracts_opus_20261031-1730）

正文在 `docs/CONTRACTS.md`，机读件在 `tests/contracts/`，样本在 `tests/fixtures/contract_sample/`。下面只写取舍的理由与以后怎样改。

- 新鲜度只按内容键（`plan_version_id`、`ledger_digest`）判，不按版本号判；两个写者会各自产生“版本 4”（样本 S4 与 V-300715-v4l），只有编号能区分。修订号与版本号照旧返回、照旧给 propose/commit 核，所以没有另建一套版本系统；以后改成整份内容哈希，只换摘要的算法。
- `ledger_digest` 用按序流水编号的 sha256，不用条数；重放会改顺序，条数可能不变而内容已变。这条在单库模式下多余但无害，A 接 Git 时不用再改接口。
- 原话水位不直接让结果过期，否则深核期间用户每发一句（“来得及吗？”）都要重算；改为“未定”，由宿主声明不带账（ack）或先记账。声明放在 `result` 上，不加在 `turn` 上：用的时候才需要它，也只多一次往返；A 若想在 turn 上加快捷声明，语义不变即可加。
- 任何账本改动都让在跑的作业过期，不按范围判；这与 propose/commit 已有的“Ledger changed”一致，避免“先展示、后被提交器拒”。代价是多重算；按范围判要 result 与 commit 同时改，留到 E2 有读数之后。
- 只改账本的 commit 不受“未定原话”拦，计划类 commit 受拦：用户说的实况先记下不会出错，旧计划被提交才会出错。
- 写入四态里 `pending_sync` 可以重复（推送被拒、重放成功、换了本地提交），`effective` 与 `conflict` 是终态；正本分支是本地分支（L1 无远程）时允许跳过 `pending_sync`，本地提交即生效。冲突通知在判冲突的同一事务里写，turn 一直返回它，直到一个基于新头的计划提交生效；不另设“已读”接口，因为服务端无法知道用户是否读过。
- 第二段“核过”“草稿”由 verdict、commit 与写入状态推出，回答数据里不另存布尔值，免得两处不一致。
- 机读包的记录内容只写一处（`records`），各组只列编号；交给宿主的是 Markdown 加去掉 records 的 `pack_meta`，满足“不把全量数据发两遍”。完整性分三档：complete、partial（编号在、原文没展开）、unknown（适用集没定）；渲染器只能把非硬约束降成编号行，不能删编号。
- 写法 9 行由 B 作为源片段维护，样本里用 `{{WRITING_RULES}}` 占位，比较前替换；这样 B 改写法不必改样本，C 装配 skill 时也收同一份片段。
- 两条用户本人的偏好没有用户定的次序时，处置不许 traded_off，只能分进不同方案；契约测试按样本核这一条（候选的两套方案、各自的块编号）。这把设计第 9.5 节变成可机械核的形状，不实现选择逻辑本身。
- 写域按“谁的代码决定字节”分，不按“谁写盘”分：首屏与视图的字节由 B 定、A 的提交器写盘；AGENTS.md、CLAUDE.md 与宿主角色文件由 C 定、A 的 init 写盘。`.gitignore`、`.gitattributes` 归 A 并由代码写，不做成模板，所以“`.tmpl` 只由 C 写”不和仓布局重叠。`checks.py`、`trip.py`、`usage.py`、`evidence.py`、`locks.py`、`query.py`、`providers/`、`solver/` 列为冻结核心，三块都不写，要改经主会话。
- 契约测试用 `jsonschema` 核形状；它是 `mcp` 的硬依赖（`uv.lock`），没有新加依赖。若主会话要在 `mcp` 之外也保证它在，可在 dev extra 里显式写上。
- 没核的：样本只覆盖一条快问、一次第一段、一次第二段和一次冲突；E1 的两克隆重放、`partial` 与 `unknown` 两档、`over_budget` 省略、英文回答都没有样本，A、B 实现时先补样本再写代码。行格式与预算的 token 估算没有冻结，以 B 的实现与回放读数为准。

# 角色与装配（C）（2026-10-31，impl_agents_opus_20261031-1805）

- 一份源出全部形式：四个角色源 `src/tripwright/agents/roles/*.md`、六个步骤源 `steps/*.md` 加 B 的片段，由 `python -m tripwright.agents build` 生成主 skill 与三个角色 skill、Claude Code 与 Codex 的 agent 文件、四个 MCP prompt、三个 L3 服务端 agent 提示词、L0 稿和服务端 instructions。目标间的差别写在源里的整行指令：`only`（只给哪些目标）、`include`（插入 B 的片段或步骤）、`reference`（在 skill 里挪进 `references/`，在 prompt 与 agent 文件里内联，因为它们打不开 skill 的 references）。没有另造模板语言之外的机制；指令写错一律拒绝构建并给出文件与行号。
- 生成说明的位置：CONTRACTS 第 4 节要求首行写“由…生成”。Claude Code 只在第 1 行认 frontmatter（官方子代理页：开头 `---` 不在第 1 行的文件被跳过），skill 同理，所以 `SKILL.md` 与 `.claude/agents/*.md` 把说明写成 frontmatter 内的第一行 YAML 注释；TOML 用首行 `#`；JSON（`build.json`、`mcp/index.json`）用首个键 `_generated`。`CLAUDE.md` 按包要求只有一行 `@AGENTS.md`，不带说明；它的“不要手改”由 `AGENTS.md` 的生成文件一节交代。要不要把这两条例外写进 CONTRACTS，由主会话定。
- B 的片段原文照收：经 B 的 `tripwright.render.writing.fragment(name, language)` 读（18:53 起；名与语言取 B 的 `NAMES`、`LANGUAGES`，片段放哪由 B 定），记成 `<名>.<语言>.md`。主 skill 正文收四片中文；`references/writing.md` 收十条规则中英两份与四片英文；MCP 主 prompt 与 L0 稿收四片中英两份。缺的片段出一行可见占位“等 B 片段：<路径>”。`generated/build.json` 记每片的摘要；“重新生成＝已提交”的测试在不一致且只因 B 的片段变了时标 xfail（“等 B 片段”），否则失败，所以 B 交新片段不会把 B 自己的测试跑红，主会话也能从 xfail 看出要重新装配。`build --check` 不放宽，片段一变就报。
- 承诺检查（`promises.py`）：包里第 1、4 项点名的每条承诺，在对应生成件里核必须有的句子与不许有的说法，构建时不过就不写盘；每条在 `tests/agents/test_agents_promises.py` 有删掉句子或加入坏说法的坏样本。这只证明字面在，不证明宿主照做；宿主是否照做由冷读与回放判。引号里提到禁用词（“成功”）算禁止，不算使用。
- 宿主格式（2026-10-31 读官方文档）：Claude Code 子代理页 https://code.claude.com/docs/en/sub-agents（必填 name、description；`tools`、`disallowedTools` 逗号串；不认识的字段静默忽略，所以生成器只放行文档列出的字段；不写 `tools` 时继承全部工具含 MCP；默认后台运行，后台子代理保留全部 MCP 工具；子代理加载 CLAUDE.md 与作为项目说明的 AGENTS.md）。Codex 子代理页 https://learn.chatgpt.com/docs/agent-configuration/subagents（`.codex/agents/*.toml` 必填 `name`、`description`、`developer_instructions`；`sandbox_mode`、`mcp_servers` 等写在 agent 文件里不覆盖父会话；子代理继承父会话的 MCP；本地 Codex 在直接请求或适用的项目、skill 说明下才派子代理）与配置参考 https://learn.chatgpt.com/docs/config-file/config-reference。
- 子代理的限制：Claude Code 三个子代理都 `disallowedTools` 掉 Write、Edit、NotebookEdit 与 `mcp__tripwright__turn`、`mcp__tripwright__commit`，核验与研究员另禁 `propose`；需求角色可调 `propose`（只出草稿，主 agent 提交）。这依赖用户仓把 MCP 服务注册为 `tripwright`；名字不同时禁令不生效，只剩角色正文里的“不调”。Codex 没有按 agent 的工具禁令，只靠正文。模型：核验、需求 `inherit`，研究员 `sonnet`（推荐默认，便宜一档足够查一条事实；用户可改）。Codex 不写模型键，继承父会话。Codex 名字用下划线（`tripwright_verifier`）：文档例子只有下划线，连字符没试过。
- 主角色不生成 Claude 或 Codex 的 agent 文件：它是主会话的工作法，走 skill 与 MCP prompt；生成成子代理会让主 agent 把自己派出去。
- MCP prompts：`register_prompts(server)` 注册四个无参数的 prompt，文本来自 `generated/mcp/*.md`（去掉说明行），标题与描述来自 `generated/mcp/index.json`。同名 prompt 已存在时先删后注册：SDK 遇到同名只记警告并保留旧的，那样主 prompt 会停在旧文本。服务端 instructions 由 `tripwright.agents.server_instructions()` 读生成件。用户仓里 C 决定字节的文件由 `tripwright.agents.user_repo_files()` 给出（路径 → 字节），A 的 init 照写。
- 〔20:19〕`turn` 能当轮记账：主角色源、第 1 步与第 5 步改成“这条消息说的事实、条目、关系、待核放进 `turn` 的 `record`（`source.event: "this"`），返回的包已含它；决定与计划仍走 `propose`/`commit`”。核验取宽包用 `state(view="pack", draft_id=…)`（核验角色与第 4 步）。用户仓加 `.mcp.json`（源 `templates/mcp.json.tmpl`，把本地服务注册为 `tripwright`，命令 `tripwright --repo . serve --stdio`），子代理的 `mcp__tripwright__*` 禁令因此对得上，有测试核两边同名。JSON 不能写注释，所以 `.mcp.json` 和 `CLAUDE.md` 一样不带生成说明。Codex 不读 `.mcp.json`，五级说明里写了 `.codex/config.toml` 的配法；要不要由 init 也生成它，由主会话定。
- 主 skill 正文：自写部分 8,172 字节（预算 8,192；每加一批都删掉与 B 片段、步骤源或参考页重复的半句来抵），连 B 的四片中文共 14,169 字节，整份 15,512 字节。CONTRACTS 规定四片都在正文，所以超出约 8 KB 的部分是片段；工具细节挪到 `references/tools.md`。L0 稿约 22.5 KB、MCP 主 prompt 约 26.4 KB，因为收了中英两份片段；有的 app 的项目说明有长度上限（没核）。以后按语言各出一份 L0 稿，可减到约 14 KB。
- 单 agent 无工具版的“写回仓”：每步无工具版末尾写本步加进“写回仓”块的内容（没有就写“本步不写”），每条回复末尾出一个汇总块。理由：步骤三可能以“回‘继续’”结束本轮，用户若就此离开，第一步的原话也要已经给出；逐步各出一块会把同一条回复切成几块，用户难提交。无工具时没有深核结论、没有提交，第二段标题写“安排（没核完）”，用户确认提交前加“（草稿）”，不写“核过”。
- 〔已被下一条取代，18:44〕一趟一仓（18:31 主会话转达的更正，R-38）：模板、五级说明、步骤源与研究员角色都不写 `trips/<趟>/`。读的顺序写到“首屏 → 当天的预编包 → 今天的视图 → 按编号取单条”；文件夹名只在无工具版的“写回仓”例子里出现，写成相对行程仓根，并写明以仓格式为准。仓格式归 A；manifest 的 `user_repo` 仍写 `trips/*/…`，要由主会话与 A 改。
- 仓布局回到一个仓、每趟 `trips/<趟>/`（18:44 主会话转达：18:31 那条是转写错误）：模板的读的顺序写到首屏点名的当前趟文件夹，步骤源与研究员角色的路径改回 `trips/<趟>/…`，长期内容在 `profile/`。模板与五级说明的测试反过来核“读的顺序进到 `trips/<趟>/`”“一趟一个文件夹”。
- 开新一趟（18:44 新要求）：主角色源加一节、步骤源加第 0 步（有工具、无工具两版；装配器允许步骤从 0 编号）。做法：在对话里列出长期条目（偏好、同行人、常驻规矩、资料分组，每条一行原句要点与来自哪趟），问这趟哪些还用，不让用户去看文件；按回答记账。没回答前长期条目照用、标“待确认”（推荐默认，理由：不用会重犯“记了没取出来”）。〔20:19 按 A abb09fc 定，取代原先的两个占位〕开新一趟 `turn(new_trip="YYYY-MM-名")`（先同步，写入没进正本分支时拒绝，提示 `tripwright sync`）；列条目 `state(view="memory")`；记“沿用／这趟不用” `turn(text=回答, request_id, record={"carry":[{id, status: confirmed|not_used, source:{event:"this"}}]})`；无工具版写 `trips/<趟>/ledger/carry/<编号>.yaml`（id、status、status_event）。承诺检查 `new-trip-calls`、`new-trip-without-tools` 核这些调用与文件，`new-trip-carry-over` 不许再出现“待 A 定”。
- 交通与地点（18:31 更正，R-39）：主角色源与服务端 instructions 写明：每个地点与每段交通在正文里给出要用的信息和在哪个 App 搜什么（确切搜索词，原文拼写），不靠用户点链接；链接进 App 或浏览器都可以。研究员的报告加一栏“search”。B 的片段原文照收，不改。
- S4 语义（18:31 更正，第 3 次判词）：主 skill、服务端 instructions 与需求角色按新语义写：`same_object_as`、`exclusive_with` 只在两条的人、日期、时段重叠时成立，重叠外两条照常生效；同一条消息里没声明、又重叠的两条都留，服务端给不挡路的“可能矛盾”提示，回答里点明两种读法与默认。提示的字段名先写“待定，现为 `crowding` 与 `competes_with`”；〔19:23 定名，按 B3d fe1e5632〕已改成 `possible_contradictions`（turn 与 propose 返回，不挡路），回应用条目的 `compatible_with` 或 `changes.relate`（`exclusive_with`、`same_object_as`、`compatible_with`，只对同条消息的两条）；只抢时间的两条仍是 `competes_with` 与 `crowding`。主角色源加“编辑计划沿用 `state` 里的 `plan.id`；锁认方案 ID 与位置、人员 ID、地点身份，不认显示名”，核验清单同样核身份不核显示名，并核包里没解决的可能矛盾。服务端 instructions 并入了 `mcp.py` 里 B3d 新加的说法（锁与计划编号、needs_replan、不编数值门槛等），A 换用 `server_instructions()` 时不会丢句。承诺检查加 `possible-contradictions-named`（不许再有“field names pending”）与 `locks-follow-identity`。
- 用户仓带 skill（18:42 主会话定）：`user_repo_files()` 另给 `.claude/skills/<四个角色>/` 的 `SKILL.md` 与 `references/`，不带 `MAINTAINING.md` 与 `evals/`（给维护者，不给宿主）。skill 先从安装包里找，找不到再从检出的 `skills/` 找；两处都没有就报错并点名要加的 force-include，不静默少带。Codex 的仓内 skill 位置，官方 Build skills 页（https://learn.chatgpt.com/docs/build-skills，2026-10-31 读）写的是 `$REPO_ROOT/.agents/skills`（也扫当前目录往上的 `.agents/skills`），没有 `.codex/skills`；要不要也写一份，由主会话改 manifest 后我再加。18:53 已采纳（CONTRACTS v1.1 第 4 节）：`user_repo_files()` 给 `.claude/skills/` 与 `.agents/skills/` 同样的字节，复制两份，不用符号链接（Git 的符号链接在 Windows 上不稳）。
- 第 0 步对齐 CONTRACTS v1.1 第 3.5 节的记忆数据：长期条目的三种状态照它的说法写（这趟沿用 confirmed、待确认照用 pending、这趟不用 not_used）；无工具版先读 `trips/<趟>/views/memory.md`，没有再请用户贴 `profile/` 的文件。
- 没做的：冷读与宿主真实触发测试没跑；Codex、Claude 手机 Code 页没实连；L3 提示词的 JSON 交回形状是给将来的核验运行器的提议，运行器还没有；`tripwright init`、MCP 的接线与 `pyproject.toml` 打包要 A 与主会话改（见交回）。

# 渲染与输出（B）（2026-10-31，impl_render_opus_20261031-1805）

代码在 `src/tripwright/render/`，测试在 `tests/render/`。下面写取舍的理由与以后怎样改。

**给别的块的函数签名（接线由 A 做）：**
- `render_pack(pack, *, sources=None) -> {markdown, tokens, required}`；`pack_meta(pack, required)` 给宿主的机读部分（去掉 records、full_quotes，带降级后的 required）。`sources` 是 `{事件号: {received_at, speaker, nature}}`，A 从原话取。
- `render_answer(answer, *, orders=None) -> str`；`orders` 传这次包里的 `orders`，用来拦“权衡掉”的字样。数据不合约定时抛 `ValueError`，理由写在消息里。
- `repo_files(states, *, current, snapshot, packs=None, sources=None, memory=None) -> {仓内路径: 文本}`；单个文件用 `repo_readme`、`trip_readme`、`today_view`、`day_view`、`pack_view`、`index_view`、`decisions_view`、`memory_view`。`state = {path, trip, records, snapshot, notices?, events?, needs_replan?, warnings?}`，`records` 要是快照那一刻的记录；`needs_replan`、`warnings` 取已提交版本的同名字段；`packs` 与 `memory` 都按趟编号给。〔20:19 起〕只有当前这趟给完整 state，别的趟只给计划（`records` 空），`repo_files` 只生成当前这趟的文件夹。
- `transit_leg`、`place_line`、`timetable_place`（交通行与地名行的数据，带搜索词与链接）；`tool_hint(region, reports=…)`；`evaluate(now, legs, hard_nodes, markers)`（查询触发）；`build_options(points, orders, names=…)`、`unauthorized_choices(...)`（方案栏）；`fragment(name, language)`（C 装配用）；`python -m tripwright.render.check_answer`。

**取舍：**
- 宽包“原句全文”行要收件时刻、说话人和来源性质，约定 3.1 的 `full_quotes` 只有原文。渲染器加了可选参数 `sources`；没有它时只写记录上的说话人，不写时刻，也不写“逐字”，免得把转述标成逐字（硬门槛一）。约定若把 `full_quotes` 改成带这三项的对象，只改 `pack.quote_lines` 一处。〔20:19 已改〕v1.1 的对象 `{event, text, said_at, speaker, nature}` 直接读，`sources` 只给 v1 的纯文本形；对象缺哪一项就写“时刻未知”“说话人未知”“来源性质未知”，不猜，也不写“逐字”。
- 预算按“Markdown＋给宿主的 JSON”算，因为宿主两样都读（设计第 6 节、反方 1.6）。token 用固定规则估：汉字等宽字符各算 1，其余每 3 个字符算 1；没用真实分词器校准，偏高。回放拿到宿主的用量后只改 `tokens.py` 的两个系数。
- 降级按节整组降，顺序是目标 → 规矩 → 偏好 → 待核 → 待定 → 已定 → 实况；已定排在实况前，因为提交器仍守锁定，实况没有这层保护。一组降成编号行不省 token 就不降：样本里的记录都短，逐条降时编号行和原行一样长，只丢信息。硬约束、总带原则、硬节点、原句全文与逐条处置不降。
- 降完仍超时分两种写：只因不降的部分超了写“硬约束过多”（约定原词）；写法片段、包头和机读部分本身就超预算时写“固定部分超预算”，不把它算在硬约束头上。实测窄包的固定部分约 1,500 token（写法片段约 560，机读 JSON 约 620），高于约定 3.2 的快问推荐预算 1,000。所以按现在的推荐值，每个快问包都会标超预算。要么把快问预算调到约 1,800，要么给宿主的 JSON 再瘦身；归主会话与 A 定。
- 方案栏：没有用户定的次序时，同一对偏好的各处取舍按整天累加，不用每处的阈值决定给不给方案（反方 E5：三处各“多走 10 分钟省 €4”合成“少走 30 分钟，多花 €12”）。有用户定的次序时照用，不再出选择，并写明依据的原话号。几处独立的取舍并进两三套完整方案，另起一行写明可以分开选；四种组合放不进三套时，不由 agent 挑掉一种。`render_answer` 拒绝：第一套带差异、后面的套没有数字差异（或“没核”）、推荐的理由在排用户偏好或不含任何事实（时刻、距离、价钱、封路、天气、末班）、有“到哪里之前定”还给默认、方案存在时任何一行写“权衡掉”。推荐理由是不是事实，机械只能拦明显的，语义由回放与判官判。
- 查询触发：硬节点按全链路（各段时长、换乘、缓冲）反推最晚出发；余量落在第一段的视野（推荐默认 120 分钟）内，或现安排出发晚于它，才查这条链上的几段。频率三档：间隔不少于 60 分钟查；已知更密不查；不知道时只在它决定眼下这一步时查一次，其余段写“班次没核”，并返回 `whole_day: false`，不承诺整天可行。时刻都是同一天的 HH:MM，跨夜链路本版不管。
- 地区表 `regions.yaml`：用户原话只说了湖区，所以只把“奥地利湖区”标已核，奥地利其他乡村巴士标推断（包里写的是“奥地利乡村巴士已核于用户原话”，我收窄成已核到湖区，免得把推断标成已核）。开源仓里不写用户的行程细节，依据写成“使用者实地反馈”。用户说过的实况优先于截图，截图优先于表。SBB、DB 的链接格式来源弱，不生成链接，只写 App 名与用法。
- 机械检查 13 项只是信号（适用条目：不把格式检查当成质量证明）。句长、模糊词、英文句长是提醒，其余算不合格。

**10-31 18:31 主会话转达的用户新要求（R-39；R-38 的读法已由 18:44 更正），已照改：**
- 不管点不点链接，正文都给出要用的信息，写清在哪个 App 搜什么。交通行与地名行的数据加 `search: {app, query}`，`query` 是原文拼写的确切搜索词。交通行在 App 能填起终点时写“<App> 搜 起点 → 终点”，代替单写“起点 → 终点”，一件事只说一次；地图 App 从手机当前位置出发，所以写“起点 → 终点 · Google 地图搜 终点”。地名行的第一行仍只放地名，第二行加“<App> 搜 <搜索词>”。链接只是方便：Google 地图链接进 App，Scotty 链接进浏览器，两种都留（用户 10-31 在手机上点过）。
- 地区表的用法改成“在这个工具里输入什么、点哪里”，放在工具上（`tools.*.usage`），每个工具标 `usage_checked: false`：按钮和栏位的叫法没在手机上核，只写动作，不引按钮上的字。
- 机械检查加第 13 项 `search_terms`：交通行或地名说明行没写“<App> 搜 …”，或链接里的站名、地名、时刻正文里没有，就报不合格。
- 契约：`schema.json` 的 `leg`、`place_line` 加必填的 `search`；三份回答样本照新格式改；`contract_checks.leg()` 与地名行核对改成带搜索词。CONTRACTS 3.4 的文字要主会话同步改（B 不改 `docs/CONTRACTS.md`）。
- ~~一趟一仓~~（18:31 那条的第二点，18:44 主会话更正为转写错误，已撤回）：仓布局回到约定第 4 节，一个仓、每趟一个 `trips/<趟>/`。仓的 `README.md`（`repo_readme`，不超过 25 行：现在哪趟、下个硬节点、到期待核、要你定、记忆计数、agent 读法、各趟入口）与每趟的 `README.md`（`trip_readme`，不超过 40 行，文件地图相对本趟文件夹）都照约定生成。

**记忆视图（18:44 主会话转达的用户新要求）：** 每次开新一趟，较稳定的偏好等长期内容直接导入，像 agent 的记忆；要用直观、可查看的方式展示。
- 两页同一份行模型（`render/memory.py` 的 `rows`）：仓内 `trips/<趟>/views/memory.md`（`memory_view`），网站 `memory.html`（`build_site(..., memory=…)`，导航多一项“记忆”，经网站原有的隐私替换）。放在每趟的 `views/` 下，因为状态是相对这一趟说的；路径落在约定已有的 `trips/*/views/**` 里，manifest 不用加行。仓与每趟的首屏各加一行记忆计数。
- 分四节，顺序固定：偏好、同行人、常驻规矩、资料；每节里待确认的排前面，因为要用户回话。每条写：状态（已确认沿用、待确认、这趟不用）、谁的、原句（没有原句写要点）、来自哪趟哪条原话（说话人、日期、逐字或转述）、存在哪个文件、最近用在哪趟哪天、状态依据的原话号。顶上一行计数，有待确认时加一句“回‘沿用’或‘这趟不用’”。
- 提议的机读形状 `tripwright.memory/1`（主会话加进接缝约定，A 照它出数据；样本在 `tests/render/fixtures/memory.json`）：
  `{schema, trip, as_of, entries: [{id, kind, text, quote, subject, strength, source, status, status_event, last_used}]}`。`trip` 是当前这趟的编号，`as_of` 等于快照的 `taken_at`。`kind` 为 preference、companion、rule、material；`text` 是一句要点，`quote` 是原句（没有为 null）；`source` 为 `{trip, event, said_at, speaker, nature, path}`，`nature` 沿用事件的四种来源性质，`path` 是正本文件（`profile/items/<编号>.yaml`、`profile/people.yaml`、`trips/<趟>/research/...`）；`status` 为 confirmed、pending、not_used；`status_event` 是这趟里确认或搁置它的那条原话（没有为 null）；`last_used` 为 `{trip, date, version_id}` 或 null。
- `memory_problems` 拒绝：带原句却没有来源性质（免得把转述显示成逐字）、既没有原话号也没有文件、未知的种类或状态、同一编号出现两次。“这趟不用”由用户的话或 agent 推断来定，归 A 与 C；B 只照数据显示，并把依据的原话号写出来。

**没做的：** ~~`src/tripwright/views.py` 的 `answer_skeleton`、`day_text` 仍被调用，没删~~（20:19 已删，见下）。~~`over_budget` 省略原因没有样本~~（A 定了“非必需”，样本 `pack_omitted` 由 A 补，B 的渲染与它逐字一致）。链接都没在手机上逐条点过（用户只点了两条样例）；SBB、DB 链接未做。

**第二批（20:19 主会话转达，A 提交 abb09fc 之后）：**
- 删掉 `src/tripwright/views.py`（只剩 `answer_skeleton`、`day_text` 两个函数，服务已改用 A 的 `repo.files.day_text` → `day_view`）。网站结果页的“第一段材料”改读这次 turn 存进上下文的约束包（`context["pack"]`）：写回答骨架（快问、上报实况用快答，其余用第一段）和包的读者版 `material_lines(pack)`（包头、完整度、每条一行、次序、没放进来的；不带写法片段、细节指针、原句全文与逐条处置，也不按预算缩）。宿主实际写出的回答服务不存，页面照旧说明。从仓重建的运行库没有包，页面写“这条材料没有存约束包”。“当前版本：今天”改成和仓里同一份日视图：经 `state(view="day")` 取 `day_view` 的文字，再按视图用到的 Markdown 子集转成 HTML（标题、列表、https 链接，其余转义）。日视图只有中文，英文界面的用户在这一段看到中文。网站的每日页（`<日期>.html`）还用原来的 HTML（带逐项核验、票价、合计），没换成 `day_view`。
- 需重排：`day_view`、`today_view`、`trip_readme` 在 state 的 `needs_replan` 为真时写“需重排：这一版计划和后来记下的情况冲突，先重排再照它走。”，并列冲突（`warnings` 里每项的 `text`，至多 3 条）；日视图只列编号里带这一天日期、硬节点或块编号的冲突，没有就写“这一天没有冲突；冲突见 <这趟 README>”。仓首屏“现在”一行加“｜需重排”。A 的 `state(view="day")` 现在自己在文字前加一行，等 A 把 `needs_replan`、`warnings` 放进 state 后去掉，否则会写两遍。
- 包里的新字段：导入后待确认的条目（`memory: "pending"`）在编号后先写“待确认”。待定按 `kind` 写清要谁做什么：`possible_contradiction` 加两种读法与“宿主：问用户两条都算还是只算一条；没答前照默认，不替用户挑读法”；`memory_pending` 加“宿主：在对话里问用户这几条这趟还用不用，按回答逐条记‘沿用’或‘这趟不用’”；`undecided` 加谁的、可选项，有默认写“要用户定；宿主：问一次，没答前照默认”，没默认写“……没答前不替用户选”。没有 `kind` 的待定行照 v1 不变。
- 来源缺说话人或性质（以前会崩）：v1 纯文本加 `sources` 时同样写“未知”，不再抛错。
- 仓首屏：别的趟只读计划的标题、起止日期与时区，不按现在的 Trip 模型重新校验，免得一趟旧计划在模型变严后让当前这趟的首屏生成失败。当前这趟已开但还没有计划时，可以传 `current=<趟>`，首屏写“现在：<趟>，还没有提交的计划”。

# Implementation decisions — B3d (2026-10-31)

Three judged rounds each bypassed the user lock with a new shape (position, then day or people, then a same-name plan or a redefined place). They shared one premise: that comparing one local field of the proposal protects the object the user chose. B3d drops that premise and checks an invariant on the resolved plan instead.

- **Lock invariant.** A user lock holds when the user's choice still holds in the plan that would be committed. For a block lock (`/blocks/{id}` or `/blocks/{id}/{field}`) the snapshot stored in `Decision.placement` resolves what the choice depends on: the day's date, the plan's stable ID and its position among the day's alternatives, the sorted person IDs of its track, the trip timezone, and a `references` map keyed `kind:id` with the identity fields of the track's people and of every place, fare, booking (and the booking's place) or baggage holder that the locked value names. A field lock names only that field's references; people, date, plan and position still apply. `locks.lock_changes` is the only comparator; propose, deep checks and commit call it through `Versions._check`. It names what changed (`value`, `date`, `plan`, `rank`, `subject`, `timezone`, `place:gym-a`, `person:u2`, `booking:…`, `fare:…`, `missing`) and the commit refusal repeats it. A later explicit user decision still supersedes the old lock (same people, scope and overlapping path), and its snapshot is taken from its own candidate.
- **Plans have identity, display names have none.** `Plan.id` is optional and unique within its day (`Duplicate plan id on …`). Before anything compares a candidate, `locks.identify` assigns missing IDs: a plan without an ID inherits the ID at the same position of that day in the previous version when the candidate does not use it elsewhere (a host that omits IDs is taken to keep the order); otherwise it gets `P<n>` that neither version uses on that day. Committed versions always carry IDs; legacy versions without them are identified the same way on the fly. The lock also compares the position, because the first alternative is the one followed (site, now-screen): moving the locked plan behind another, inserting an alternative before it, swapping plan IDs, stripping IDs and reordering, or moving the block into any other plan of any name is a change. Adding alternatives after it, renaming plans and reordering the alternatives behind it are not. Trade-off: removing an earlier alternative also shifts the position and needs a new user decision.
- **Identity versus description of referenced objects.** Place: `name`, `name_local`, `kind`, `area`, `coords`, `transit_id`. Coordinates both present and within 100 m are one place (a precision fix); adding coordinates or a transit stop to a place that had none is a change, because it picks a branch the user did not pick. Description that may change: `notes`, `hours`, `price`, `links` (key information is in the text per D-28; links are generated from `name_local`), `reason`, `checked`, `rain_ok`, `priority`, `subject`. Person: `name` (a same-name person with another ID is another person; renaming or swapping names in the locked track is a change). Fare: `segment`, `ticket`, `subject`, `date`, `included_in`, `alternative` (amounts may change). Booking: `date`, `kind`, `what`, `subject`, `place` (status, due, contact, why_now, where, if_missed may change; bought or booked arrangements stay separately protected). Samples: `tests/core/test_b3d_regressions.py::test_s6b_*` and the generator's place, person and booking transformations.
- **Other lock paths.** Positional paths outside blocks are rewritten to identities when recorded: list elements with an `id` (places, people, bookings, plans, anchors) or a day's `date` are addressed by it (`/places/0` → `/places/gym-a`, `/days/0/title` → `/days/2031-05-01/title`), so a reorder cannot point the lock at another element. Tracks have no identity and keep their index; a track reorder then looks like a change (fails closed). These locks compare the value at the path only.
- **Locks stored earlier.** Locks without a B3d snapshot are rebound from the last committed plan, where they last passed. An old placement that cannot be rebound stays as stored; it never matches a current snapshot, so the lock fails closed until the user decides again. Drafts stored before plan IDs get IDs at commit the same way propose assigns them.
- **Limits kept.** A copy of the locked activity under a new ID elsewhere is not an override (the original still holds; a copy that overlaps the same person's time is a chain conflict). A lock recorded ledger-only on a block that is not yet in the plan binds only its value until the first committed version that contains the block. Check IDs such as `chain:{date}:{plan name}:…` still use display names; two same-name plans can share an ID prefix, which affects reading, not lock decisions. The contract sample (`tests/fixtures/contract_sample/`) has no plan IDs and `contract_checks.blocks` maps blocks to plan names; both are main-session files.
- **S1a: activity coverage per person.** For must, prefer and optional items with a recognized activity kind, the checker records per person and per alternative whether some in-scope block is at a matching place (`checks.activity_coverage`). A day covers a person only when every alternative plan of that day does (each alternative is a choice the user may take); a person is covered when some day in scope does; `*` means every trip person. Statuses per person are `covered`, `missing` and `unknown` (unplanned day, or only free text mentions it). A must item is `passed` when everyone is covered, `conflict` when anyone is missing, else `unverified`; the check details list each person. `usage.account_usages` reads the same coverage result: a `used` claim is `server_checked` only when every person is covered, so a partial group is never marked server-checked; for a soft item it becomes `host_reported` with the uncovered people named, for a must item the conflict makes the label `invalid`. "Together" is not checked: two people covered on different days both count.
- **S4a: a declaration only holds where the two items overlap.** `same_object_as`, `exclusive_with` or a shared key make two items of one message pending only when their people, dates and time windows overlap (method §4: object, people, scope and time first). Day one go to X and day two do not, morning and evening, Ash and Bea: both stay active, no question. Across messages this was already scoped.
- **S4b: possible-contradiction notice.** Two items of one message with different keys, overlapping people and scope, no declaration and different readings both stay active and get `relations.possible_contradiction_with`. Notice shape: `{kind: "possible_contradiction", item_ids: [a, b], quotes: [a.quote, b.quote], readings: ["Both hold together: …", "Only one is meant: … or …"], default: "Keep both active (the first reading) until the user says otherwise.", blocking: false, resolve, subject, scope}`. It appears in `propose` (`possible_contradictions`: all open notices after the change) and `turn` (open notices touching the items of that turn's package), until resolved; the package items also carry the relation. Same-window pairs keep their `crowding` notice as well. Hosts resolve it with `Item.compatible_with` up front or later with `Changes.relate` `{items: [id, id], relation: exclusive_with | same_object_as | compatible_with, source}` on two active items of one message: exclusive or same-object makes overlapping items pending with one question naming both readings; compatible clears the notice. A message recorded over two proposals gets the same notice. Not in the contract pack: whether `pack` should carry notices is the main session's call.
- **X1a: query arguments are checked at the boundary.** `query.checked_arguments` runs first in `Tripwright.query` (so stdio, HTTP and the service API share it): typed known fields (target as an object with `block_id` or `anchor_id`, `date`, `fact` among the supported facts, optional `place_id`, `draft_id`; weather `place` as `{name, lat, lon}`; strings, integers, numbers, booleans; `day` as an object) and required fields per kind and action. A refusal lists each field, the expected type and what was sent, e.g. `Invalid query arguments: arguments.target must be an object {block_id or anchor_id, date, fact, optional place_id and draft_id}, got array`. Unknown fields pass through as before; an empty target still means no target; downstream code receives the validated values. Other internal exceptions are not converted, so their text still stays out of tool results. `propose.usages` entries that are not objects with a string `item_id` are refused with their index. The solver's own `Malformed day input` text is unchanged (solver is frozen core).
- **How it is tested.** `tests/core/test_b3d_lock_aliases.py` is a metamorphic generator: 26 legal transformations and 32 alias attacks (6 of them change only locked values, so they break a whole-block lock but not a place-field lock) on a fictional trip with two same-name alternatives and two people named Bea; each attack alone, each legal change alone, each redecidable attack followed by the user's new decision, 48 random compositions per lock kind (whole block and place field) with asserted mix, and committed walks of legal versions with attacks after each; attacks must be refused at propose naming the lock and again at commit. No new dependency (`random` with fixed seeds). 20 mutations of the new judgements all turn the B3d tests red, including the stdio and HTTP server subprocesses, which import the same mutated copy.

# 仓、状态与快照（A）（2026-10-31，impl_repo_opus_20261031-1922）

代码在 `src/tripwright/repo/`、`packs.py`，测试在 `tests/repo/`。下面写取舍的理由与以后怎样改。

- **正本与索引。** 用户 Git 仓是正本，数据库降为索引、发件箱与运行态。索引只装“当前这一趟＋画像的对话默认”，沿用“一个身份一趟”的表结构，换趟时整代重建；别的趟留在仓里，只在首屏列出。所以领域模块（ledger、versions、checks）不知道仓存在。以后要同时开几趟，给表加趟这一维，接口不变。
- **一代一换。** 新头到来或推送被拒：在内存里建新头的整代索引，在它上面按操作重放发件箱，再用 `Store.replace` 一个事务换入。读者只会看到旧一代或新一代。原话表的“只追加”触发器只在这个换入里暂时让路，换完在同一事务里重建。
- **物化而不是逐条写文件。** 提交器每次把索引整体写成文件，只暂存与 HEAD 不同的工具文件。原话路径一经写下就按编号尾巴找回、不再挪动；流水按 UTC 日期分文件，免得时区一改就把只追加的行挪到别的文件。这是对设计“当地日期”的偏离，理由是只追加。
- **版本账不带计划。** `plan/versions.jsonl` 写除计划外的全部字段和计划摘要。每个版本单独一个提交，带 `Tripwright-Version`，旧计划从历史里取回。迁移（`init --from-db`）也是一版一提交，所以往返逐值相等。
- **生成文件可核。** 视图依赖的东西都能从提交本身取回：记录（树）、时刻（提交时间，精确到秒）、所据提交（父提交）、本次写入与当时显示的通知（trailer `Tripwright-Op`、`Tripwright-Notice`）。所以 `check` 能做“重新生成＝已提交”。〔20:19 主会话指出、20:25 改〕原先视图快照的 `head` 是父提交号、“未入仓”算本提交带的操作，读者会以为这些写入还没进仓，其实它们就在这个提交里。改成：生成文件用的快照里，`head` 是给读者看的标签“<上一个提交> 的下一个，本次带入 N 条写入”（文件写不出自己所在的提交号）；“未入仓”不算本提交带入的操作，在文件里恒为 0。仓首屏、每趟首屏、视图与预编包都经同一个 `files.view_repo` 拿到，所以说法一致。工具返回的快照不变，仍是提交号与真实的未入仓操作。这借用了 B 的“提交 {head}”字样；B 若改成按结构字段渲染，A 再传 `parent` 与条数。别的趟的视图停在它最后一次是当前趟的样子，`check` 只核当前趟与仓首屏。
- **写入四态。** 每次写工具调用是一个 `OP-<日期>-<随机>`，与写入同事务进发件箱。turn、commit、作业证据（`job`）、手改收编（`manual`）各是一种。单库模式也记操作，状态直接是 effective，`write` 的形状两种模式一样。commit 默认等至多 5 秒（`commit_wait`），先刷一次，再看是否已生效。推送被拒后的重放：原话不冲突；turn 记的账重走 ledger；计划提交在基础版本 ID 变了时判 `plan_base_changed`，账本在别处变了时判 `replay_refused`（照现有“Ledger changed”规则）。两处把同一条待核关成不同结果时，先到的算数，后到的转成待定（`record_status_race`，带 `question_id`）。推送回执丢失时按 trailer 认出已进正本分支，不重复提交。运行库丢失后，trailer 只恢复状态（pending_sync、effective），不恢复重放数据；这样的操作要重放时判冲突并说明，不假装能重做。
- **turn 当轮记账。** turn 新增 `record`（items、facts、relate、carry、pending_checks，`source.event` 写 "this"），与收件、编包同一事务，所以“本人没箱子＋急问”当轮答对；它不产生版本。决定要计划快照，仍走 propose/commit。记账后若与现行计划冲突，turn 返回 `recorded.warnings` 与 `needs_replan`。
- **快照与新鲜度。** 内容键 = (版本 ID, 流水摘要)。context、draft、job 都存整份快照。作业按内容键过期（不再比版本号；求解器作业没有快照，仍比版本号）。`result` 每次取回都比快照；只多了不带账的原话时返回 unsettled，用 `ack_events` 声明，声明记在作业上；计划类 commit 认同一份声明；只改账本的 commit 不受拦。result 的错误文字从 `error` 移到 `notice`（约定的形状不许多字段）。
- **约束包（子代理写，我审）。** 什么算“非必需”：窄包里到期在窗口结束之后的开着的待核，以及 quick、status 场景里的目标。理由：前者是“开着、还没到期”，设计第 6 节只要求带到期或逾期的；后者不约束眼下这一步。其余在范围内的都必需，只能被 B 降成编号行。预算先拿掉非必需的（列进 omitted，原因 over_budget），再让 B 降级。快问只按人、时、地、通道收窄，不按种类丢。可能矛盾与待确认的长期条目作为“待定”记录进包（`PC-…`、`MEM-…`），B 现有的待定行就能渲染。宽包 `full_quotes` 照 v1.1 是对象；schema、样本与 B 仍是 v1 文本形，`present` 交给 B 前转换（过渡）。
- **核验者怎样取宽包。** `state(view="pack", draft_id=…)` 返回宽包 Markdown、pack_meta、完整候选与检查。没有放进 result：约定的 result 回复不许多字段，而且核验要在作业完成前开始读。
- **记忆与开新一趟。** 条目加 `long_term`（说成长期的）与 `always`（总带原则）；提交时长期条目另写进 `profile/items/`，带原话出处。开新一趟用 `turn(new_trip=…)`（CLI `new-trip`），不加第八个工具。开之前所有写入都要已生效，所以换趟后不会出现“旧趟的操作要在新趟的索引上重放”。导入的条目复制进新一趟的账本，保留原出处，并加 `origin`；同时去掉旧趟的日期范围，因为长期条目不绑那一趟的日子。人与资料只记在 `ledger/carry/`。没答之前照用并标出；回答用 `turn(record={"carry": [{"id", "status"}]})`。记 `not_used` 后条目不再适用。导入的条目原出处在旧趟，审计事件挂在开趟那条原话下。资料清单 `profile/materials.yaml` 由用户维护，工具不写也不提交。
- **手改收编。** 每次提交前看工作副本与 HEAD 的差。计划文件当提案走全部检查，通过才收为版本，记 `origin: external_edit`。不过的不覆盖用户的改动，留通知。生成文件与原话文件覆盖或恢复，说一次。格式坏了的记录索引保留旧值、文件不动、持续报。别的记录被手改则按账恢复并说一次：记录只经工具改，不收手改。
- **入参类型（判 4 反例 B，19:53 转达）。** `Changes.resolve_pending` 改成有类型的 `Resolution`（id 字符串、status、result、evidence_ids），在存储调用前由 pydantic 拒，路径为 `changes.resolve_pending.0.id`。`Item.check` 的嵌套键核类型（metric、op 字符串，value 数字，currency 字符串）。工具入参里余下的 dict 字段有三类：`query.arguments` 由 `checked_arguments` 核；`usages` 由 `account_usages` 核；`Decision.locked_paths`、`Item.condition`、`interpretations` 只存不解引用。关闭已关的待核现在明确拒绝。
- **没做的。** 远程托管写回（GitHub App、服务端镜像、webhook）只留了接口：提交器只经 `Git` 一类调用远程，换成镜像推送时只换这一层。turn 不在前台 fetch，只靠后台约 45 秒的轮询与 `sync`，所以 webhook 到来前包头的“基于提交”可能落后，`sync` 字段写明多旧。pre-commit 钩子没装。

# Implementation decisions — B3e (2026-10-31, b3e_fixes_opus_20261031-1955)

The fourth judged round bypassed the lock one level further down: the block and its fare stayed identical while the pass the fare is included in became another ticket. All four rounds shared one premise: that a local field, or a snapshot expanded a fixed number of steps, stands for the object the user chose. D-35 replaces it with "protect by default". This section replaces three bullets of B3d: the snapshot content in "Lock invariant", "Identity versus description of referenced objects", and "Other lock paths … compare the value at the path only". The rest of B3d (plan identity and position, stable paths, earlier snapshots rebound from the last committed plan) still holds.

- **References come from the model.** `trip.Ref(kind)` marks a string as the ID of another object; the aliases `PersonId`, `PlaceId`, `BookingId`, `FareId`, `AreaName`, `EvidenceId` carry it, and every reference field of the trip models uses one. `trip.reference_fields()` reads the marks; `locks.py` no longer lists reference fields. A kind that names a trip model (person, place, booking, fare) is followed; `area` (a name in `Trip.areas`) and `evidence` (records outside the trip document) are compared as values. `Day.base` was a place reference that the validator did not check (`render/screens.py` reads `places[day.base]`); it is now checked like the other place references. The marks are `Annotated` metadata, so the JSON schema of `Trip` is byte-identical.
- **What a lock holds.** `locks.resolve` is the one selection resolver. From the locked value it follows every reference field except description fields, and from each object it reaches also the object it is a line of (a fare to the booking that holds it; derived from the model as the nearest enclosing object of a referenced kind), until nothing new is reached; a key already reached ends the walk, so `included_in` cycles terminate. A block lock also holds where the block sits (date, plan ID, position, track people, who join the walk) and the trip timezone, as in B3d. Locks on other parts (`/bookings/ride`, `/bookings/ride/fares/F0`, `/days/{date}`) get the same walk; a fare lock reaches its booking. The containers of a locked block (track, plan, day) are not part of the walk; its position is.
- **Every field is protected except these description fields** (`locks.DESCRIPTION`): Place `notes`, `reason`, `hours`, `price`, `links`, `checked`, `rain_ok`, `priority`, `subject` (facts about the same place that other checks judge, and who it was suggested for); Booking `why_now`, `where`, `if_missed`, `due`; Block `note`, `evidence_ids`; Anchor `evidence_ids` (deep checks attach evidence to the plan they verify). A description field's references are not followed. A field the model gains later is protected until it is listed here. Two changes of protected fields keep the object: a booking's purchase step (`to_buy→bought`, `to_book→booked`, `to_contact→contacted`, `on_site→bought`; cancelling or any other move is a change), and saved coordinates within 100 m of each other (D-34). Lists of people compare as sets; other lists compare in order, so reordering a reached booking's fares fails closed.
- **Newly protected compared with B3d** (D-35 option 甲, recommended default): Booking `contact`; Fare `amount_per_person`, `currency`, `estimated`; every field of a booking reached through a fare, including its other fares and holders, and of every booking along `included_in`, with their places and holders. A system that fills in a price or a contact of a locked arrangement now needs the user's decision again. The locked block's `note` is newly free (B3d compared the whole block value).
- **One comparator.** `locks.differs` compares the locked value with the decided value (normalized through the model type at the path, so a host may send the block it was shown) and each recorded object with the same object in the candidate. `lock_changes` stays the only entry point; propose, deep checks, commit and external edits reach it through `Versions._check`, and commit refuses any lock conflict whether or not `accept_unverified` is set (unchanged; now pinned by tests). Names in `changed`: `value`, `date`, `plan`, `rank`, `subject`, `timezone`, `kind:id` for each recorded object whose protected fields changed, `references` when the set of reached objects changed and nothing else was named, `snapshot` for an earlier snapshot that could not be rebound.
- **Stored shape.** `Decision.placement[path]` keeps the B3d keys and `references` now maps each reached `kind:id` to its protected fields; `graph: "reachable"` marks the shape. B3c and B3d snapshots are rebound from the last committed plan by the unchanged `Versions._stabilize_locks`; one that cannot be rebound yields `snapshot` (fails closed). A whole-block lock that reaches 19 objects stores about 4 KB, a place-field lock about 0.5 KB; check details repeat it as `decided_placement`.
- **Limits kept.** A user decision supersedes an older one only when their lock paths overlap (ledger): a user decision that locks only `/bookings/pass1` does not release a block lock that reaches pass1, so the host records the block again with the new choice (fails closed). Check details carry the whole stored snapshot.
- **How it is tested.** `tests/core/test_b3e_reference_graph.py` (66 tests): every string field of the trip models is a marked reference or declared plain in the test, a model with a new unmarked reference field is reported, and the marks equal the fields the Trip validator enforces as references (an unknown ID is refused, another existing ID accepted). Lock cases are generated from the model over a fictional trip whose locked block names a place, an option, an origin, a destination, a fare in one booking included in a three-pass chain, a booking and a baggage holder: for the whole block and each of its seven reference fields, every protected field (all but `id` and DESCRIPTION) of every reached object is changed to another valid value — 232 cases over places, people, bookings and fares at depths 1–5 — and each is refused at propose naming the object and at commit with `accept_unverified=True`; every DESCRIPTION field of a reached place, booking and the locked block changes and commits, and people named only in a description field do not join the lock; objects the lock does not reach change freely. Also: purchase steps versus cancelling, `included_in` cycles of two bookings and of one, a changed line inside a reached booking, locks on a booking, a fare and a day, earlier snapshot shapes, deep check and external edits, and the judge's counterexample A with the user's own new decision. `tests/core/test_b3e_mcp.py` runs counterexample A over real stdio and Streamable HTTP clients. The judge's `reference_graph.py` (36 cases) now refuses all 24 identity changes, accepts the 6 description updates and refuses the 6 direct fare changes.

# Implementation decisions — B3f (2026-10-31, b3f_fixes_opus_20261031-2132)

〔B3k (D-53): D-36's zero exemptions and three ways of change now hold only for user decisions marked must; the user's other decisions are records the agent changes with a reason. See the B3k section.〕

The fifth judged round confirmed that the reachable graph of B3e reaches what the user chose; every remaining
counterexample was an automatic exemption applied after reaching it: booking fields listed as description although they
were the user's choice (channel, refund terms, deadline), a 100 m coordinate tolerance that swapped an entrance the user
had ruled out, and a status pairing that marked an on-site purchase bought. D-36 removes every automatic exemption. This
section replaces two bullets of B3e: "Every field is protected except these description fields" (its table and its two
kept changes) and the names in `changed` under "One comparator". The reachable graph, its walk and the single
comparator stay.

- **Zero automatic exemption.** `locks.tolerated`, `PURCHASED` and `SAME_PLACE_METRES` are gone. The comparator
  (`locks.differences`) skips only description fields; a protected field that differs is a change, whatever the
  distance, status step or source of the candidate, with or without `accept_unverified`.
- **The field table.** Every field of the trip model has one class in `locks.FIELDS`: `description` (changing it changes
  nothing the user gets: where, which entrance, when, with whom, what it costs, how to buy, what happens if missed, the
  ticket's state), `external` (a fact about the world) or `choice` (what the user chose). External facts and choices are
  protected alike; the class decides only what a correction may cite (evidence backs external facts). A field nobody
  classified is protected. 107 fields: 13 description, 19 external, 75 choice. `tests/core/test_b3f_field_table.py`
  reads the table below and fails when it differs from `locks.FIELDS` or a model field has no row. Where unsure, the
  field is protected: asking once more costs less than overwriting a choice (D-36 option 甲). Place `subject`,
  `priority`, `hours`, `price`, `rain_ok` and the booking's `where`, `if_missed`, `due` were description in B3e and are
  protected now; Place `checked` stays description because nothing reads it as verification.
- **Refusals name the field.** `changed` lists field paths: `booking:entry/status`, `place:gym-a/coords`, `value/start`
  (inside the locked value), `value` (a locked leaf), next to `date`, `plan`, `rank`, `subject`, `timezone`,
  `references`, `missing`, `snapshot` as before. List elements are named by ID where both sides have the same one
  (`booking:ride/fares/F0/ticket`); a list of another length, a person set or a coordinate pair is one value. The commit
  refusal repeats them: `locked:D-…:/blocks/d1 (booking:entry/status changed)`.
- **Stored shape.** Snapshots now carry `graph: "reachable:b3f"`. B3e snapshots (`graph: "reachable"`) left out the
  fields B3e exempted, so they are rebound from the last committed plan by the unchanged `Versions._stabilize_locks`,
  like the earlier shapes. Trade-off as before: a value an agent changed under B3e's exemptions and committed is what
  the rebound lock holds.
- **The three ways a protected field changes, each with its source in the tool's answer.**
  1. A new user decision (unchanged): it supersedes the old lock and takes its snapshot from its own candidate.
     〔B3g：它只看 basis 标签，截图、缺或外来说话人、更早的消息都能借它换锁；现在与 2、3 的确认同过 `Ledger.admit`，见 B3g 节。B3h：按路径取代，旧锁没被盖到的用户路径留在余下的决定里；授权消息不得早于最近一次改这把锁的消息，见 B3h 节。〕
  2. `changes.reports` `{target, field, old, new, quote, source}`: what the user's own message says happened ("I bought
     the tickets": `booking:entry` `status` `on_site` → `bought`). The source must be a received message with nature
     `user_verbatim` or `host_restatement` (not `agent_inference`, not `screenshot_extraction`), whose `speaker` is a
     person of the committed trip, and the quote must be in its text. The message must be the one the proposal answers
     (its turn's message, `Tripwright._said_now`〔B3g：并入 `Ledger.admit` 后删除〕), so an agent cannot reuse an earlier user message, such as the lock's
     own "buy on site", as the user saying it was bought. Any protected field may be reported, since the user is the
     authority, as for a decision.
  3. `changes.corrections` `{target, field, old, new, basis, ask, evidence_ids, source}`: the agent thinks a protected
     value should change (coordinates, hours, a price, a deadline). It is stored as an open `undecided` question with
     `kind: "correction"` (question text with the old and new value, the basis and what the user is to answer; options
     confirm or keep; default keep), the people of the locks that hold the field, and the whole trip as scope, so every
     package for those people lists it under questions. It changes nothing: a plan with the new value is refused until
     the user answers. `evidence_ids` are accepted only for an external fact and only when each record exists and is
     neither stale, synthetic (`demo:`) nor of unknown validity; the user confirms even then, because it touches a lock.
     A correction of a field no user lock holds is refused (change it in the plan). The user answers in a later message
     with `changes.confirm_corrections` `{id, answer: confirmed|rejected, quote, source}`: same source rule as a report
     (including the message of this turn), received strictly after the message the correction was proposed in〔B3h 取代：晚于开出这条更正的提案的时刻，source 引用的事件不算，见 B3h 节〕. Confirmed, it takes effect; rejected, the
     question closes and nothing changes; an answered correction cannot be answered again.
  Reports and confirmed corrections take effect the same way (`Ledger._amend`): every active user lock that holds the
  field, in its locked value, its recorded objects (a fare also inside its booking), its path, or the snapshot's
  timezone, must hold exactly `old` (otherwise the change is stale and refused), and gets a new decision that copies it
  with `new` in the locked value and the snapshot, an `amends` record (replaced decision, report or correction ID,
  `via` user_report or confirmed_correction, target, field, old, new, message) and the old decision superseded. Records
  only ever change status, and the repository stores decisions whole, so the change survives a rebuild. A lock path
  that addresses only part of the field (`/places/gym-a/coords/0`) is refused: the user decides again. Not amendable:
  IDs and a day's date (they name the object), fields that hold other plan objects (`Booking.fares`, `Track.blocks`,
  …), description fields (change them in the plan). Old and new are validated by the field's type; equal values are
  refused. All three go through propose and commit, like decisions, so the plan carrying the new value and the change
  of the lock commit together; `turn(record=…)` refuses them with the existing rule.
- **What the tools say.** `propose` returns `lock_changes` `{reports, corrections, confirmations}`: per entry the field,
  old and new value, the message (and quote), when it takes effect (on commit; only after the user confirms) and the
  locks that hold the field now. `commit` returns `lock_changes` `{amended: [{decision, replaces, change, via, target,
  field, old, new, event}], corrections: [{id, status, question}]}`. Nothing else changes in either reply.
  〔B3g 取代：两边的 `lock_changes` 都由 `ledger.lock_receipt` 从准入并落地的同一份操作生成，加了 decisions，每行带消息的性质与说话人，形状见 B3g 节。〕
- **X1a: the remaining untyped inputs.** `query.SolverDay` (with `DayStart`, `DayEnd`, `DayCandidate`, `DayOverride`)
  types `arguments.day` for `action=plan_day`: strings, integers within the solver's ranges, clock times checked with
  the solver's own `mins`, the date and IANA zone, `open` as two clock times, `deps` items as [departure, arrival,
  optional label]; unknown keys inside the day are refused (they used to pass and be ignored, so a misspelt `candiates`
  silently dropped every candidate). `usage.Usage` types each `propose.usages` entry, including `reason` as a string.
  One model gives each contract: the MCP schema of `query.arguments` is `Arguments` inlined (`WithJsonSchema`; the tool
  still receives a dict so the service's own message is the one returned) and `propose.usages` is `list[Usage]`; the
  service checks the same models (`query.checked_arguments`, `usage.checked_usages`) for stdio, HTTP and direct
  callers. One formatter, `trip.input_problems`, writes every refusal as `<path with list positions> must be <type>, got
  <type>` (`arguments.day.candidates.0.stay must be an integer, got array`, `usages.0.reason must be a string, got
  object`); over MCP a wrong `usages` entry is refused by the framework with the same path (`usages.0.reason … valid
  string`). Audit of the other untyped values in the seven tools: `turn.record` and `propose.changes` are `Changes`
  (typed); inside it `Item.check` keys are checked (A), `Item.condition` is read only as present or not,
  `Item.interpretations`, `Decision.options`, `Decision.revisit_when` are stored and rendered with a tolerant reader,
  `Decision.locked_paths` values are normalized through the model at the path and a wrong shape fails closed (the lock
  conflicts); `Item.value` and `Fact.value` are open by design; `Place.price` is stored only; `Booking.contact`,
  `Day.candidates`, `Trip.budgets` are typed maps. `commit`, `result`, `state` and `explain` take scalars or string lists.
  `Fact.value` is open except for the four keys the checker compares with the plan (`equipment`, `companions` lists of
  strings, `visited` a string or list, `baggage_holder` a string): a fact with `equipment: 5` used to fail inside the
  checker with "'int' object is not iterable" and is now refused when recorded (`equipment fact: value must be an
  array, got integer`).
- **How it is tested.** `tests/core/test_b3f_field_table.py`: the table equals the code and the model (a field the model
  gains is reported and compared meanwhile), and cases generated from the table change every field of every model on
  an object a lock holds (whole-block lock: people, place, fare, bookings, the block; day lock: tracks, plans, anchors,
  the day; trip-level locks): each external fact and choice is refused at propose naming it and at commit with
  `accept_unverified=True`, each description field commits. `tests/core/test_b3f_three_paths.py`: path 1 on a formerly
  exempt field; path 2 marks the ticket bought (verbatim and host restatement), refused for `agent_inference`,
  `screenshot_extraction`, no or a foreign speaker, a quote not in the message, a stale old value, a description field,
  an ID, a container, an unknown target or field, a wrong type or an unchanged value, and an earlier user message
  reused from a later turn (the lock's own message included); a report releases only its field;
  a ledger-only report marks the plan for replanning; two reports apply in order; turn refuses them. Path 3: before the
  answer nothing changes and a plan with the new value is refused (also with `accept_unverified`), the question is in
  the next package, a later message confirms it; the proposing message or an agent cannot confirm; rejected closes it;
  answered twice is refused; evidence only for external facts and only real (stale, synthetic, unknown validity,
  missing are refused); a correction no lock holds is refused; a confirmation after the user decided otherwise is stale.
  The judge's A1 (67 m entrance) and A2 (refund terms, channel, deadline, status) are refused and A1 passes by the
  user's report. `tests/core/test_b3f_inputs.py`: one bad sample per nested layer of the solver day (33) and per usage
  field (9), legal controls (a queued day, string reasons, a version's own usages sent back), the shared schema, and the
  day and usages over real stdio and HTTP. `tests/core/test_b3f_mcp.py`: an agent's refund-term change, the user's report
  and a correction before and after confirmation over real stdio and HTTP. `tests/core/test_b3f_repository.py`: with a
  trip repository, a report and a confirmed correction are files, the replaced decisions only lose `status: active`, and
  an index rebuilt from the files still refuses moving either value back.
- **Tests changed because the model changed (none weakened).** `test_b3e_reference_graph.py`: the documented-table test
  now pins the B3f table; "a reached booking may take its purchase step" became "an agent cannot move a reached booking
  status" (all seven steps refused); "people named only in a description field" now expects Place.subject refused; the
  generator no longer skips purchase steps; refusals are asserted with field paths. `test_b3d_lock_aliases.py`: four
  legal transformations relied on exemptions (place hours, price, rain flag, priority; a few metres of coordinates; the
  booking's channel, deadline, refund terms; buying the booking) and are attacks now; the description edits keep only
  pure description fields. `test_b3d_regressions.py`: the place-description test changes only description fields and
  then checks each formerly exempt field is refused; object names became field paths. `test_b3e_mcp.py`: field paths.
- **Limits kept.** A report needs `speaker` on the turn; hosts that never pass it cannot report (they get a reason
  naming the fix) and fall back to a correction or a new decision.〔B3g：新决定也要 `speaker`，没有它只剩更正一条路。〕 A correction's question is in packages of the
  locks' people for the whole trip until answered. Supersession by a later user decision still needs overlapping lock
  paths (B3e)〔B3h：只取代重叠的那些路径，旧锁其余的用户路径留锁〕; a confirmation after such a decision is refused as stale, and the host proposes a new correction.

Field table (one row per model field; `tests/core/test_b3f_field_table.py` keeps it equal to `locks.FIELDS`):

| Field | Class | Why |
|---|---|---|
| Person.id | choice | Which person; another ID is another person. |
| Person.name | choice | Who the person is; a swapped name changes who goes. |
| Place.id | choice | Which place the plan names. |
| Place.name | choice | The place's identity as the user knows it. |
| Place.name_local | choice | The search name picks the branch the user goes to (D-28). |
| Place.area | choice | Where the place is. |
| Place.kind | external | What the place is (a climbing hall, a museum); activity checks read it. |
| Place.subject | choice | Whom the place is meant for; the user may have named them. |
| Place.priority | choice | How much the user wants it (must, recommended, optional). |
| Place.reason | description | Why it was suggested; text only. |
| Place.coords | external | Which entrance or branch; 67 m can be another entrance the user ruled out (judge 5, A1). |
| Place.hours | external | Opening hours: when the user can go. |
| Place.price | external | What it costs. |
| Place.links | description | Links for reading; the channel is Booking.where and map links come from name_local (D-28). |
| Place.checked | description | When someone last looked; verification comes from evidence records, never from this field. |
| Place.notes | description | Free notes; text only. |
| Place.rain_ok | external | Whether it works in rain; weather checks read it. |
| Place.transit_id | external | Which stop transit lookups use. |
| Fare.id | choice | Which fare. |
| Fare.segment | choice | Which leg it pays for. |
| Fare.ticket | choice | Which ticket. |
| Fare.subject | choice | Who holds it. |
| Fare.amount_per_person | external | What it costs. |
| Fare.currency | external | What it costs. |
| Fare.date | choice | When it is valid. |
| Fare.included_in | choice | Which pass covers it. |
| Fare.alternative | choice | Whether it is the backup option or the chosen one. |
| Fare.estimated | external | Whether the amount is a confirmed price. |
| Booking.id | choice | Which arrangement. |
| Booking.date | choice | When it is used. |
| Booking.kind | choice | What kind of arrangement. |
| Booking.what | choice | What is bought or booked. |
| Booking.subject | choice | Whom it is for. |
| Booking.status | choice | The ticket's state (to buy, on site, bought); changes by the user's report. |
| Booking.place | choice | Where it is collected or used. |
| Booking.due | choice | The deadline the user works to (judge 5, A2). |
| Booking.why_now | description | Why to act now; text only. |
| Booking.where | choice | How to buy: the purchase channel (judge 5, A2). |
| Booking.if_missed | choice | What happens if missed: refund or buy again (judge 5, A2). |
| Booking.contact | choice | Whom to contact; names the merchant. |
| Booking.fares | choice | The lines of the arrangement; each line's fields are rows under Fare. |
| Block.id | choice | Which block. |
| Block.start | choice | When it starts. |
| Block.end | choice | When it ends. |
| Block.kind | choice | What kind of activity. |
| Block.place | choice | Where it is. |
| Block.options | choice | The alternatives the user may pick on site. |
| Block.text | choice | The activity in words; a block may have only text. |
| Block.certainty | external | Whether the time is fixed or estimated. |
| Block.mode | choice | How to travel. |
| Block.source | external | Where the time or fact came from, shown to the user as the source. |
| Block.app | choice | Which app the user searches or buys in (D-28). |
| Block.fare | choice | Which fare pays for it. |
| Block.booking | choice | Which arrangement it uses. |
| Block.note | description | Free note; text only. |
| Block.transfer_minutes | external | Travel time before it. |
| Block.walk_minutes | external | Walking time. |
| Block.baggage_holder | choice | Who does the task. |
| Block.equipment | choice | What to bring; facts about the people are checked against it. |
| Block.evidence_ids | description | References to evidence records; deep checks attach them and checks judge the records, not the list. |
| Block.critical_facts | external | Which facts must be verified; dropping one drops a check. |
| Block.origin | choice | Where from. |
| Block.destination | choice | Where to. |
| Block.line | choice | Which line or service. |
| Track.subject | choice | Who goes together. |
| Track.blocks | choice | What they do; each block's fields are rows under Block. |
| Plan.id | choice | Which alternative; locks follow it and the position (B3d). |
| Plan.name | description | Display name; identifies nothing (B3d). |
| Plan.note | description | Free note; text only. |
| Plan.tracks | choice | Who does what in this alternative. |
| Anchor.id | choice | Which hard node. |
| Anchor.time | external | When the hard node is, usually a timetable time. |
| Anchor.text | choice | What the hard node is. |
| Anchor.subject | choice | Who must make it. |
| Anchor.place | choice | Where it is. |
| Anchor.booking | choice | Which arrangement it uses. |
| Anchor.buffer_minutes | choice | The margin the user keeps. |
| Anchor.travel_minutes | external | Travel time to it. |
| Anchor.evidence_ids | description | References to evidence records, as for blocks. |
| Anchor.consequence | external | What happens if missed. |
| Anchor.alternative | choice | The fallback the user takes. |
| Anchor.app | choice | Which app the user searches or buys in (D-28). |
| Anchor.destination | choice | Where to. |
| Anchor.line | choice | Which line or service. |
| Day.date | choice | Which day; it names the day. |
| Day.title | description | Display title; text only. |
| Day.area | choice | Where the day is. |
| Day.pace | choice | How full the day is. |
| Day.base | choice | Where the user stays. |
| Day.sunset | external | When it gets dark. |
| Day.anchors | choice | The hard nodes; each one's fields are rows under Anchor. |
| Day.plans | choice | The alternatives; the first is the one followed. |
| Day.candidates | choice | The places to switch to (rain). |
| Day.rain_plan | choice | What to do if it rains. |
| Day.notes | description | Free notes; text only. |
| Trip.id | choice | Which trip. |
| Trip.title | description | Display title; text only. |
| Trip.start | choice | When the trip is. |
| Trip.end | choice | When the trip is. |
| Trip.timezone | external | The clock every time is read in. |
| Trip.areas | choice | Where the trip goes. |
| Trip.people | choice | Who travels. |
| Trip.default_pace | choice | How full days are by default. |
| Trip.budgets | choice | What the user means to spend. |
| Trip.places | choice | The places; each one's fields are rows under Place. |
| Trip.bookings | choice | The arrangements; each one's fields are rows under Booking. |
| Trip.days | choice | The days; each one's fields are rows under Day. |


# 回答层 v2（2026-10-31，answer_v2_opus_20261031-2220）

起因：E3（用户在手机上问两个路上问题）被退回：不是两段、乱、不简明、没有麦当劳一类的选项、交通不直观、没核验。改的是宿主读的说明（C 的角色与步骤、B 的回答片段）和用户仓的配置模板，服务端代码没动。

- **回答放在主 skill 最前，按场景给例子。** 主 skill 先写“两段”和回答片段，再给两组好坏对照（形状照 E3 的两问，地名虚构），最后才是每条消息的步骤。记账、声明关系、锁、写入状态、开新一趟挪进 `references/records.md`；MCP 提示词与 L0 文本照旧内联。理由：E3 的主 skill 约七成在讲机制，回答部分是一套七节骨架，宿主照骨架写出十几行。
- **片段改写，token 估数不变。** `writing.{zh,en}.md` 改成九条路上回答的规矩（第一行答所问、假设写在前两行、每个目的地一行、硬节点才写时刻、吃的给 2—3 个开着的且至少一个常见连锁、核过带来源与时刻），估数保持 561、653，所以包的预算与契约样本不用动。`skeleton` 改成两段的形状，`transit`、`triggers` 删掉与 `writing` 重复的规矩。
- **第二段的核验先派后台、再写第一段。** Claude Code 的子代理一律后台跑，每份报告另起一轮；主 agent 自己同步核时，下一步是工具调用，它两次把第一段只写进思考（重放 r3，两问都只剩第二段）。所以改成：先派一个后台 `tripwright-researcher`，带上全部要核的事，再把第一段写成这一步结尾的正文，报告回来再写第二段。只派一个，因为派三个时第二段被拆成三条（r1）。没有子代理的宿主：先写第一段，再自己搜。
- **“核过”的两种标题。** 契约的“核过的安排／安排（没核完）”只管提议了计划、有深核作业的第二段；路上问题没有草稿，第二段写“# 第二段 · 核过了（HH:MM 查）”或“# 第二段 · 没核完：原因”。契约要不要收进这一条，交主会话。
- **不对用户说写入状态。** 已收件、待同步、已生效只留在记录里；只有写入冲突的那一行给用户看。契约只规定“已生效之前不说记下了”，不冲突。
- **用户仓模板。** `.mcp.json` 给 tripwright 进程设 `TRIPWRIGHT_PROVIDERS=live`（不改 `query.py` 的默认值）；新增 `.claude/settings.json`，放行 `mcp__tripwright__*`、WebSearch、WebFetch、Agent 与只读 Bash（主会话 23:06 转达的用户要求）。它要进 `tests/contracts/manifest.json` 与 `docs/CONTRACTS.md` 的用户仓清单，那两处归主会话。
- **承诺检查跟着挪。** `promises.py` 里讲机制的承诺改核 `references/records.md`、单 agent 页、MCP 提示词与 L0；新加三条：两段且第二段有真来源、不对用户说写入状态、吃的有常见连锁。
- **重放脚本。** `scripts/host_replay.py` 记每行流的到达秒数，回答里标每段在第几秒出现，子代理的文字不算进回答；给 claude 进程设 `MCP_TIMEOUT=90000`，因为本机 tripwright 服务要 20—28 秒才回 `initialize`，默认 30 秒的连接时限下工具随机失败（r1 两问都失败）。
- **锁定值的三条改法进按需页。** 用户新决定、`changes.reports`（turn 带 `speaker`）、`changes.corrections` 加 `changes.confirm_corrections`（B3f 节）写进 `references/records.md`；主 skill 第 6 步只留一句，第 1 步要求传 `speaker`；步骤 5、verifier 清单、服务端 instructions 同步改掉“只有用户新决定能换锁”。L0 文本没有工具，仍只认用户新决定。

# 回答层 v2b（2026-11-01，answer_v2b_opus_20261101-0050）

起因：非作者判官按质量界卡 v1 判 r7—r11，0/5 合格；8 类跨轮毛病里最重的是“现在几点”取错、计划里存的班次当事实、“几点查”是编的、第二段只写差异（和用户存的偏好 I-261031-uzp6i2 相反）。

- **钟不让模型猜。** 第一段第二行固定“按现在 HH:MM 算”，钟取 `turn` 给的现在（服务端在加），没有就先跑 `TZ=<tripwright.yaml 的时区> date`；用户的说法和钟不一致时照钟，在这一行点明。第二段写之前再跑 `date`，标题带这个时刻；核过的事只写来源，不写“几点查”。
- **存的时刻不当事实。** 第一段用仓里存的班次、营业时间，写“计划里存的，几时查的”或列进“没核”；计划里留的余量（“05:45 前到柜台”）和外部截止（柜台几点关）分开写。核验清单固定包含第一行答案依赖的每个时刻与硬节点、现在之后还有的班次、计划里存的替代走法。
- **第二段全量。** 三块：和第一段不同（只引第一段真写过的；第一行的每个时刻原样再写一次，变了写“从 HH:MM 改成 HH:MM”）、完整安排（每项一行，以时刻开头）、没核。行数上限照旧是推荐，写不下完整安排时照写全。吃的至少落到一处核到营业时间、到时开着的；一处都核不到就明说并给最稳的后备。
- **“默认”只给用户定过的那套。** 偏好相挤的完整规则挪进 `references/records.md`，主 skill 正文留一句；“默认”只标仓里用户决定选过的方案，并写明是哪条。
- **写法片段的 token。** `writing.{zh,en}.md` 改写后宽字符、窄字符的个数都和改前一样（514/139、0/1958），包的预算与契约样本不用动；只看单个片段的估数不够，包按全文合计后再取整。
- **机械检查。** `scripts/answer_checks.py` 对一个重放目录跑五条：只两条消息；第一段前两行有和发出时刻相差 5 分钟内的时刻，写“现在”的时刻也要对；第二段标题与“HH:MM 查”不晚于发出；不含标签与内部用语；第一段第一行的时刻（除去“几时查的”）在第二段的核过、没核或和第一段不同里再出现。`host_replay.py` 跑完自动调它。它是回归信号，不代替判官。
- **连接时限。** 用户仓 `.claude/settings.json` 加 `env.MCP_TIMEOUT=120000`。实测生效（`--settings` 与已信任工作区里的项目设置都看到 120000ms）；代价是服务卡死时，无头会话的第一次调模型要等到超时（假服务实测 120 秒）。

# A2：服务端起步、“现在”与“几时查的”（2026-11-01，a2_serve_pack_opus_20261101-0050）

- **r9—r11 连不上的原因。** 这三轮用 `--ref <提交>` 检出，工作区是游离 HEAD；`Repository()` 调 `git symbolic-ref --short HEAD` 报错，进程在握手前退出（Claude Code 的 MCP 日志里有 Traceback）。原因不是慢。`Git.branch()` 改成：游离 HEAD 跟指向同一提交的分支，先本地、再远程；没有就跟远程默认分支，再没有用 `main`。代价：游离 HEAD 落后于分支尖时，第一次同步会像在分支上一样，把工作副本移到分支尖。重放旧提交时，先在裸副本里给它建分支，再检出那个分支。
- **先回握手，再开仓。** `serve --stdio` 先起 MCP 服务。`Opening` 在服务的 lifespan 里开一个线程打开仓（新克隆要从全部文件重建索引），开完再起作业与提交器。每次工具调用先等仓开完，至多 120 秒。仓打不开时进程不退出，每个工具都拒绝并写明原因。`cli.py` 不在握手前导入 `service`。HTTP 照旧先开仓再起服务。
- **冷启动的数（本机，新克隆，没有 `.tripwright/`，从起进程到回 initialize）。** 改前 wheel d3e987f3：热缓存 1.96—2.06 s，冷缓存 3.67—3.86 s，当时负载约 6—10。前后交替测（热缓存，负载 28—35）：改前 3.32—5.17 s，改后 2.72—3.81 s。重放日志里 10—60 s 的连接时长，此刻复现不出，原因未定。Claude Code 2.1.301 先发 `server/discover` 探测；服务端答得晚时，它改发 initialize，被拒后再发 discover。连上的时刻约等于服务端就绪的时刻，探测本身不另加等待。冷缓存的下限是 uv 下载并装 35 个包（约 27 MB，其中 cryptography 11 MB、pydantic_core 4.8 MB 都是 mcp 带来的），加上首次导入时编译字节码。
- **“现在”。** 读取时刻换算到行程时区：用计划的时区；没有计划用 `tripwright.yaml` 的；再没有用 UTC。形状是 `{at, date, time, weekday, timezone}`，`weekday` 1—7 表示周一到周日。turn 的结果与 state 的每个视图都带 `now`，另加 `text`。turn 的窄包在 `header.now` 带同样的字段（不带 `text`），渲染后第一行以“现在 HH:MM（时区，MM-DD 周X）”开头。写进仓的预编包不带 `now`，因为它是以后才读的，包头仍只写“编于”。
- **硬节点“几时查的”。** 包里每个硬节点记录与 turn 的 `hard_nodes` 都带这几项：`deadline`（external、plan、unmarked）；`stored`（计划从哪一版起一直存着这个时刻，及那版的提交时刻）；`checked`（最新一条非演示证据的来源、取到时刻、证据号、有效期、是否陈旧，没有时为 null）；`refresh_hours`；`may_have_changed`。推荐默认：硬节点按班次算，6 小时；营业时间 7 天（表在 `packs.REFRESH_HOURS`；包里现在没有营业时间，这一项先备着）。没核过的从存进计划的时刻算起。证据自己标了陈旧或过了有效期，也算可能已变。
- **外部截止与计划目标。** 锚点模型没有这个字段，`trip.py` 冻结。现在的做法：锚点带了 `deadline` 就照用（要主会话在 `Anchor` 加这个字段）；有核验证据的算外部截止；其余写“没标是外部截止还是计划目标”。锚点的 `buffer_minutes` 是计划留的余量，渲染成“计划留 N 分钟余量”，和外部时刻分开写。
- **turn 的硬节点。** 每个都带 `passed`（时刻已过），没过的排在前面。已过的节点里写的后果（例如“只剩某班末班”）是历史，不是今晚的选项。
- **旧账。** `state(view="day")` 不再自己加“需重排”一行。`generated()` 与 `day_text` 交给视图的 state 带版本的 `needs_replan`、`warnings`，由 B 的日视图写那一行，只写一次。`packs.rendered` 不再把 `full_quotes` 转回纯文本，渲染器直接读对象，缺的项写“未知”。`pack_problems` 不假定窗口只有一天：这要包头写出窗口的首日与末日，牵动契约、样本和 4 份样本的 token 数；草稿补丁放在任务目录，没有核完。
- **和样本比的测试。** 契约样本 v1.2 没有 A2 的字段。`tests/repo/test_packs.py` 先用 `v12()` 去掉这些字段，再和样本逐字比。A2 的字段另有测试：`tests/repo/test_a2_now_and_timing.py`、`tests/core/test_a2_service_now.py`、`tests/render/test_pack_golden.py` 的最后一条。样本升版后删掉 `v12()`。

# A3：`Anchor.deadline`，分开外部截止与计划目标（2026-11-01，a3_anchor_deadline_opus_20261101-0135）

- **字段。** `Anchor.deadline: Literal["external", "plan"] | None = None`。external：运营方或外部规定的截止，例如柜台关、末班开走；plan：用户或计划自己留余量的目标，例如“几点前到柜台”。没标为 null。旧数据没有这个字段，照样读；之后的提交把计划文件里的锚点写成带 `deadline: null`。`packs.node_timing` 先读这个字段；没标时，有核验证据的算 external，其余 unmarked（A2）。
- **锁的类别：用户选择。** 理由：这个标记决定回答把这个时刻当成能不能让步的底线，也就决定用户被告知的最晚时刻；改它等于改用户承担的风险，只能经用户新决定、用户说的实况或用户确认的更正。外部证据能证明的是时刻本身（`Anchor.time`、`consequence`，都是 external），不是“这个时刻要不要当底线”，所以更正这个字段不能只凭证据（`ledger._correction` 对 choice 字段拒绝 `evidence_ids`）。B3f 的字段表从 B3f 节读到文件末尾，这一行写在本节：

| Anchor.deadline | choice | Whether the answer treats this time as the limit or as the plan's own margin. |

- **对现有行程。** 锁只在用户锁住的对象上起作用。现在的评测仓只锁了区块的地点，碰不到锚点，补标 `deadline` 走普通计划提交即可；锁住了某天的仓，补标要经用户。
- **入口与报错。** `propose` 的 `plan` 用同一个 `Trip` 模型，MCP 的工具 schema 随之带上这个字段与说明。服务端先按模型核计划，形状不对时一行写一处：`Invalid plan: plan.days.0.anchors.0.deadline must be 'external' or 'plan', got string`（B3f X1a 的写法，`service.checked_plan`）。`changes.reports`、`changes.corrections` 可以把 `anchor:<id>` 的 `deadline` 当字段改，新旧值按字段类型核，错时写 `correction <id>.new must be …`。
- **字段表计数。** choice 由 75 变 76；`tests/core/test_b3f_field_table.py` 的计数同步改。

# A5：“核过”只挂在工具证据上（2026-11-01，a5_evidence_latest_opus_20261101-0245）

- **为什么。** 第二次质量判 5/8 轮写错“核过”：子代理自己读 Transitous 原始 JSON，把 UTC 当本地、把含步行的起止当车次；回答把有限的查询结果推成“唯一”“最后”。D-42 定：这些由工具算、由工具记。不加第 8 个工具（工具数是契约），都走 `query` 的 `kind`。
- **交通按段、本地时刻、带窗口。** `query(kind="transit")` 的结果加 `timezone`、`connections`、`window`、`summary`。每条 `connections` 按段列 `legs`：`mode`、`walk`、`line`、`from`、`to`、`depart`、`arrive`（行程时区 HH:MM，另有 `*_at` 带偏移的 ISO）。`leave`、`reach` 是含步行的门到门起止；`boards`、`alights` 是第一段、最后一段车的上下车。这样起止不会被当成某趟车的时刻。时区取 `arguments.timezone`，再取计划的，再取仓的默认；Transitous 按这个时区出结果，存下的证据值也换成这个时区（同一瞬间，不再出现 UTC 钟点）。`window`：按出发查时是从 `when` 到最后一条结果的出发；按到达查时是从最早一条的到达到 `when`。`text` 写明窗口之外没查。
- **最晚出发。** `query(kind="latest_departure")`，输入起点、终点（地名、坐标或计划里的地点编号），以及 `anchor_id` 或 `by` 二选一。目标按锚点的 `deadline` 定：`plan_by` 是时刻减去计划留的 `buffer_minutes`；`limit` 对 external 和没标的是时刻本身，对 plan 是它后面的外部截止。外部截止先取 `external_deadline`；没给时，在目标之后 12 小时内的 external 锚点里取最早的：先看同一地点的，没有再看没写地点的，目标自己没写地点时最后看全部；都没有就是 null，结果的 `limit_from` 写取的是哪个锚点。结果有四项。`primary`：到达不晚于 `plan_by`、出发最晚的一条。`later`：到达在 `plan_by` 之后、不晚于 `limit` 的，标 `past_plan_target`。`fallback`：比主选晚出发、不晚于 `limit` 到的第一条；没有时为 null，并在 `fallback_note` 写原因。到达按门到门（含最后的步行）算。搜索从 `after` 开始；没给 `after` 时，从现在和目标前 4 小时里取较晚的。每次从已查到的最后一班之后 1 分钟接着查，最多 6 次，查到“越过 limit”为止：有一条比最后一条赶得上的更晚出发、却到晚了的，或有一条在 limit 时或之后出发的。查不到这一步时 `window.covers_target=false`，文字写“窗口只到 HH:MM”，主选不能说成最后一班；一条都没查到时写“HH:MM 起没查到班次”，不能说没有车。结果记成证据（`kind=latest_departure`），带来源、服务端取到的时刻和有效期（取查询结果里最短的）。演示数据记成 `synthetic`，状态是 unverified。
- **网页证据。** `query(kind="web_evidence")` 收 `url`、`quote`（逐字）、`claim`、`type`（rule、opening_hours、event、place、price、other）、`read_from`（page、search_snippet）。时刻由服务端记，宿主带 `fetched_at`、`at` 这类字段一律拒绝。有效期按种类：营业时间 7 天，活动 1 天，规则、地点、价格 30 天，其他 7 天。live 时服务端抓一次页面（只要 http 和 https、公网地址，跳转也查，最多 3 MB；脚本、样式不算正文），核原句在不在，写 `quote_found: true|false|unchecked`。比较时不分大小写、全半角、弯直引号和连续空白。`counts_as_checked` 只对“页面上读到的，且原句没被证明不在”为真；搜索摘要和抓到页面却找不到原句的，都不算。不算的证据，`ledger` 在更正和关闭待核时拒绝，`packs.last_check` 也不拿它当硬节点的核验来源。证据只记账，不改计划和锁定字段；要改受保护的值，仍走 D-36 的三条路，在更正里引证据号。
- **命令行。** `tripwright [--repo .] query <kind> …`，与 MCP 走同一个 `service.query`。stdout 是同形 JSON，stderr 是一行 `summary`；参数错误照 MCP 的原文写到 stderr，退出码 1。默认用 live 数据（`--providers synthetic` 用演示数据）。`--repo` 模式下记下的证据随提交器停下时提交并推送，和 MCP 一样。
- **turn 的记录报错。** `turn(record=…)` 先按 `Changes` 核，一处问题一行，带路径、要什么、送来什么：`Invalid record: record.facts.0.true_from: must be an ISO time with a timezone offset (e.g. 2030-06-10T09:00:00+02:00), got 'this'`。`Fact.true_from`、`valid_until`，`Item.valid_from`、`valid_until`，`PendingCheck.due_at` 改成带校验的类型，`propose` 的 `changes` 也就带上同样的话。MCP 的 `turn.record` 照 `query.arguments` 的做法：工具 schema 仍由 `Changes` 生成，校验交给服务端，所以经 MCP 报的也是这一行（以前是 pydantic 的多行原文）。
- **改了旧测试一处。** `test_provider_integration` 原来比证据值里的 UTC 字符串（`08:05:00+00:00`），现在比同一瞬间的本地写法（`10:05:00+02:00`）。

# A6：服务端核回答草稿（`check_answer`），`latest_departure` 出精简结果（2026-11-01，a6_check_answer_opus_20261101-0354）

- **为什么。** 回答层三轮没守住写进 skill 的规矩，v2d 冻结 16 问里 7 问仍把网页事实写成核过。D-43 定：宿主发出一段之前，把草稿交给服务端核，按这一趟的证据与钟。工具数照旧七个，走 `query(kind="check_answer")`。
- **入口与形状。** 参数 `text`（一段的草稿全文）、`part`（first、second）、可选 `request_id`（本问 turn 的请求号）。回 `ok`、`problems[]`、`summary`、`now`、`evidence {since, ids, windows}`。每条 problem 是 `{check, line, text, why, fix}`：检查名、草稿里的行号（从 1 数，整段的问题为 null）与该行原文、为什么、怎样改的一句话。服务端只核，不改草稿，也不记账。
- **钟与证据的范围。** 钟取服务端此刻，换到行程时区（计划的，再取仓的，再取 UTC）。证据取 `request_id` 那问 turn 收到前 6 小时起、到现在为止记下的；没给 `request_id` 从现在往前 6 小时。6 小时是 A2 给班次定的刷新时长，同一会话前几问也落在里面。演示、陈旧、搜索摘要、原句不在页上的证据都不算。
- **能撑“核过”的。** 交通结果里每段的时刻；`latest_departure` 的主选、`later`、后备的时刻（不含目标：那是计划自己存的时刻）；算核过的 `web_evidence` 的说法与原句；以及这些证据的号。窗口的终点：交通查询的 `window.to`；`latest_departure` 的 `window.to`，覆盖到截止时再加上截止时刻。
- **第二段的检查。**
  - `checked_has_evidence`：写“核过”的行（或光秃“核过：”标签下的列表项）要对上证据。行里点名的证据号都算数，就过；否则行里每个时刻（除去“HH:MM 查”和“现在 HH:MM”）都要在工具结果或算数的网页原句、说法里；没有时刻的行，要和算数的网页说法或原句共用 4 个字以上。比 `scripts/answer_checks.py` 原来的“任一时刻对上就过”严：一行里混进网页时刻（例如柜台几点关）就不过。
  - `only_inside_window`：说“唯一”“只有这一班”“最后一班”“之前没有”等，且没标没核、网上说法的行，所说的最晚时刻要在某个窗口终点之内（按钟在 24 小时表盘上比）。
  - `fallback_later`：“误了”行里的后备不早于被误的那班（判法照原脚本）。
  - `plan_heading`：有 Markdown 标题“完整安排”。
  - `title_time_not_later`：标题括号里的时刻和“HH:MM 查”不晚于现在。
  - `no_tags`：没有标签、工具或系统状态句（“没连上”“已收件”“I'm still…”）、小写下划线的字段名（链接目标不读）。
- **第一段的检查。** `clock_in_first_part`（前两行有和钟相差不超过 5 分钟的时刻；写“现在 HH:MM”的必须是钟）与 `no_tags`。
- **已知漏判。** 点名一个算数的证据号，这一行就过，不看证据是否支持这行的话。时刻对上就过，不看那个时刻在证据里是什么（例如把某班的到站时刻说成柜台关门）。没有时刻的行只比字面重合，网页说法换个说法写可能漏，也可能误拦。工具查到的用时（“约 23 分钟”）不带时刻会被拦，要写上时刻。窗口按表盘比，跨 12 小时以上的说法判不了。
- **一套检查，两处用。** 检查放在 `render/check_answer.py` 的新节，原有 13 条渲染检查不动。`scripts/answer_checks.py` 只剩读重放流记录（谁说了什么、几点发、这一趟查到什么），十条检查都调包里的同一套；从没装包的环境跑它时，先加 `src` 到路径，再不行就经 `uv run` 重跑自己。原脚本的 `tests/agents/test_answer_checks.py` 不改照过。
- **web 证据记的时刻。** `evidence.web_record` 改用 `storage.now()`（服务端统一的钟），不再直接读系统钟；行为不变，测试能固定时刻。
- **命令行。** `tripwright [--repo .] query check_answer --part second --text-file <文件>`（`-` 读标准输入，也可 `--text`、`--request-id`），stdout 与 MCP 同形 JSON，stderr 是一行 `summary`。
- **`latest_departure` 精简结果。** 默认 `detail="compact"`：`target`、`primary`、`fallback`、`later` 前 3 条（另给 `later_count`）、`window`、`summary`、`fallback_note`、`connections_count` 与证据的号、来源、时刻、有效期。每条班次去掉带偏移的 ISO 时刻、单条摘要、`reaches_plan_target`、`within_limit` 和空的段字段，保留 `boards`、`alights`、`past_plan_target` 与按段的方式、线路、上下车站、开到时刻。`detail="full"` 给 A5 的完整结果。证据记录照旧存完整的（`later` 全部）。从街道地址出发、5 段换乘、24 条班次的假数据：精简 9.4 KB，完整 120 KB（`pydantic_core.to_json(indent=2)`，即 MCP 的文字内容）。

# A7：固定钟、发出的文字绑定核验、check_answer 加三条、最晚出发给可比候选、吃的固定来源（2026-11-01，a7_pin_candidates_opus_20261101-0458）

- **为什么。** 第三次质量判（D-44）：格式已稳，内容不稳。工具只给一条主选，宿主把“没返回”推成“走不通”；没出处的“赶不上”送核也通过，因为 `check_answer` 只核标了“核过”的行，也不绑发出的文字；饿了没有具名地点；地址被解析到别处，可地点表里存着坐标；重放的钟点不是原题时刻。
- **固定钟（只给重放与测试）。** `storage.now()` 与新的 `storage.clock()` 认 `TRIPWRIGHT_NOW`（带偏移的 ISO）：钟在第一次读时显示这个时刻；设了 `TRIPWRIGHT_NOW_SET_AT`（Unix 秒）就从那一刻算起；之后按真实时间往前走。不冻结，因为冻结的钟会让“确认晚于提问”这类顺序判断失效，同一会话后一问也要晚于前一问。turn、包、查询、`check_answer`、证据记的时刻、证据号的日期、数据源的取数时刻都走它。没设时照旧读系统钟。`scripts/host_replay.py --now <ISO>` 给会话设这两个变量，并在运行目录的 `clock/` 里放一个 `date`，排在 PATH 最前，只对这次会话生效；它用同一起点算出时刻，再交给真正的 `date` 照参数格式输出（`TZ=…`、`-u`、`+%H:%M` 都照常）。提交器、作业租约、仓的检查时刻仍读系统钟。
- **发出的文字绑定核验。** `check_answer` 对第二段 `ok` 时，把草稿的哈希与全文记进 `.tripwright/answers.json`（单库模式记在库旁的 `<库名>.answers.json`），按请求号各留最后一份，最多 50 份，另记服务端时刻和墙上时钟；结果多一项 `passed {sha256, remembered}`。哈希前统一换行、去掉行尾空白和首尾空行。`tripwright [--repo .] hook stop` 是 Claude Code 的 Stop 钩子：从标准输入读钩子输入，从 `transcript_path` 读会话记录，取最后一条真用户消息之后主 agent 最后显示的文字（输入里若有 `last_assistant_message` 就用它）。它是第二段（有“第二段”或“完整安排”标题）、又不等于这一问（墙上时钟晚于那条用户消息）过核的任何一份时，按钩子协议在标准输出写 `{"decision": "block", "reason": "第二段和核过的草稿不一致，请把发出的文字交 check_answer 再核"}`；这一问没有过核的，理由换成“还没过 check_answer”。第一段、闲聊、一致的第二段不输出。`stop_hook_active` 为真时只记日志、不再拦，防止来回打转（D-48 起改成每问最多拦 3 次，见“四补”）。钩子不打开行程、不连网；出错时不拦，把原因写进 `.tripwright/hook.log`，每次判定也写一行。
- **`check_answer` 加的检查。**
  - 第二段 `unreachable_needs_lookup`：有“赶不上、接不上、来不及、没有车、没车了、没查到能接上、走不通、到不了、坐不上、赶不及”的行，没写“没核”的，要这一趟有过 `latest_departure` 的结果。不带条件的这类句子，不能点名工具列为赶得上的班次（主选、later、后备、候选的时刻）。含“如果、要是、万一、假如、一旦、否则、不然、再晚、晚于、之后才、过了”的条件句只要求有查询结果。
  - 第二段 `meals_named`：本问原话（`request_id` 那问，没给就取最近收到的一问）有饿、吃、饭、餐一类字时，第二段至少两处具名吃饭地点。地点按回答的写法认：地名单独一行，下一行是带“ · ”和中文的说明行。说明里或其后两行内写了营业时间，营业时间有出处：地点表里存着它的营业时间（名字对得上），或这一趟记过和它名字共用 4 个字以上的网页证据（搜索摘要也算出处）。
  - 两段都查 `internal_words`：“外部截止”“计划目标”、证据号 `EV-…`、小写下划线的字段名（从 `no_tags` 移来；链接目标不读）。证据号不再进正文，所以 `checked_has_evidence` 的改法改成“这一行写原句和网站名”；点名了算数证据号的行仍算对上证据，但会被 `internal_words` 拦。
  - 第一段 `first_part_lines`：正文不超过 8 行，地名行与说明行不算。供重放检查用。
- **最晚出发的候选。**
  - 结果多 `candidates`：主选和可比的走法，同一条班次只列一次，`route` 列出它同时是哪几种（`first_choice`、`via_station`、`fastest`）。每条有 `label`、`leave`（最晚出门）、`reach`（到达）、`past_plan_target`、`ticket {need: extra|unknown, why}`，经车站的另有 `via`。
  - `fastest`：主查询里不晚于截止到达、用时最短的一条。
  - `via_station`：行程里的车站，一个起点最多两个。先认计划里坐火车的出行块：它的终点就是查询终点，或离终点不超过 3 公里，它的起点就是车站。没有这种块，再认 `kind` 是 station 一类的地点。对每个车站做两次查询：车站到终点的火车，取赶计划目标的最晚一班，没有就取赶外部截止的最晚一班；起点到车站，取赶得上这班火车（留 3 分钟换乘）的最晚一班；前三班火车都接不上才放弃。起点就在车站时，只查火车。
  - 票：长途大巴（`coach` 或 FlixBus）写“要单独买票”，其余写“票价和通票没查”。
  - 起点、终点先对地点表：ID，或地点的名字、本地名单独出现或出现在地址前（“Blockfabrik, Schloßgasse 10-12”）。对上的用存的站号或坐标，不拿地址去地理编码；结果的 `resolved` 写每一端认成了哪个地点、用什么查。
  - 精简结果里，候选和 `later` 只给乘车段，主选与后备给全部段。从街道地址出发的假数据精简后不超过 10 KB。
- **地点的营业时间。**
  - `Place.opening_hours`：规则列表，例 `Mo-Fr 06:00-22:00`、`Sa,Su 08:00-20:00`、`Mo-Su 00:00-24:00`。结束不晚于开始的时段跨过午夜，例 `Fr 18:00-02:00`。
  - `Place.hours_source`：`{url, evidence_id}`，写营业时间是从哪一页读来的。`propose` 时 `evidence_id` 必须是本趟已有的证据，否则报 `plan.places.N.hours_source.evidence_id`。
  - 规则用校验过的字符串，不另建模型，所以字段表只加两行。原有的自由文字 `hours` 保留。
  - 写入路径：`web_evidence(type=opening_hours, url, quote=页面原句)` 记账，再在 `propose` 的计划里给地点写 `opening_hours`、`hours_source`，再 `commit`。锁住的地点改 `opening_hours` 要走更正（外部事实，可附证据）。
  - 查询 `query(kind="open_places", arguments={near, at?, km?, text?})`：在 `near`（地点或坐标）`km` 公里内（默认 2），列出 `at` 时（默认现在）按存的营业时间开着的地点，近的在前，带何时关、规则和出处。关着的、没存营业时间的分开列。
- **字段表（B3f 的规矩，本节两行）。**

| Place.opening_hours | external | The operator's opening hours, read from its page; a correction may cite the page's evidence. |
| Place.hours_source | description | Where the opening hours were read; it changes what the trip records, not what the user chose. |

- **第一段的钩子（D-45 补充第 6 条）。** `tripwright [--repo .] hook first-part` 是 PreToolUse 钩子，matcher 写 `*`，由程序自己判哪些调用要拦。
  - 拦的：主 agent 的查询类调用，即在前台派子代理（Agent、Task）、`mcp__…tripwright…__query`（`kind` 为 `check_answer` 的不拦）、WebSearch、WebFetch、命令行里的 `tripwright … query`（`check_answer` 不拦）。
  - 不拦的：`turn`、读文件、加载 skill 等。在后台派的子代理（`tool_input.run_in_background` 为真）不拦（D-46，见下）。子代理里的调用也不拦：钩子输入带 `agent_id`、`agent_type`，2.1.301 上实测。
  - 判法：从会话记录里找本问最后一条真用户消息之后主 agent 显示过的文字。有一段过得了第一段的检查（前两行有那一刻的钟、没有标签与内部字样、正文不超过 8 行），就放行；没有就按协议输出 `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "先把第一段发给用户（现在怎么走、最晚几点），再查；…"}}`，理由后面接没过的检查。
  - 钟：取文字显示那一刻、行程时区（读仓的 `tripwright.yaml`）的钟。固定钟时按同一起点换算，所以重放里也对得上。
  - 防死锁：同一问拦满 3 次后放行，并记日志，防止宿主怎么也发不出合格第一段时一直查不了。
  - 本机实测：`python -m` 约 0.25—0.28 秒；经 `uvx --from <wheel>` 约 0.35—0.38 秒；新 wheel 第一次约 2.3 秒（uv 建环境，只一次）。
- **“真用户消息”的认法。** 2.1.301 实测：后台子代理做完时，Claude Code 往会话记录里写一条 `type: user` 的通知，`origin.kind` 为 `task-notification`，正文以 `<task-notification>` 开头；Stop 也会在这之后再触发一次（换了 `prompt_id`）。两个钩子都不把它当用户的新问题；工具结果、`isMeta`、子代理的条目也不算。Stop 输入带 `last_assistant_message`，钩子优先用它。
- **钩子在重放里真的跑（D-45 补充第 7 条）。** 11-01 05:27 CET 在本机实测（Claude Code 2.1.301，`claude -p --setting-sources project --permission-mode bypassPermissions`，新建、从没信任过的工作区）：stderr 写着 permissions.allow 因为工作区未信任被忽略，但仓内 `.claude/settings.json` 的 PreToolUse 与 Stop 钩子都跑了。所以重放不需要写信任，`host_replay.py` 也不动 `~/.claude.json`。两个钩子每次调用在 `.tripwright/hook.log` 写一行（`<时刻> <事件> block|pass|error <说明>`）；`host_replay.py` 跑完把每个钩子跑了几次、拦了几次、出错几次记进 `run.json` 的 `hooks`。
- **重放检查。** `scripts/answer_checks.py` 的报告加 `more`，每轮四项：`first_part_lines`、`internal_words`、`unreachable_needs_lookup`、`meals_named`。问题原话取运行目录的 `turnN.json`，地点表取工作区的 `trips/*/plan/places.yaml`。四项都计入 `passed`。原来十项的名字不动，因为回答层的测试钉着它们。
- **改了的旧测试。** `test_b3e_reference_graph`：两个新字段声明为普通值（不是引用），加一张“合法改法”表给锁的生成用例用；说明字段白名单加 `hours_source`。`test_b3f_field_table`：计数改成 14、20、76，加两个新字段的改法。`test_a6_check_answer`：证据号与字段名改由 `internal_words` 拦。
- **退回修：升级前的锁（11-01 05:54 退回）。**
  - 原因：A7 之前记的锁快照里，被引的地点没有 `opening_hours`（`kept` 记成缺或 None）；新草稿过模型后是 `[]`。比较器拿两者直接比，算成改了，所以有锁连到地点的仓，升级后任何 commit 都被拒。更正也一样：`amend` 读到快照里的 None，和 `old: []` 对不上，报“stale”。
  - 改法：`locks.field_value` 在比较器 `differences` 一处统一读法，propose、深核、commit、更正（`amend`）共用。记录里没有的字段，或模型不收 None 却记了 None 的字段，按模型默认值、经 `normalized` 同样的处理来读；记录里有的值照原值比。
  - 不放宽：快照里有值、现在不同，仍算改了；锁住地点的 `opening_hours` 真被改仍被拒，要写营业时间仍走更正。
  - 同一问题对以前各批加的字段也成立：凡是默认值不是 None 的字段（列表、Literal、布尔、数字）都会，这一处修一并管住。测试按字段表逐个参数化。
- **第一段钩子的理由（第 11 条；已被 D-46 取代）。** 11-01 06:0x 拦下时理由末尾曾加“写完第一段后，在同一条回复里接着派子代理或查询；只写文字的回复会结束这一轮”。D-46 把顺序退回 v2e（先在后台派，再把第一段写成这一轮最后的文字），这句话和它相反，06:2x 换成下一条的“核查请在后台派 researcher”。
- **再补：后台派发放行，Stop 挡只发状态句（D-46，11-01 06:20）。**
  - 原因：回答层两对预检 8 问都丢段。主 agent 在思考里写好第一段就去派 researcher，被 first-part 拦下后照样重派；拦满 3 次后自己查，只发了第二段。v2e 的顺序（先在后台派 researcher，再把第一段写成这一轮最后的文字）16 问两段都在。
  - `hook first-part` 放行主 agent 在后台派的 Agent、Task（`tool_input.run_in_background` 为 `true`；v2c 重放的 stream 里 Agent 的输入就是这个键）。前台的 Agent、Task，自己的 `query`（`check_answer` 除外）、WebSearch、WebFetch、命令行 `tripwright … query` 照拦。理由改成“先把第一段发给用户（现在怎么走、最晚几点），再查；<没过的检查>；核查请在后台派 researcher（Agent 加 run_in_background: true）”。日志记 `first-part pass background dispatch (Agent)`。
  - `hook stop` 加一条：这一问（最后一条真用户消息之后；后台子代理完成的通知不算）主 agent 派过子代理（前台、后台都算）或调过查询，而这一问显示过的文字里，既没有一段有第一段的样子（D-47 起只核结构：前两行有那一刻的钟、没有标签与工具状态句；06:20—06:5x 曾和 first-part 钩子同一套，也核内部字样与行数），也没有第二段，就输出 `{"decision": "block", "reason": "把第一段写成回复文字再结束（现在怎么走、最晚几点）；<没过的检查>"}`。Stop 输入的 `last_assistant_message` 若还没写进会话记录，也算这一问显示过的文字（按现在的钟核）。没派、没查的问（闲聊、只记账）不拦；`stop_hook_active` 为真不拦（D-48 起改成每问最多拦 3 次）；第二段照原来的比对。
  - 钟的时区：两个钩子都先取 `check_answer` 上次记下的时区（计划的时区，写在答案文件的 `zone`），再取仓的 `tripwright.yaml`，都没有用 UTC。`--db` 方式没有 `tripwright.yaml`，原来只能用 UTC。
  - 在旧重放的会话记录上回放这条 Stop 规则（`~/.claude/projects` 里各轮的会话记录，按重放固定的钟，只看判定）：
    - v2f 预检 4 轮 8 问：4 问派过核查、却既没有合格的第一段也没有第二段，都被拦；2 问最后发的是第二段，照原来的比对，这条不管；2 问有合格的第一段，放行。
    - v2e 全部 12 轮 24 问（后台通知之前 Stop 共触发 25 次）：第一段都在，但 9 次会被拦一次，宿主要再写一遍第一段：8 次是 A7 加的 `internal_words`（“外部截止”7 次、“计划目标”1 次），1 次是正文 14 行，超过 8 行。v2e 写于这两条检查之前。
    - 所以回答层的第一段不用内部字样、不超过 8 行，就不会多这一轮；否则每次多一轮，用户看到两遍第一段。D-47 因此把 Stop 改成只核结构，见下一条。
- **三补（D-47，11-01 06:43）。**
  - 命令行 `check_answer` 的 receipt：不带 `request_id` 时，`check_answer` 取最近一条收到的消息当本问。事件表里还有记账写的变更记录（`_audit` → `append_change`），它们没有 `receipt`，取最大值时 KeyError，命令行报 `tripwright: 'receipt'` 退出（回答层的评测仓 65 条事件里 46 条是这种）。这个分支是 A7 为 `meals_named` 加的。改成只看收到的消息（`repo.snapshot.received_events`，快照也用它）；一条也没有时照没有原话处理。
  - `hook stop` 对第一段只核结构：`first_part_shape`，即前两行有那一刻的钟（差 5 分钟内）、没有标签与工具状态句。内部字样与行数留给 `check_answer` 和重放检查。`hook first-part` 照旧用完整的第一段检查（它只在主 agent 自己查之前起作用）。
  - 在旧会话记录上重放（同上一条的做法）：v2e 25 次后台通知前的 Stop 全部放行（上一版拦 9 次）。v2f 预检 8 问里，上一版拦的 4 问现在拦 2 问：另 2 问最后一段是 16 行、前两行有钟的文字，只是超了行数，现在算第一段。
  - `scripts/answer_checks.py` 用重放的固定钟：`run.json` 的 `now` 列出各条消息的固定时刻（`host_replay` 把第 N 条消息定在第 N 个时刻；时刻比消息少时，从最后一个时刻接着走），每段文字的时刻 = 它那条消息的固定时刻 + `text_at`（消息发出到这段文字出现的秒数）。没有固定钟才用记录里的时刻。只有一个时刻的旧重放，固定钟在建工作区之前就开始走，所以会差几秒，在 5 分钟的容差里。在 v2f 的 6 个固定钟重放上：12 轮里 8 轮的 `clock_in_first_part` 由红变绿；另 4 轮仍红，因为没有第一段，或第一段前两行没有钟。
- **四补（D-48，11-01 07:00）。**
  - 原因：回答层冻结第 1 对里，连上那轮第二问主 agent `turn`、读地点表、发了说“第二段核完再发”的第一段就结束，没派没查，两个钩子都不管，第二段丢了；第一问的第二段以“有变化：”开头、没有“第二段”标题，`check_answer` 也让它过了。
  - `hook stop` 改成有上限的推进器（`stop_decision`）。这一问还没有第二段时：
    - 派过子代理或查过、却没有带第一段样子的文字 → 拦，“把第一段写成回复文字再结束（现在怎么走、最晚几点）”（D-46、D-47，不变）。
    - 有一段带第一段样子、且说了还有第二段的文字（词表取自骨架：“第二段”“核完再发”，英文 “second part”），或这一问派过子代理、查过，而没有在跑的后台核查 → 拦，“在后台派 researcher 核查，再写第二段（先过 check_answer）”。
    - “在跑”：这一问在后台派的 Agent、Task，还没有它的完成通知。2.1.301 的通知正文带 `<tool-use-id>`，和派它的那次调用对上（fz 两轮的会话记录核过）；通知不带这个号时，算最早那个做完。
    - 发出的是第二段：照旧和过核的草稿比。
    - 快问（没说第二段、没派没查）、闲聊放行。
  - 上限：同一问（会话号＋最后一条真用户消息的时刻）三条理由合计最多拦 3 次，之后放行并记日志（`let through after 3 blocks for this question`）。`stop_hook_active` 不再一律放行，也照这个上限算。计数和 first-part 的放在同一个 `.tripwright/hook-state.json`，键前加 `stop:`。
  - `check_answer` 第二段加 `second_heading`：要有一行以 `#` 开头的“第二段”标题，标题里带时刻；没有就报，改法“第一行写 `# 第二段 · …（HH:MM）`”。标题可以不在第一行：骨架让写入冲突那一行排在回答最前。标题时刻不晚于现在，仍由 `title_time_not_later` 核。英文骨架的 “# Second part · …” 也认，但其余第二段检查（“核过”“完整安排”）仍只认中文，这是原有的缺口。
  - 在旧会话记录上重放（v2e 12 轮、v2f 有会话记录的 10 轮；在每次 Stop 触发处判，不计上限；v2e 每问末尾的第二段比对不算，那时还没有过核草稿的记录）：新加的推进一条只在 fz_on_r1 第二问拦 1 次，就是这次要挡的形状。其余各处的判定和 D-47 相同：v2f p2_off 两问、s1_on 两问在后台通知前因没有第一段被拦（D-46 那条）；fz_off_r1 第一问因第二段没过核被拦（实际也拦了）。fz 两轮的 r2 还在跑，没算。
- **五补（11-01 07:41；冻结已跑完，不进被判的版本）。**
  - 原因：回答层冻结里，连不上 MCP 的 4 轮第一问（只问去机场）都被 `meals_named` 拦。命令行 `check_answer` 不带 `request_id`，服务端拿最近收到的一条消息当本问；评测仓里那条是写入吃饭地点时记的设置消息（channel system，含“吃饭”）。用户真实仓里，连不上 MCP 时这一问根本没经 `turn` 记下，最近一条会是上一次对话的消息。
  - `check_answer` 收可选 `question`（这一问的原话），有就以它为本问，也压过 `request_id` 那条消息的原文（`request_id` 仍定证据从何时算起）。命令行 `--question <文件或 ->`；`--text-file -` 与 `--question -` 不能同时读标准输入。
  - 两个都不给时：只认 channel 不是 system、收到时刻不早于服务端的钟 30 分钟前的消息，取最近的一条（同一时刻的取后记下的）。找不到就当本问原话不明：`meals_named` 不判，第二段的 `summary` 末尾加“本问原话不明，吃的检查没做。”。
  - 测试夹具 `fixed()` 也把收消息的时刻定在同一时刻（`events.now`），否则消息记的是真时间，离固定的 2030 年的钟远远超过 30 分钟。

# B3g：改锁的三条路共用一个来源准入；回执带出处；turn.record 先核形状（2026-11-01，b3g_admission_opus_20261101-0916）

〔B3k（D-53）：本节的锁规则只对用户标了 must 的决定生效；没标的用户决定只记录，agent 改它要给理由，见 B3k 节。〕

依据：锁链第 6 次判的反例 A—C 与机制补充 1—3；任务决定 D-49。修前：用户新决定只看 `basis == user_explicit`，截图提取、缺说话人、外来说话人、引用更早的用户消息都能借它取代旧锁并提交；合法新决定的 propose、commit 回执没有 `lock_changes`；`turn.record` 的条目或 `source` 是数组时只回通用错误。

- **一个准入入口：`Ledger.admit`。** 用户新决定（`decided_by: user`，或 `parts` 里标 user 的部分）、`changes.reports`、`changes.confirm_corrections` 在生成新锁、取代旧决定或改受保护字段之前都过它，按顺序核五项：是收到的消息；是本次提案回答的那条（`Ledger.apply(answered=…)`，由 versions 从 ctx 或草稿的 `source_event` 传入，不给就拒）；来源性质是 `user_verbatim` 或 `host_restatement`；说话人是已提交行程里的人；有引用句时句子在消息里。拒绝写成 `<哪条> not admitted (<哪项>): …`，哪项为 received message、this turn、nature、speaker、quote 之一。过了的来源记成消息本身的性质，basis 记 `user_explicit`。agent 能自填的标签不再授权：`decided_by: user` 仍要 basis `user_explicit`（标签前后一致，保留原报错），能不能改锁只由准入定。`Tripwright._said_now`（只核实况与确认是不是本轮）并入准入后删除。
- **为什么放在账本层。** 三条路都在 `Ledger.apply` 里落地（新决定取代旧决定、`_amend` 改锁），propose 的预演、deep_check、commit、仓的重放也都经 `apply`；入口放这里，新加一条改锁路径也要经过它。原来本轮的检查在服务层，只覆盖了两条。
- **行程的人取已提交的版本。** 第一次提交之前没有行程的人，用户决定要等计划先提交（拒绝里写 `no plan committed yet`）。这是从紧的默认：候选里的人员由 agent 写，拿候选认说话人，等于让 agent 自己加人。〔B3h 取代（D-50）：第一份计划可同次记用户锁，说话人取这份首次计划的人员；已有计划之后照旧只认已提交的，见 B3h 节。〕
- **回执出自同一份准入操作：`ledger.lock_receipt`。** `apply` 另返回 `locks`，即准入并落地了的改锁操作。propose 把预演那次的操作做成回执存进草稿 `lock_changes`，commit 把提交那次的存进 `committed_lock_changes`；服务直接回这两份。键与内容：
  - `decisions`：`{id, event, nature, speaker, locks, replaces: [{decision, locks}]}`，`locks` 是这条决定里用户定的路径，`replaces` 是被它取代的决定和它们的锁。
  - `reports`：`{id, target, field, old, new, event, nature, speaker, quote, locks: [{decision, path}]}`；`confirmations` 同样，多 `answer`。
  - `corrections`：`{id, target, field, old, new, event, locks, question}`。
  - propose 每行加 `takes_effect`，不带 `amended`（新决定号提交后才有）和 `status`；commit 的 reports、confirmations 每行带 `amended`，并把全部 `amended` 汇到顶层（原形状 `{decision, replaces, change, via, target, field, old, new, event}`，加 `paths`），corrections 带 `status`。
  - 旧的 `Ledger.lock_effects`、`Tripwright._lock_changes` 删除。B3g 之前提交的草稿没存回执，对它重复 commit 时不再回 `lock_changes`（只影响幂等重放的回答）。
- **turn.record 先核形状：`service.checked_record`。** 在收消息之前核 `record` 是对象，`items`、`facts`、`pending_checks`、`relate`、`carry` 是数组，元素是对象，元素的 `source`（有就核）是对象；过了才补占位来源（`placeholder_sources`），再由账本的模型做类型与业务校验。错时按 X1a 的写法给路径、要的类型和键、收到的类型，例如 `Invalid record: record.facts.0.source must be an object {event ("this" for this message), optional nature and basis}, got array`；消息不记。改的是校验顺序，没有多捕一种异常。
- **老测试怎样改（没有放宽断言）。** 锁原来常从不带说话人的用户消息来，或引用 `conftest.source` 那条在任何 turn 之外收的旧消息；现在都改成本轮、带说话人的用户消息：`test_b3e_reference_graph.say` 与 `test_b3d_regressions.say` 给用户消息带 `u1`（agent 消息不带）；`core_helpers.decide` 收一条带说话人的用户消息并从它记决定（`test_versions`、`test_b3a_regressions`、`test_b3c_regressions` 用它）；`test_a3`、`test_a5`、`test_b3d_lock_aliases`、`test_b3d_mcp`、`test_b3e_mcp`、`test_provider_integration`、`tests/repo/test_manual` 给定锁那条消息补 `speaker`。`src/tripwright/demo.py` 的演示消息补 `speaker="p2"`。
- **测试。** `tests/core/test_b3g_admission.py`：反例 A 七个样本（`user_verbatim/u1`、`host_restatement/u1` 放行；`agent_inference`、`screenshot_extraction`、缺说话人、外来说话人、更早的用户消息拒，并核拒绝点名的那项、计划与现行决定不变、锁仍挡住同一计划）× 三条路，用同一组样本参数化；反例 B 三条合法路径与开更正的 propose、commit 回执逐字段断言；agent 决定里用户定的部分同样过准入；标成 agent 推断的用户决定被拒；不经提案直接 `apply` 改锁被拒；反例 C 十个形状各一坏一好（好的真记下，carry 先放一条导入的长期条目）。`tests/core/test_b3g_mcp.py`：真实 stdio 与 HTTP 各一轮，截图、外来说话人、更早消息的新决定被拒，合法新决定的两份回执，十个坏形状与五个好形状。
- **变异（仓外副本，五个测试文件 185 项）。** 修前的源码 39 项红；分别去掉准入的性质、说话人、本轮、引用句检查：12、10、6、1 项红；新决定绕过准入：8 项红；去掉 basis 标签检查（判官的那个变异）：1 项红；回执不列决定：6 项红；去掉形状检查：12 项红。判官的 `own_paths.py`、`own_mcp_b3f.py` 复制出来重跑（判官目录没动）：26 个路径样本全按预期；stdio 与 HTTP 上截图、外来说话人的新决定被拒，合法新决定的两份回执都有 `decisions`，十个记录形状都给出字段路径。脚本与结果在任务目录 `agents/b3g_admission_opus_20261101-0916/`。
- **限制。** 准入核的是消息的性质、说话人和是不是本轮，不核消息的意思与所改的票、地点是否对得上（判官列为范围外）；宿主是否如实标性质与说话人，服务端核不了。

# B3h：改锁的授权按事件先后单调；更正记真正提出的时刻；混合决定能取代旧锁；首次计划可同次记锁（2026-11-01，b3h_authority_opus_20261101-1024）

〔B3k（D-53）：本节的锁规则只对用户标了 must 的决定生效；没标的用户决定只记录，agent 改它要给理由，见 B3k 节。〕

依据：锁链第 7 次判的反例 A—D 与机制补充；任务决定 D-50。修前：用旧消息的同一 request_id 重调 `turn`，拿到绑在当前版本上的新 ctx，就能凭这条旧消息把更晚的用户决定改回去（A）；更正的 source 引用更早的事件，服务端把它当成提出时刻，早于真正提出的确认也能生效（B）；agent 决定里标 user 的部分经准入后，仍不能取代已有的用户锁（C）；没有已提交计划时，带用户决定的首次提交被拒（D）。共同结构：授权的有效性与先后全靠上游来源检查，一次授权能被反复领取。修在锁这一层，不靠“宿主别引用旧消息”的条款。

- **授权记在锁上，按收件先后单调（`Ledger._in_order`）。**〔B3j：改名 `Ledger.in_order`；提交处对这次改到的每把基础锁再查一次，账本怎样重记都要过，见 B3j 节。〕 每条用户锁（用户新决定、混合决定、实况与确认派生的决定）存 `authority {event, received_at}`：授权它的用户消息与服务端收件时刻。B3h 之前的锁没有这个字段，取 `amends.event`，没有就取 `source.event`（`Ledger.authority`）。改一把锁之前（取代旧决定、`_amend` 改字段），授权消息不得早于锁上的授权：先比收件时刻，同刻按收件顺序（固定钟上会同刻）。早的拒，写 `… not admitted (order): the authority is out of date: lock <id> was changed by message <事件> (received …) after message <事件> (received …)`。
  - 重试旧 request_id 照旧只取回原收件；它产生的 ctx 绑当前版本，但不让旧消息变新，所以改不回更晚的决定。服务层、stdio、HTTP、跨会话（新客户端、重启的服务读同一库）都是这一处判。
  - 合法的照旧：同一条消息出几份草稿，提交一份后其余因账本变了被拒；同一条消息再改它自己设的锁（同刻不算早）〔B3j 取代（D-52）：一条消息改过一把锁并提交之后，再用它改这把锁（含后继锁与余锁）在提交处拒，`not admitted (used)`；同一提案里多字段、多把锁，同一消息分两次改不相干的锁，同一份草稿重放，都照旧，见 B3j 节〕；更晚的消息改锁；同输入的纯取回。
  - 为什么不发一次性令牌（D-50 乙）：同一消息多草稿、重试取回都要另加规则，状态多一张表；按先后比只用已有的收件时刻，旧锁也能推出授权。〔B3j：“只用一次”仍不发令牌，记在提交的版本上（`version.authorized {event, locks}`），由提交处查，见 B3j 节。〕
- **更正记真正提出的时刻。** `Ledger.apply` 收 `Proposal(answered, at, plan)`；`at` 是提案的服务端时刻（草稿的 `created_at`，提交与仓的重放都用它，所以重放不会把提出时刻挪后）。`_correction` 在问题上记 `asked {event: 这次提案回答的消息, at}`，`source` 引用哪个事件都不改它。确认的消息必须在 `asked.at` 之后收到，且不是 `asked.event` 或问题的 `source_event`，否则拒（“the user answers in a later message than the proposal that opened the correction …”）。B3h 之前开的问题没有 `asked`，取它存下的时刻 `since`。
- **混合决定取代旧锁（`Ledger._replace`）。** 用户新决定与 agent 决定里标 user 的部分走同一处：对同人、同范围的现行决定，凡锁路径与这些 user 路径指同一对象的，按路径取代。〔B3i 取代（D-51）：按比较器的对象与引用图判，新决定完整盖住旧锁定的才取代，只碰到一部分就整份拒，见 B3i 节。〕旧决定还锁着别的用户路径时，旧决定标 superseded，那些路径连同原快照、原授权留在一条“余下”决定里（`remainder_of`），回执的 `replaces` 行带 `kept`。〔B3j：余下还收旧锁护着、新决定不承接、仍在计划里的项，按能写成锁路径的名字记（如块锁护着的时区记成 `/timezone`），见 B3j 节。〕agent 部分不取代任何锁，照旧由比较器核。这也改了用户新决定：以前只要一条路径重叠就整条取代，旧锁别的路径从此不受保护；现在只取代重叠的。〔B3i：“重叠”原按路径字符串前缀判，子字段决定会撤掉整块锁（第 8 次判反例 A），现改为完整覆盖才取代。〕
  - 一并修：`versions.propose` 把 `parts` 的键也换成稳定路径（原来只换 `locked_paths`，用位置路径写的 user 部分对不上锁路径）。
- **首次计划可同次记用户锁。** 没有已提交计划时，说话人取这次提案候选计划里的人员（`Ledger._people`），回执该行加 `people_from: "the first plan (no plan was committed yet)"`。已有计划之后照旧只认已提交计划的人员，候选里新加的人不算。理由：首次提交没有可被覆写的旧锁，这条放宽不碰硬门槛 2；若有反例证明它能覆写已有的用户选择，回到两段式（D-50 第四条乙）。
- **测试。** `tests/core/test_b3h_authority.py`（16 项）：A 的 A→B→重试 A（决定与实况两种），更晚消息再改、同一消息多草稿只交一份、同输入纯取回、同一消息再用、旧锁推授权；B 的 t0<t1<t2（更正回答新 turn 与回答重试的旧 turn 两种），后一条消息确认的对照，旧问题按 `since`；C 的混合决定真提交、agent 部分仍不能改用户锁、取代时旧锁其余路径留锁（用户决定与混合决定两种）；D 的首次计划同次记锁、首次计划外的人不算、已提交后候选新加的人不算。`tests/core/test_b3h_mcp.py`：stdio、HTTP 各一轮（D、A、B、C 共 8 个判定），跨会话一轮（新客户端重试、重启服务后重试、之后更新的消息改锁）。
- **变异（仓外副本，六个测试文件 202 项）。** 修前的源码 14 项红；去掉先后检查 6 红；实况与确认不做先后检查 1 红；更正改回用 source 事件的时刻 5 红；混合决定不走取代 4 红；取代时丢掉旧锁其余路径 2 红；首次计划的人员不用 4 红。判官第 7 次判的三份复现脚本复制出来重跑：`judge7_gate.py` 50 个样本全按预期，`judge7_mcp.py` 两种传输的旧请求重绑与提前确认都被拒，`judge7_cross_session.py` 版本 11 → 11、渠道仍是 B。脚本与结果在任务目录 `agents/b3h_authority_opus_20261101-1024/`。
- **限制。** 先后只按服务端收件时刻与顺序比，不按消息的意思；用户真在一条更早的消息里说过、宿主晚了才记的决定，若中间有更晚的消息改过这把锁，要用户再说一次。确认在固定钟上与提出同刻时仍按“不晚于”拒。

# B3i：取代旧用户锁只在完整覆盖时发生；部分碰到就拒（2026-11-01，b3i_cover_opus_20261101-1129）

〔B3k（D-53）：本节的锁规则只对用户标了 must 的决定生效；没标的用户决定只记录，agent 改它要给理由，见 B3k 节。〕

依据：锁链第 8 次判的反例 A、B 与共同结构；任务决定 D-51。修前：B3h 的取代器按路径字符串前缀认“同一个选择”，比较器按对象与引用图认，两边不一致。用户新决定（或混合决定的 user 部分）只锁 `/blocks/<id>/start`，就把旧的整块锁置为 superseded，之后 agent 不经用户就能改这块的活动文字与引用的购票渠道（同一候选夹改也成功，A）；只锁 `/bookings/<id>/where` 的决定与旧整块锁没有前缀关系，旧锁照旧拒，用户没有路可走（B）。

- **用比较器的对象认“同一个选择”（`locks.identity`、`claimed`、`held`、`reach`）。** 锁路径按模型走成对象名：引用图的 `kind:id`（place、booking、fare、person）、块 `block:<id>`、一天 `day:<date>`、行程本身的字段 `trip/<字段>`，后面接对象里的字段路径（`/blocks/d1/start` → `block:d1/start`，`/bookings/entry/where` → `booking:entry/where`，`/bookings/ride/fares/F0` → `fare:F0`）。
  - 一个决定“定了什么”（`claimed`）：路径本身，加值里套着的有名对象（一天里的各块、一张票里的各段 fare）。
  - 一把锁“护着什么”（`held`）：它定的，加块的位置、快照引用到的每个对象（连同其中套着的）、时区。〔B3j：块的位置有了名字 `where:block:<id>`，块路径护它，一天（天列表、整份行程）也护它所含各块的位置，一天里的某个方案不护；时区只由快照记了时区的锁护，天锁不护，见 B3j 节。〕这和 `resolve`、`lock_changes` 比的是同一张图。
- **判法（`Ledger._replace`）。** 对同人、同范围、来源消息不同的现行决定，逐条用户路径用 `reach` 判：
  - 新决定定的把旧锁定的每一项都盖住（相等或包含它）→ 取代。旧锁经引用护着的对象随新决定一起重定，与 B3f 起“用户在整块上重定，候选的新值成新锁”一致；授权先后与余下路径照 B3h。
  - 盖不全，但碰到旧锁护着的任何一项 → 部分碰到：旧锁不撤，整份提案拒。这包括新路径在旧锁之内（子字段），或是旧锁经引用持有的对象字段。
  - 什么都不碰 → 并存。
  - agent 定的路径不执行，只要被碰到就随新决定取代，照旧。
  - 结果：同一块重定、包含它的一天重定都取代；块里一个字段、它引用的票的一个字段都拒；另一块、同一块别的字段的锁都并存。B3h 时一天的决定盖不到天里按 `/blocks/<id>` 记的块锁，现在能。〔B3j：一天取代块锁时，块锁护着、天锁不护的时区留成 `/timezone` 余锁（第 9 次判反例 B）；这天在基础版本里含的块（含这次去掉的）也算这天定的，去掉的块随用户的授权释放，见 B3j 节。〕
- **拒绝怎样说（第 3 条）。** 决定那行带 `refused: [{decision, locks, decides, reason, fix}]`，`fix` 写 `record the decision on <旧锁路径>: the user decides that whole part again`，propose 的 `takes_effect` 写 `nothing: the proposal is refused`。草稿 `refused` 列出原因，`status` 为 rejected，只记账本的提案也一样；`notice` 以 “Refused, nothing will be recorded” 开头。commit 拒，`Commit refused: user decision … decides only a part of what lock … holds at …`。成功取代时 `replaces` 照旧。
- **为什么不做字段级的部分更新（D-51 乙）。** 那要把锁展开成受保护字段集合，只更新相交部分，并重写取代器、回执与重放，又是新面。现在只拒，不撤任何保护；代价是用户只改开始时间，也要宿主在整块上重记一次决定。
- **测试。** `tests/core/test_b3i_cover.py`（25 项）：
  - A：user 与混合 × 只改 start 与同次夹改，共 4 项；都拒，旧锁仍在，之后 agent 改活动文字与购票渠道仍被拒。另有只记账本的窄决定也拒。
  - B：user 与混合的 where 字段决定被拒并提示整块重定；同一改动在整块路径上记成功，其他选择仍受保护。
  - 合法对照：同一路径与包含它的一天都取代；块决定取代它的字段锁；两条互不相碰的字段锁并存；另一块的锁留着。
  - `reach` 的七种相遇、`identity` 的六种路径。
  - `tests/core/test_b3i_mcp.py`：stdio、HTTP 各一轮，每轮 7 个判定。
- **变异（仓外副本，七个测试文件 227 项）。** 修前的源码 23 项红；取代改回路径前缀 10 红；不查引用图 6 红；部分碰到不拒 9 红；commit 不拒 7 红。
- **判官复现脚本重跑。** 判官第 8 次判的 `judge8_cases.py`、`judge8_mcp.py`（连同它们引用的 prior7 辅助脚本）复制出来重跑：
  - 与判官预期不同的只有按 D-51 改拒的 8 行：窄 start 决定与引用字段决定，user、混合两种，`judge8_mcp.py` 里 stdio、HTTP 各两行。
  - 窄决定之后 agent 改活动与渠道（`agent-unapproved8-*`）两种传输都拒，其余样本全按预期。
  - 脚本与结果在任务目录 `agents/b3i_cover_opus_20261101-1129/`。
- **限制。** 盖住按对象名判，不判值的意思。改人员或时区这类被很多锁经引用护着的字段，也会被判成部分碰到而拒，要在相关的锁上重定。

# B3j：提交处以基础版本为锚，守保护守恒与授权只用一次（2026-11-01，b3j_conserve_opus_20261101-1250）

〔B3k（D-53）：本节的锁规则只对用户标了 must 的决定生效；没标的用户决定只记录，agent 改它要给理由，见 B3k 节。〕

依据：锁链第 9 次判的反例 A、B 与共同结构；任务决定 D-52。修前：同一条用户消息改了一次锁并提交后，用同一 request_id 重调 `turn` 拿到新 ctx，就能凭这条消息再交一份草稿，又改同一把锁（实况→实况、决定→实况、实况→决定都成，stdio、HTTP、新客户端、重启服务都复现，A）；块锁的快照记了时区，用户在包含这块的一天上重定（取代成功）后，天锁不记时区，agent 直接改 `Trip.timezone` 就提交成功（user、混合两种，B）。共同前提（第 6—9 次相同）：账本先重记或取代现行锁，提交时比较器只信重记后的锁；重记时认了不该再用的授权，或少承接了旧保护，比较器就没有东西可比。这次不再只补点名的两处，在提交这一步加一层只以基础版本为锚的检查。

- **放在哪（`Versions._guard`）。** 提交前已提交版本里的现行用户锁是锚（`base`，账本重记之前取），新计划是对照。这层在 propose 的预演、deep_check、commit 三处跑同一个函数；propose 把拒绝写进回执 `lock_changes.refused` 与草稿 `refused`，草稿 rejected，notice 以 “Refused, nothing will be recorded” 开头（沿用 B3i 的通道）；commit 拒，原因排在比较器的冲突之后，合在同一条 `Commit refused: …` 里（只有比较器先抛时，决定→决定的重试看不到 `used`）。纯函数放 `locks`，与比较器同一张对象图；提交处只做读取与裁决，不写账本。
- **保护守恒。** 对每把基础锁的每条用户路径：
  - 改了的项（`locks.changed_items`：把 `lock_changes` 的改动名换成对象名，如 `value/start` → `block:<id>/start`、`date`/`plan`/`rank` → `where:block:<id>`、`timezone` → `trip/timezone`、`booking:entry/where` → 本名）要有用户对它的授权：这次提案经准入、未被拒的用户决定（含混合决定的 user 部分），它定的（`locks.decides`）完整盖住旧锁定的（B3i 的 `covers`），且它护着这一项；或这次的用户实况、已确认的更正正是这个字段（`locks.field_name`）。否则 `not admitted (conservation): lock <id> at <路径>: <项> changed without the user's authority for it; the user decides all of <路径> again, or reports or confirms that field`。
  - 旧锁护着、提交后仍在计划里的项（`locks.required(新计划, 路径)` 与旧锁 `held` 的交）要被提交后的某把现行锁护着（原锁、后继锁或余锁）。否则 `… <项> would be protected by no lock after this commit; the user decides <路径> (or the day holding it) again as a whole`。不在计划里的项不要求：用户重定一天时去掉的块随这次授权释放；agent 去掉锁住的块，是改了的项（`missing`）而没有授权，照拒。
  - 这层只读基础版本、新计划、这次准入的操作与提交后的现行锁；第一条不依赖账本怎样重记，第二条读重记后的锁，只问“还有没有人护着”。来源对不对（是不是这条消息、用户原话、说话人、引语）它不判，那是 `Ledger.admit` 的事。
- **授权只用一次（`version.authorized`）。** 提交成功的版本记 `authorized {event, locks}`：这次提案回答的消息，与它改到的基础锁和新生的锁（后继锁、余锁）。之后同一消息的另一份草稿再改其中任何一把，提交处拒：`not admitted (used): message <事件> already changed lock <id> in version <N>; only a new message of the user changes it again`。“改到”按保护守恒同一套判：基础锁的项变了，或锁不再现行。合法的照旧：同一份草稿重放（幂等，取回原版本，不进这层）；同一提案里多字段、多把锁一次完成；同一消息分两次提交改两把不相干的锁（锁集不相交）；之后的新消息。没有把 `_in_order` 的 `<` 改成 `<=`：那会拒同一提案里第二个字段的修改。为什么记在版本上不另立表：版本本来就是提交的唯一记录，同一提案的多处修改天然是一次；账本不另记“消息→提交”，重放与回滚不用同步两份。
  - 先后在提交处也查一次：对改到的每把基础锁调 `Ledger.in_order`（B3h 的同一函数）；确认的消息必须晚于更正被提出的时刻（`asked.at`，旧问题取 `since`），否则 `not admitted (order): …`。
- **账本侧随之改的（让合法的整天重定还能成）。**
  - 余锁扩到未承接的保护（`Ledger._keep_unclaimed`）：取代时，旧锁护着、新决定既不定也不护、仍在计划里、能写成锁路径（`locks.path_of`：`/timezone` 这类行程字段，整个 place、booking、person、fare）的项，连同旧锁的授权与快照里那一部分，留进 B3h 的余下决定，回执 `replaces[].kept` 列出。整天重定后块锁护着的时区成 `/timezone` 余锁（反例 B 在账本侧就有锁可比）；混合决定的余锁标 user 部分。
  - 写不成锁路径的项（块的位置 `where:block:<id>`）不留余锁；新决定也不护它时，提交处按第二条拒，说明在整块或包含它的一天上重定。例：用户只定一天里的某个方案（`/days/<d>/plans/<p>`）去取代块锁，方案不护块在一天几个方案里的位置，拒。
  - `locks.decides`：一个决定定的，除 `claimed` 外，再加它的路径在基础计划里含的有名对象。整天重定时，这天原来的块（包括这次去掉的）也算被它定了，取代对这些块的锁才是完整覆盖。
  - 名字：块的位置名 `where:block:<id>`（原来与块本身同名），块路径与包含它的一天、天列表、整份行程护它，一天里的某个方案不护。
- **判别实验（D-52 第 3 条；`agents/b3j_conserve_opus_20261101-1250/experiment-b3j.json`）。** 第 6—9 次判的硬门槛 2 反例 148 行与合法对照 26 行，在仓外副本上关掉不同的层重跑（判官脚本复制改写，令牌改成 `tw-b3j-*`）：
  - 现行代码：148 行全拒，26 个对照全成。
  - 账本侧全关（B3g 准入只留“收到过这条消息”、B3h 先后与提出时刻、B3i 判法改回路径前缀、B3j 余锁），提交处全开：漏 30 行，全是来源准入样本（决定、实况、确认、混合决定的 user 部分 × 推断、截图、无说话人、外人、更早的请求、更早的会话、改写性质、引语不在原文）。守恒不判来源，被认作用户授权的就算授权，归准入管。第 9 次判的 A、B 与第 6—8 次判的 S6i、S6k、旧请求重绑、提前确认全拒。合法对照里 3 个被拒（两种整天重定、去掉一块的整天重定）：B3i 关了，一天的决定盖不到块锁，比较器拒，失败在关闭一侧。
  - 只留守恒（再关提交处的 used、先后与确认时刻）：再漏 7 行，都是授权的投递问题（S6i 同消息重用 3 种、旧授权的实况与决定、旧请求重绑、更正提出前的确认），归“只用一次”与先后管。
  - 提交处也全关：再漏 12 行（S6k 窄决定之后 agent 改活动或渠道），共 49 行。即账本侧的 B3i 判法关掉后，第 8 次判的反例 A 由守恒挡住。
  - 只关 B3j 的余锁、提交处全开：0 漏；两种整天重定被守恒拒（时区没人护）。再把提交处也关：漏 5 行，第 9 次判的 A 三种与 B 两种。
- **测试。**
  - `tests/core/test_b3j_conserve.py`（20 项）：A 的四种组合（判官的三种加决定→决定），重试的 ctx 回执不变、版本不变、预演写 `used`、提交拒、之后新消息能改；合法：同一份草稿再提交取回同一版本、一个提案改一把锁的两个字段、同一消息分两次改不相干的两把锁；B 的 user、混合两种整天重定后 `kept: ["/timezone"]`、余锁的值与授权、agent 改时区被余锁拒；整天重定同次改时区预演写守恒、提交拒；只定一个方案去取代块锁因块位置无人护被拒；整天重定去掉一块，块锁释放；agent 去掉锁住的块被拒；账本侧在测试里削弱（不留余锁、改锁时不查先后），提交处照拒（守恒、先后）；`changed_items` 四组、`required` 的天与方案。
  - `tests/core/test_b3j_mcp.py`（7 项）：A 在 stdio、HTTP 各一轮（三种组合，每种首改、重试、新消息共 10 个判定），HTTP 跨连接与重启服务一轮（5 个判定）；B 的 user、混合 × stdio、HTTP（每轮 3 个判定）。
  - 老测试按新行为改两处：`test_b3h_authority.py` 的“同一消息仍能再改它设的锁”改为 `…does_not_change_it_again`；`test_b3i_cover.py` 整天取代那项的回执带 `kept: /timezone`。
  - 修前修后：同一组 MCP 场景在 HEAD（B3i）源码上跑，A 的重试在四种连接方式下都提交成功，B 的 agent 改时区在四种组合下都提交成功；修后全拒（`agents/b3j_conserve_opus_20261101-1250/scripts/before_after_b3j.py`）。
- **变异（仓外副本，九个测试文件 254 项；`agents/b3j_conserve_opus_20261101-1250/mutations-b3j.json`）。**
  - 修前的源码 26 红。
  - 守恒：去“改了的项要授权”1 红，去“仍在计划里的要有锁护”2 红，两条都去 3 红。
  - 去“只用一次”8 红（A 的全部）。去提交处的先后 1 红（账本在测试里不查先后的那项）。去提交处的确认时刻 0 红：账本的同一检查是行内代码，测试削弱不了，只有判别实验能看出它。
  - 去账本余锁 7 红，原因是提交处按守恒拒了整天重定，失败在关闭一侧；余锁与守恒都去 10 红，这时 agent 改时区真的提交成功。只去其中一层，B 都还被另一层挡住。
- **判官复现脚本重跑。**
  - 判别实验的现行代码一行就是第 9 次判的 `ninth_cases.py`（含第 7 次判的 `judge7_gate.py`），共 118 + 50 行，与判官预期 0 处不符。
  - `ninth_mcp.py` 41 行与 `day_mcp.py` 12 行，0 处不符。
  - `judge8_mcp.py` 18 行，不符的 4 行是 D-51 有意拒的 `child8-*`，与 B3i 时相同。
- **限制。** “只用一次”按锁的身份判。用户一条消息说了两件事，宿主分两次提交，而第二次又碰到第一次改过的锁，就会被拒，要用户再说一次。`used` 每次扫这个用户的全部版本。守恒按对象名判，不判值的意思（同 B3i 的限制）。

# B3k：锁改成“记录为主、必须项才护”（2026-11-01，b3k_record_first_opus_20261101-2030）

依据：用户 11-01 20:19 的话（Q15，语音转录）：“有些东西需要保持灵活性，而有些则是必须去的地方……加锁之类的操作会不会太 heavy 了？……更多的其实是给他们做好相关的记录”；任务决定 D-53、D-56。B3f—B3j 的全量锁（D-36 零豁免，D-49—D-52 的准入、先后、只用一次、守恒）是主 agent 定的设计默认，不是用户的要求。这一轮把它收到用户说了“必须”的项上，其余改成记录。

- **必须标记：`Decision.must: bool = False`。**
  - 用户说了“必须”（必去的地方、硬节点）才填 true。没说就当没有（R-58）。
  - 不复用 `Place.priority`（must/recommended/optional）。理由：它在候选计划里由 agent 写，任何一次提案都能改它，当不了用户的授权；而且三档里只有一档有行为。词沿用 “must”，与 `Item.strength`、`Place.priority` 一致。
  - 混合决定里 `parts` 标 user 的部分随整条决定的 `must`。要分开的，宿主记成两条决定。
- **只有 must 锁（`locks.binding`）。** 原来“用户定的部分”这一判定散在五处：比较器、`_user_locks`、`holders`、`_replace`、`_guard`。现在统一成一个函数，即用户定的部分并且标了 must。准入（B3g）、先后（B3h）、完整覆盖与余锁（B3i、B3j）、提交处守恒与只用一次（B3j）、实况与更正改锁（B3f）都只对它生效。
  - 没标 must 的用户决定照记：`_source` 核这条消息收到过、性质不升级，不过 `admit`。回执行带 `must`、`paths`（用户定的）、`locks`（锁的，非 must 为空）。
  - 碰到 must 的新决定（`Ledger._meets_must`：它定的经比较器的对象图碰到任一现行 must），不管自己标没标 must，都要过准入。否则 agent 可以借一条“非必须的用户决定”取代 must。
  - 用户本轮的新决定完整盖住一把 must、自己又没标 must，就是用户放掉了这项：取代照 B3i，不留 B3j 的余锁（`_keep_unclaimed` 只在新决定是 must 时跑），提交处守恒把这把锁护着的全部算作随用户授权释放（包括新决定不护的时区）。同一提案里改那些项仍要用户授权（守恒第一条照旧）。
  - 旧数据：B3k 之前存的决定没有 `must`，读成 false，一律不锁，升级不报错。`_stabilize_locks` 留着：它把旧的位置路径换成稳定路径，记录与理由检查也要用。
- **理由（`changes.revisions [{decision, reason}]`，另有可选的 `changes.why`）。**
  - 检查放在 `Versions._changelog`，在 propose 预演、deep_check、commit 三处跑。
  - 判法：对基础版本里每条非 must 的用户决定，用比较器同一套（`lock_changes`，值与快照取自基础计划，不取决定里存的值）看这次计划改了它护着的哪些项。决定存的值旧了不会误报。
  - 改到的项若这次有用户自己的记录护着（准入或记下的用户决定、实况、已确认的更正），算用户改的。否则要有点名这条决定的 revision。
  - 没有就拒：`not recorded (reason): the plan changes what the user decided in <D> at <路径> (<项>) and gives no reason; fix: add changes.revisions {decision: "<D>", reason: "<why>"} …`。拒绝走 B3i 的通道：草稿 rejected，notice 以 “Refused, nothing will be recorded” 开头，commit 拒。
  - revision 点名不存在的决定或 must，propose 直接报错：must 只能由用户的新决定、实况或确认更正改。
  - 提交成功时，被改的决定照旧现行，账本记一条 `revise_decision` 流水（版本号、理由、改了哪些字段），`explain(<D>)` 看得到。
- **变更记录（`changelog`）。**
  - 形状：`locks.plan_changes` 按对象逐字段列，每行 `{target, field, change, old, new}`，`change` 为 changed、added 或 removed。另加 `{by, basis, why}`，agent 修改用户决定时再加 `revises`。
  - 对象名与实况的 target 相同：`trip:<id>`、`person:`、`place:`、`booking:`、`fare:`、`day:<日期>`、`anchor:`、`block:`。块的位置是 `block:<id>` 的 `position`（日期、方案、方案次序、轨道的人）。
  - 归属：用户自己的记录护着的行是 `by: user`。basis 是这条消息加上决定、实况或更正号（更正带证据号）。why 依次取：决定的 reason，没有就用“问题: 选择”；实况是“the user said: <原话>”；更正是“the user confirmed: <依据>”。其余行是 `by: agent`，basis 是这次回答的消息加上对象的证据号，why 取 revision 的理由或 `changes.why`。
  - 首次计划只记一行（trip added）。手改的计划（adopt）每行都是 `by: user`，basis `{edit: external, author}`。只记账本的提案没有计划改动，记录为空。
  - 在哪里：propose 回执与草稿带 `changelog`，commit 的版本带同一份；`state(view='history')` 按版本列出；`explain` 的 `version_usage[].changelog` 也有。
- **实况（已买、已订的票，硬节点）。** `paid-booking` 与 `preserved-anchor` 两条检查照旧拒任何改动，只放过这次已准入的实况或已确认的更正点名的那几个字段（`versions.sourced`，fare 按 `fare:<id>` 点名）。
  - 修前：已买的票连用户的实况也改不了。探针：先报“买了”，再报“换到 B 窗口”，结果是 `paid-booking:hall-entry` 冲突。
  - `Ledger._correction` 也接受指向这类事实的更正（`Ledger._fact`）。以前只能指用户锁。
  - 删掉一张已买的票、把硬节点挪到别的日子，仍然拒，因为没有字段级的实况能说这件事。
- **删减（删除测试）。** 逐条看了只为全量锁存在的东西，代码一条没删，都改成只管 must：
  - 准入、先后、只用一次、守恒、完整覆盖、余锁、三条改法：must 还要它们，删掉会让 agent 能挪掉必去的地方。
  - 旧快照的重绑与“旧形状失败在关闭一侧”：新建的 must 都是现行形状，这条路径现在走不到。但 `field_value` 的默认值处理以后模型加字段时还要用，判不准，留着。
  - 测试：20 个文件里构造用户决定的地方加 `must=True`，因为它们测的就是锁的机理，现在只对 must 成立。另有两处期望改了：更正无处可指时的报错文字，以及回执行多了 `must`、`paths`。没有删测试。
- **测试。**
  - `tests/core/test_b3k_record.py`（19 项）：
    - 非 must 带理由改成功，留记录与流水；不带理由被拒，fix 写明要补什么；经引用到达的票的渠道也算；没人定过的改动不要理由，带 `why`；revision 点名错的报错。
    - must：agent 挪掉或改都拒，给 revision 也拒；用户本轮新决定改 must 成功，不留余锁；整天重定放掉 must 不留 `/timezone`；引用更早的用户消息放掉 must 被准入拒。
    - 旧数据（删掉 `must` 键）不锁；手改非 must 的部分照收，记成用户的。
    - 已买的票改渠道：agent 被拒，用户实况成功；实况只点名一个字段时，另一个仍拒；确认更正成功。硬节点同理。
    - 首次计划与用户记录的归属；历史与 explain 按版本列；只记账本无记录；`plan_changes` 的命名。
  - `tests/core/test_b3k_mcp.py`：stdio、HTTP 各一轮，10 个判定。
  - 写域外一处：`tests/repo/test_manual.py` 的锁咖啡馆加 `must=True`（测的是手改锁住的块不入版）。
- **变异（仓外副本，五个测试文件 104 项；`agents/b3k_record_first_opus_20261101-2030/mutations-b3k.json`）。**
  - 不看 must 9 红，不要理由 6 红，实况不认来源 3 红。
  - 碰到 must 不过准入 1 红，放掉 must 仍要锁护 2 红，放掉 must 仍留余锁 1 红。
  - 第一轮后两项没红，因为样本分不开好坏。改了样本之后，还找出整天放掉 must 时守恒仍要时区的问题，已修。
- **限制。**
  - 理由按决定给，不按字段给。
  - 判“改了用户定的”包括经引用到达的票、地点和时区，所以改被选中的块用到的票也要理由。
  - 删掉已买的票、挪硬节点的日子仍拒。
  - 变更记录在大改动时行数多，没有合并。

## A8：交通事实由服务端写清、写准、写明限制（2026-11-01，D-57、D-60）

依据：D-57；用户 11-01 20:53 的补充（D-60：各地区通用、开源数据源为先，实时这次不做）；21:12 的补充（D-61，取代 D-60 第二条：时刻表查得到的直接写准，不在每句后加“请人工看一眼”；拿不到的实时晚点、站台、临时停运另给一个字段写去哪查、怎么查，回答层放到末尾）；调研 `agents/research_transit_sources_20261101-2025/` 第三、四、五节。第四次质量判的交通错都在我们这一层，所以只改服务端，数据源照用 Transitous。

- **否定与全称句要有撑（`render/check_answer.py`）。**
  - `only_inside_window` 逐句读（句号、分号分开），词表扩到 末班、首班、头班、只剩、没有更晚、没有火车/车/班次、最晚、别指望。最晚、只剩、别指望只在说出行的句子里算（“最晚 22:00 前吃完”不算），而且这一问有查询才核：一次查询都没有时，checked_has_evidence 与 unreachable_needs_lookup 已经要人去查，不重复报（旧测试的期望也是这样）。
  - 判法：句里的时刻不超过某次查询的窗口（窗口按出门时刻记，查到的车按上车时刻也算）。说“X 之后没有”时，查到的 X 之后的班次（到句里写的界为止，“21:30 前”“到 04:22”）都要写在句里，否则报“工具查到 X 之后还有 Z”。没写界的“末班 X”还要查到过 X 之后的班次，并写出下一班（“末班 00:53，下一班 04:22”）。首班要查到前一班并写出来；“X 之前没有”要从现在查起。
  - latest_departure 查到赶不上目标为止（`covers_target`）时：“最晚 X”只要 X 是它给的最晚时刻；“最后一班”“之后没有”要点到它最后一班赶得上的车，句里的时刻都要是这次查询的。没写时刻的“没有更晚的走法”只有这种查询能撑。
  - 新检查 `direct_needs_one_ride`：说 直达、不用换车 的句子，按出门、上车时刻（或最后一程的时刻，或没写时刻时按首程线路）对上的那条连接要只有一程；对上了却要换车，fix 写出每一程；对不上就报。
  - fix 都是能照抄的句子（“查到 HH:MM 为止；之后的没查”）。`fallback_later` 只在“误了”所在的那句里找后备（多句写在一行时会误报）。
  - 证据：`evidence_found` 多给 `lookups`（每次查询的连接，分钟数），`latest_departure` 的证据多存 `departures`（查到的全部连接，精简）。旧证据没有 `departures` 时用它显示的那几条。
- **交通句由服务端写好（`providers/connections.py` 的 `sentence`，`query.py` 的 `latest_sentences`）。** transit 与 latest_departure 的结果多一个 `sentences`，宿主照抄：
  - 每程照序写（时刻、线路、起止站、到站时刻）、换几次车或不用换车、到达时刻、“按时刻表，不是实时”（有实时的段写“X 是实时，其余按时刻表”）。句子里不加“请人工看一眼”（D-61）。
  - 时刻表给不了的另放 `how_to_check`（transit 与 latest_departure 都有，紧凑版留 what、text、link）：`what` 列拿不到的（`real_time`：有一程不是实时；`platform`：有一程没站台；`disruption`：总有；超出有效期时是 `not_yet`），`app`、`search`、`clock`、`steps`（regions.yaml 的操作说明）、`link`，`text` 是能照抄进回答末尾一小节的一段（例：“实时晚点、站台和临时停运时刻表里没有，出发前在 ÖBB App 查。搜 Wien Leibenfrostgasse → Wien Stephansplatz，出发时间填 22:00。起点栏填起点站……结果里看实时时刻、站台和停运通知。”）。App 按查询碰到的数据清单选 regions.yaml 的行：at→ÖBB App、de→DB Navigator、ch→SBB Mobile，其余 Google 地图。搜什么：所取连接的首程上车站 → 末程下车站；没有连接时用行程地点的当地名或上游查到的站名，两端有一端说不出名字（坐标）就不给。
  - latest_departure：主选（查到底才写“最晚”，否则写“可以坐”）；误了它之后还有几班、最早一班，或“之后没有赶得上的”（查到底时），或“没查到”；查到底时写“赶得上 HH:MM 的最后一班是 X；再下一班 Y 开、Z 到，过了 HH:MM”，没查到底写“查到 HH:MM 为止；之后的没查”。
  - 句子前面先写“起点按 <站> 查（你写的是 …）”和覆盖限制。测试 `test_the_servers_sentences_pass_check_answer` 核服务端自己的句子过 check_answer（分行写、写在一行都核）。
- **摘要逐程列出。** “13A Wien Leibenfrostgasse → Wien Stephansplatz”出自 `query.ride_text`：它只写首程的线路，接着写首程上车站和末程下车站。transit 的 `summary`（命令行 stderr 打印的就是它）与 latest 的 `summary` 都经过它。现在它逐程写，用“，换”连接。证据：`~/clones/eval_runs_20261031/answer_v2f_fz2_off_r1/turn2.stream.jsonl` 第 81 行调用 `tripwright query transit`，第 83 行是返回的摘要（判词写的 `off_r1` 是这个目录）。`connections.summary` 本来就逐程写，不是它。
- **覆盖与有效期（`providers/coverage.py`、`transitous_coverage.json`）。**
  - 数据从 Transitous 全部 143 个数据清单生成（commit 9ba2f2a，11-01 读），不只奥地利。生成的部分：每个清单的数据集数、被跳过的数据集（237 个，带原因）、按规则算出的有效期。手写的部分：有效期规则 `periods`（现在只有奥地利交通联盟的数据：名字带年份、`x-mvo-id`、年度到 12 月第二个星期六）和轮廓 `areas`（奥地利粗轮廓，给坐标找清单；克恩顿州轮廓，对应被跳过的本地巴士数据集）。
  - 站按 Transitous 站号前缀归到数据集，各地区一个判法。法国等清单名字里的年份是季节名，不当有效期用。
  - 查询日期超出有效期：窗口文字与 `reason` 改成“2026-12-13 起换新时刻表，这段日期的新时刻表还没进来，现在查不到；不能说没车”，状态 unverified。空结果的通用文字也改成“不能说没车”。
  - 端点在克恩顿州轮廓里：句子写“这一带的本地巴士不在数据里，下面只有火车和步行；以当地运营方网站为准”。
  - 刷新：`python -m tripwright.providers.coverage refresh <checkout>/feeds --commit SHA`。新年度进来、克恩顿恢复，只改数据文件，不改代码（有测试）。
- **地名解析（`transitous.ranked`）。** 纯文字地名不取第一个匹配：排序依次看站名含打字的词多少、在不在打字点名的地方（站的 areas）、是不是火车站或枢纽（rail 模式，或名字含 Bahnhof、Hbf、Busterminal 等），最后看上游次序。`resolve` 记 `how`（train station、hub、first stop match…）、`said`、`alternatives`；plan 从回应自己的 `from`/`to` 记实际查的站（`Sourced.meta`），结果的 `searched` 列出，句子写“起点按 Bad Ischl Bahnhof 查（你写的是 Bad Ischl）”。用 11-01 的真回应核：Bad Ischl→Bahnhof、Hallstatt→Lahn (Busterminal)、Hallstatt Bahnhof→Bahnhst，Wien Hbf、Graz Hbf 不变。
- **车头方向。** `normalize` 丢掉不含字母的 headsign（Postbus 541、546 的 “0”）。
- **Transitous 条款。** 单用户每 24 小时 `/plan`、`/one-to-many`、`/one-to-all` 合计最多 720 次（`RollingBudget`；自建 HTTP 客户端的实例，即真网络，记在 `~/.cache/tripwright/transitous-routing.log`，让命令行各进程共用；可用 `TRIPWRIGHT_TRANSITOUS_USAGE`、`TRIPWRIGHT_TRANSITOUS_ROUTING_PER_DAY` 改）。User-Agent 默认 `tripwright/<版本> (+https://github.com/zlxdengtianhei/tripwright)`，所有 HTTP 提供方与网页核查同用。署名链接 https://transitous.org/sources/，加 OpenStreetMap。
- **接口增量（都是加字段，不改旧字段）。** `Sourced.meta`；`Place.modes`、`Place.areas`；transit 结果的 `sentences`、`searched`、`coverage`、`how_to_check`、空且超期时的 `reason`；latest 结果的 `sentences`、`coverage`、`searched`、`how_to_check`（紧凑版有 `sentences`、`coverage`、精简的 `how_to_check`，A6 那条街道样例 9,786 字节，上限 10,000）；证据的 `coverage`、`sentences`，latest 证据 `value.departures`；check_answer 的新检查名 `direct_needs_one_ride`。
- **测试与夹具。** `tests/providers/test_a8_transitous.py`、`tests/core/test_a8_transit_facts.py`，每条有坏样本与合法对照。夹具 `tests/providers/fixtures/transitous/` 是 11-01 调研的真回应（精简，只含公开站点）。放在 `tests/providers/` 下，因为 manifest 里 `tests/fixtures/` 只有 `contract_sample`。
- **没做的。**
  - 实时（Wiener Linien、VAO）：照 D-60 由设计 v0.3 做成可选，做计划时问。
  - 不接 Google。
  - 存的计划事实带来源与查询时刻、复用前重查：在账本与计划层，B3k 正在改那几个文件，交主会话。
  - 第一段不核否定句：第一段在查询之前写，钩子要轻（D-47）。
  - 除克恩顿外，被跳过的数据集没有轮廓，所以别的地区缺数据时只能说“没查到、不能说没车”，说不出“这一带缺本地巴士”。
  - 有效期规则只有奥地利一条。

## v2g

- **交通与事实边界。** 回答层照抄 A8 的 `sentences`，保留每程、窗口、覆盖与实时范围；不把首程线路接到末程终点，不把空查询或空后备写成没有车。第一段在查询前发，不沿用存的“只剩、末班”。稳定事实由 agent 查好；拿不到的事才把 `how_to_check.text`、`link` 放在末尾“怎么查、怎么问”。已有时刻表／实时说明就不重复，没有才补一次。
- **末尾求助。** 第二段（单段快问则该段）最末留“怎么查、怎么问”：App、搜什么、步骤、链接；要问人时，最后只给一句起草邀请，写英语与当地语言，用户要了再起草。来源、网站链接与改动说明都在此节之前。
- **记录为主。** 只有用户明说的必须项才标 `must: true`；排计划、排天、选地点先问，工具给了问题就照问。agent 改非必须用户决定写 `changes.revisions` 理由。检查后、定稿第二段前提交，读接受后的 `changelog` 写改了什么与为什么；有 `revises` 的 agent 改动写“你之前定的 X，我改成了 Y，因为 Z；要改回就说一声”。`not recorded (reason)` 补理由或问用户，`locked:` 与授权 `used` 不改说法重试，拒绝时明确“这次没改成”。
- **源与生成。** 新回答与写法页放在 `agents/roles/tripwright-main.md`，示例移到生成的 `references/examples.md`；MCP prompt 与 L0 带入这些页的正文，不靠宿主打开引用页。装配器保留公共 fragment API，但主角色不再嵌入 v2f 公共片段，`writing.md` 改由角色源生成，免得旧的“不列逐段”和“空后备即没有后备”随构建回来。需主会话同步 CONTRACTS 第 4 节原先的片段来源说明。主 SKILL 20,595 → 12,238 字节。
- **其他角色与宿主。** researcher 交回服务端原句、覆盖与单独的查询引导；verifier 分开核必须项、可调整决定和回执理由。各宿主生成件与服务端 instructions 同步含核心规则，Claude Code 钩子不参与正确性前提。MCP prompts 可用不代表聊天宿主会自动请求；建议 M1 的工具 `next` 重复给必须项问题、末尾引导与回执解释，询问起草也需可见数据。无终端宿主的第二段时钟刷新仍需服务端返回时钟，真实表现交主会话部署后重放。
- **评分。** `scripts/answer_checks.py` 从完整 evidence 或紧凑连接构造 `evidence_found` 的输入，把 `lookups` 传到 `window_problems`，并加 `direct_problems`；跨轮沿用已有的证据累计方式。缺少每程的历史摘要只保留窗口检查，不伪造为查到了零班车。新增 11 个参数化用例覆盖遗漏更晚班次、两程误说直达、单程对照、完整／紧凑 transit 与 latest、跨轮连接。
- **核验范围。** `tests/agents` 作者自验 151 passed；`python -m tripwright.agents build --check` 通过。全量一次为 1312 passed、2 failed：一个是远程块缺清单归属，另一个是评分脚本相关旧测试漏列新检查名；后者补断言集合后，回答层与该项合跑 152 passed，未重复全量。远程清单失败留主会话／R1，详细原始输出在 worker 的 `REPLY.md` 及同目录 `pytest-*.txt`。真实宿主重放、用户场景与非作者核验由主会话部署后做，本 worker 不跑宿主重放、不提交。

## R1：远程 OAuth、租户镜像与 Git 写回（2026-11-01）

依据 D-58、D-61 七、D-63 二与三；一天版用现成 OAuth 代理，GitHub App 仅用于登录与最少点按的应用登记，写仓仍用部署钥匙。

- **协议与策略。** 用 `fastmcp-slim[server]` 4.1.4 的 OAuthProxy 与 GitHubTokenVerifier，不替换 MCPServer、七工具或业务服务，不自建授权服务器。上游负责发现、CIMD、动态注册、PKCE S256、客户端同意页与浏览器绑定、JWT 受众、刷新轮换。`remote/auth.py` 只补账号名单，发下游令牌前与每次工具鉴权均检查 GitHub 登录名；GitHub 上游令牌不能直接访问 MCP。选型证据、候选与回头条件见 R1 worker 的 REPLY。
- **配置即租户边界。** `remote/config.py` 的 accounts 把 GitHub 登录名映射到租户，tenants 决定仓、分支、索引 owner、SSH 钥匙和已核主机钥匙文件，token_tenants 把旧请求头令牌接入同一租户。目录键只接受安全文件名，密钥只从进程环境或 600 专用 env 读取；专用 env 不得放在租户镜像中。
- **一个租户一个写者。** `remote/tenants.py` 惰性克隆到 `data_dir/tenants/<id>/mirror/`，复用 Repository、Store、Tripwright、Committer。origin 或当前分支与配置不符时拒绝打开；SSH 使用独立钥匙与 StrictHostKeyChecking。`remote/app.py` 按认证身份选择服务，不接受调用方指定 owner／租户。打开操作和 MCP 生命周期由每租户的常驻任务持有：HTTP 请求取消不会启动第二个写者，AnyIO task group 在同一任务启动和关闭。请求边界设置现有 request_user；独立目录、库与服务对象的两租户样例已验，公共多用户运营留以后。
- **写回接缝。** 不改现有 committer、outbox、replay 或 service；只在租户镜像配置提交者 tripwright[bot] 与 SSH 命令。沿用普通 push、推送被拒后按操作重放。裸仓测试实际捕获 rejected → ok，核对用户消息的操作 trailer 在正本各只出现一次。
- **用户建应用，服务器收凭据。** `remote/setup.py` 用原有令牌换十分钟设置链接，用户在自己的 GitHub 账号确认创建 App。manifest 不申请仓权限、不启用 webhook。回调核一次性 state、同一浏览器 cookie、App owner 对应租户，直接换回凭据并原子写入 600 env，响应只含完成状态；下一请求自动启用 OAuth。私钥与 webhook 密钥保留在服务器 env，当前不用来写仓。部署钥匙公钥由主会话经 gh 添加，命令见 deploy/REMOTE_AUTH.md。
- **接线。** serve --http 的 --remote-config 或 TRIPWRIGHT_REMOTE_CONFIG 启用新路径；未启用时原单库／repo HTTP 与 stdio 保留。HTTP 工厂新增组合鉴权接缝；远程模式不挂旧结果页，避免绕过新边界。公开 Host／Origin 显式交 SDK 校验。Caddy 现有全路径反代覆盖发现、授权、回调及设置路由；健康程序接受带资源元数据的 Bearer challenge，镜像加入 openssh-client。配置、密钥位置与用户步骤见 deploy/REMOTE_AUTH.md。
- **证据边界。** tests/remote 的 8 项作者自验通过，覆盖假的 GitHub HTTP 上游＋真实代理＋官方 MCP SDK、令牌路、错误账号／PKCE／回调、刷新、加密状态重建、manifest、Git 推送与重放、两租户隔离和首请求取消。全量命令、原始输出及缺口在 R1 worker 的 REPLY 与 pytest-full.txt。本轮不创建真实应用、不加真实仓钥匙、不重新部署；真实 GitHub、Claude／手机与非作者核验由主会话安排。

## M1：每一问把该考虑的记忆摆到 agent 面前（2026-11-02，D-61 六、D-63 四；m1_memory_consider_opus_20261101-2136）

依据：用户 11-01 21:22 的话（Q20）：偏好、决定、记忆这些文件非常关键，要有机制让 agent 在用户每次提要求后先想清楚哪些该用，最好由 MCP 这边引导；记忆分会变的（想去的地方）和定了基本不变的（目的地定了，数据源就固定）。21:12（Q18）：每趟可配置的数据源，每个相关节点都读取并用上。都在服务端做，靠工具结果，不靠宿主的钩子或 skill。

- **`turn` 的新字段。** `consider {entries[], more, more_how?}`、`ask[]`、`offer`、`next`。
  - `entries` 每条 `{id, kind, text, stability, from, must?, status?, …}`。种类：rule、decision、must_go、destination、person、long_term、preference、want_to_go、source、app、ask_languages、note、stability。`from` 是账本记录 `{record, event, basis, tier: trip|long_term}`、计划路径 `{plan: "/people/0"}` 或产品表 `{table: "regions.yaml", row}`。`status` 为 pending（长期条目待确认）或 inferred（agent 推断，或地区表里没核的行）。
  - 账本条目取自这一问的约束包（packs.py 已按人、日期、时段、地点收窄），不另起一套取用；计划给人员、目的地、标了必去或推荐的地点；`consider.sources` 给来源与须知。
  - 列多少按场景（`consider.yaml` 的 scenarios）：quick 6 条，只列偏好、规矩、决定、想去的、App、询问语言、须知，不列人员和目的地；plan 16 条，全列；没有计划时（从零计划）不限。必须项在前。超出的数进 `more`，`more_how` 写“另有 N 条没列，state(view='memory') 可看”。`state(view='memory')` 多出 `consider`（全部）与 `sources`。
  - `next`：先逐条看、说用到哪条、权衡掉哪条；ask 里的照问；这条消息替换了常变的条目时，写“回答里说一句这次改了什么：旧 → 新”。
- **两类改动频率（数据，不写死）。** `src/tripwright/consider.yaml` 的 kinds：stable 有目的地（计划 `/areas`）、人员（`/people`，按编号与名字比）、数据源、App、询问语言、改动频率本身、长期偏好（`long_term` 或从 profile 导入）；changing 有想去的地方、偏好、规矩、决定、必须去的回答、须知。用户可改：一条 setting `stability:<种类>`，value stable 或 changing，这一趟的盖过长期的。
  - 账本条目：新条目要取代或质疑一条 stable 的现行条目（同 key 或声明同一对象、范围与人重叠、值或文字不同）时，要 `basis: user_explicit` 并过 B3k 那一道准入（`Ledger.admit`：本轮消息、用户自己的话、说话人是这趟的人、引文在消息里）。`turn` 里不过的条目不记，进 `recorded.not_recorded`（part items，带 `ask`：先问用户），其余照记；propose 里直接报错。实现：`Ledger.stable_refusal`。
  - 计划字段：候选改了 `/areas` 或 `/people`，要同一提案里有一条用户决定的路径盖住它，并从本轮消息过准入；否则草稿 rejected，`lock_changes.stable` 一行，notice 与 commit 写 `not admitted (stable): the plan changes /areas (destination, set once): …; fix: …`。候选同时改了 must 锁护着的东西时，让锁的拒绝先出（它更准），本检查不报。实现：`Ledger._stable_plan`。
  - 常变的照 D-53：可改，B3k 变更记录照旧；`turn` 的 `recorded.replaced [{id, replaces, kind, stability, old, new}]` 列这条消息替换了什么。
- **来源与须知。** 账本条目种类 `setting`（`Item.kind` 新值）：`source:<id>`（value `{on, which}`）、`app:timetable`（value 是 regions.yaml 的工具名，`scope.place` 是地区行）、`ask_languages`（语言代号）、`note:<slug>`、`must_go`（none 或 named）、`stability:<种类>`。`long_term: true` 照 D-32 进 profile、下一趟导入并问“这趟还用吗”。setting 不进约束包、不进 `compile` 的 items、不参与可能矛盾的提示，所以不要 usages 处置。
  - 合并：这一趟 > 长期 > 产品地区表。地区表 `render/regions.yaml` 每行加 `match`（行程地区里出现哪些词算这一行）、`languages`（当地语言）、`realtime`（可选实时来源）、`notes`（须知，标 verified 或 inferred），顶上加 `sources.transitous`（每趟默认开）。询问语言总带英语。
  - 读它的地方：`consider`；`query` 的 transit 与 latest_departure（`how_to_check` 按这一趟的 App 选择写，经 `regions.choose` 的 reports）；`ask` 的实时问句举的例子；`offer` 的语言。
- **该问的问（`ask[]`，每条 `{id, say, default, record, scope?}`）。** must_go：场景是 plan、replan、compare、requirements，或还没有计划，且这一范围（整趟或问的那几天）没有回答（must_go 条目的范围盖住它，或范围里有 must 决定、must 的目标）。realtime：有计划（目的地定了），场景同上，这一趟没有任何 `source:` 条目。长期条目待确认也并进来（id memory），原来的 `memory` 字段照留。
  - 沉默：下一条消息没答（没记 must_go 或 source 条目），服务端按默认算（没提就当没有；不配），写进这一轮 `recorded.defaulted`，并存在这一轮的 context（`settled`）里，以后不再问。不写账本：第一版写成账本条目时，全量 57 红，因为每个计划问之后的下一问都多出条目、仓里多出文件，而宿主可能根本没把问题发出去。代价：库重建后会再问一次。
- **起草询问只给一句（`offer`）。** `turn` 带 `offer {say, languages, spec}`：`say` 例“我可以帮你起草询问内容（英语、德语）。”，spec 写必含英语与列出的当地语言、每种不超过 4 句、地名车次时刻照工具原文、不编订单号价格时刻、只在对话里给不记录。transit、latest_departure（含紧凑版）、open_places 的结果带 `offer {say, languages}`（按查询碰到的地区）。不做服务端代写。
- **“锁定”字样（B3k 交接）。** 包的决定条目带 `must`；`details` 只把 must 决定的块列进去；渲染只给 must 写“锁定 {路径}”，其余写“涉及 {路径}（可调整）”，决定页写“用户定（可调整）：路径 = 值”。没有 `must` 字段的记录（B3k 之前写的包，例如 contract 样本）照旧写“锁定”，所以 contract 样本字节不变；样本该补 `must`，交主会话。
- **测试。** `tests/core/test_m1_consider.py`（17 项，每条坏样本与合法对照）：快问不列人员目的地、计划列全；超限截断并指向 state(view='memory')；must 在前；setting 不进包；改目的地没有本轮用户决定被拒、有就过；用早先消息的决定加人被拒；常变的愿望被 agent 替换成功并在 next 里说出、长期偏好被推断替换被拒、用户本轮说了就过；用户把想去的地方改成 stable 后替换被拒；合并次序三层；每个地区行都给 App、英语与当地语言；how_to_check 用这一趟的 App；must_go 问、沉默后不再问、整趟的回答盖住单日、单日的回答不盖整趟；实时问有目的地才问、答了不问；offer 的语言；只有 must 写锁定。`tests/core/test_m1_mcp.py`：真 MCP stdio 两次 turn，看 consider、ask、offer、next、defaulted。
  - 改了的旧测试一处：`tests/core/test_b3d_lock_aliases.py` 的夹具加一条用户 `stability:person = changing`，因为它的合法步里有“加一个同名的人”，M1 之后人员定了基本不变。
- **限制。** 地区只按行程地区里的词匹配，地区表没有的国家只有英语、没有须知；沉默的默认不进仓（见上）；人员只比编号与名字；`consider` 里的计划地点只列标了 must 或 recommended 的；`repo/memory.entry_kind` 把长期 setting 列成“偏好”（不在写域，交主会话）；回答层怎样用这些字段在 v2g 那一块。

## A9：“最晚、末班、还有几班”由服务端连续查清；存档的否定句不进回答（2026-11-02，D-64）

依据：D-64（第 5 次质量判 0/3，错都在“最晚几点去机场”：查询窗口的空档写成“别指望”“还有三班”，存档的“最晚 21:50”“只剩 23:53 末班”进了第一段，check_answer 没拦）；D-57 已定的“存的否定句不沿用”“否定与全称句要有窗口覆盖到的查询”补做全。只改服务端与检查，回答层照抄（要改的句子在 worker 的 REPLY）。

- **连续覆盖的查法（transit 的 `until`，`query.py`）。** `query(kind="transit", arguments={origin, destination, when, until})`：从 `when` 起一页页接（下一页从上一页最后一班之后一分钟起，沿用 A5 的翻页与去重），直到查到的班次过了 `until`、一页没有新班（数据到当天末尾）或额度到限。`until` 是 ISO 带偏移或 HH:MM（比 `when` 早的 HH:MM 算次日）；和 `arrive_by`、`action` 同用报错。守 Transitous 额度：每页一次 /plan，走 A8 的 RollingBudget（720/24h）；翻页中途额度到限（ROUTING_BUDGET_EXCEEDED）不整单报错：返回已查到的，window 写明“额度到限”与查到哪。结果：`window {from, to, by, count, text, continuous, covered, until, pages, limited?}`——continuous 表示 from 到 to 之间没有空档，covered 表示查到了 `until`；`connections` 全部列出（不再只 5 条），`sentences` 每班一句（沿用 A8 写法）加窗口句与总结句；`summary` 是那句总结：“Wien Hauptbahnhof → Flughafen Wien Bahnhof：21:13 IC 747、…、00:53 REX 22787；再下一班 01:25 FlixBus N877 开；从 21:00 到 01:00 连续查全（3 次查询，每页接着上一页查），24 班；01:00 之后出发的没查”（按上车时刻排列，只列到 until，之后的头一班另写）。
- **latest_departure 分清两种输入。** `by` 写明是“到达终点的期限”；新参数 `board_at` 是“在 destination 站上车的时刻”（target 照它算，deadline 记 `board`）。返回 `target.meaning` 写明 `arrival_deadline`/`boarding_time`。传错时（`by` 没有任何班次赶得上、却与查到的某程上车时刻差不到 2 分钟）报错并给改法：要赶那班用 `board_at`（destination 写成那个站），要到达期限给真正到终点的时刻。
- **存档的否定句（`packs.py`、`service.py`、`render/labels.py`、`render/screens.py`）。** 词表 `ARCHIVE_WORDS`（末班、最后一班、只剩、没有更晚、来不及、别指望）放 labels；`claim_parts`/`split_claims` 按 。；与全角括号切句。`packs.archived_claims` 把硬节点 text/consequence 里的这类句子改成“计划里写过“…”（存档，写于 MM-DD HH:MM，没核）”（写于 = stored_since 的提交时刻，没有就用最近核的时刻），并给结构化的 `archived_claims [{field, claim, written_at, source, superseded}]`；本轮（6 小时内）transit/latest 证据里有更晚、同方向（终站名含锚点地点名）的车次就标 `superseded` 并写“这轮查到更晚的 HH:MM，这条作废”。turn 的 hard_nodes 与 pack 都过它；screens 的仓内视图（hard_line、日视图的“误了”）标“（存档，没核）”（无时刻信息）。turn 的 `next` 在有存档句时加一句：第一段不照用，要说先查（transit 加 until 或 latest_departure）。
- **check_answer 拦这类句子（`render/check_answer.py` 的 `coverage_problems`）。** 两个新检查名：`denied_needs_coverage`（别指望、别靠、都误了）与 `count_needs_continuous`（还有 N 班）。判法：句中（子句级）的时刻要落在本问某次查询的 [from, to] 里——落在窗口外或两次查询的空档就报错，fix 写“查到 HH:MM 为止”或“先用连续查法（transit 的 until）从起点连续查到 HH:MM”；“别指望 X”而 X 是工具查到的班次，报“工具查到这班还在开”。计数句：从“误了 HH:MM”（或句中最早时刻）到最后一个被数时刻要链式连续覆盖（相邻窗口端点差 2 分钟内算连续；查到底的 latest_departure 窗口端点延伸到它的目标），有空档报出空档位置；连续后工具查到而句里没写的班次报出来（行内别的子句写到的算写过），数量与“还有 N 班”不符也报。“赶得上 HH:MM”的时刻算到达界，只数到得了它的。“这次没查到/没查/不能说”不再豁免结论句；“存档、网上说法、搜索摘要、没核”仍豁免（标好的存档句照抄不拦）。lookup 结构加 `end`（窗口的 to）。
- **“按时刻表，不是实时”一问一次。** `connections.sentence` 只在有实时程时带 basis（“（实时）”“（X 是实时，其余按时刻表）”）；纯时刻表不再每条都带。transit 与 latest_departure（含紧凑版）的结果加 `note`（“以上交通时刻按时刻表，不是实时（交通那段说一次就够，不用每条都写）。”；全实时/混合另有写法），回答层在交通那段说一次。latest 的“之后没有赶得上的/就赶不上/最后一班”几句同理只在有实时程时带。
- **接口增量（都是加字段）。** arguments：`until`、`board_at`；transit 结果 window 的 `continuous/covered/until/pages/limited`、结果与证据的 `note`；latest 的 `target.meaning/deadline="board"`；hard_node 的 `archived_claims` 与改写后的 text/consequence；check_answer 的两个新检查名。check_answer 的 lookup 加 `end`。
- **测试。** `tests/core/test_a9_transit_window.py` 9 条，每条坏样本与合法对照：until 连续覆盖到 01:00（逐班、总结、“再下一班”、总结句自己过 check_answer）；额度到限即停并写明；until 与 arrive_by/action/latest 同用报错、ISO 早于 when 报错；判词原句“计划里存的 23:53 末班 REX 这次没查到，别靠它们”“误了 22:53 之后火车还有 23:53、00:23、00:53 三班”被拦（窗口外/空档），连续查全后的“…还有 23:23、23:53、00:23、00:53 四班（查到 01:00 为止）”通过，漏写 23:23 被拦，“别指望 23:53”在查到时被拦；board_at 的 meaning 与误用报错；存档标注、作废、next 提醒、存档句过 check_answer。A8 测试按“note 说一次”更新。真 Transitous 跑了一次“11-07 周六 21:00 起 Wien Hbf → Flughafen Wien 到 01:00”（10-31 是周六，查的是今天以后的同星期几）：3 次路由调用，24 条，连续覆盖到 01:00，再下一班 01:25；输出在 worker 的 REPLY。
- **没做的。** `scripts/answer_checks.py` 还没把两个新检查接进重放评分（scripts/ 不在写域，交主会话）；回答层（skills/、agents/）照抄 note 与 until 的说法、存档句不抄未标的部分，交主会话；`archived_claims` 的同方向判定按终站名含锚点地点名，锚点没写地点就不标作废（宁缺）；计数句的英文形式没做（check_answer 的词表本来就是中文为主）。

## A10：“最晚几点出发去 X”由服务端定框，第一段由服务端起草（2026-11-02，D-67）

依据：D-67。自重放 a9b（2d0334f4）两问都没有第一段（宿主只发一句英文状态句）；第二段把计划里一班火车在总站的上车时刻（锚点带 line 与 place）当成到机场的期限，首行“最晚 21:53 出门”框错；check_answer 报了“最后一班”仍发出，第二段发了两次。共同前提“宿主自己选‘最晚’的框、服务端只核句子”三次都错在选框（第 5 次判、A9 后自重放、a9b），换成服务端定框、服务端起草第一段。用户原话 Q8（两段、核过）、Q15／Q18（能查的查好、限制说清、不推给用户）、Q18／R-62（harness 中立）、Q21（达标）。

- **锚点的意思按字段定，一张表（`query.py` 的 `ANCHOR_MEANINGS`、`anchor_meaning`）。** 带 `line` 或 `destination`（车去哪）且有 `place` 的锚点是“在 place 上车的时刻”（boarding_time）；其余是“到 place 的时刻”（arrival_deadline，按 `deadline` external、plan、未标）。不按锚点 ID 判。`deadline_target` 用它定 `meaning`；boarding 锚点的目标是“在 anchor.time 前到 anchor.place”（`deadline="board"`，同 A9 的 `board_at`），查询的终点换成 `anchor.place`，`resolved.destination` 记 `said`（宿主写的）与 `boarding: true`，`target` 多 `board_place`、`line`、`toward`。句子写“赶 22:53 在 Wien Hauptbahnhof 开的 REX 7，最晚 …”（不再写“要 22:53 前到”，那句在 a9b 被读成到机场）；`board_at` 没有锚点时站名取终点。矛盾报错带改法（`anchor_conflict`）：锚点旁再给 `by` 或 `board_at`；boarding 锚点的 `destination` 既不是它的 place 也不是它去的地方。`by` 与 `board_at` 同给也报错。用到的地方：`deadline_target`、`node_after`（只取 arrival_deadline 当“之后要守的时间”）、`render/first_part.py`（boarding 锚点写成“计划里写过：… 坐 …（存档，没核）”）。
- **今晚最后一班（`latest_departure` 不给 anchor_id、by、board_at）。** 不新加 kind（MCP 的 kind 枚举在 adapters/mcp.py，不在写域）。`QueryRouter._last_connection`：从现在（最多今晚结束前 6 小时；有 `after` 用 `after`）连续查到今晚结束（`until`，默认次日 03:00 `SERVICE_END`；可给 `until`），沿用 A9 的 `_window` 分页，最多 8 页（`LAST_PAGES`，每页一次 /plan，守 720 额度；额度或页数到限写明查到哪）。返回：
  - `first_line`（第二段首行照抄）：查到今晚结束且末班后至少 2 小时才有下一班（`NIGHT_GAP`）时“今晚最后一班：最晚 HH:MM 出门，…；下一班 明早 HH:MM … 开”；夜里一直有车时“03:00 前最后开的是：…；03:00 之后也有车，下一班 …”（不说末班）；没查到今晚结束时“查到的班次里最后开的是：…”（不说最后、最晚）。
  - 末班要另买票（长途大巴）时加“不用另买票的最后一班：…”；总的末班不是火车时加“火车最后一班：…”（真 Transitous 11-07：Hbf → 机场 REX 到 00:53，机场大巴整夜有）。
  - `window`（A9 的形状，加 `covers_target`）、`last`、`last_without_extra_ticket`、`last_train`、`next_after`、`tonight_ends_after_last`。
  - `hard_node`：计划里到这个终点之后（36 小时内）最早的到达型硬节点，外部截止照 `external_after` 配上，再用 latest_departure（不比较路线，记自己的证据）算它从起点的最晚出发；句子“之后要守的时间：你计划明早 05:45 前：…；明早 07:05 …”和“明早直接从 X 去：…（今晚就到 Y 的话用不上）”。没有就 `null`，句子只说“计划里没有到 Y 之后要守的时间”，不编期限。
  - 证据记 `latest_departure`，`value.frame` 存框（kind、可写的时刻、目标时刻、末班时刻、服务端首行），`departures` 的 in_time 改为“今晚结束前开”。
- **`turn` 的 `first_part`（`render/first_part.py`）。** `{text, lines, asks_latest}`，只用存的数据：第一行“按现在 HH:MM 算：…”（钟取 turn 的 now）；问最晚、末班、来不来得及、赶紧（`LATEST` 词表）时第一行“真正最晚要查，第二段给。”，再列之后最多 3 个硬节点（不用“外部截止、计划目标”字样）与 consequence 里的存档句（“计划里写过“…”（存档，没核）”，作废的写作废）；末行“没核：…，第二段核完再发。”；最多 8 行，过 check_answer 的第一段检查。`next` 改为先写“先把 first_part.text 原样发给用户（只可改称呼和语言），再去查。”；问最晚时加“latest_departure 只给 origin 和 destination…第二段首行照抄它的 first_line”；都加“第二段过了 check_answer 才发，只发一次。”；存档那句改为“只照 first_part 的写法引用”。不靠钩子：MCP stdio 测试里没有 skill 的宿主也拿到 first_part 与 next。
- **check_answer 核首行的框（`frame_matches_server`）。** 第二段标题后的第一行里，关于出行的“最晚、末班、最后一班”结论（未标没核、存档等）所指的时刻，要是本问服务端框里的时刻：一条连接的出门、上车或各程开车时刻；“HH:MM 前”可指期限框的目标时刻。本问的框 = 最近一次带框查询前后 30 分钟内的（`FRAME_MINUTES`）；其中有今晚最后一班的框时只认它（期限框的时刻放首行算框错，报“这是赶某班车的时刻；这一问服务端定的框是今晚最后一班”）。fix 给服务端的 first_line。latest_lookup 的 `last` 对今晚最后一班取框里的末班时刻（含不用另买票的、火车的），所以服务端自己的句子过 `only_inside_window`。
- **演示数据标明。** synthetic 数据的 transit、latest_departure（两种）结果里每条 `sentences`、`summary`、`first_line` 开头加“（虚构演示数据，不能用于出行）”（`demo_marked`）。默认数据源不动。
- **回答层。** 主 role 与生成件：第一段照抄 `turn.first_part.text`；问最晚、末班用 latest_departure 只给起终点、第二段首行照抄 `first_line`；第二段通过才发、只发一次；server instructions、tools.md、writing.md、examples.md、researcher 同步。主 SKILL 12,161 字节（原 12,171）。promises 加 first-part-from-turn、last-connection-from-the-server、second-part-sent-once，example 的第一段承诺改为服务端草稿的首行。
- **紧凑版大小。** 期限型 latest_departure 的紧凑结果不另带 `first_line`（它就是 sentences 里第一条期限句；`detail="full"` 有），守住 A6 的 10 KB。
- **测试。** `tests/core/test_a10_frame.py` 13 条：规则表只看字段；a9b 的调用（boarding 锚点＋机场作终点）照上车算、对照到达型锚点；矛盾报错三种、对照合法终点；今晚最后一班＋硬节点＋自己的句子过 check_answer；a9b 首行（期限框的时刻）被 frame_matches_server 拦、对照服务端首行通过；没硬节点不编期限；末班是大巴时给不用另买票的；夜里一直有车时不说末班、给火车末班；额度到限不说末班；first_part 的六行、next、a9b 英文状态句不过第一段检查；非最晚问题的草稿；MCP stdio 拿到 first_part；演示数据标明。改了的旧测试：A5 的“exactly one”两例（没有期限现在是今晚最后一班）、A9 的 board_at 句首、agents 的示例与字节上限（12,236 → 12,171）。
- **限制。** 今晚结束按 03:00 一刀，跨夜整夜有车的城市只说“之后也有车”；“火车”按 RAIL 模式集合判（地铁不算）；硬节点只取终点同一地点（按 place 编号）；框的“本问”按 30 分钟批次近似（service.check_answer 不在写域，拿不到本问起点）；首行只核时刻，不核“到哪”的字面；第二段发两次没有服务端拦法（只在 next 与 skill 里写），Stop 钩子仍只 Claude Code 有。

## R2：别人部署的那一份不坏、不丢、坏了看得见（2026-11-02，D-66、D-68；健壮性审查第 1–5、7 条，Q23）

### 第一批（Docker 版能用）

- **真实数据是服务默认。** `serve --http` 与远程模式在启动路径里显式传 `live`（`cli.py`、`remote/tenants.py`），只有 `TRIPWRIGHT_PROVIDERS=synthetic` 才用虚构数据；`QueryRouter` 的默认值不动（命令行与测试仍按原样）。Dockerfile 写 `ENV TRIPWRIGHT_PROVIDERS=live`。
- **额度计数文件。** 远程模式默认放 `data_dir/transitous-routing.log`，Dockerfile 写 `TRIPWRIGHT_TRANSITOUS_USAGE=/data/transitous-routing.log`。`RollingBudget.take` 遇 `OSError` 抛 `ProviderError("USAGE_FILE")`，带路径和修法，查询回 unverified；不发这次请求（不计数的请求可能违反 Transitous 条款）。
- **写入卡住看得见。** `Git.push` 按 stderr 分 `denied/protected/rejected/unreachable/failed`（`failure_kind`：auth、protected、behind、network、other）；提交失败记 `commit`。原因存在运行库 `repo` 表的 `blocked` 记录（kind、since、hint、最后的错误、次数），推送或提交成功即清。写入回执在操作未生效时带 `blocked`，`state(view="sync")` 带 `blocked`；原因出现或变化时写一行日志，同一原因重试不再写。受保护分支不再当成“落后”无限重放。
- **残留锁。** 开租户时清 `.git/*.lock` 与 `.git/refs/**/*.lock`（D-68：一个数据卷只一个实例，进程是镜像唯一写者）。
- **启动就开租户。** `create_app` 的 lifespan 为每个租户在后台 `tenant_app`（开服务、起提交器与任务线程），再 `git ls-remote` 和 `git push --dry-run`（`TRIPWRIGHT_CHECK_PUSH=0` 可关）。dry-run 能查出只读钥匙，查不出受保护分支（规则只在真推时判），后者靠第一次推送的 `blocked`。
- **停机有总时限。** `Committer.stop(budget=15)`：等线程、提交等着的写入、剩余时间内推一次（推送时限取剩余时间）。Git 子进程放进独立进程组，超时杀整组（只杀 git 时 ssh 子进程攥着管道，等待会和 ssh 挂起一样长）。compose 设 `stop_grace_period: 40s`。实测容器停机 0.7 s，批处理窗口里刚应答的写入在停机时提交并推上远端。
- **健康与就绪。** `/healthz`：存活，200，正文列每个租户状态；`/readyz`：所有租户 ok 才 200，否则 503，每个租户给 `status/kind/hint/unsynced/oldest_unsynced_age_seconds`。不带路径和 Git 原文。单库 `serve --http` 也有 `/healthz`。Caddy 直接转发这两个路径。启动检查失败后，若之后有推送成功（任何类别）或拉取成功（只对 network），就绪自动恢复。
- **日志。** `serve --http` 设最小日志（tripwright 的 INFO，库的 WARNING，格式一行一事）；uvicorn 访问日志关掉（网址密钥在路径里）。写一行的事件：数据目录状态、租户打开失败（类别＋Git 原文末三行，只进日志）、远端检查失败、写入卡住与恢复、提交器错误（同一错误只写一次）、工具里未预期的异常（`adapters/mcp.py` 的 `expose_errors`：写一行，回宿主“Unexpected server error in <tool> (<类型>)；重试一次，再失败告诉用户找运维”，不再是 SDK 的裸“Error executing tool”）。
- **持久卷标记。** 启动时在数据目录写 `.tripwright-volume`（创建时刻、启动次数）。新的空目录记一行“只应在第一次启动出现”；有文件没标记记“已补标记”。单库模式在库旁边，仓库模式在 `.tripwright/`（被 Git 忽略）。
- **空闲开销。** `Store` 加写入代号（本对象每次写加一）和 SQLite `data_version`（别的连接提交），`Store.changes()` 读这两个；outbox 加 `json_extract(payload,'$.state')` 的表达式索引，`Outbox.open/find` 按字段查（`Store.where`），不再解码整张发件箱。提交器循环只在被叫醒、有写入或有到期的计时（批处理窗口、重推、轮询）时做一整步，其余每秒读一次 pragma；任务循环在上一次整步没留下排队或运行中的作业、且之后没人写过时直接跳过。实测：空闲 2.5 s 内两个循环对表的读取为 0（基线每 0.25 s 各解码一次整表）；容器空闲 69.6 MiB、CPU 0.22%（这时 45 秒拉取仍在，第二批按活动窗口停掉）。
- **没有 GitHub 也能用（Q23）。** `Tenant.repo_url` 可不写：服务在 `data_dir/tenants/<id>/mirror` 用 `workspace.init` 建本地仓作正本，照常提交、不推送，写入直接 effective；`state(view="sync")` 加 `storage: {kind: local, note}`，写明只在这个卷或这个目录、备份自己做。`public_url` 允许 `http://localhost`／`127.0.0.1`（同机桌面宿主不用隧道）；`oauth: false` 时 `accounts` 可空。
- **网址密钥登录。** 只在设了 `TRIPWRIGHT_URL_KEY`（身份 local）或 `TRIPWRIGHT_URL_KEYS`（JSON，密钥 → 身份）时开；身份要在 `token_tenants` 里，密钥至少 32 个字符、只含字母数字 `-` `_`。连接器地址 `<public_url>/mcp/<密钥>`；比较先哈希再对全部密钥做常数时间比较；错密钥回 404；启动日志提醒“网址就是钥匙”。请求头令牌照旧。
- **部署文件。** `compose.personal.yaml`（个人电脑：只绑 127.0.0.1、数据在 `deploy/data`、`.env` 可选）、`personal.example.yaml`（无 GitHub、网址密钥）、`.env.example`、`deploy/.gitignore`；`healthcheck.py` 改查 `/healthz`；README 重写成“先选部署方式 → 个人电脑 Docker → 有域名服务器 → 健康与日志 → 出了问题”，作者实例的运维记录降为附录。
- **测试。** 新增 `tests/providers/test_r2_usage_file.py`、`tests/repo/test_r2_blocked_writes.py`、`tests/remote/test_r2_startup.py`、`tests/remote/test_r2_tool_errors.py`，共 23 条；除合法对照（可写计数文件）外，放到基线代码上全部失败。

### 第二批（Railway；审查第 6、10 条）

- **OAuth 校验缓存。** `AllowedGitHub` 传 `cache_ttl_seconds=300`、`max_cache_size=1000`：同一令牌 5 分钟内不再问 GitHub；名单检查每次照做（缓存的是 GitHub 的身份结论，不是放行结论）。代价：在 GitHub 上撤销授权最多 5 分钟后生效。
- **部署文件不写死作者的实例。** Caddyfile 站点地址 `{$TRIPWRIGHT_DOMAIN}`，compose 用 `${TRIPWRIGHT_DOMAIN:?…}` 传给 Caddy，没设就拒绝启动；`check_https.py`、`setup_github_app.py` 用 `--url`、`--ssh`、`--env-file` 或 `TRIPWRIGHT_PUBLIC_URL`、`TRIPWRIGHT_SSH`、`TRIPWRIGHT_CHECK_TOKEN`；`remote.example.yaml` 用占位符，仓地址示例改 443 写法。作者实例的运维命令改用 `--env-file /opt/tripwright/compose.env`（源码目录外，更新不丢）。
- **环境变量配置**（`remote/envconfig.py`）。没有 `--remote-config` 而有 `TRIPWRIGHT_PUBLIC_URL`、`TRIPWRIGHT_REPO_URL` 或 `RAILWAY_PUBLIC_DOMAIN` 时，`serve --http` 从环境变量建单租户配置：公开地址缺省取 `https://$RAILWAY_PUBLIC_DOMAIN`；仓地址可不填（本地仓）；GitHub 地址的各种写法改成 `ssh://git@ssh.github.com:443/…`（`TRIPWRIGHT_GITHUB_PORT_22=1` 保留原样）；部署私钥写到 `data_dir/ssh/deploy_key`（600，网页表单里的字面 `\n` 还原成换行）；known_hosts 给了就写，GitHub 没给就从 `api.github.com/meta`（HTTPS 校验）取 `github.com` 与 `[ssh.github.com]:443` 两组；`TRIPWRIGHT_GITHUB_LOGINS` 有值才开 GitHub 登录。选代码直接读环境变量而不是入口脚本写 YAML：一处校验、测试能直接调。实测：用取到的 known_hosts 对 `ssh.github.com:443` 做一次只读握手，回 “Permission denied (publickey)”（主机钥匙通过，一次性钥匙被拒）；空 known_hosts 对照回 “Host key verification failed”。
- **`$PORT`。** `serve --port` 缺省读 `PORT`，再缺省 8000；Dockerfile 的 CMD 不再写死端口；`healthcheck.py` 也读 `PORT`。
- **卷权限。** 镜像以 root 启动，`deploy/entrypoint.py` 把 `/data` 里属主不对的文件交给 10001（只改不对的），再 `setgroups/setgid/setuid` 降到 10001 执行 tripwright；以 10001 启动时（两个 compose 都加了 `user: "10001:10001"`，因为 `cap_drop: ALL` 下 root 没有 CHOWN/SETUID）直接执行。改不了属主时写一行，提示设 `RAILWAY_RUN_UID=0`。实测：卷目录原为 root:root 755，启动后 10001:10001 700，进程 UID 10001。
- **空闲零出站。** `Committer.active_window`（服务端由 `http_app` 设，`TRIPWRIGHT_ACTIVE_WINDOW` 缺省 600 秒；本地 stdio 与命令行保持 None，照旧一直轮询）：最后一个已认证请求之后的窗口内才每 45 秒拉一次远端，之后停；`http_app` 在认证之后包一层 `refreshing`：空闲后的第一个请求先 `sync` 一次再答（最多等 15 秒，超时让它在后台跑完）；这次拉取算作循环的一次轮询，避免同时拉两次。未认证请求不算活动。待推的写入照常按退避重推（那是必要的出站）。实测：容器空闲 301 秒网卡出站 0 包。
- **测试。** 新增 `tests/remote/test_r2_oauth_cache.py`（2 条）、`tests/remote/test_r2_railway.py`（12 条：443 改写 5 种写法、别家托管不改、环境变量建 GitHub 租户（600 私钥、443 主机钥匙）、无仓本地租户、缺项报错、`PORT` 与环境变量启动远程应用、空闲停拉与首请求先拉、本地模式照旧轮询）。

## A11：服务端句子里六处明确的错（2026-11-02，质量判第 6 次）

依据：判词 verdict-quality6 的“跨轮的毛病”B、C、D，“共同前提”2–4，“机制补充”第 3、6、10 条。重放 q6_on_r1、q6_on_r2、q6_off_r1 三轮都照抄了服务端句子，句子里的错就原样到了用户面前。这一单只修明确的错；第一段改成按这一问写（判词 A 类）、第二段长度、24 小时票都没动，等用户定。

- **B：“火车最后一班”取火车那一程（`query.py` 的 `train_ride`）。** 只有最后一程是铁路（RAIL：rail、regional_rail、suburban 等，不含 tram、subway、bus、coach）的走法才叫火车（`arrives_by_rail`，a10c 起）；首行“火车最后一班 HH:MM 线路”和补充行“火车最后一班 HH:MM 线路：最晚 …”的时刻和线路都取这一程，不取第一程。重放原句“火车最后一班 00:47 18”里 18 是去总站的电车，火车是 00:53 REX。框的 `last` 加上这一程的开车时刻。
- **C：网上做的硬节点不是到场期限（`Anchor.where`，规则在 `ANCHOR_MEANINGS` 第一行）。** 锚点加可选字段 `where`：`online`＝在手机或电脑上做（网上值机截止），人在哪都行；`on_site` 或不写＝要在 place 到场。规则表第一行 `online_deadline`：`where: online`。这类节点不进 `node_after`（不当“之后要守的时间”），不当计划目标背后的外部截止（`external_after`），`anchor_id` 指它时报错并给改法，`check_plan` 不核“能不能赶到”，`where: online` 不能带 `travel_minutes`（于是哪里都算不出最晚出发）。硬节点记录带 `where`。只照字段判；数据没标的照旧按到场算（重放仓的值机锚点没标，要补 `where: online` 才生效）。
- **D：how_to_check 指首行推荐的那一班。** 今晚最后一班那一问：夜里一直有车时指首行点名的火车那条连接；没有火车时指现在起的下一条；有夜间空档或没查全时照旧指首行那条（不用另买票的优先）。不再指 03:00 前最后一班大巴。证据 `candidates` 带上这条，check_answer 认得它。期限型照旧指 `primary`。
- **地名（`match_place`、`QueryRouter._where`）。** 存了坐标或站点的地点按存的查：宿主只写名字、存的名字后面带地址时（“X, 街道 门牌”），按名字逗号前那段比；两个地点同名时不认。名字不是这趟存的地点时，先让数据源解析；解析出的点离这趟每个有坐标的地点都超过 `FAR_KM`（300 公里）就报错，写出解析成了哪、多远、改法（给地点 ID、坐标或站点 ID），不查路线。300 公里：Salzkammergut 的行程查 Wien Hbf 是 211 公里，要能查；重放的 Hannover Ahlem 离那趟每个地点都超过 500 公里。
- **查失败、超时与没核的节点。** latest_departure、transit 查失败（数据源没回应）或超时（服务端 `query_timeout`）时，结果带一句 `first_line`／`sentences`：“这次没查到班次（查询超时）：还有没有车、几点走都没核，出发前在交通 App 里查”；`alternative` 不再写“retain the previous safe plan”（重放 off 轮照它用了计划里存的“23:53 末班”）。latest 句子里引用硬节点只用它自己的话（`own_words`），存档的“末班、只剩”留在 turn 的硬节点记录里；节点自己的查询失败时也一样。外部截止或未标、`checked` 为空的节点，在 first_part 和 latest 句子里都带“（存档，没核）”（判词第 6 条的 07:05）；计划自己的目标不带。first_part 那行改成“计划里存的班次说法没核，第二段给。”，不再写“末班”。
- **过程话与写作说明。** 窗口句去掉“（N 次查询，每页接着上一页查）”“（查了 N 页）”，时刻表说明去掉“（交通那段说一次就够，不用每条都写）”（英文同）。check_answer 加检查 `no_process_notes`（第一段、第二段都核）：一行里有“N 次查询、每页接着上一页、查了 N 页”这类过程话，或“说一次就够、不用每条都写”这类写作说明就拦。
- **锁的类别：外部事实。** `Anchor.where` 说的是运营方这一步在网上做还是到场做（网上值机是航空公司的规定），不是用户选的；更正可以引运营方页面的证据。B3f 的字段表读到文件末尾，这一行写在本节：

| Anchor.where | external | Whether the operator's step is done online or on site; a correction may cite the operator's page. |

- **测试。** `tests/core/test_a11_fixes.py` 8 条，每条有重放形状的坏样本和合法对照；源码退回基线时 8 条都不过。A8、A10 测试里随句子改了的预期同步改。
- **限制。** `where` 要写计划的人标，服务端不从文字猜“网上值机”；远近只按直线距离和存的坐标，存的地点都没坐标时不核；超时句不知道地区，只写“交通 App”。
- **a11b（收尾）。** 终点给坐标、又不是存的地点时，“夜里一直有车到 {终点}”退到那条连接的下车站名，再没有写“终点”（`arrival_name`；真 Transitous 复核时见到“到 None”）。`CONTRACTS.md` 3.9 补 `where` 与 `no_process_notes` 两条，文件头版本不动。写计划的回答层加一句“网上、手机上做的截止记锚点时标 `where: online`”：没有单独的 planner 角色，计划由 tripwright-main 经 `propose` 写，这句放在主角色“Split, label, declare”一节（进 `references/records.md`、MCP 与 L0 生成件，不进主 SKILL）；requirements 角色同加一句；promises 加 `online-deadline-anchor` 守这两处。

## R2d：网址密钥由服务自己生成（2026-11-02，Railway 试部署前置）

- **开关。** `TRIPWRIGHT_URL_KEY_GENERATE=1`（也接受 true/yes/on；0/false/no/off 或不设为关）。它只是开关，可以写在命令行里。其他值直接拒绝启动，报错说“这是开关，不是密钥”，免得有人把密钥填进开关。
- **文件。** 位置是数据目录下的 `url-key`：Railway 和 Docker 默认是 `/data/remote/url-key`，`--remote-config` 时是 `data_dir/url-key`。
  - 生成：`secrets.token_urlsafe(32)`，得到 43 个字符。先以 600 写到旁边的临时文件、fsync，再 `link` 到正式名字，所以文件要么完整、要么不存在。两个进程同时生成时，后到的一方用先到的那个密钥。
  - 生成时机：在启动的 lifespan 里，数据目录标记检查之后。这样第一次启动时，日志仍会如实说目录是新的。
  - 已有文件：在 `create_app` 里就读，坏了就带着修法拒绝启动（“删掉文件再重启”），报错里不带文件内容。权限比 600 宽时先收紧到 600，并记一行警告。
- **日志。** 生成时只写一行 `generated a URL key in <路径> (not shown in logs): read it with `railway ssh -- cat <路径>` … or `docker exec <container> cat <路径>` …`；之后每次启动写一行“用的是 <路径> 里生成的密钥”。两行都不带密钥，`/healthz`、`/readyz` 也不带。
- **优先。** 同时设了 `TRIPWRIGHT_URL_KEY` 或 `TRIPWRIGHT_URL_KEYS` 时，用明确给的值，不读也不建文件，日志写一行说明哪个变量优先、哪个文件没用。
- **身份。** 生成的密钥登录为 `local`。配置里没有 `token_tenants.local` 时拒绝启动，也不建文件。
- **测试。** `tests/remote/test_r2d_generated_url_key.py` 共 19 条，包括坏样本和对照：文件权限 600、权限过宽会被收紧、重启不重新生成、删掉文件会换新的、明确给的优先、日志和健康接口不含密钥、开关填的不是开关值、文件内容坏了、缺 `local` 身份、数据目录写不了。
  - 把实现分别改坏 5 处（密钥进日志、文件 644、每次重新生成、明确给的不优先、不收紧权限），每处都有测试不过。

## A12：第一段按这一问写；第二段压短；存过的票用上（2026-11-02，质量判第 6 次 A、E、F）

依据：判词 verdict-quality6 的“跨轮的毛病”A、E、F，“共同前提”第 1 条，“机制补充”第 2 条；界卡 v1.1 第 2、3、5、7 条，v1.2 第 10 条；D-08、D-61、D-67。自重放 a11_self_on_r1（A11 之后的版本）第 2 问：用户刚从岩馆出来、饿了、想看万圣节、问是不是要赶去机场；第一段（28 秒）和第 1 问只差钟点，第二段（242 秒）才说“不用现在去机场”。

- **A：第一段按这一问的要点写（`render/first_part.py` 的 `Point`、`point_draft`；`packs.point_facts`）。**
  - 读懂问题是宿主的事（D-08）。宿主把这一问点名的每件事写进 `turn(points=[{kind, said, place}])`，服务端不解析原话。
  - 服务端为每个要点给存的事实：
    - go：今晚早些时候查过的同终点框（`stored_frame`，读 `latest_departure` 框里新存的 `told`：全夜有车、火车最后一班或今晚最后一班；夜没过、那班还没开、不是演示或过时的数据）、到那里的硬节点、存的票；
    - eat：离用户 2 公里内、`kind` 是吃饭类、存着营业时间且现在开着的地点（`place_rows`，和 `open_places` 同一份实现）；
    - do：宿主点名的存的地点；
    - ticket：存的票。
  - 第一段：首行答最急的，go 排在前面（误了车补不回来，饭可以晚点吃）；第二行写钟点和人在哪（用户说了就写它和原话，没说就写“假设你在 X（计划里这时在这）”，计划也没有就写“你没说在哪”）；之后每个要点一行，剩下的行给硬节点，末行待核，不超过 8 行。
  - 早些时候查过的框，从别处起算时不写“最晚几点出门”，只写火车和它在哪站开，并标“（HH:MM 查过）”。go 有框时，计划里去同一终点的上车节点不再列（框已答今晚怎么去）。没存的写“第二段给”，不编，不现查（R-17、R-45 的权衡：现查会拖慢第一段）。
  - 宿主没给要点：有计划时照旧按存的计划写（最晚的框或存的计划），`next` 开头让宿主带 points、用同一 `request_id` 再调 `turn`；check_answer 的 summary 写明这一问没核逐件回应。没计划时不提 points（没东西可答）。
  - `turn` 把要点存进这一问的 context（`packs.point_record`），check_answer 第一段按 `request_id` 取来核三件：`answers_each_point`（每个要点的原话、原话里连续三个字，或它的地点名出现；吃和票另认“吃、饭、麦当劳、超市”和“票”）、`place_in_first_lines`、`archived_marked`（没核过的硬节点时刻、估的票价出现的行要带标）。行数照旧是 `first_part_lines`。
- **E：存的票（`query.stored_tickets`、`ticket`）。**
  - 查清来源：两份重放里，服务端每条连接都写 `ticket {need: unknown, why: 票价和通票没查}`，`turn` 只列 24 小时票那条事实的编号，不带 bookings 和 fares。宿主据此写了“票价和机场段要不要补票（没核）”（a11_self）和“市内票可能不够、查票罚款”（判词引 on_r2）。所以句子是宿主写的，起因是服务端说“没查”，而存储里有答案。
  - 修在服务端：`latest_departure` 结果带 `tickets`（当天和前一天存的票：已买或现场买、票种、管哪段、价钱，估的标“估的”），`sentences` 末尾说一次；有存票时连接的 `ticket.need` 是 `stored`。`turn` 的 go、ticket 要点带同一份。服务端不判断哪张票管哪段，只说存了什么。
- **F：第二段压短。**
  - check_answer 第二段加 `second_part_lines`，按界卡第 3 条计：正文不超过 12 行，地名行与说明行、带链接的行、“## 完整安排”下各项不计，完整安排里的“没核：”行照计。拿重放核过：q6_on_r1 两问的第二段计 17 行、21 行（判词说约 16、约 20）。
  - 服务端句子同一件事只说一次：夜里一直有车时，首行已说火车最后一班和之后要守的时间，下面火车那行改成“这班火车怎么坐：…”，节点行只说首行没说的外部截止（“另外要守：…”）。
- **回答层。** 主角色：`turn` 第 1 步带 `points`；第一段那条写“它按 points 逐件答；没有它时逐件答，先答最急的”；第二段那条加“正文最多 12 行”；交通那条加“票照 `tickets`（存的票）说”；服务端 instructions、英文写法、例子页同步。主 SKILL 一进一出：第 7 步删掉和上面“每次计划改动都从 changelog 取”重复的半句，去掉答案那条里和“核前”一节重复的“Synthetic or demo evidence is not a source.”，12,140 → 12,135 字节。`promises.py` 加三条（`first-part-answers-the-points`、`second-part-twelve-lines`、`tickets-as-stored`）。
- **测试。** `tests/core/test_a12_first_part.py`，每条有重放形状的坏样本和合法对照（虚构岩馆、吃饭地点和票）。A10、A11 测试里随句子改了的预期同步改（全夜火车那行、`next` 开头、工具说明里的 first_part 形状）。
- **限制。**
  - 要点由宿主写，写错或漏写时服务端不知道；检查只核第一段有没有提到每个要点，不核答得对不对。
  - “完整安排”下的内容不计行数，宿主把正文都塞进去时 `second_part_lines` 拦不住（a11_self 第 2 问就是这样：正文 0 行超标）。
  - 第一段只用存的事实。吃的只认存了营业时间的地点，没存就写“第二段给”。活动只认宿主点名的存的地点。
  - 票只按日期和种类取，不判断哪张票管哪段。

## r2e：个人部署默认网址密钥；写给 agent 的首次部署引导（2026-11-02）

- **默认登录改成网址密钥，GitHub 登录降为可选。** 宿主 App 里的 GitHub 连接只让那个 App 能读 GitHub，不能向本服务证明“连进来的是你”，所以代替不了 GitHub 登录。默认用网址密钥（`TRIPWRIGHT_URL_KEY_GENERATE=1`，见 R2d）可以省掉建 GitHub App 这一步；代价是安全性降一档：网址泄露就等于钥匙泄露。这一点写进了引导，让用户自己选。`deploy/README.md`、`RAILWAY.md`、根 `README.md` 的说法已统一。
- **`deploy/SETUP.md`**：按执行顺序写给 agent：先问两句并分路；单列只能本人做的事；Railway 的 CLI 序列（主会话在真实账号上走通的）；Docker 与隧道；不经回显交付连接器地址；Claude、ChatGPT、Claude Code 加连接器；存储与 GitHub 部署钥匙；休眠；费用；可选的 GitHub 登录。界面写法只照抄官方页面，官方没写的标“按当前界面”。
- **核实时改正的两处：**
  - Claude 的自定义连接器（包括 Claude Desktop）是从 Anthropic 云端去连服务器的，所以 Docker 的 `http://localhost:8000` 只适合 Claude Code、Cursor 这类从本机发起连接的宿主。`deploy/README.md` 原先把 Claude Desktop 列为 localhost 可用，已改。
  - ChatGPT 官方现在的说法是：从 Plugins 直接加自定义 MCP 服务器，不需要先开开发者模式；Pro 账号只能用读取类工具，记录、提交等写入工具用不了。
- **新仓要先 `tripwright init --remote` 再推送。** 本机核实：只有 README 的仓，服务打开租户时报 `config`；完全空的仓报 `branch`。所以引导写的是：建空的私有仓，用 `tripwright init` 初始化并推送，再加部署钥匙。
- **Railway 试用的两个风险写进引导：**
  - 没验证的账号是“受限试用”，出站网络受限；
  - 试用账号的卷在额度到期 30 天后会被删除。
  - 出处是 Railway 的 Free Trial 文档。
- **空闲费用的实测数字留了占位**：〔主会话实测后填〕。

- **a12b（主会话 A12 自重放 a12_self_on_r1 的两处）。**
  - 第 1 问第一段首行是“去最晚几点出发机场 怎么走：真正最晚要查”。宿主给的 go 要点 `place` 是“机场”，不是存的地点，草稿就把用户原话当了地名。
    - 现在先取接下来硬节点所在或要去的、存的名字里有这个词的唯一一处（`packs.stored_destination`，“机场” → 维也纳机场）；没有，或有几处，就不带地名，只写“真正最晚要查，第二段给。”。
    - check_answer 的 `answers_each_point` 对 go 另认“最晚、出发、出门、末班、不用赶”。
  - 第 1 问第二段写“计划里写的 22:42 RJX 19959 这次没查到，以下面的班次为准”。查清：那次 `latest_departure`（21:01，从计划里这时在的地方）的证据 `departures` 里就有 22:42 RJX 19959（22:39 出门）。精简结果不列全部班次，宿主没看到，就写成了没查到。句子是宿主写的。
    - 修在服务端：今晚最后一班的结果加 `plan_rides`，计划里去这个终点的车在不在这次结果里，各说一句；不在的写“不在这次的查询结果里…要坐它，先在 App 核一下”，不说没了，也不说以下面的为准。
    - check_answer 第二段加 `missing_ride_found`。拿重放原文和那次的证据核过：第 4 行被拦，“说 22:42 RJX 19959 没查到，这一问的查询结果里有这班（22:39 出门）”。
  - 只在“今晚最后一班”的框里说计划的车。期限框（给了 `anchor_id`、`by`、`board_at`）和 `transit` 没加。

## A13：两边都改计划时，后到的在新版本上重做（2026-11-02，D-75，R-104）

- **以前。** 一份计划提交在重放时，如果发现基础版本变了，就成 `conflict`（`plan_base_changed`）；如果只是账本在别处动了，就成 `replay_refused`。用户在 Claude Code 和连接器之间来回切换时，一分钟内两边各改一次计划，后到的那份一定被拒。用户认为这是错的。
- **现在：在新版本上重做（`repo/rebase.py`、`repo/replay.py`）。**
  - **对齐方式**：取三份计划（这次提案的基础版本、提案、新版本），按对象身份逐层对齐：天按日期；人、地点、预订、票价、硬节点、方案、块按 ID；方案里的分组按人（排序后的人号）。其他值（时刻、名字、ID 列表）整体比较。
  - **取值规则**：
    - 只有一边改的字段，取那一边；
    - 两边改成一样的，照收；
    - 两边改成不一样的，算冲突；
    - 一边删、另一边改了同一个对象，也算冲突，不悄悄丢掉；
    - 列表的顺序跟改了顺序的那一边，两边都改了顺序且不同时算冲突；
    - 我方新加的对象，排在它在我方列表里的前一个对象后面。
  - **不做文本合并**，仍然按操作重放。
- **检查照旧。** 重做后的计划写回草稿，再走 `versions.commit` 的全部检查：硬约束、必须项锁（D-52、D-53）、用户决定的变更理由。过不了就照旧拒，记 `replay_refused`，`detail` 以 “Redone on the plan changed elsewhere, then refused by its checks:” 开头。基础版本没变、只是账本在别处动了的提交，也在新账本上重查后提交，不再因此拒。
- **冲突时问用户。** 冲突仍用 `plan_base_changed`（契约 schema 里的 reason 枚举不变）。`detail` 列出冲突的路径；通知的 `user_line` 点名这一项和两边的值，并问要哪个，例如：“两处改了同一项、改得不一样：2030-06-10 的「…」（d1-art）的开始时间：别处改成了「10:15」，这次改成了「10:45」。要用哪个？”。冲突超过两处时，只点名前两处，再写“另有 N 处”。没有更早的计划可以拿来重做时，照旧用通用句。
- **写明“重做了”。** 重做成功的版本带 `redone {on_version, on_version_id, from_version, note}`，`changelog` 每一条带 `redone`，note 都是“在别处的改动之上重做了这次改动”。`changelog` 只列这次提案自己的改动，因为比较的是新版本和合并后的计划。`commit` 等到重放完成时（最多等 5 秒），回执里重新读取这个版本，就带上 `redone`。
- **回答层。** `records.md` 里 `conflict` 那条（源文件在 `agents/roles/tripwright-main.md`）改成新语义；主 SKILL 不变。CONTRACTS 第 1 节的重放规则、2.1 的 `conflict`、2.3 的 `user_line` 示例同步改了，版本号不动。
- **测试。**
  - `tests/repo/test_a13_redo.py` 5 条：
    - 两边改不同的天，两边的改动都在，并带 `redone`；
    - 回执带 `redone`；
    - 同一字段两边改得不一样，仍是冲突，并点名这一项；
    - 重做后在新版本上过不了必须项锁，照旧拒；
    - 三方合并对增删和顺序的处理。
  - `test_replay.py` 原有那条（两边改同一天的标题）留作同字段冲突的对照，只改了 `user_line` 的预期。
  - 把源码退回基线后，新增的 4 条场景测试都不过。
- **限制。**
  - 两边新加的对象碰巧用了同一个 ID、内容又不同时，算冲突。
  - 重做后计划不符合格式（例如两边各自的改动合起来让块的开始晚于结束）时，由提交的格式校验拒绝，记 `replay_refused`。
  - 手工改文件（`manual` 操作）的重放规则没有改。

## A14：修第 7 次质量判点名的明确错（2026-11-02，质量判第 7 次）

依据：判词 verdict-quality7 的“跨轮的毛病”1—6、“共同前提”1—4、“机制补充”。三轮重放（q7_on_r1、q7_on_r2、q7_off_r1）的工具记录与回复原文。每条有重放形状的坏样本与合法对照（`tests/core/test_a14_fixes.py`）；坏样本也在基线源码（e795228d）上跑过，都复现。

- **一、起点（`packs.asker`、`plan_place`、`plan_here`；check `assumed_place_matches_plan`）。**
  - 来源：A12 的 `plan_place` 取“第一条和问话人有重叠的线”，没给 subject 时就是大家一起那条线（午饭 11:40—13:30），于是 21:01 假设在午饭的店。用户本人那条线此刻是岩馆（17:30—21:45）。
  - 改：问话人是 `speaker`，否则 `subject` 里唯一的人，否则这趟的第一个人。在问话人自己的块里取覆盖此刻的块；交通段取起点。没有覆盖的块时，取此刻之前最近结束的块；交通段取终点，因为人已经到了，这一点包里写的是“取起点”，我按“走完的路取终点”处理。
  - 检查：第一段写“计划里这时（你）在这”的行，要写出计划里此刻那个地点的名字。两份 on 重放的原第一段都被拦在第 2 行。
  - 同名：短名相同的两个地点（重放里同一连锁的两家岩馆短名一样）改用存的原文名在地址前那段。
- **二、05:45 不再算成要到场（`_node_latest`）。**
  - 来源：重放的 `latest_departure` 句子“明早直接从 X 去：要 05:45 前到，最晚 05:12 出门”。下一个节点是计划留的排队余量（a1101-queue，`deadline: plan`），服务端给它算了最晚出门。
  - 改：计划目标写成“你计划 05:45 前到（留的余量）：…”，不另算最晚出门；那一行只算它背后要到场的外部截止（07:05 柜台关），没有就不写。第一段的计划目标也用这个说法。
  - 数据：重放仓里网上值机 a1101-checkin 仍是 `deadline: external`、没标 `where: online`，所以在那份数据上仍会出现“要 05:45 前到”，要等数据补上标记（服务端不从文字猜，A11）。测试里坏样本与对照都写了。
- **三、怎么查指主选（`_last_connection`、`HOW["missed"]`；check `search_matches_main`）。**
  - 来源：A11 的 how_to_check 指首行点名的那班。全夜有车时，那就是火车最后一班（00:53），而三轮的完整安排主选都是计划里的 22:42。
  - 改：计划里的车在这次结果里、还没开时，搜那班；第二行写“误了主选，查末班：…”，给末班的搜索词和时刻。框记下主选那条连接的时刻（`main`），check_answer 核“怎么查”里的出发时间。
- **四、第一段（`point_draft`、`planned_train`）。** 硬节点只留一行，取下一个要到场的。go 没查过的框时，首行先给计划里存的车（存档，没核），再说真正最晚第二段给。去掉“计划里存的班次说法没核”那行，末行不再和首行重说。没给 points 的退回草稿（A10）照旧。
- **五、覆盖说明打架（回答层“今晚”改 03:00；check `coverage_said_short`）。**
  - 来源：researcher 按回答层的“今晚查到次日 01:00”跑了 transit，窗口句写“01:00 之后出发的没查”，被写成“不能说 00:53 之后没有火车”。同一问的 `latest_departure` 连续查到了 03:00。
  - 改：主角色、researcher、verifier 与步骤页里的“今晚”都改成次日 03:00，和 `SERVICE_END` 一致。check_answer 拦“HH:MM 之后没查”这类说法，条件是这一问的今晚连续查询覆盖了那个时刻。拿 on_r2 的原文与证据核过：第 12 行被拦。
- **六、状态句与第二段发两次（`next`、Stop 钩子理由、主 SKILL 第 5 步）。**
  - 来源：两条状态句都是宿主以为工具结果里的 first_part 用户看得到（“第一部分已经在上面了”“上面这段”）。第二段两次：没过 check_answer 就发了，Stop 钩子要它“把发出的文字交 check_answer 再核”，核过后宿主又发了一遍。
  - 改：
    - `next` 加“用户看不到工具结果：第一段要你发出来才算”；
    - Stop 钩子的两个理由加“这段用户已经看到了：过了就结束，别再发一遍；没过只发一条“更正：””；
    - 主 SKILL 第 5 步“A draft in your thinking is never shown, nor is a tool result.”；
    - 第一段的退回写法同步改成先给存的车、只列下一个要到场的硬节点。
  - 主 SKILL 12,135 → 12,130 字节。
- **限制。**
  - “这趟的第一个人”当问话人是约定，不是数据里的字段；宿主给了 `speaker` 时按它。
  - 怎么查的主选只认计划里存的车，宿主在完整安排里另选的主选服务端不知道；`search_matches_main` 只在服务端框里的主选和末班不同时才核。
  - 退回草稿（没给 points）没改。

## A15：不经工具改的计划文件不被冲掉（2026-11-02）

依据：主会话 A15 派单。判官重放的工作区里，评测仓给硬节点 a1101-checkin 手加了 `where: online` 并提交，`tripwright check` 报 “plan files differ from the last version (a hand edit not yet taken in)”，下一次工具写入按上一版重新生成计划文件，把手改冲掉了。

- **来源。** 原来的手改收编（`repo/manual.py`）只看工作副本与 HEAD 的差（`git status`），而且只在提交前看。手改一旦已经在提交里（评测仓、GitHub 网页、别的 agent 或手工 commit），工作副本与 HEAD 一致，看不到；`attach` 从 HEAD 重建索引时只取 `plan/versions.jsonl` 里的版本，手改的计划被当作 `manual_plan` 报出来就丢下了。下一次提交照最后一版重新生成 `plan/`，手改就没了。另一个同源的问题：工具已经有一个还没写进文件的新版本时（commit 之后、flush 之前），手改被当成在新版本上改的，收编时把工具那次改动倒回去了。
- **改：怎么认手改。** 拿计划文件和“文件最后记下的版本”比：工作副本 `plan/versions.jsonl` 的最后一行，用那一版的计划生成计划文件，逐个文件比文本，不一样的就是手改过的文件。一条还开着的“有未入版的手改”通知记着它第一次被看到时的基础版本，之后就以它为准。这样无论手改在工作副本还是已在提交里，都认得出；工具有还没写进文件的新版本时也不会误认。
- **改：什么时候收。**
  - 打开租户：`attach` 之后，无论是否重建；
  - 见到新头：`sync` 的 `_rebase` 换入之后（推送被拒时的 `sync(push=False)` 也走这里）；
  - 写入前：`turn`、`propose`、作业证据（`record_evidence`，即 `job` 写入）先收，这次写入就以收进来的版本为基础；
  - 提交前：`flush` 照旧先收。`commit` 不先收，免得刚 propose 的草稿因此变旧；它之后的 flush 会把手改在新版本上重做。
- **改：怎么收。**
  - 基础版本就是当前版本：照旧走 `versions.adopt` 的全部检查。
  - 工具在基础版本之后有更新的版本：用 A13 的三方合并（`rebase.onto`，先把三份计划都按行程模型补齐默认值再按对象身份合并）在新版本上重做，两边改法不同的字段不收，通知点名这一项和两边的值。
  - 已收的手改（`manual` 操作）在重放时遇到基础版本变了，也在新版本上重做，不再一律算冲突。
- **过不了检查。** 不收、不覆盖：手改过的计划文件进 `held_paths`，之后的提交不写它们；版本不变；通知 `manual:plan` 写清楚哪个文件、计划里哪一天哪一项、为什么，加“版本没变，手改留在文件里；改好或撤掉后，下次写入时收进来。”。模型校验错不再把 pydantic 的原文（含输入回显和网址）放进通知。查锁、硬约束的冲突仍以“有未入版的手改：冲突 ”开头。
- **测试。** `tests/repo/test_a15_hand_edits.py` 7 条：已提交的只改 YAML 的手改接着一次 job 写入（坏样本）、打开租户就收、写入前收、工具版本待写时重做、Git 网页推上来的手改经 sync 收、过不了检查（对照）、两边改同一字段（对照）。源码退回基线后 7 条都不过；坏样本在基线上 HEAD 里的 `where` 被写回成空。
- **限制。**
  - 被扣住的手改文件在之后的提交里不写，这些提交记下的版本的计划就不在 Git 历史里（`check` 会报 “the plan of V… is not in this history”）；用户改好或撤掉后下一版写回才补上。原来扣住工作副本手改时也是这样。
  - 只改了空白、注释、键的顺序的计划文件也算手改过：计划没变，就关掉通知、不立版本，文件由下一次写入重新生成。
  - 手改的记录文件（账本、证据）照旧不收，按账恢复。

## A16：修第 8 次质量判点名的错（2026-11-02，质量判第 8 次）

依据：判词 verdict-quality8 的“跨轮的毛病”1—4、6、7，“回归清单”，“共同前提”与“机制补充”；三轮重放（q8_on_r1、q8_on_r2、q8_off_r1）的工具记录与回复原文。主会话的决定：在句子的来源上修，保留“服务端给交通句、宿主照抄”（D-58、D-67）。每条有重放形状的坏样本与合法对照（`tests/core/test_a16_fixes.py`）；同一份合成行程的服务端句子在基线（ad9d9b71）上也跑过，判词点名的形状都复现。

- **一、一行一件事（`LAST_SAY`、`margin_text`、`marked`、`stored_tickets`、`named_lines`、`window_until`）。**
  - 来源：首行把“今晚不用赶”、末班、之后要守的时间拼在一起，计划目标的句式“你计划 05:45 前到（留的余量）：”后面又是存的原文“…05:45 前到…（计划留的余量；…）”；票价行是记录原文；“下一班”取下一程的首段线路（U6、U1 不到机场）；窗口句与计划车句是查询的说法。
  - 改：首行只到末班；截止与余量各一行，余量写“你计划留的余量：”加原文（去掉原文里说余量的括号片段，时刻前加“明早”）；票价一句话，估价写“约”，一张票价只对一个人时不写“/人”；“下一班 HH:MM 出门”；查全了今晚的窗口不另成一行，窗口句与计划车句换成用户的话；中文的电车线路写“N 路”。
- **二、起点跟下一步（`packs.next_place`、`next_step`；`_last_connection`）。**
  - 来源：`latest_departure` 只按宿主给的起点算；用户说先去吃、去市区，句子照旧从岩馆算。
  - 改：`turn` 的上下文记下这一问的下一步地点（eat、do 里宿主顺序最后一个解析出的、不是用户所在处的；eat 没给地点时取第一段推荐的第一家）。30 分钟内、从这一问用户所在处查到这一问 go 的地点时，从下一步地点查，并说一句“按你先去 X 算”。多个下一步取最后一个：从那里才去终点。从下一步出发时不出“明早直接从 X 去”，也不为它另查。
- **三、出门不晚于关门（`origin_closes`、`leave_words`、`connection_sentence`）。** 起点存了 `opening_hours`、查询开始时开着时，晚于关门的出门时刻改说：首程在关门后 30 分钟内开的“关门出来，走到 X（坐 HH:MM 的 …）”，否则“HH:MM 在 X 上车”。只读 `opening_hours`，不读 `hours` 文字（A11 定的不从文字猜）。
- **四、怎么查等于主选第一程。** 框记下主选第一程（站、时刻）；`search_matches_main` 核“怎么查”的搜索站名与时刻，链接里的 `S=`、`time=` 也核。起点改从下一步后，主选第一程自然在那里。
- **五、第一问的第一段给出门时刻和走法（`packs.planned_way`、`first_part.go_line`）。** 问话人自己那条线里，从用户所在处（说的或假设的）到计划那班车上车站、还没开始的交通块：首行写它的开始、到站与那班车，下一行写它的 line 或 mode 的第一段，都标存档没核；没有就写“几点出门、怎么走，第二段核完给”。
- **六、地点解析（`packs.resolve_place`）。** points 的 place 先按 ID 与存的名字；再找被 place 或原话包含的存的地点名；再按拉丁字母相似度取唯一一处（≥ 0.68，领先第二名 0.15）。0.68 是按重放仓的地点名量的：宿主给的第一家岩馆的英文意译对存的原名 0.696（要取）；一个广场站名对旁边教堂的名字 0.667（不取）。
- **七、check_answer。** `search_matches_main`（站名与时刻）、`leave_before_closing`、`per_person_alone`；另加第一段的 `latest_needs_lookup`（最晚、末班、最后一班挨着时刻、这行没有“HH:MM 查过”），`no_process_notes` 也拦“连续查全”“在这次的查询结果里”。新检查不进钩子（D-47：钩子只认第一段的形状）。
- **八、没有 MCP（`tripwright first-part`、`Tripwright.draft_first_part`；主角色“工具不在时”那句）。**
  - 来源：off_r1 的宿主读计划文件自己写第一段，首行是锚点原文里的“最晚 21:50 出门”和存的“23:53 末班”。第一段前的英文状态句是宿主自己写的：它在后台派出 researcher 后、写第一段前，以为第一段已经在上面；那一轮没有 `turn`，也就没有 `next` 里“第一段之前不发状态句”“用户看不到工具结果”那两句。
  - 改：命令行出与 `turn.first_part` 相同的第一段（只读，不记消息），带同样的 `next`；主角色里“工具不在时先读包和日文件”那句改成用这个命令，主 SKILL 12,130 字节不变。服务端第一段的推迟句不再用“最晚”字样；钩子要第一段时的理由不再写“最晚几点”。
- **限制。**
  - 重放仓的两家岩馆 `opening_hours` 是空的（营业时间只在 `hours` 文字里），第三条在那份数据上不生效，要补 `opening_hours`。
  - 下一步只认解析出的地点；“去市区”这类没解析的 do 不改起点。宿主在完整安排里另定的主选，服务端仍不知道。
  - 相似度只比拉丁字母；中文地名只认包含。

## A17：A16 自重放里的两处小毛病（2026-11-03，主会话自重放 a16_self_on_r1、a16_self_off_r1）

依据：两轮自重放的回复原文与工具流。每条有重放形状的坏样本与合法对照（`tests/core/test_a17_fixes.py`、`tests/agents/test_a17_dispatch.py`）；源码退回基线（610d7735）后三条都在坏样本处不过。

- **一、第一段同一行出现两次（`first_part.point_draft`）。**
  - 来源：off 轮第一问，宿主把“最晚几点出发机场”和“怎么走”拆成两个 go 要点，地点都是机场；第一段按要点各出一行，第 1 行与第 4 行相同。on 轮宿主只给了一个 go 要点，没出。
  - 改：同一种类、同一地点（解析出的地点，没解析时是宿主给的 place）的要点合成一行，取排在前面的那个；没有地点的要点不合并（各说各的）。整段再去掉完全相同的行，合并后腾出的行留给硬节点。`turn` 与命令行 `first-part` 都经 `point_draft`，一处改两处管。`first_part.points` 仍每个要点一项，合并的几项 `line` 相同，`answers_each_point` 照旧逐个核。
- **二、没有 MCP 时第一段前的两句英文状态句。**
  - 第一句（19.6 秒，“the points format needs objects, so I'm retrying”）：宿主第一次调 `first-part` 时 `--points` 给的是字符串列表，命令报 pydantic 原文退出 1。改：命令行 `read_points` 把列表里的字符串当成 `{kind: other, said}`（去空白，空串略去，超过 60 字截断，与 `Point.said` 的上限一致）；真格式错（不是 JSON、不是列表、kind 不对、元素不是对象）照旧退出 1，错误末尾加一个可照抄的例子；顺带把整体不是列表时的路径从“points.”改成“points”。字符串要点没有种类，第一段对它们只写“…：第二段给。”，不给存的车；要存的车，宿主得给 kind 与 place（例子里有）。
  - 第二句（38.2 秒，“I'm still checking tonight's actual departures…”）：宿主在后台派出 researcher 之后、第一段之前写的。查了主角色与步骤页，没有一句让它在第一段前说话；有一句让它“以为第一段已经发了”：派单那条要它把“what the first part says of it”交给 researcher，而 D-46 的顺序是先派、再把第一段写成这一步最后的文字。两轮四次派单（on、off 各两问）的 prompt 里都写了“First part already told user”或“The first part said”，当时第一段一次都还没发；派完之后 on 轮没写字、接着自己调了查询，外壳补了一句“没有可见输出”才发第一段；off 轮写了这句状态句，Stop 钩子拦下后才发第一段。
  - 改（主角色，生成的主 SKILL 12,130 → 12,129 字节，一进一出）：派单那条改成交“what the plan stores of it”；第 5 步末句“A draft in your thinking is never shown, nor is a tool result.”换成“The user sees no dispatch, draft in your thinking or tool result.”。researcher 回报格式的 `answer line` 跟着改成“what the plan stored”。新承诺 `dispatch-carries-the-plan`：主 SKILL 要有新说法、不许出现旧说法；`multi-agent-first` 的对应一条换成新句。D-46 的顺序（先在后台派、再发第一段）不变。
- **限制。**
  - 第二句的改法是说明文字，宿主照不照做要看下一次重放；钩子没有改（Stop 钩子已经会拦“I'm still”这类状态句，但拦的时候用户已经看到了）。
  - `turn.next` 里“先把 first_part.text 原样发给用户，再去查”没改：后台派单不算查询（PreToolUse 钩子放行），与 D-46 不冲突。

## A17b：退回 A17 的两处，留下去重（2026-11-03，主会话自重放 a17_self_*）

依据：主会话用 A17 跑的 5 轮自重放，与 A16 的 2 轮比。

- **为什么退。**
  - 第一段前、两段之间多发状态句的问，从 4 问里 1 问变成 10 问里 7 问（“还在核第二段，马上发。”“上面发的第二段已经核过，内容不变，不用再看一遍。”“正在核今晚去机场的车次，核完发第二段。”）。主会话判断最可能来自 A17 改的两句回答层说明；单轮波动也可能，但比例差太大。
  - 没有 MCP 时宿主照旧把 `--points` 写成字符串列表，A17 把它们当 other，第一段只剩“今晚到明天：第二段给。”这类行，答不了所问（a17_self_off_r2、off_r3 第一问）。A16 时报错，宿主改成对象重试，第一段反而是对的。
  - 去重有效：5 轮里没再出现重复行。
- **退回。** 主角色第 5 步末句（“A draft in your thinking is never shown, nor is a tool result.”）、派单那条（“what the first part says of it”）、研究员回报格式的 `answer line`、`promises.py`（`multi-agent-first` 原句，去掉 `dispatch-carries-the-plan`）都回到基线原文，生成件重建；主 SKILL 回到 12,130 字节，`agents/`、`skills/` 与基线无差。`tests/agents/test_a17_dispatch.py` 删去。
- **命令行 `first-part --points`。** 字符串列表照 A16 报 pydantic 原文退出 1，错误末尾保留 A17 加的可照抄的例子（`cli.read_points`）；其他格式错同样带例子。整体不是列表时错误路径写“points”而不是“points.”，保留。
- **留下。** `first_part.point_draft` 的合并与去重（A17 第一条）不变。
- **测试。** `tests/core/test_a17_fixes.py`：`test_points_of_one_kind_to_one_place_share_one_line` 照旧；字符串那条改成 `test_the_command_line_refuses_plain_strings_with_a_right_shape_to_copy`（坏样本：重放的三个字符串，退出 1、信息里有例子；对照：例子换上行程的地点编号后出带存档车的第一段）。源码退回基线后两条都在坏样本处不过。

## S4a：给 agent 的引导按“最差入口”重写（2026-11-03，任务包 4a，调研 td/apps-github.md）

- **为什么。** 原来的引导按 Claude Code 写：默认能派后台子代理，默认能在一轮里先发第一段、再接着核、再发第二段。调研（第一节第 4 条、4.1、5.3）查到的事实是：没有一家应用保证“一次任务中途先发一段再继续”；Claude 聊天、豆包工作没有子代理；豆包工作只连 MCP、不读仓里的文件。server instructions 是 5,715 字节的一段英文连写，步骤和完成条件分不出来。
- **一份核心步骤，同源生成。**
  - 源：`src/tripwright/agents/core.yaml`。8 步，每步一个动作和一句完成条件，完成条件看工具结果或发出的文字就能核对；另有 4 条始终成立的规矩，最后一条是停点。
  - 生成四处：server instructions（3,445 字节，上限 3,600，构建时超了就拒）；`generated/server/steps.json`，`turn` 和命令行 `first-part` 照它返回 `steps`、`always`；主 skill 与 MCP prompt 的“Every message”；`generated/repo/AGENTS.init.md`，init 写成用户仓的 `AGENTS.md`，`CLAUDE.md` 仍是 `@AGENTS.md`。
  - 这个文件不叫 `AGENTS.md`：在本仓里工作的宿主会把它当本仓的说明读。
  - instructions 只放步骤；长规则留在 MCP prompt `tripwright-main`，instructions 最后一句写“写第二段、记录或步骤不清时读它”。只连 MCP 的入口按 instructions、`turn` 的 `steps` 与 `next` 走，`check_answer` 照旧兜底。
  - 承诺里 16 条原来也核 server instructions，现在只核 MCP prompt 和 skill；核 instructions 的只留步骤本身说到的 8 条。新增 3 条，每条核四处：`subagents-optional`、`second-part-in-the-next-reply`、`stop-only-before-what-cannot-be-undone`。`dispatch-before-the-first-part` 改用核心步骤的字句，也核四处。
- **子代理可选，D-46 的顺序不变。**
  - 第 3 步写：应用能在后台跑子代理、并在它回报时通知（Claude Code），先派一个 researcher，否则跳过。
  - 第 4 步：派了的，第一段是这一轮最后的文字。
  - 第 5 步：子代理可以替你查；没有就自己一件一件查。
  - 不写“同时查”。原 skill 的“several searches at once”删掉了。
  - Codex 要在 AGENTS.md 里明写才会派，所以 AGENTS.md 加一句“In Codex, spawn `tripwright_researcher` for step 5 when you can run subagents”。
  - 没把顺序改成“先发第一段再派”：D-46 的预检里那样做 8 问都丢了第一段。
- **第二段怎样到用户手里。**
  - 选的做法：在 Claude Code 里，派后台 researcher 的，回报开新一轮；在 Codex 里，同一轮接着做。其他应用，以及不确定的，第一段作这一轮的完整回复，以 `first_part.later`（“要核过的第二段，回我一句“继续”。”）结束。用户下一句以 `points: []` 进 `turn`，结果带 `second_part_due`，宿主照第 5—8 步做，`check_answer` 用那一问的 `request_id`。
  - 服务端认“欠着”的办法：上一问的上下文记了 `request_id`、第一段说了有第二段（`promises_second`），而答案文件（`check_answer` 过核时写、Stop 钩子读的那份）里没有这个 `request_id`。只在 6 小时内认。上一句也这样要过，还指那一问。一句点名了新事（给了要点），就当新问题，不把旧的第二段塞进来。
  - 没用 `clientInfo` 判应用，原因三条：
    - 远程 HTTP 服务是 stateless，SDK 2.3 在旧协议下给工具调用建连接时 `client_info` 是 None（`streamable_http_manager._handle_stateless_request` 调 `Connection.from_envelope(pv, None, None)`），用户手机上的 Claude Code 连的正是这条路；
    - Cowork 和 `claude -p` 也报 `claude-code`，同一个名字背后显示行为不同（Claude 5 模型在工具调用之间的文字可能成为看不到的进度思考块）；
    - 读到它要改 `adapters/mcp.py` 的 turn 签名，超出本包写域。
  - 子代理调研查到的名字：Claude Code `claude-code`，Codex `codex-mcp-client`，Claude 网页与 Cowork 远程连接器 `Anthropic/ClaudeAI`（社区日志），豆包工作没查到。
  - 代价：
    - 豆包工作这类应用里，第二段要等用户回一句，核查在下一轮才开始，用户再等一次核查的时间；
    - 宿主对“自己是不是 Claude Code、Codex”的判断不由服务端核对；
    - 一句“继续”之外又问新事时，旧的第二段不再发。
- **停点。** 只在三处：带 `accept_unverified` 的提交、改用户定为必须的事、发给用户以外的人的东西。其余步骤写明“不用问，接着做”（第 4 条规矩）。
- **没有 MCP 时第一段前的状态句（第 5 条）。**
  - 查法：子代理逐轮读了 a17b、a16、a17 的 off 轮流水（含思考与钩子消息）；我抽查了 a17b_off_r1 第一问。
  - 结果：12 轮里 7 轮有，共 9 句，都在第一段之前；on 轮没有。
  - 9 句里 6 句在同一个位置：后台派出 researcher 之后、第一段之前。
  - 每一轮在发第一段前，都在工具结果里见过第一段的原文成行出现：命令行把它印在 stderr，宿主又常加 `2>&1`。a17b_off_r2 那一轮是宿主自己用 python 印出来的，因为上一问 `2>&1 | json.load` 读坏了。
  - 有两句直说“第一段已经发给你了（上面那段）”“第一段发完了”。
  - 4 句前面紧挨一次 PreToolUse 拒绝，其中两次拒的是 `tripwright … query --help`。拒绝理由的“核查请在后台派 researcher”让它先派，派完写状态句。
  - on 轮的第一段只在 `turn` 的 JSON 里，6 问没有一句状态句。
  - 其余 3 句：A16 的 points 错误没给例子（A17b 已加例子）；A17 的别名查询；A17 的一次核查重试。
  - 来源上的改法：
    - 命令行 `first-part` 只出 JSON（`cli.run_first_part`）；
    - 钩子按分段判查询，带 `--help`、`-h` 的那段不算，经别名的 `query <种类>` 算（`render.check_answer.lookup_command`）。
  - 没改说明措辞，也没改 D-46 的顺序。效果要主会话重放才知道。
- **测试。**
  - `tests/agents/test_s4a_core.py`：
    - `test_server_instructions_hold_every_step_and_its_check_within_the_limit`
    - `test_the_build_refuses_instructions_over_the_limit_and_a_step_without_its_check`
    - `test_the_four_places_carry_the_same_steps`
    - `test_a_changed_step_reaches_every_place_only_through_the_build`
    - `test_codex_is_told_to_spawn_the_researcher_and_no_entry_must_have_subagents`
    - `test_the_main_skill_did_not_grow`
    - `test_sources_load_the_core_from_the_checkout`
  - `tests/core/test_s4a_delivery.py`：
    - `test_a_reply_that_ends_with_the_first_part_gets_the_second_part_on_the_next_message`
    - `test_only_a_message_that_names_nothing_asks_for_the_latest_second_part`
    - `test_one_question_goes_through_from_the_instructions_and_the_tool_results_alone`
    - `test_the_command_line_first_part_carries_the_steps_and_the_line_that_ends_a_reply`
    - `test_the_command_line_prints_the_first_part_only_inside_its_json`
    - `test_reading_the_command_line_help_is_not_a_lookup`
  - 改了的旧测试：
    - `test_agents_prompts`：instructions 的开头；
    - `test_agents_templates`：改看 init 写出的 AGENTS.md，首行带 core.yaml；
    - `test_assemble`：`Sources` 用 `replace` 造；a9 那条不再核 instructions；
    - `test_a10_frame`：`next` 第一句的尾巴改成“再照 steps 往下做”；
    - `tests/agents/conftest.py`：throwaway checkout 也拷 core.yaml 和 templates。
- **主 SKILL。** 12,130 → 12,119 字节。核心步骤换掉了原来的“Every message”和“Check before the second part”两节；“The answer”里与步骤重复的几句收短。

## s4c：分享给朋友的去隐私公开页（D-82、D-83、D-89）

命令 `tripwright --repo <行程仓> share build|publish`（`site/share.py` 出页，`site/publish.py` 推送）。下面是现行的定法。

- **出什么。**
  - 只出首页和每天一页，来源是**已提交的计划**：有远端读 `origin/<branch>`，没有读本地分支，与服务的 `effective` 一致。还没推到远端的本地提交不出页。
  - 每天一页写：日期与星期、标题、区域、日落、过夜处（住处只写城区）、要准时的时刻和说明、当天说明、每套安排（第一套是推荐）。
  - 每段写：时间、类型、标题、内容、“怎么走”（`mode`／`line`／步行分钟）、备注、当地名＋地图链接、备选地点；另有按时段的候选地点和下雨的安排。
  - 不写：出处、把握度、核验、营业时间、价格字段、地点的 reason／notes、App 提示；决定、记忆、原话、证据也不读。
- **代号。**
  - 所有人编成 Traveler A–Z／旅伴 A–Z。顺序是 `trip.yaml` 的 `people`，再接 `profile/people.yaml` 里其余的人。
  - 用户本人也编代号：记录里没有字段标明哪一个是用户本人。
  - 名字本身是代称、后面带编号的（“队友 1”“同行人 A”）只替换，不当私密词挡。单独一个代词（“我”）不动。
- **住处。**
  - 怎样认：地点 `kind` 属于 hotel、hostel、apartment、airbnb、guesthouse、pension、lodging、accommodation、stay、home、住宿、住处、酒店、青旅、公寓、民宿、家（不分大小写）。
  - 写成“住在 <城区>（酒店）／Staying in <district> (hotel)”，没有地图链接。
  - 城区按序取：地点的 `district`；当地名里“邮编＋城镇”那一段（如 1070 Wien）；`area`。括号里的类型按 kind 写，默认“酒店”。
  - 当私密词的有：名字、当地名、地址前两段（名字、街道门牌）；名字里长度至少 4 的拉丁字母词，但泛称（Hotel、Hostel、Haus 等）和在别的公开地点、区域名里出现过的除外；小数至少 4 位的坐标。
  - 城区、城市、国家不算私密。
- **航班号。**
  - 规则：两位航司代码＋1—4 位数字，中文改成“航班”，英文改成“flight”。
  - 不算航班号的：三个字母开头的（REX、RJX、VAB）；字母加数字开头、后面只跟一位数字、中间又没有空格的（N25、S45）；时刻、小数；数字后跟单位的（站、路、楼、min、′ 等）；列车类别和常见缩写（IC、EC、RJ、RE、EN、NJ、EG、OG、UG、XL 等）。
  - flight 类 booking 原文里出现的号码一律算航班号。
- **电话。**
  - 算电话的：带 + 或 00 的国际号码，至少 8 位数字；0 开头、中间有分隔的本地号码，至少 7 位数字；中国手机号。
  - 记在非住宿地点上的号码是商家公开电话，放行；住处的号码照挡。
- **订单。**
  - 原有规则之外，认这些标签：Buchungsnummer、confirmation/reservation/order/ticket number、订单编号、取票码、票号等。
  - booking 里键名像订单号的字段（reference、confirmation、pnr、order、voucher 等），值带数字、长度至少 5 的，原值当私密词。
- **英文。**
  - 程序生成的字是双语的。用户写的中文从 `<行程仓>/.tripwright/share/en.yaml` 取英文（Git 忽略）。它的键是已换好代号的中文，所以文件里不会有真名。
  - `share build --todo` 把还没英文的句子加进去、值留空，agent 填好再出一次。
  - 没有英文的照原文显示，标“(中文)”。
  - 页面默认英文（`<html lang="en">`），右上角 EN／中文 切换，也认 `?lang=`。
- **扫描与换上。**
  - 先写到输出目录旁的临时目录，扫描过了再用一次 rename 整体换上。扫不过就删掉临时目录，原输出不动。
  - 已有目录不是分享页（没有 `.tripwright-share` 标记）时拒绝覆盖。
  - 扫描逐个文件，先解 HTML 实体与 URL 编码再扫。类别：email、order-like identifier、flight number、phone number、person name、lodging、private word。只报“类别 → 页名”，不报原值。
- **发布。**
  - 第一次必须带 `--create`。依次做：`gh repo create <名> --public`、克隆、提交并推送、`gh api -X POST repos/<o>/<r>/pages`（legacy，main，`/`），再读 `html_url`。
  - 之后每次：fetch；把副本整个换成网页；再扫一遍副本；提交、推送。内容没变就不提交。
  - 第二道扫描不过时，先把副本 reset、clean，再报错，线上保持上一版。
  - 提交者固定为 `tripwright <share@tripwright.invalid>`，日期用 UTC。
  - 仓名：没给 `--name` 时用 `trip-<趟 id>`。`--private` 只在付费计划可用（网页本身仍公开）。
  - 公开仓里只有几页 HTML、`.nojekyll`、`robots.txt`（Disallow）、`.tripwright-share` 标记；每页加 `noindex`。
- **退出码与提醒。**
  - 退出码：0 成功，1 出错，2 扫描没过、没推。
  - 被挡时在行程仓索引留一条 `share_blocked` notice（只写类别和页名），下一次 `turn` 带出；下一次发布成功时关掉（CONTRACTS 3.9）。
- **本机状态。** 都在 `<行程仓>/.tripwright/share/`：
  - `config.json`：仓名与网址，有它就表示这趟开了分享页；
  - `site/`：最近一次干净的出页；
  - `public/`：推送用的副本；
  - `last.json`：最近一次结果，只记类别；
  - `en.yaml`。
- **谁来触发。**
  - 提交生效后由主 agent 跑 `share publish`：主角色第 6 步，S4a2。
  - 服务端不推公开仓：它没有那个仓的凭据。远程部署时 `share_blocked` 只在跑命令的那台机器的索引里。

## S4a2：分享页接进引导与约定，later 一行过核（2026-11-03）

- **提交后重出分享页（D-89）。**
  - 写在第 6 步“Update the site”的 With tools 里，进 skill 的 `references/single-agent.md` 和 MCP prompt；主 SKILL 读表那一行加“or the share page after a commit”，instructions 的读那句加“the share page after a commit”。
  - 内容：
    - 行程仓有 `.tripwright/share/config.json` 时，写入 `effective` 后跑 `tripwright share publish`。
    - 退出 2 是扫描没过、没推，下一次 `turn` 带 `share_blocked`；照类别经 `propose`、`commit` 改记录，不把原值写进回答或记录，再发布一次。
    - 第一次建公开仓（`--create`）是对外发布，要先停下问用户：说清 GitHub Free 只能从公开仓发 Pages、仓里只有网页、仓名是什么，用户同意再建。
  - 核心步骤不加步。停点那条规矩从“anything sent to someone other than the user”改成“anything sent or published to others”，公开网页也算在内，不点具体命令。
  - 承诺 `main-agent-never-runs-the-command-line` 放行 `share publish`：它在提交之后跑，不查任何东西。新承诺 `share-page-after-commit` 核第 6 步的这几句。
- **接缝约定。** `share_blocked` 写进 CONTRACTS 3.9 和 schema（`$defs/share_blocked_notice`；`$defs/notice` 成为冲突通知与它二选一）。
- **later 一行。**
  - 第一段以 `first_part.later` 结尾时，`check_answer` 不把那一行算正文，Stop 钩子让这一轮结束（原来按 D-48 推第二段）。
  - `later` 之后还有字报 `ends_with_later`。第一段里的状态句报 `no_status_line`，规则见 `check_answer.STATUS_LINE`，服务端自己的“没核：……”行除外。Stop 钩子遇到这两种情况拦下。
  - `first_part.LATER` 是这一行的唯一出处，服务端和检查共用。
- **测试。**
  - `tests/agents/test_s4a2_share_guidance.py::test_after_a_commit_the_share_page_is_published_and_the_first_public_repository_waits_for_the_user`
  - `tests/site/test_s4a2_share_notice.py::test_a_blocked_share_page_leaves_a_contract_notice_without_values_until_a_clean_publish`
  - `tests/core/test_s4a2_later.py::test_a_first_part_may_end_the_reply_with_later_and_nothing_after_it`
  - 第 4 条的坏样本与对照都在最后这一条里。
  - 改了的旧测试：`test_a14_fixes` 的状态句坏样本现在也报 `no_status_line`。
- **主 SKILL。** 12,119 → 12,108 字节：“写法（对话里的回答）：”那一行标签删了，读表多半行。
## s4b：工具注解、交通查询跨进程共享、check 与 status 只读（2026-11-03）

依据：主会话 s4b 任务包第 1—3 条。

- **工具注解按实情标（`adapters/mcp.py`）。** ChatGPT 按 `readOnlyHint` 判断读写，没有这个注解的算写，Pro 只能用读取类工具。
  - 逐个工具在带远端的行程仓上调用，比较调用前后的索引库、工作区、HEAD 和远端 main，结果是：只有 `state`（各个 view）和 `explain` 什么都不改。
  - `query` 原来标成只读，但它的大多数 kind 都写：`transit`（含 `stops`）、`latest_departure`、`weather`、`geocode`、`nearby`、`saved`、`web_evidence` 每次都新记一条证据，提交并推送；`plan_day` 排一个作业；`check_answer` 的第二段通过时写本机的 `.tripwright/answers.json`（不提交）。只有 `opening`、`open_places` 和第一段的 `check_answer` 什么都不写。
  - 改：`query` 标为写（`readOnlyHint: false`、`destructiveHint: false`、`idempotentHint: false`、`openWorldHint: true`）；`commit` 换掉当前计划、`result` 的 cancel 停掉作业，这两个标 `destructiveHint: true`；`turn`、`propose` 不变。这一批不拆 `query`。
  - 代价：ChatGPT Pro 只剩 `state`、`explain`，不能查交通；Codex 对 `query` 要先批准（`codex exec` 不批准就拒，`default_tools_approval_mode = "approve"` 可放行）。
  - 测试：`tests/repo/test_s4b_tool_annotations.py`。
- **交通查询跨进程共享（`providers/common.py` 的 `SharedLookups`，`providers/transitous.py`）。**
  - 来源：11-03 02:1x 四轮自重放并行，每轮一个本地 stdio 服务，同样的查询各发一遍。本机共享的 720 次/24 小时计数用满（`ROUTING_BUDGET_EXCEEDED`），其余排队超时。
  - 改：上游的 JSON 回答按请求（路径加参数）存进共享目录（`TRIPWRIGHT_TRANSITOUS_SHARED`，默认在计数文件旁的 `transitous-shared/`），在有效期内各进程直接用。
    - 同一请求只有一个进程去取，其余等它的回答。
    - 所有进程的请求间隔至少 0.5 秒，同时最多 2 个。
    - 429 和 502—504 先停 1 秒、3 秒再试（`Retry-After` 不超过 10 秒时按它），每次重试都计入 720。
    - 锁都是不阻塞地取、取不到就短睡再试，被取消的查询不会留下锁；目录写不了就关掉共享，查询照常。
  - 有效期沿用各查询原来的内存缓存：路线 30 分钟，整天时刻表 6 小时，站名 7 天，步行时间 30 天。用的回答保留原来的抓取时刻（`fetched_at`、`valid_until` 按它算），不当成新的。`fresh` 的查询（存的计划事实复用前重查，D-57）只认这次查询开始之后取到的回答。
  - 测试：`tests/providers/test_s4b_shared_lookups.py`。
- **`check`、`status` 只读（`cli.read_repository`、`committer.read_status`、`Store(read_only=True)`）。**
  - 来源：命令行打开行程仓时先 `attach`：索引不在 HEAD 时重建，并把 HEAD 上比远端多出的带 `Tripwright-Op` 的提交恢复成待推的写入；命令结束时 `committer.stop()` 收手改、提交并推一次。11-03 08:08 在 my-travel 副本上跑 `check`，就这样把一个还没核完的合并提交推上了远端 main。远端已经前移时这次推送被拒，那条路不再重试。
  - 改：`check` 只用 `Repository` 读提交；`status` 以只读方式打开索引库，不重建、不 fetch、不 push、不提交，输出补上 `fetched: false`、`index_head`、`index_at_head` 和一句说明。同步只在 `sync` 和写入时做。
  - 测试：`tests/repo/test_s4b_check_reads_only.py`。

## S4e：Codex 也在下一轮出第二段，later 一行认字不认引号（2026-11-03，主会话；任务 D-93）

- **为什么：** 合并版（8dda6d99）上两轮合成数据干跑。Codex（gpt-6-luna）发完第一段就停、没出第二段，第一段前还多一句开场话：D-88 的回头条件。Claude Code 不给子代理那轮把 later 一行抄成英文引号，`check_answer`、Stop 钩子与重放脚本按原句逐字比，没认出，重放就没补发“继续”。
- **改了：**
  - `agents/core.yaml` 第 4 步：没有后台子代理的应用（含 Codex）都以 `first_part.later` 结束这一轮，下一句出第二段；第 1 步加“写任何文字之前先调 `turn`”。生成文件随之重出（instructions、steps、主 SKILL、AGENTS.init）。
  - `render/check_answer.py`：`is_later` 按字比，引号、空格与句末标点不算；四处原来的 `in LATER_LINES` 都换成它。测试 `tests/core/test_s4a2_later.py` 加一条：英文引号、直角引号、去掉句号都算结束，后面跟状态句照旧拦。
  - 钩子：用户回“继续”的那条消息算同一问的后半（`answers_later`：上一条真消息之后的最后一段文字以 later 一行结束）。first-part 钩子放行这时的查询；Stop 钩子把第一段当已发，没出第二段就催第二段。来源：answer-v4 上 Claude Code 不给子代理的干跑，“继续”之后 first-part 钩子拦了查询，宿主写了一段解释又重发第一段。测试同一文件加一条，带对照：上一段没以 later 结束时，新消息的查询照旧等第一段。
- **没改：** 第一段的内容与 `first_part.text`、`second_part_due` 的判法。

## S4h-a：服务端核这一轮的参数与形状（2026-11-03，阶段 4 出口判不合格，任务 D-96）

依据：阶段 4 出口判词（6 轮重放，0 轮合格；共同前提 A：宿主没照指令做，check_answer 照样放行；前提 B：“继续”那一轮不带原问题的 points）与 codex_r1、ns_on_r1、mcponly_r1、ns_off_r1 的工具记录。做法：错的参数在服务端拒收或认对，不再只靠 `next` 里的说明。每条有重放形状的坏样本与对照（`tests/core/test_s4h_server.py`）；源码退回基线（9eb3334）后 8 条测试都在坏样本处不过。

- **一、quick 场景问最晚时，查询不收硬节点（`Tripwright._keep_frame`）。**
  - 来源：codex_r1 第 1 问。`turn` 的 `next` 写了“不给 anchor_id、by、board_at”，宿主第一次照做，第二次自己加了计划目标 05:45 排队的 `anchor_id`，查成明早的框；第二段主选改成“明早 04:56 出门”，丢了存的“机场过夜”。
  - 改：`turn` 的上下文记 `frame {scenario, asks_latest}`。最近一次 `turn`（30 分钟内，“继续”那一轮带原问题的）是 quick 或 status、`first_part.asks_latest` 为真时，`latest_departure` 带 `anchor_id` 拒收；`by`、`board_at` 的时刻正好是某个硬节点当天的时刻，也拒收（同一个硬节点换个字段）。报错写明“只给 origin 和 destination”，结果里有今晚最后一班和之后要守的时间（`hard_node`）。
  - 为什么拒收、不忽略：约定里错的参数一律“报错并给改法”（A9 上车时刻当 `by`、A11 网上做的锚点）；照服务的框静默改查，宿主拿到的不是它要的，又不一定读说明。拒收不花查询额度。
  - 放行：用户自己说的时刻（不是任何硬节点的）照常按 `by` 查；plan 等其他场景、`turn` 30 分钟之后照常。
- **二、`turn` 的 `place` 和 `said` 对不上时拒收；认不出就明说（`packs.place_conflicts`、`packs.aliased`、`Tripwright._checked_places`、`_unknown_places`）。**
  - 来源一：ns_on_r1 第 2 问。用户说“Block Factory 还是什么的那个”，宿主把 at 的 place 填成计划里的另一家岩馆，第一段写“你在 <计划的岩馆>（你说的“…Block Factory…”）”，末班也从那里算。
  - 改：at、go、do 要点的 place 认出一处存的地点，said 按同样的规则（包含存的名字、拉丁字母相似度、下面的类别词）认出另一处时，`turn` 在收件之前拒收：`Invalid points: points.N: place … is the stored …, but its said “…” names the stored …: send place …`。什么都没记，同一个 `request_id` 可以再发。命令行 `first-part` 同样拒（退出 1）。said 认不出、认出两处或认出同一处，都不算冲突。
  - 来源二：mcponly_r1 第 1 问。宿主把 go 的 place 填成英文 “airport”，行程里存的是“维也纳机场 / Flughafen Wien”，什么都没认出，第一段只有“几点出发要查，第二段给”，没给计划里的车。
  - 改：`resolve_place` 最后按类别词认（`PLACE_WORDS`：airport、机场、Flughafen、aéroport…；station、火车站、Hauptbahnhof…）：先取 kind 就是这一类的唯一一处，否则取短名（中文名“（”前、原文名地址前）里有这一类词的唯一一处。短名不含括号里的说明和地址，所以“汉堡王（机场到达大厅）”不算机场。两处都像就不认。
  - 还认不出（不是存的地点，也不是坐标）：要点记 `unknown`（at 记在 `points.at.unknown`），`next` 开头写一句：哪些没认出、第一段没给它们存的车和时刻、也没拿计划里的地点顶替，存的地点 ID（前 8 个）可以改填，用同一个 `request_id` 再调。不拒收：用户可以在任何没存的地方。
- **三、“继续”那一轮带上原问题（`Tripwright._unanswered`、`_second_part_due`）。**
  - 来源：mcponly_r1 第 2 问。“继续”的 `turn` 回了 `second_part_due`，之后 `latest_departure` 仍从岩馆算，没按原问题的下一步（先去车站吃）。没走“继续”的 sub_on、codex 起点对了。
  - 真因：`packs.next_step` 取最近一份带 `points` 的上下文。“继续”那一轮的上下文存的是 `point_record(None)`（`given: false`，没有 `at`、`next`），比原问题的新，`next_step` 就取了它，什么都不改。
  - 改：带 `second_part_due` 的那一轮，上下文的 `points` 和 `frame` 照抄原问题的；`second_part_due` 的判法不变。之后的查询从原问题的下一步算，第一条的框也照原问题。再回一句“继续”还指同一问，也带它。
  - 顺带：同一时刻写的两份上下文，`next_step` 和 `_keep_frame` 取后写的那份（原来 `max` 取先写的；冻住钟的测试里会碰上）。
- **四、存的关门时刻可以写进第二段（`render.check_answer.stored_times`；`query.LAST_SAY` 的 closing）。**
  - 来源：判词“跨轮的毛病”第二点。第 1 问各轮把服务的“22:00 关门出来，走到 … 坐 22:16 的 5B”写进标“核过”的行，`checked_has_evidence` 以“时刻 22:00 不在这一趟工具查到的结果里”拦下，修法是“改成网上说法”；宿主删了时刻，“最晚几点出发”只剩车站上车时刻。
  - 查清：拦的是 `evidence_problems`。22:00 来自起点存的 `opening_hours`（`origin_closes`），不在任何查询结果里；服务自己的句子没说它是存的。
  - 改：服务句子写“22:00 关门出来（按存的营业时间）”。`checked_has_evidence` 不把“自己带标的时刻”算作核过的说法：时刻后面、下一个时刻或分句结束之前（括号里的标注到右括号）有“存的、存档、没核”之一（英文 stored、archived、not checked），这个时刻不要证据。没带标、又正好是这一问查询起点存的关门时刻（`frame.closes`），照样拦，修法改成“照 latest_departure 的句子写“22:00 关门出来（按存的营业时间）”，时刻留着”。`leave_before_closing` 的修法同步加标。
  - 没带“没核”：起点的营业时间可能有网页出处（`hours_source`），这次查询没核它，存的是实情；“存的”已经是标。
- **五、第一段“（20:51 查过）”早于 21:01 的问（`providers/common.cached`、`storage.pinned_since`）。**
  - 真因（查实）：不是 UTC 显示。ns_on_r1 第 1 问的查询在服务钟 21:04:45—21:05 做，证据 `fetched_at` 是 19:51:35Z，即 20:51 CET，比查询早 14 分钟；按 UTC 显示会是 20:05。差的 14 分钟正好是墙钟上 sub_on_r1 做同一查询（13:33:36Z）到 ns_on 做它（13:47:15Z）的间隔。s4b 的共享答案按墙钟上的年龄倒推抓取时刻（`fetched = 服务钟 − (墙钟现在 − 墙钟抓取)`）；重放把每一轮的服务钟钉在 21:01，别的轮在更早的墙钟时刻抓的答案，就被放到这一轮第一问之前。mcponly 的 20:35（早约 27 分钟）、ns_off 的 20:46（早约 19 分钟）同理，往回推都落在墙钟上 ns_on 第 1 问发查询的 13:47Z。
  - 改：钉住的钟只从钉住那一刻起和墙钟同步（`storage.pinned_since`：`TRIPWRIGHT_NOW_SET_AT`，否则进程第一次读钟的墙钟时刻）。钉住之前抓的共享答案，按这次查询的时刻算；钉住之后抓的照 s4b 按年龄倒推。没钉钟时不变：别的进程 10 分钟前抓的答案，照旧说 10 分钟前，`valid_until` 也照旧。
  - 为什么不改显示：没钉钟时，显示的就是数据真实的抓取时刻，可以早于这一问（同一台机器上别的会话 30 分钟内查过同一程），这是实情。错只在重放：别的轮在另一条钉住的时间线上抓的，放到这一轮里没有意义。
- **测试。** `tests/core/test_s4h_server.py`：
  - `test_a_quick_latest_question_refuses_a_lookup_that_names_a_hard_node`
  - `test_a_place_the_users_words_contradict_is_refused_before_anything_is_recorded`
  - `test_a_word_for_a_kind_of_place_names_the_one_stored_place_of_that_kind`
  - `test_a_place_not_recognized_is_said_and_never_taken_for_the_plans`
  - `test_the_lookups_after_go_on_start_where_the_continued_question_goes_next`
  - `test_the_stored_closing_time_may_stand_on_a_checked_line_when_it_says_so`
  - `test_an_answer_another_replay_fetched_earlier_is_not_dated_before_this_replays_question`
  - `test_on_a_pinned_clock_an_answer_fetched_after_the_pin_keeps_its_age`
  - 改了的旧测试：
    - `test_a16_fixes` 第 3 条：关门句与 `leave_before_closing` 的修法加“（按存的营业时间）”；
    - `test_a12_first_part` 的 a12b 一条：原话里有“机场”时，现在按类别词认出存的机场（不拿原话当地名的保证不变）；“几处都像或都不像就不写地名”改用不含类别词的原话核，另加一条“place 填‘维也纳’、原话说机场”认出机场。
- **限制。**
  - 第一条只看最近一次 `turn`：宿主不调 `turn` 就查，判不出。用户自己说的时刻正好是某个硬节点的时刻时，`by` 也被拒；这时服务的框已经给了它（`hard_node` 或“你计划留的余量”那行）。
  - 第一条没做判词机制补充 1 的另一半（第二段主选的车不在今晚的框里、计划存着机场过夜时报出）：框错的查询拒收之后，第二段拿不到明早的框。
  - 第二条的冲突只认得出“said 里像另一处存的地点”。said 什么地点都没说、宿主填了计划里的地点（假设用户在那），服务端分不出。
  - 类别词只有机场和火车站两类；地铁站、公交站名太多，不收。
  - 第四条只放行带标的存的时刻；宿主把存的时刻写成“核过”而不带标，照旧拦。

## S4h-b：命令行少拦人、分享页住处段只说先后、工具连得慢时先等（2026-11-03，主会话 191cd345）

依据：阶段 4 出口判词（第 9 次质量判）前提 C、机制补充 4 与 6、出口第 17 条“几点到住处”段；坏样本 runs/s4_ns_off_r1。S4h-a 同步改服务端文件（service.py、query.py、render/），互不相碰。

- **命令行少拦人（`cli.py`）。**
  - `first-part --points` 收宿主自己写的 kind 近义词（S4h-b：deadline、route、time、latest_departure、transit、departure → go；food、meal → eat；event、activity → do；place、where → at；大小写与空白照收，`place: ""` 当没给），stderr 出一行“read kind 'x' as 'y'”说明收成了什么；stdout 仍只有 JSON（S4a 不变）。认不出的 kind 仍退出 1，报错里除原有示例外，再给一条改好的完整命令（`try: tripwright [--db|--repo] first-part --text … --points '[…]'`，未知 kind 换成 other、空 place 去掉），照粘贴即可运行。坏样本：off_r1 第一问宿主填 departure/route/time，连报三次错、第一段拖晚。
  - `query check_answer` 没带 `--request-id`（也没带 `--question`）时，CLI 取同一会话最近一次 turn 的 request_id（事件表里最近收到的非 system 消息；与服务的 30 分钟回退同一“同时刻取后录”规则），stderr 写明用的哪一条与收到时间；一条 turn 都没有时报错退出 1，报错里给带 `--request-id` 的完整命令例子（并提 `--question`）。给了 `--question` 时不自动取（那是 D-48 设计的“turn 没记下时给原话”路径）。坏样本：off_r1 两问的 check_answer 没带 request_id，回“本问原话不明，吃的检查没做”照旧放行，过的不是这一问的检查。
  - 测试：`tests/core/test_s4hb_cli.py`（16 例，改前全红）；`test_a17_fixes` 一行随 read_points 新签名（返回 (points, 说明)）更新。
- **分享页构建不出住处进出的时刻（`site/privacy.py`、`site/share.py`、`site/templates.py`）。**
  - 规则：时间线里起点或终点是住处的那一段，不出分钟级时刻（HH:MM，含全角冒号）、步行分钟、到站时刻，也不出 “from your door”“N minute walk”“门口/楼下” 这类和住处连用的说法；只说先后（“回住处 / Back to the lodging”+线路）。公共地点之间的时刻照留。
  - 判“这段贴着住处”：块的 place 是住处；或块是无 place 的移动块（或 kind travel），而相邻块的 place 是住处、或它自己的 text/mode/note 提到住处（重写后的“住处/our stay”）、或它是当日基于住处的第一个移动块。带公共 place 的块（参观、吃饭）不算，即使它的 note 拿住处当地标。
  - 实现：`privacy.py` 加 `MINUTE_TIME`、`WALK_MINUTES`、`AT_THE_DOOR`、`STAY_WORDS` 与 `stay_segment_dirty`（中英一起查；提到住处的字句再带裸“N 分钟”也算）；`share.py` 的 `block_view` 对这种段把 start/end 置空、walk_minutes 不出、text/mode/note/title 里有这些内容的整句不出现（两语言任一脏即整句不出）、mode 被丢弃时线路（line）仍保留、标题退到“回住处/Back to the lodging”或“从住处出发/From the lodging”；模板的时间跨越加空守卫。
  - 测试：`tests/site/test_share.py` 用合成计划（住处→某站→景点、景点→住处）核中英两种输出（改前红），加一条对照（干净的“回 住处 放行李”照旧可见）。
- **工具连得慢时不转命令行（`agents/core.yaml` 第 1 步）。**
  - 第 1 步末尾加一句：“While Tripwright's tools are still connecting, retry every few seconds (up to about 30) before using the command line; never write about connections.”。生成物重出（instructions 3,585/3,600 字节、steps.json、主 SKILL、MCP prompt、AGENTS.init），`build --check` 过。主 SKILL 的字节钉因这句从 12,171/12,130/12,119 提到 12,225（三处守护测试各注一句 S4h-b 后改钉）。
  - 测试：`tests/agents/test_s4hb_core.py`（2 例，改前红）。
- **契约：** `docs/CONTRACTS.md` 3 节加一组 S4h-b（命令行两条与分享页一条；无形状变化，机读件与样本不动）。

## S4h2：第 1 问两段走法对上；“按时刻表”一句由检查要求（2026-11-03，S4h 自重放，任务 D-97）

依据：主会话 S4h 自重放（`evidence/s4h_selfreplay_20261103.md`）“还在出现的四类”第 2、3 类；坏样本 `s4h_mcponly_r1/turn1.reply.md`（sub_on、ns_on、维也纳 codex 同形）与 `s4h_ns_off_r1/turn1.reply.md`、`turn2.reply.md`。每条有坏样本与对照（`tests/core/test_s4h2_server.py`）；源码退回 3f24c99 后 3 条都在坏样本处不过。

- **一、计划那班车的句子说计划的走法还用不用（`query.plan_rides`、`planned_leave`；`LAST_SAY` 的 `plan_way`、`plan_way_missing`）。**
  - 来源：第一段照计划写“21:45 从岩馆出门，22:13 到总站，坐 22:42 RJX”（`packs.planned_way`，存档没核）；第二段的主选是 `latest_departure` 的“计划里的 22:42 RJX 19959 赶得上：22:00 关门出来（按存的营业时间），走到 Brigittaplatz 坐 22:16 的 5B”。两条都赶得上，可句子没交代第一段那条。两边各取各的：第一段取计划存的交通块，第二段取查到的最晚一条。
  - 改：`plan_rides` 拿到查询起点。计划里有从这个起点到那班车上车站、当天的交通块（取最晚开始的那块），它的出门时刻还没过、又和查到的最晚出门差 5 分钟以上时，句子后面接一句：
    - 查到的连接里有在它前后 5 分钟出门、也坐这班车的：“；计划的 21:45 出门那条（存的走法）也赶得上，想早走就照它”；
    - 没有：“；计划的 21:45 出门那条（存的走法）这次没在结果里，照这条走”。
  - 为什么接在这句上：这句就是第二段的主选句，宿主照抄；关系写在同一句里，宿主删不掉一半。主选仍是最晚那条，用户问的是“最晚几点出发”。
  - 为什么标“存的走法”：21:45 是计划存的出门时刻，查到的连接出门时刻可能差一两分钟；带标后，宿主把这句放进标“核过”的行，`checked_has_evidence` 不要求 21:45 有证据（S4h-a 的 `stored_times`）。
- **二、`check_answer` 第二段加 `first_times_followed`（`render.check_answer.followed_problems`；`first_line_times` 从回放检查的 `first_line_problems` 抽出共用）。**
  - 带 `request_id` 时，取那一问 `turn` 存的第一段原文（`first_text`；“继续”那一轮取被续那一问的）。第一行里第一个时刻（不算钟点和“HH:MM 查过”）是这一段要用户照着做的时刻，比如出门、上车。第二段既没再写它，也没有一行说改了（“第一段”“改成”“换成”“改为”），就报，修法给“照 latest_departure 的句子写第一段那条还用不用”或“和第一段不同：…”。
  - 没有那一问的第一段（不带 `request_id`、只走命令行 `first-part`）时不核。
  - 为什么只看第一个时刻、不照回放检查要每个时刻都再写：回放检查的那一项在自重放里误报和真错混在一起（自核记录“没核到的”）；到站时刻 22:13 不再写不算错，出门时刻没交代才是两套走法。
- **三、`check_answer` 第二段加 `timetable_said`（`timetable_problems`）。**
  - 来源：只走命令行的一轮，宿主自己写第二段，没写“按时刻表，不是实时”，`check_answer --question` 照样放行；走 MCP 的几轮是照抄了服务的 `note` 才有。
  - 改：第二段有一行（不是标题）带交通词、又写了这一趟查询（`transit`、`latest_departure`）里的时刻，而全文没有“按时刻表”“不是实时”“非实时”“是实时的”（英文 timetable, not real time、not live 一类）时，报出，修法给 `note` 的原句。MCP 与命令行走同一个 `check_answer`。
  - 只认这几种说法，不认单独的“实时”“时刻表”：服务的“怎么查”一句里就有“实时晚点、站台…时刻表里没有”“看实时时刻”，不能算说了。
- **测试。** `tests/core/test_s4h2_server.py`：
  - `test_the_plans_train_sentence_says_whether_the_plans_own_way_still_holds`
  - `test_a_second_part_that_drops_the_first_parts_time_without_saying_so_is_refused`
  - `test_a_second_part_with_looked_up_rides_says_they_are_the_timetables`
  - 改了的旧测试：
    - 第二段样本补上时刻表一句：`test_a10_frame.second_part`、`test_a8_transit_facts.second` 末尾加一行；`test_a6_check_answer.GOOD_SECOND` 和两处现写的样本（`test_a6` 的窗口一条、`test_a7_candidates_places` 的大巴一条）并进原有一行，不加行，免得改动后面行号；
    - 计划那班车的句子后面多了“；计划的 21:45 出门那条（存的走法）这次没在结果里，照这条走”：`test_a16_fixes` 第 3 条两处、`test_s4h_server` 第 4 条。这些行程的计划里有 21:45 从岩馆去总站的块，假数据里只有一条接驳。
- **限制。**
  - 第一条只在计划存了从查询起点出发的交通块时说；第一段若写的是别的（只有“计划里是今晚 22:42 在 X 坐 Y”），句子不变，第二条照样核第一个时刻。
  - 第二条只核第一行第一个时刻；宿主把它写进别的意思（例如“21:45 之前别走”）也算提到了。
  - 第三条不核说的是否和数据一致（数据是实时的却写“按时刻表”）。

## S4h3：命令行取最近一次 turn 只认最近 30 分钟（2026-11-03，主会话 191cd345）

依据：S4h2 自重放 s4h2_ns_off_r1。只走命令行那一轮，这一问没有 turn（宿主用的是命令行的 first-part，它不记），命令行的 `query check_answer` 不带 `--request-id`、`--question`，S4h-b 的“取最近一次 turn”取到了评测仓里 10-31 导入时的 turn，于是拿一个没人问过的问题核这份草稿：吃的检查没跑，宿主发出了没有具名吃饭地点的第二段。

- **改（`cli.py` 的 `latest_turn`）：** 只取最近 `RECENT_QUESTION_MINUTES`（30 分钟，服务自己的“最近一条用户消息”回退用的同一个窗口）内收到的非 system 消息；没有就照旧报错，报错里给带 `--request-id` 的命令与 `--question`。
- **测试：** `tests/core/test_s4hb_cli.py::test_check_answer_never_takes_a_turn_of_another_hour`（只有 21:01 的 seed turn、钟到 23:30：退出 1；旧代码上这条红）。

## S5：方法论 v0.5 三点（2026-11-03，worker s5-impl；主会话 191cd345 派）

依据：用户 Q42 的 R-147—R-149（`METHOD.md` v0.5 第 2.9、3.2、6.1 节）；现状见 s5 盘点（`gap-inventory.md`）。约定见 `docs/CONTRACTS.md` 3.10（v2.0）。

- **一、agent 的不取代用户的（`ledger.apply`；METHOD 2.9、4）。**
  - 来源：盘点 1.1 的探针。同 key、同人、同范围时，后到的 `agent_inference` 条目把用户明说的条目改成 `superseded`（`ledger.py` 原 1043—1046 行，取代时不看来源）。
  - 改：两层按 `source.basis` 分。用户层是 `user_explicit`、`direction_set`（`consider.USER_LAYER`），其余是 agent 层。同一对象（同 key 或声明、人与范围相交）上，agent 层的新条目不取代、不作例外、不使用户条目待定（旧条是 must 时也不开问题）。两条都 `active`，agent 那条记 `relations.conflicts_with_user`。同一条消息里的一对（同批，或先记 agent 的、后记用户的）同样处理。用户层的新条目照旧取代用户层与 agent 层的旧条目，“用户自己改口”不受影响。
  - 连带改：跨层的一对不再记 `possible_contradiction_with`。不改的话，agent 每发回一条推断，就和同一消息里用户的每条各生成一个“可能矛盾”的问题，进窄包的必需问题组，快问包更易超 1,800。
  - 不改：“定了基本不变”的种类照旧先走 `stable_refusal`（agent 推断碰上就进 `not_recorded`，问用户），比“并存”更严。实况（facts）的取代照旧不看来源：查询得到的新实况常该盖过用户早先说的，要分层得另定规则。
- **二、`consider` 每条标来源层，截断先留用户的（`consider.build`、`consider.layer`；METHOD 3.2）。**
  - 改：每条加 `layer` 短词：用户说的、我的推断、我的默认、我定的、计划里的。产品地区表的条目写“我的默认”。agent 条目带 `conflicts_with_user`。
  - 截断：必须项与用户层条目不计入条数上限、永不截；其余（agent 的、计划的、地区表的）按种类次序填到上限。这照 METHOD 3.2“用户层全量加这一范围最多若干条”。没取“按层排序、仍按上限硬截”，因为用户层自己超上限时，用户的偏好仍会因为种类次序排在用户规矩后面被截。
  - `next`：列出 agent 层条目时加一句“不说成用户要的”，有冲突时加一句“照用户说的”。`more_how` 指向 `explain(record_id)`。
  - 窄包与它的 1,800 预算不动：来源层只加在 `consider` 上，包的渲染没改。
- **三、按编号取单条（`service.explain`、`service._record_card`）。**
  - 改：扩 `explain`，不加工具，工具仍七个。`record_id` 是账本记录（条目、实况、决定、待核、待定问题）时，结果加 `record`：原文与引句、来源层与 basis、原话出处（`said`，取自 `packs.message`，长期条目导入的也有）、状态、被谁取代、取代链末端现在生效的那条（`current`）、关系、让给它的 agent 条目（`conflicted_by`）。
  - `version_usage` 不给 `version` 时只回最新一版。每版只带这一条的用法、那版约束包带没带它（`carried`）、点名它的变更行，不带整份约束包。不是账本记录的编号照旧。
  - 只经 MCP、不读仓文件，远程同样可用。
- **四、云端写回（`agents/core.yaml` 第 6 步、`service.turn`、`ledger.offered`、`usage.account_usages`、`packs.compile_pack`、`Store.attempt`；METHOD 6.1 第 3 步）。**
  - 第 6 步改为“用户这句定了或改了的，和你推断或选了的（basis `agent_inference`），放进同一份 `propose` 再 `commit`；两者之间不另记”。这句随生成物进 instructions、`steps`、主 SKILL 与 AGENTS.md。
  - 为了守住字节上限，同时删了别处的字：开头一句、“停下问用户”一句、读长规则一句的括注（保留“the share page after a commit”）。另外删了主 SKILL 读表两行与第 3 步注里“it reads the candidate itself, so”。
  - 字节数：instructions 3,586（≤3,600，`build --check` 过）；主 SKILL 12,217（≤12,225）。
  - `turn` 加 `write_back {say, record}`：一句提醒和要发回的原形（来源是本问事件号，basis `agent_inference`，`quote` 空）。不占 instructions 的字节。
  - agent 层的软条目（agent 层且 `prefer`／`optional`，`ledger.offered`）不是必需条目。计划提案的 `usages` 里可不给（给了照常核）；宽包标 `non_required`，不进 `dispositions`，宽包仍不因预算省记录。窄包里它们是第三种非必需记录（CONTRACTS 3.1），放不下时先整批走，再轮到用户条目缩成编号行；两条都是这种条目的可能矛盾问题同样非必需；它们不进偏好次序的配对。must、forbid 无论谁记都必需。
  - 为什么窄包也要改：写回的推断按范围进窄包。实测“用户 3 条偏好、agent 发回 2 条推断”的快问包：不改时 1,892 token，用户的条目只剩编号；改后 1,626，用户的原文都在。基线是 1,775，那时推断还取代了用户的“轻松一点”。
  - `turn.record` 出错不拒整条 `turn`：形状错、多给了 `decisions` 一类、账本拒，都照收原话、照出第一段。`record` 整份不记：新加的 `Store.attempt` 是存档点，回滚不留半条；`_record` 里原来那次嵌套写入也改用它。`recorded.not_recorded` 加 `{part: "record", records, reason, fix}`，`reason` 是原拒绝原文，`fix` 说改好后放进这一问的 `propose`；`next` 加一句。
- **测试。** `tests/core/test_s5_layers.py`（14 条；每条先在改前的代码上红，记录见 REPLY）。
- **改了的旧测试（都因第一、二、四条的语义变化，测试原意保留）：**
  - `test_ledger.py` 三条、`test_b3a_regressions.py::test_preview_does_not_mutate_relations_or_duplicate_supersedes`：取代、例外、撞 must 的新条目原来没填 basis（默认 `agent_inference`），而它们是用户的话。现在补上 `basis: user_explicit`。
  - `test_m1_consider.py`：“常变的被取代并说出来”原来用 agent 推断去取代用户的话。现在先断言推断不取代，再用用户的话取代。“长列表截到上限”改用 agent 的推断条目，因为用户的条目不再截。
  - 坏 `record` 原来整条拒 `turn`。下面这些现在断言原话已收，同一句拒绝原文出现在 `recorded.not_recorded[].reason`：`test_b3g_admission.py`（10 个参数）、`test_b3g_mcp.py`、`test_a5_cli_and_stdio.py`、`test_a5_latest_and_evidence.py`、`test_b3f_three_paths.py::test_reports_and_corrections_go_through_propose_not_turn`、`tests/repo/test_memory.py::test_the_users_answer_is_recorded_through_turn_and_a_not_used_entry_stops_applying`。
  - `tests/contracts/contract_checks.py::non_required` 加第三种：窄包里 agent 层的软条目。
- **限制。**
  - 没填 basis 的条目算 agent 层。宿主若把用户的话记成不填 basis，这些条目会被标“我的推断”，挡不住 agent 推断的覆盖，也不再是宽包必需。提醒写在 `write_back` 和 `turn` 的工具说明里，服务端不核。
  - 同一 `request_id` 不能带改好的 `record` 重发（会报复用），只能放进 `propose`。没有提案的快问，写回就多一次只改账本的 `propose`＋`commit`，在第一段之后、第二段之前。
  - 包里的“细节”行仍是行程仓路径（盘点 tw-6），云端读细节要靠 `explain(record_id)` 与 `state(view='day')`。
