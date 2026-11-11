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

