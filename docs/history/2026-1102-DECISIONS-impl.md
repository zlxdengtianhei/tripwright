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

