# 接缝约定 v1.9：快照、写入、包与回答、文件归属

> 状态：冻结 v1.9（2026-11-02 12:1x 按 D-67 与 A10 交回在 3.9 加一条：服务端定“最晚几点出发去 X”的框、`turn` 的 `first_part`、`check_answer` 的 `frame_matches_server`；只加字段与检查，不改已有形状）；v1.8（2026-11-02 01:3x 按 A8、B3k、R1、v2g 交回加 3.9 与写域两处，02:1x 按 M1 交回补 3.9 一条与 consider 归属；v1.7 起于 2026-11-01 13:4x 按 D-52 与 B3j 加提交处的锁检查与余锁扩大；v1.6 起于 05:5x，06:3x 按 D-46、07:1x 按 D-47 与 D-48 改两个钩子的拦法与第二段标题检查；09:5x 按 D-49 与 B3g 加改锁的准入与回执；10:5x 按 D-50 与 B3h 加改锁的先后；11:5x 按 D-51 与 B3i 加完整覆盖才取代；主会话按 D-44、D-45 与 A7 交回改 3.6：固定钟、`check_answer` 四条新检查与 `passed`、`latest_departure` 的 `candidates` 与 `resolved`、`open_places`、地点营业时间、两个钩子；第 4 节 settings 带钩子）；v1.5（2026-11-01 04:11，主会话按 D-43 与 A6 交回在 3.6 加 check_answer 与 latest_departure 精简）；v1.4（2026-11-01 03:12，主会话按 D-42 与 A5 交回加 3.6 查询与证据）；v1.3（2026-11-01 01:4x，主会话按质量判词与 A2 交回加“现在”：包头可选 `now`；turn 结果与 state 每个视图顶层带 `now` `{at, date, time, weekday, timezone, text}`；硬节点记录带 `deadline`（external、plan、unmarked；计划里的锚点 `Anchor.deadline` 可选 external 或 plan，锁的字段表归“用户选择”，A3；锚点标了就照用，没标时有核验证据的算 external，其余 unmarked）、`stored` `{version, at}`、`checked` `{source, at, evidence_id, valid_until, stale}`、`refresh_hours`、`may_have_changed`，turn 的 `hard_nodes` 另带 `passed`；见 `docs/DECISIONS-impl.md` 的 A2 节）；v1.2（2026-11-01 00:24，主会话按用户 Q11 与 D-39 加用户仓 `.claude/settings.json`，生成者 C）；v1.1（2026-10-31 18:5x，主会话按 B、C 交回与用户 Q4、Q6 改：交通行与地名行带搜索词、记忆数据 3.5、宽包原句带时刻与性质、快问预算 1,800、生成说明两条例外、用户仓带 skill；v1 由接缝负责人 contracts_opus_20261031-1730 冻结）｜依据：设计 v0.2.1 第 5、6、8.4、9、13 节，反方审查第 1、4 节｜机读件：`tests/contracts/schema.json`（形状）、`tests/contracts/manifest.json`（写域）｜样本：`tests/fixtures/contract_sample/`（虚构行程，逐字预期）｜改约定：本页、机读件、样本在同一提交里改，具名通知受影响的块（经主会话）。

A（仓、状态与快照）、B（渲染与输出）、C（角色、步骤与装配）按本页对接。B、C 先对样本写，不等 A。带 † 的字段名取自 B3c 交回后的提交 2e3242fe（例如 `facts_only` 已改名 `ledger_only`，新增 `competes_with`、`crowding`）；B3c 若再改，以它的代码为准。

**术语，全仓同一用法：**
- **已收件**：写入进了发件箱，本轮回答已经按它答。不等于已生效。
- **已生效**：写入进了约定的正本分支。只有这一状态能对用户说“记下了、存好了”。
- **核过**：第二段标题写“核过的安排”，当且仅当深核结论为 `passed`（取回时新鲜、宽包各组 complete、逐条处置没有 unaddressed、没有冲突）且计划提交被接受。否则标题写“安排（没核完）”。
- **草稿**：计划类写入到“已生效”之前展示的走法，标题加“（草稿）”。
- 写入不叫“成功”，工具只返回状态。

## 1 有效快照

**1.1 组成。** 每次读取（turn、propose、作业、result、commit）绑定一个 `snapshot`（`schema.json#/$defs/snapshot`）。ctx、draft、job、收件箱消息都存整份快照；现有的 `base_version`†、`revision`† 保留，值与快照里的同名量相等。
- `plan_version`：计划版本号，即现有 `base_version`†、`versions.number`。
- `plan_version_id`：该版本记录的编号，即现有 `versions.id`。两个写者会各自产生“版本 4”，编号不会重。
- `ledger_revision`：现有 `Ledger.revision()`（流水条数）。
- `ledger_digest`：按顺序把流水编号用 `\n` 连起来算 sha256，取前 16 位，写成 `sha256:<16 位>`。重放后条数可能相同而内容不同，摘要能区分；DECISIONS-impl 早已预留按内容判。
- `raw_watermark`：原话水位，`count`（按收件序的原话条数）、`last_event`、`last_received_at`。
- `repo`：`canonical`（约定的正本分支，如 `origin/main`；没有远程时是本地分支名，不带 `/`）、`head`（索引所据的提交）、`head_checked_at`、`unsynced_ops`（处于已收件、待同步的操作号）。单库模式为 null。
- `taken_at`：读取时刻。

**1.2 新鲜度只有一条规则。**
- 内容键 = (`plan_version_id`, `ledger_digest`)。内容键变了 → **过期**（stale）。版本号与修订号一起返回，给人读。
- 内容键没变、原话水位前进了：水位之后的每条原话，要么有流水引用它（那时内容键已经变了），要么宿主声明它不带账（ack）。还有没归属的 → **未定**（unsettled）。
- `repo` 只说明同步到哪：头前进、`unsynced_ops` 变少，都不让结果过期。外来新头若改了计划或账本，内容键自然会变。
- 任何账本改动都让在跑的作业过期，与 propose/commit 现在拒“Ledger changed”一致。以后按范围精细判，result 与 commit 必须同时改。

**1.3 何时原子换入。**
- 本次调用带来的写入（原话、实况、条目）与收件、编包在同一个事务里进索引。返回的包已经含它：用户在这条消息里说“没有箱子”，这条回答的包里就有。
- 见到新的正本头（webhook、轮询、fetch）：在旁边建“新头＋未生效操作重放”的完整一代索引，建完在一个事务里换入。重放判冲突的操作在同一事务里移出，并写冲突通知（第 2.3 节）。
- 计划提交在重放时发现基础版本变了（A13，D-75）：不拒，在新版本上重做。把这次提案相对它的基础版本改了什么，按计划里的对象身份（天按日期；人、地点、预订、票价、硬节点、方案、块按 ID；方案里的分组按人）作用到新版本的计划上；只有一边改的字段取那一边，两边都改且改法不同的字段才算冲突。不做文本合并，仍是按操作重放。重做后的计划照常在新头上过提交的全部检查（硬约束、必须项锁 D-53、用户决定），过不了照旧拒（`replay_refused`，`detail` 以 “Redone on the plan changed elsewhere, then refused by its checks:” 开头）。基础版本没变、只是账本在别处动了的计划提交，也在新账本上重查后提交，不再因此拒。重做成功的版本带 `redone {on_version, on_version_id, from_version, note}`，`changelog` 每条带 `redone`，note 都是“在别处的改动之上重做了这次改动”；commit 等到重放完成时，回执就是重做后的版本（带 `redone`）。
- 不经工具改过的计划文件（`trip.yaml`、`plan/**`，A15）：在打开租户、见到新头（sync、拉取后的重建）、每次写入（turn、propose、作业证据）前和每次提交前先收。看的是计划文件和它们最后记下的版本（工作副本 `plan/versions.jsonl` 的最后一行）是否一致，所以已经在提交里的手改（Git 网页、别的 agent、手工 commit）和只在工作副本里的一样算。过得了检查的收为新版本（`origin: external_edit`，`changelog` 每条 `by: user`、basis `{edit: external, author}`）；工具在那之后已有更新的版本时，按上一条的对象身份在新版本上重做，两边改法不同的字段不收。过不了的不收、不覆盖：这些文件在之后的提交里保持原样，版本不变，首屏和每次 turn 的通知写“有未入版的手改：…”（点名文件、哪一项、为什么，以及“版本没变，手改留在文件里；改好或撤掉后，下次写入时收进来”），改好或撤掉后自动关。已收的手改（`manual` 操作）在重放时同样在新版本上重做。
- 每次调用只读一代。读不到“新头已到、未生效操作还没重放”的中间态。

**1.4 取回与提交怎样核。** `result` 每次取回都把作业的快照和当前快照按第 1.2 节比，不只看作废时打的标（`schema.json#/$defs/result_response`）：
- 过期：`status: "stale"`、`changed: ["plan"|"ledger"]`、`result: null`、`notice: "已过期，需重算"`。不交旧结论。
- 未定：`status: "unsettled"`、`unsettled_events: [...]`、`result: null`。宿主有账就先记账（结果随即过期，重新 propose）；没账就调 `result(job_id, ack_events=[...])`。ack 记在作业上，留审计。
- 新鲜：`status: "completed"`，带结果。结果里 `verdict` 为 passed、conflict 或 incomplete，`usages`† 是逐条处置。
- 计划类 commit 用同一规则：内容键变了拒；有未定原话拒，提示先 ack 或记账；认该草稿深核作业上的 ack。只改账本的 commit 不因未定原话被拒，用户说的实况不被扣住。propose 照现有，只核版本与修订号。
- 现状差（A 改）：`jobs.result` 只看作废标记；`jobs.invalidate` 只比版本号；现有的（含 B3c 的）ledger-only commit 仍把版本号加一。按本节核两种实现都成立。

## 2 写入结果

**2.1 四个状态**（`schema.json#/$defs/write_op`）：
- `received` 已收件：操作与回执在同一事务进了发件箱，并已进索引。原话、实况、条目本轮就用；计划类展示时标“草稿”。
- `pending_sync` 待同步：已写成本地仓的一个提交（trailer 带操作号），正本分支还没有。
- `effective` 已生效：正本分支的头包含这个提交。去掉“草稿”。
- `conflict` 冲突：重放时被拒，附 `reason`（`plan_base_changed`：两边改了计划里同一对象的同一字段且改法不同，或没有更早的计划可重做；`record_status_race`；`replay_refused`：重做后过不了检查等）、`detail` 与通知号。同一条记录被两个写者改成不同状态时，后到的转成待定，附 `question_id`。

转移：received → pending_sync → effective；正本分支是本地分支时 received → effective；received 或 pending_sync → conflict；pending_sync → pending_sync（推送被拒、重放成功、换了本地提交）。effective、conflict 是终态。每次转移记 `at`；pending_sync、effective 记提交号。

**2.2 操作号与幂等。** 一次写工具调用是一个操作，编号 `OP-<YYMMDD>-<随机>`。幂等键：turn 用 `request_id`，commit 用 `idempotency_key`，作业写入用作业的幂等键。同键同输入返回原操作号与当前状态，同键异输入拒（沿用现有）。turn 与 commit 返回 `write: {op_id, state, unsynced}`，`unsynced` 是处于 received、pending_sync 的操作数；propose 不写仓，不返回。操作还没生效、且服务器推不上去或提交不了时，另带 `blocked: {kind, since, hint}`（kind：auth、protected、network、behind、other、commit），推送或提交成功后不再带。`state(view="sync")` 给正本头、未入仓的写入与头的新旧，也带 `blocked`（同上）；没有远端仓时带 `storage: {kind: "local", note}`，note 写明文件只在这个卷或这个目录里，备份要自己做。提交说明带 trailer `Tripwright-Op`、`Tripwright-Request`。运行库丢失后，从 trailer 恢复 pending_sync 以后的状态；还在 received 的操作会丢，丢失窗口按实际未入仓水位报。

**2.3 冲突怎样通知已取回结果的消费者。** 判冲突的同一事务里写一条通知（`$defs/notice`）：`kind: "write_conflict"`、`op_id`、`shown`（草稿号、作业号、取回时刻）、`reason`、`replan`（日期、人）、`user_line`。之后每次 turn 在 `notices` 里返回它，直到一个基于新头的计划提交到了 effective。B 把 `user_line` 渲染成回答第一行。两边改同一项时它点名这一项和两边的值并问要哪个（例：“两处改了同一项、改得不一样：2030-06-10 的「Oslo Art」（d1-art）的开始时间：别处改成了「10:15」，这次改成了「10:45」。要用哪个？”）；其他情形照旧（例：“上次的安排没存进去：别处记了新的情况，需要重排。”）。

**2.4 计划类结果的草稿标记。** commit 返回 `write.state`；第二段的回答数据带上它，state 不是 effective 就加“（草稿）”。A 可以让 commit 最多等 5 秒（推荐默认）再返回，常见情形就直接是 effective。

## 3 机读包与回答数据

**3.1 机读包**（`$defs/pack`）。字段：
- `pack_id`（沿用 CTX 编号）、`kind`（narrow、wide）、`scenario`、`header`（人、日期与时段、时区、地点、通道、编于何时；v1.3 起 turn 实时编的窄包另带可选 `now` `{at, date, time, weekday, timezone}`，行程时区的当前时刻，渲染时包头第一行以“现在 HH:MM（时区，MM-DD 周X）”开头；写进仓的预编包不带）、`snapshot`。
- `required`：六组 `items`、`facts`、`decisions`、`hard_nodes`、`checks_due`、`questions`，每组 `{status, ids, reason}`。status：`complete`（适用的都在，原文都在包里）、`partial`（编号都在，有的原文没展开，要按编号取）、`unknown`（适用集本身没定，`reason` 写原因）。必需的只能降成编号行，不能消失。
- `records`：每个编号的内容只写在这里，各组只列编号。条目带 `section`（principle、hard、preference、rule、goal），由 A 分类，渲染器照放。`speaker`、`overdue`、`latest_departure` 由 A 算好。
- `orders`：偏好之间的次序。`status: "user"` 带次序与来源原话；`"none"` 表示“未定次序 → 冲突时给方案”。A 至少把同一人、范围重叠、没有用户定次序的偏好列进来，可用 `competes_with`†。
- `omitted`：`{id, reason}`，reason 为 `out_of_scope:people|date|time|place|channel` 或 `over_budget`（只给非必需的）。
- `details`：`{path, ids}`，细节去哪读。`candidate`：宽包里的候选草稿号，不计预算。
- 宽包另带 `full_quotes`（每条必需记录所出原话的全文；v1.1 起每项是对象 `{event, text, said_at, speaker, nature}`，A 编包时带上，渲染器不另外取原话，过渡期 `render_pack(pack, sources=…)` 仍可用）与 `dispositions`（每个必需条目一行，初值 `unaddressed`）。处置填进作业结果的 `usages`†，格式沿用 `usage.py`：used、host_reported、traded_off、not_applicable（后两种写理由），used 与 host_reported 带 `block_ids`。两条用户本人的偏好没有用户定的次序时，不许 traded_off：各自落进不同方案，或留待定。
- `digest`：`sections`（设计第 6 节 11 节的固定顺序；宽包后加 `full_quotes`、`dispositions`）、`budget_tokens`、`writing_rules`（zh、en）。
- 宿主收到渲染好的 Markdown 加 `pack_meta`（包去掉 `records`、`full_quotes`），全量数据不发两遍。

**3.2 窄包与宽包。**
- 窄包（快答、第一段）：人是发问的人；时段从现在到这次回答管到的时刻（最晚的硬节点，或用户问到的时刻）；本地点；通道 conversation。按范围收窄，不按种类丢：适用的偏好与实况照带。预算推荐默认：快问 1,800（v1.1 由 1,000 调上：写法片段约 560、给宿主的 JSON 约 620，固定部分约 1,545，见 D-33）、上报实况 500、第一段 3,000 token。现状差（A 改）：`compile(scenario="quick")` 只留硬类和 `focus_keys`。
- 宽包（深核）：本趟全员、候选涉及的整天、全部通道，不按场景删；带 `full_quotes` 与 `dispositions`；预算 8,000，不含候选计划。

**3.3 渲染器。** B 提供两个纯函数：不读钟、不读库，同输入出同样的字节。
- `render_pack(pack) -> {markdown, tokens, required}`：每个必需编号至少出现一次。放不下时，把 hard、principle 以外的记录降成编号行，并把该组 status 降为 partial（返回的 `required` 是降级后的）。只因硬约束就超预算时照样超，包头写“硬约束过多”；只因固定部分（写法片段、给宿主的 JSON）超预算时，包头写“固定部分超预算”，不算在硬约束头上。`{{WRITING_RULES}}` 一行换成 B 的写法片段原文。
- `render_answer(answer) -> markdown`。
- 样本里的 `.md` 是逐字预期，各行格式以样本为准；比较前先把 `{{WRITING_RULES}}` 换成片段。B 要改格式，就改样本，并具名通知 C。

**3.4 回答数据**（`$defs/answer`）。三种骨架，栏与上限照设计第 9.2 节：
- `quick`：answer（1—2 行）、places、legs、hard_node（只在触发时）、check（至多 1）。
- `first`：heading_time、now（1—3）、latest（每个硬节点 1 行，至多 3）、options（只在 1—2 小时内分叉时）、places、legs、tool_hint（本次第一次出现时）、checks（至多 2）。
- `second`：result（作业号、状态、verdict）、commit（是否接受、草稿号、版本号）、write、differences（至多 3）、hard_nodes（至多 4）、schedule_from 与 schedule（每项一行，以时刻开头，可带 leg）、options、todo（至多 4）、unchecked（至多 3）、conditions（1—2）。标题由 verdict、commit、write 按术语一节推出，数据里不另存。
- 共用：`notices`（冲突通知行，排在最前）、`pack_id`、`at`、`language`。
- 地名：`{place_id, name_local, line, search, link}`，渲染成两行：地名一行（原文拼写，能单独复制），“说明 · <App> 搜 <词> · [链接]”一行。`search` 是 `{app, query}`，必填：不点链接也要能照着做（D-28）。
- 交通行：`{verb, from, to, timing, note, search, link}`，渲染成 `verb · <App> 搜 from → to · timing · note · [label](url)`；地图 App 从手机当前位置出发、只搜终点时渲染成 `verb · from → to · <App> 搜 to · …`；空栏省掉，`search` 必填。链接进 App 或进浏览器都行，由 B 按地区表生成，宿主照抄。
- 没核的事：`{item_id, what, consequence, onsite}`，渲染成“没核：what → consequence → onsite。”（第二段用“没核到：”）。
- 方案栏：`{common, decide_before, variants[2—3]{label, diff, how, item_ids, plan}, recommend, default}`。第一套 diff 为空，作基准；recommend 必须带事实理由；default 只在必须马上定时给，标“默认”。

**3.5 记忆数据**（`tripwright.memory/1`，v1.1 加；D-32）。长期条目在当前这一趟的状态，A 在每次提交时出，B 渲染成每趟的 `views/memory.md` 与网站的记忆页。形状：`{schema, trip, as_of, entries[]}`；`trip` 是当前这趟的编号，`as_of` 等于快照的 `taken_at`。每条 `{id, kind, text, quote, subject, strength, source, status, status_event, last_used}`：`kind` 为 preference、companion、rule、material；`text` 一句要点，`quote` 原句（没有为 null）；`source` 为 `{trip, event, said_at, speaker, nature, path}`，`nature` 沿用四种来源性质，`path` 是正本文件；`status` 为 confirmed（这趟沿用）、pending（待确认，照用）、not_used（这趟不用）；`status_event` 是这趟里确认或搁置它的那条原话（没有为 null）；`last_used` 为 `{trip, date, version_id}` 或 null。样本在 `tests/render/fixtures/memory.json`；B 的 `memory_problems` 拒绝带原句却没来源性质、既无原话号也无文件、未知种类或状态、重复编号。是否沿用由 agent 在对话里问、用户回答（R-42），记账调用由 A 定、C 写进 skill。

**3.6 查询与证据**（v1.4 加；D-42，A5）。工具仍是七个，新能力走 `query` 的 `kind`，命令行 `tripwright --repo . query <kind> …` 出同形 JSON（stderr 一行摘要；MCP 不在时用）。时刻一律行程时区的本地 HH:MM，另有带偏移的 `*_at`。
- `transit`：另带 `connections[]`（`leave`、`reach` 是门到门；车次时刻只看 `boards`、`alights` 与车段 `legs[].depart/arrive`；步行段 `walk: true`）与 `window {from, to, by, count, text}`；说“唯一”“最后”时所说时刻必须在窗口里。
- `latest_departure`：参数 `origin`、`destination`，目标 `anchor_id` 或 `by`（可带 `deadline`），可选 `external_deadline`、`after`；回 `target {deadline, time, plan_by, limit, limit_from, buffer_minutes, text}`、`primary`（赶 `plan_by` 的最晚一条）、`later[]`（只按外部截止还能坐的，`past_plan_target: true`）、`fallback`（必晚于主选，没有为 null 并写 `fallback_note`）、`window {…, covers_target}`、`summary`、`evidence`（演示数据为 `synthetic: true`、不算核过）。
- `web_evidence`：参数 `url`、`quote`（逐字）、`claim`、`type`（rule、opening_hours、event、place、price、other）、`read_from`（page、search_snippet）；宿主不得带任何时刻，服务端记 `fetched_at`；回 `quote_found`（true、false、unchecked）、`counts_as_checked`、`evidence`、`reason`。搜索摘要与页上找不到原句的永不算核过，也不能支撑更正。证据只记账，不改计划与锁定字段。
- `check_answer`（v1.5，A6；v1.6 加 `question`）：参数 `text`（一段草稿全文）、`part`（first、second）、可选 `request_id`、可选 `question`（这一问的原话，有就以它为本问；命令行 `--question <文件或 ->`；两者都不给时只认 30 分钟内、channel 不是 system 的用户消息，找不到就不判吃的，`summary` 加“本问原话不明，吃的检查没做。”）；回 `ok`、`problems[] {check, line, text, why, fix}`、`summary`、`now`、`evidence {since, ids, windows}`。只核不改、不记账。第二段的检查：`second_heading`（v1.6，D-48：要有带时刻的 `# 第二段 · …（HH:MM）` 标题行）、`checked_has_evidence`、`only_inside_window`、`fallback_later`、`plan_heading`、`title_time_not_later`、`no_tags`，加下面 A7 的四条；第一段：`clock_in_first_part`、`no_tags`、`internal_words`、`first_part_lines`。证据范围是该问 turn 收到前 6 小时到现在。命令行 `query check_answer --part … --request-id … --text-file <文件或 ->`。
- `latest_departure` 默认精简（`detail: compact`，约 10 KB 内：主选、后备、`later` 前 3 条与 `later_count`、窗口、摘要、证据要点），`detail: full` 取全部。
- 证据记录新种类 `latest_departure`、`web`，字段见 `docs/DECISIONS-impl.md` 的 A5 节。`turn.record` 的参数错误一处一行，写字段路径（例 `record.facts.0.true_from: must be an ISO time …, got 'this'`）。
- **固定钟（v1.6，A7，只给重放与测试）：** 环境变量 `TRIPWRIGHT_NOW`（带偏移的 ISO）设了，服务端的“现在”从它起算、按真实时间往前走；turn、包、查询、`check_answer`、证据时刻与证据号都走它。`scripts/host_replay.py --now …` 同时给宿主垫一个输出同一时刻的 `date`；`run.json` 记 `now`。
- **`check_answer` 加四条（v1.6，A7）：** 第二段 `unreachable_needs_lookup`（“赶不上、来不及、没有车、走不通”一类句子要有这一趟的 `latest_departure` 结果撑，且不能点名工具列为赶得上的班次）、`meals_named`（本问原话有饿、吃一类字时，至少两处具名吃饭地点，地名行加“ · ”说明行，营业时间出处在地点表或这一趟的网页证据里）；两段都查 `internal_words`（“外部截止”“计划目标”、证据号、字段名；字段名从 `no_tags` 移来）；第一段 `first_part_lines`（正文不超过 8 行）。第二段 `ok: true` 时结果带 `passed {sha256, remembered}`，服务端按请求号记下过核的草稿（`.tripwright/answers.json`，最多 50 份）。
- **`latest_departure` 的候选（v1.6，A7）：** 精简与完整结果都带 `candidates[] {route (first_choice、via_station、fastest，可多个), label, via, leave, reach, minutes, past_plan_target, ticket {need: extra|unknown, why}, rides[], walk_minutes}` 与 `resolved {origin, destination}`（每端认成哪个地点、实际用什么查）；`summary` 末尾写“别的走法”。经车站的走法取计划里坐火车去终点（3 公里内）的出行块起点，没有再取 station 类地点，最多两个。起点终点写地点 ID、名字或名字在前的地址时用地点表存的坐标，不地理编码。
- **`open_places`（v1.6，A7）：** 参数 `near`（地点 ID、名字或 "lat,lon"）、可选 `at`、`km`（默认 2）、`text`；回 `open[] {id, name, name_local, kind, distance_km, closes, opening_hours, hours_source}`（近的在前）、`closed[]`、`unknown[]`、`summary`、`notice`。命令行 `query open_places --near … --at … --km … --text …`。
- **地点营业时间（v1.6，A7）：** `Place.opening_hours`（规则串，例 `Mo-Su 03:30-16:00`、`Fr 18:00-02:00`）属外部事实，被锁时走更正、可附证据；`Place.hours_source {url, evidence_id}` 是说明字段。写入路径：先 `web_evidence` 记官网原句，再经 `propose`／`commit` 写进地点；引不存在的证据号被拒。
- **改锁的准入与回执（v1.6，B3g；D-49）：** 改受保护字段的三条路（用户新决定，含 agent 决定里 `parts` 标 user 的部分；`changes.reports`；`changes.confirm_corrections`）都经同一个准入：只认本次提案回答的那条消息、用户自己的话（`user_verbatim` 或 `host_restatement`）、说话人在已提交的行程里、引用句在消息里；拒绝写 `not admitted (<项>)`。没有已提交计划时，首次计划可与用户决定同次提交（见下一条，B3h）。propose、commit 的 `lock_changes` 键为 `decisions`、`reports`、`confirmations`、`corrections`，commit 另有 `amended`；每行带 `event`、`nature`、`speaker`。`turn.record` 的条目或 `source` 不是对象时在收消息之前拒，写 `record.<part>.<i>[.source] must be an object {…}, got <type>`。
- **改锁的先后（v1.6，B3h；D-50）：** 每把用户锁记授权它的用户消息与服务端收件时刻（`authority`）；改锁的消息不得早于最近一次改这把锁的消息，拒绝写 `not admitted (order)`；重试 `turn` 只取回原收件，不重新授权。更正自提出它的那次提案起算（`asked`），确认要来自之后收到的用户消息，`source` 引用的事件不改提出时刻。用户新决定与 `parts` 标 user 的部分都按路径取代同人同范围的旧用户锁，其余路径留锁（回执 `replaces` 行带 `kept`；v1.7 起也留旧锁护着、新决定不护、仍在计划里的项，见 B3j 一条）。没有已提交计划时，说话人取首次候选计划的人员，回执带 `people_from`。
- **完整覆盖才取代（v1.6，B3i；D-51）：** 用户新决定与 `parts` 标 user 的部分，只在完整盖住一把旧用户锁定的东西时才取代它（按比较器同一套对象与引用图判，不按路径前缀）。只碰到一部分时（锁里的一个字段，或锁经引用持有的地点、票、fare 的一个字段），整份提案拒：`lock_changes.decisions[].refused` 为 `{decision, locks, decides, reason, fix}`，草稿带 `refused`、`status: rejected`，commit 拒；`fix` 指在那把锁的路径上整块重定。字段级的部分更新不做。
- **提交处的锁检查（v1.7，B3j；D-52）：** propose 预演、deep_check、commit 都以基础版本（这次提案基于的已提交版本）里的现行用户锁为锚再查一次，不依赖账本怎样重记锁。拒绝有四种：`not admitted (conservation): lock <id> at <路径>: <项> changed without the user's authority for it; …`（旧锁护着的项变了，却没有这次准入、完整覆盖它的用户决定，或正是这个字段的用户实况、已确认的更正）；`… <项> would be protected by no lock after this commit; the user decides <路径> (or the day holding it) again as a whole`（旧锁护着、提交后仍在计划里的项没有锁护）；`not admitted (used): message <事件> already changed lock <id> in version <N>; only a new message of the user changes it again`（一条用户消息改一把锁，含后继锁与余锁，只随一次提交生效；重试 `turn` 拿到的新 ctx 也不行）；`not admitted (order): …`（提交处的先后与确认时刻）。propose 的 `lock_changes.refused` 与草稿 `refused` 列出它们，`status: rejected`，notice 以 “Refused, nothing will be recorded” 开头；commit 写 `Commit refused: <比较器冲突>; <这层的拒绝>`。版本新增 `authorized {event, locks}`。照旧放行：同一提案里多字段、多把锁一起改；同一条消息分两次改不相干的锁；同一份草稿重放（取回原版本）。代价：同一条消息说的两件事若碰到同一把锁，要放进同一份提案，分两次提交时第二次被拒、要用户再说一次。余锁：取代时旧锁护着、新决定不承接、仍在计划里、能写成锁路径的项（`/timezone`，整个 place、booking、person、fare）留成余锁，沿用旧授权，回执 `replaces[].kept.locks`；整天重定后块锁护着的时区留成 `/timezone` 锁。块的位置在拒绝里叫 `where:block:<id>`，只有块路径与包含它的一天（或天列表、整份行程）护它；只定一天里某个方案（`/days/<d>/plans/<p>`）不能取代块锁；整天重定时去掉的块，它的锁随这次授权释放；agent 去掉锁住的块照拒。宿主收到 `used`：告诉用户这次没改，要再改等用户的新消息，不换 ctx 重交；收到守恒拒绝：照 `fix` 请用户在整块或这一天上重定，或说出那个字段的新值作为实况。
- **两个钩子（v1.6，A7；D-44、D-45）：** `tripwright --repo . hook first-part`（PreToolUse）：本问最后一条真用户消息之后主 agent 还没有过得了第一段检查的文字时，拒绝主 agent 自己的 `mcp__…tripwright…__query`（`check_answer` 除外）、WebSearch、WebFetch、命令行 `tripwright … query` 与前台的 Agent、Task；后台派的 Agent、Task（`run_in_background: true`，D-46）、子代理里的调用、`turn`、读文件、Skill 不拦；同一问拦满 3 次放行。`tripwright --repo . hook stop`（Stop）：发出的最后一段若是第二段（“第二段”或“完整安排”标题），要等于本问某一份过核的草稿（统一换行、去行尾空白后比），否则 `decision: block`；这一问派过子代理或查过、却没有带第一段样子的文字（只核结构：前两行有现在的钟、没有标签与工具状态句，D-47）也没有第二段时，`decision: block`（“把第一段写成回复文字再结束”，D-46）；有带第一段样子且说了还有第二段的文字，或派过、查过，而第二段没发、也没有在跑的后台核查时，`decision: block`（“在后台派 researcher 核查，再写第二段”，D-48）；快问（没说第二段、没派没查）与闲聊不拦；同一问三条合计最多拦 3 次，`stop_hook_active` 为真也照算。两个钩子都不连网、出错放行，`.tripwright/hook.log` 每次一行；重放的 `run.json` 记 `hooks {first-part, stop} {runs, blocked, errors}`。

### 3.9 v1.8—v1.9 增补（11-02，A8、B3k、R1、v2g、M1、A9、A10）

- **交通事实（A8，D-57、D-60、D-61）：** `transit` 与 `latest_departure` 的结果带 `sentences`（服务端写好的交通句，宿主照抄）、`searched`（查过的窗口）、`coverage`（数据集覆盖与有效期，取自 Transitous 全部清单）、`how_to_check`（`what`、`text`、`link`：去哪查、怎么查）；结果为空且超出有效期时带 `reason`。紧凑版 latest 只有 `sentences`、`coverage` 与精简 `how_to_check`，上限约 10 KB。`check_answer` 加 `direct_needs_one_ride`（说“直达”要有一程直达的查询撑），`only_inside_window` 另判漏掉更晚的车、首班、没写时刻。
- **记录为主、必须项才护（B3k，D-53）：** 用户决定带 `must`（默认 false，旧数据读成 false）；只有 must 锁，v1.6—v1.7 的准入、先后、完整覆盖、余锁、提交处守恒与只用一次只对 must 生效。agent 改到非 must 的用户决定要给 `changes.revisions [{decision, reason}]`，否则 `not recorded (reason)` 拒；其他改动可给 `changes.why`。propose、commit 都带 `changelog`（逐字段：target、field、change、old、new、by、basis、why、revises）；`state(view='history')` 按版本列出。已买已订的预订与硬节点是实况：只在本次实况或已确认更正点名的字段上改。回执决定行加 `must`、`paths`；`locks` 只列 must 的路径。
- **远程入口（R1，D-58、D-63）：** `tripwright serve --http` 读 `TRIPWRIGHT_REMOTE_CONFIG` 指的 YAML（`public_url`、`data_dir`、`secrets_file`、`oauth`、`accounts` 登录名到租户、`tenants.<id>` 的仓地址与部署钥匙、`token_tenants`）。`oauth: true` 时用 FastMCP 的 OAuthProxy 接 GitHub 登录（CIMD、DCR、PKCE、同意页），只放行 `accounts` 里的账号；请求头令牌路照留。每个租户一份镜像、运行库与服务对象，写回沿用发件箱与按操作重放。远程模式不挂旧 `/r` 结果页。
- **记忆每问都考虑（M1，D-61 六、D-63 四）：** `turn` 的结果加 `consider`（这一问该考虑的记忆：偏好、规矩、决定带 `must`、人员、目的地、想去的地方、这趟的来源与须知；每条有编号、一句话、出处、`stable` 或 `changing`）、`ask[]`（要问用户的话与怎样记回答：必须去的地方、要不要配实时数据、长期条目还用不用）、`offer`（一句起草询问的邀请，英语加当地语言，带起草规格）、`next`；`recorded` 加 `replaced`、`defaulted`，`not_recorded` 加 stable 一类。分类是数据（`src/tripwright/consider.yaml`），用户可用 `stability:<种类>` 改。`Item.kind` 加 `setting`（`source:<id>`、`app:timetable`、`ask_languages`、`note:<slug>`、`must_go`、`stability:<kind>`），不进约束包；按“这一趟 > 长期 > 产品地区表”合并。stable 的条目被取代、计划改 `/areas` 或 `/people`，要本轮用户的话（`not admitted (stable)`，`lock_changes.stable`）。包的决定条目带 `must`。`state(view='memory')` 加 `consider`、`sources`；query 结果加 `offer`。
- **最晚、末班由服务端连续查清（A9，D-64）：** `transit` 加 `until`（从 `when` 起服务端一页页接到 `until`，每页一次 /plan、守 720 额度，到限即停写明查到哪），`window` 加 `continuous`、`covered`、`until`、`pages`、`limited`，`summary` 一句总结。`latest_departure` 的 `by` 只表示到达终点的期限，在某站上车用 `board_at`；返回 `target.meaning`（`arrival_deadline`｜`boarding_time`），把上车时刻当 `by` 传报错并给改法。结果加 `note`（“按时刻表，不是实时”说一次），交通句不再每条都带。`turn` 的硬节点与包里存档的“末班、只剩、最晚”改写成“计划里写过……（存档，写于何时，没核）”，加 `archived_claims [{field, claim, written_at, source, superseded}]`，本轮查到更晚同方向车次就标作废。`check_answer` 加 `denied_needs_coverage`、`count_needs_continuous`：否定或计数可达句的时刻落在查询窗口外或两次查询空档里就拦。
- **服务端定框与第一段（A10，D-67）：** 锚点的意思按字段定（`query.ANCHOR_MEANINGS`：有 `place` 且有 `line` 或 `destination` 的是“在 place 上车”，其余是“到 place”）；boarding 锚点查到它的 place，与 `by`、`board_at` 或别的终点同给报错并给改法。`latest_departure` 不给 `anchor_id`、`by`、`board_at` 时返回今晚最后一班：从现在连续查到 `until`（默认次日 03:00；A9 分页，守 720 额度），带 `first_line`（第二段首行照抄）、`last`、`last_without_extra_ticket`、`last_train`、`next_after`、`window`、`hard_node`（终点之后最早的到达型硬节点、外部截止与它的最晚出发；没有为 null，不编期限），`target.meaning = last_connection`。期限型紧凑结果不另带 `first_line`。`turn` 加 `first_part {text, lines, asks_latest}`（服务端只用存的数据写，≤8 行，首行带钟；问最晚时首行“真正最晚要查，第二段给”），`next` 首句要宿主原样发。`check_answer` 加 `frame_matches_server`（第二段首行的“最晚、末班”要是本问服务端框里的时刻，fix 为服务端 first_line）。synthetic 数据的 `sentences`、`summary`、`first_line` 前缀“（虚构演示数据，不能用于出行）”。
- **锚点的 `where`（A11）：** 计划里的锚点可选 `where`（`online`｜`on_site`，不写＝on_site）。online 是在手机或电脑上做的截止（例：网上值机截止），人不用在 place，不算到场期限，没有最晚出门：不当终点之后要守的时间，不当计划目标背后的外部截止，`latest_departure` 用它的 `anchor_id` 报错并给改法，`check_plan` 不核能不能赶到，不能带 `travel_minutes`。规则在 `query.ANCHOR_MEANINGS` 第一行（`online_deadline`），只照字段判。锁的类别是外部事实。`turn` 的硬节点记录带 `where`。
- **`check_answer` 加 `no_process_notes`（A11）：** 第一段、第二段都核。一行里有过程话（“N 次查询”“每页接着上一页”“查了 N 页”）或写给宿主的写作说明（“说一次就够”“不用每条都写”）就拦；服务端的句子不再带这两类。
- **回答层（v2g）：** 主回答从角色源生成；规矩见 `skills/tripwright-main/SKILL.md`，服务端 instructions 同源。
- **这一问的要点与第一段（A12）：** `turn` 加可选 `points`，每项 `{kind, said, place}`。
  - `kind`：at（人在哪）、go（去某处、要不要赶）、eat、do（活动）、ticket、other。`said` 是用户的话，不超过 60 字；`place` 是地点编号、存的名字或 `lat,lon`。最多 7 项，at 最多 1 项；形状不对报 `Invalid points: points.N.字段: …`。
  - 读懂问题是宿主的事（D-08），服务端不解析原话。
  - 给了 points，结果加 `points`：`at` 是用户说的地方，没说就是计划里这时的地方（`assumed: true`）；每个要点带 `facts` 与 `keys`（回应它要提到的词）。`facts` 按种类：go 是今晚早些时候查过的同终点框（`frame {evidence_id, fetched, origin, all_night, train, ride}`）、到那里的硬节点、存的票；eat 是离 at 2 公里内、存着营业时间且现在开着的吃饭地点；do 是宿主点名的存的地点；ticket 是存的票。
  - `first_part` 加 `points`（每个要点那一行）与 `at`。首行答最急的（go 先），第二行写钟点和人在哪（没说就写“假设你在 X”），每个要点一行，剩下的行给硬节点，不超过 8 行。没存的写“第二段给”。
  - 有计划而没给 points：`first_part` 照旧按存的计划写，`next` 开头让宿主带 points、用同一 `request_id` 再调 `turn`。
  - `check_answer` 第一段带 `request_id` 时，按那次 `turn` 存的要点加三项：`answers_each_point`；`place_in_first_lines`；`archived_marked`（没核过的硬节点时刻、估的票价所在的行，要带“存档”“没核”“存的”“查过”“估的”之一）。那次没给 points 时前两项不核，`summary` 写明。
- **存的票（A12）：** `latest_departure`（两种框）的结果加 `tickets`：这趟当天和前一天存的预订，kind 为 ticket 或 pass、没取消，有 `text` 一句与 `items[{id, status, what, fares}]`；没有时为 null。`sentences` 末尾说一次。有存票时，连接的 `ticket.need` 为 `stored`（`why`“按这趟存的票”）；长途大巴仍是 `extra`，没存票仍是 `unknown`。
- **第二段行数，服务端句子只说一次（A12）：** `check_answer` 第二段加 `second_part_lines`：正文不超过 12 行。地名行与说明行、带链接的行、“## 完整安排”下各项不计，其中的“没核：”行照计。夜里一直有车时，`latest_departure` 首行已说火车最后一班和之后要守的时间，下面不再重说：火车那行改为“这班火车怎么坐：…”，节点行只说首行没说的（“另外要守：…”）。框另存 `told`，供后面的 `turn` 引用。
- **计划里的车在不在这次结果里（a12b）：** `latest_departure` 不给期限时，结果加 `plan_rides[{anchor_id, time, line, found, found_line, sentence}]`。它列计划里去这个终点、带 `line`、时刻落在这次查询时段里的上车节点，每条在 `sentences`（窗口句之后）说一句：
  - 查到同一分钟、同一线路的车：“在这次的查询结果里（HH:MM 出门）”；
  - 同一分钟、同类车但号不同：加“数据源写作 X”；
  - 都没有：“不在这次的查询结果里（可能数据源没收，或车次号不同）：要坐它，先在 <App> 核一下”。不说那班没了，也不说以下面的为准。
  - `check_answer` 第二段加 `missing_ride_found`：一行说某车“没查到”而这一问的查询里有这一分钟的这班车，拦；说没查到又写“以下面的为准”，也拦。
  - go 要点的 `place` 不是存的地点时，取接下来硬节点所在或要去的、名字里有这个词的唯一一处；没有就不带地名。不拿用户原话当地名。
- **第 7 次质量判的明确错（A14）：**
  - 假设的位置：没说在哪时，取问话人（`speaker`，否则 `subject` 里唯一的人，否则这趟的第一个人）自己那条线里覆盖此刻的块；块是交通段时取起点。此刻没有块时，取此刻之前最近结束的块，交通段取终点（人已到）。`check_answer` 第一段加 `assumed_place_matches_plan`：写了“计划里这时在这”的行，要写出计划里此刻的那个地点。
  - 之后要守的时间：计划目标（`deadline: plan`）写成“你计划 HH:MM 前到（留的余量）：…”，不另算最晚出门；“明早直接从 X 去：要 HH:MM 前到…”只算它背后要到场的外部截止，没有就不写。`where: online` 照旧不进到场计算。
  - `how_to_check`：今晚最后一班的结果里，计划里的车在这次结果里、还没开时，搜索指那班；`text` 第二行“误了主选，查末班：在 <App> 搜 …，出发时间填 HH:MM。”，并加 `last {search, clock, link}`。框另存 `main`（那条连接的出门和各程开车时刻）。`check_answer` 第二段加 `search_matches_main`：“## 怎么查、怎么问”里的出发时间要有一个是 `main` 的；“误了主选”那行不算。
  - 第一段（给了 points）：go 没查过的框时，首行先给计划里存的去那里的车（“计划里是今晚 HH:MM 在 X 坐 Y（存档，没核）；真正最晚的第二段核完给。”）；硬节点只留一行，取下一个要到场的（优先 go 的目的地），计划目标、网上做的、上车节点都不列；不再加“计划里存的班次说法没核”那一行，末行一律“没核：上面存的时刻和说法，第二段核完再发。”。
  - `check_answer` 第二段加 `coverage_said_short`：一行说“HH:MM 之后…没查”或“不能说 HH:MM 之后没有”，而这一问“今晚最后一班”的连续查询查过了 HH:MM，拦。回答层的“今晚”从次日 01:00 改到 03:00，和 `latest_departure` 的 `SERVICE_END` 一致。
  - `turn.next` 加一句“用户看不到工具结果：第一段要你发出来才算”；Stop 钩子拦第二段时的理由加“这段用户已经看到了：过了就结束，别再发一遍；没过只发一条“更正：””。

## 4 源文件与生成文件

写域的机读件是 `tests/contracts/manifest.json`；契约测试核“仓里每个文件恰好属于一个写域”“生成文件在生成者的写域里”“`.tmpl` 只在 C”“机读件里的每个路径式都写在本页”。生成文件首行写“由 <生成器> 生成，源：<路径>；不要手改”（Markdown 用 HTML 注释，TOML 用 `#`）。两个例外（10-31 主会话定）：带 YAML frontmatter 的文件（`SKILL.md`、`.claude/agents/*.md`）把这句写成 frontmatter 里第一行的 YAML 注释，因为宿主只在第 1 行认 frontmatter；用户仓的 `CLAUDE.md` 只有 `@AGENTS.md` 一行，不带这句。生成者自己的测试核“重新生成＝已提交”。

**写域（文件与目录）：**
- A：`src/tripwright/repo/**`（仓格式、正本分支、发件箱、重放、同步、外部改动、索引与快照）、`src/tripwright/packs.py`（约束的语义筛选与完整性标记）、`src/tripwright/storage.py`、`src/tripwright/events.py`、`src/tripwright/ledger.py`、`src/tripwright/consider.py` 与 `src/tripwright/consider.yaml`（每一问该考虑的记忆、来源与须知的合并、改动频率表；M1）、`src/tripwright/versions.py`、`src/tripwright/jobs.py`、`src/tripwright/service.py`、`src/tripwright/cli.py`、`src/tripwright/demo.py`、`src/tripwright/adapters/__init__.py`、`src/tripwright/adapters/mcp.py`、`src/tripwright/remote/**`（远程入口：OAuth 代理、账号名单、单人租户、写回用户仓；R1）、`examples/**`、`docs/FORMAT.md`、`tests/repo/**`、`tests/core/**`、`tests/remote/**`。
- B：`src/tripwright/render/**`（包与回答渲染、交通行与地图链接、地区表 `regions.yaml`、查询触发、机械检查、写法规则源片段 `fragments/*.md`、首屏与视图）、`src/tripwright/site/**`、`src/tripwright/adapters/web.py`、`tests/render/**`、`tests/site/**`。
- C：`src/tripwright/agents/**`（角色源 `roles/*.md`、单 agent 步骤源 `steps/*.md`、模板 `templates/*.tmpl`、生成器与生成件 `generated/**`）、`src/tripwright/adapters/prompts.py`（MCP prompts 注册）、`skills/**`（主 skill 只由 C 装配）、`tests/agents/**`。
- 冻结核心（三块都不写，要改经主会话）：`src/tripwright/__init__.py`、`src/tripwright/checks.py`、`src/tripwright/trip.py`、`src/tripwright/usage.py`、`src/tripwright/evidence.py`、`src/tripwright/locks.py`、`src/tripwright/query.py`、`src/tripwright/providers/**`、`src/tripwright/solver/**`、`tests/providers/**`、`tests/solver/**`。
- 主会话与接缝负责人：`pyproject.toml`、`uv.lock`、`uv.toml`、`.python-version`、`.gitignore`、`.github/**`、`README.md`、`LICENSE`、`homepage/**`、`scripts/**`、`deploy/**`、`docs/CONNECT.md`、`docs/DATA_SOURCES.md`、`docs/CONTRACTS.md`、`tests/contracts/**`、`tests/fixtures/contract_sample/**`。
- 只追加：`docs/DECISIONS-impl.md`，各块只在末尾写自己的节。

**本仓里的生成文件：** `skills/tripwright-main/SKILL.md` 由 C 装配，源是 C 的角色源与步骤源；写法页 `references/writing.md` 由角色源的 `reference: writing.md` 生成（v1.8 起，v2g；B 的 `render/fragments/*.md` 不再整段嵌入主 skill，公共片段接口照留，供 B 自己的渲染用）；其他 `skills/tripwright-*/SKILL.md` 与 `src/tripwright/agents/generated/**`（`.claude/agents`、`.codex/agents` 格式、MCP prompts、L3 提示词、L0 步骤稿、服务端 instructions）也由 C 生成。

**用户行程仓里的最终路径**（运行时由 A 的提交器写盘；“生成者”指决定字节的块）：
- A 生成：`tripwright.yaml`、`profile/**`、`trips/*/trip.yaml`、`trips/*/raw/**`、`trips/*/ledger/**`、`trips/*/plan/**`、`trips/*/evidence/**`、`.gitignore`、`.gitattributes`（布局由代码写，不用模板）、`.tripwright/**`（不进 Git）。
- B 生成：`README.md` 与 `trips/*/README.md`（首屏）、`trips/*/views/**`（今天、每天、预编包、索引、决定、记忆 `memory.md`）。A 在同一次提交里写盘，不改字节。
- C 生成：`AGENTS.md`、`CLAUDE.md`（源是 `templates/*.tmpl`）、`.claude/agents/*.md`、`.codex/agents/*.toml`、`.mcp.json`（把 MCP 服务注册为 `tripwright`，Claude 子代理的禁令按这个名字写）、`.claude/settings.json`（预先放行 tripwright 的工具、网页搜索与抓取、子代理和只读 Bash，免得手机与云端会话逐次问；不放行写、删、推送类；v1.6 起带 3.6 的两个钩子；源是 `templates/settings.json.tmpl`，D-39、D-45）、`.claude/skills/*/**` 与 `.agents/skills/*/**`（四个角色的 skill，Claude Code 与 Codex 各自的仓内位置，让 L1 宿主不连 MCP 也能发现工作法；源是 `generated/**` 与 `skills/**`）。A 的 init 与升级写盘。
- 用户：`trips/*/research/**` 是原件，工具只追加。

**跨块的接线（要对方改的一行，经主会话转达）：** `adapters/mcp.py`（A）调 C 的 `register_prompts(server)`，并从 C 的生成件读服务端 instructions；`service.py`（A）调 B 的 `render_pack` 与 `render_answer`；A 的提交器调 B 的视图函数取首屏与视图的字节。

样本的时间线与各文件用途见 `tests/fixtures/contract_sample/README.md`。契约测试 `tests/contracts/` 只核样本自洽与写域，不实现 A、B、C。
