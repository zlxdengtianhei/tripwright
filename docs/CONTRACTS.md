# 接缝约定 v1.9：快照、写入、包与回答、文件归属

> 状态：冻结 v2.0（2026-11-03 按 S5 在 3.10 加：用户层与 agent 层的取代、`consider` 的来源层与截断、`explain(record_id)` 取单条、`turn` 的 `write_back` 与 `record` 出错不拒整条、agent 层软条目不进必需；改了 3.6 B3g 一句；机读件与样本不变）；v1.9（2026-11-02 12:1x 按 D-67 与 A10 交回在 3.9 加一条：服务端定“最晚几点出发去 X”的框、`turn` 的 `first_part`、`check_answer` 的 `frame_matches_server`；只加字段与检查，不改已有形状）；v1.8（2026-11-02 01:3x 按 A8、B3k、R1、v2g 交回加 3.9 与写域两处，02:1x 按 M1 交回补 3.9 一条与 consider 归属；v1.7 起于 2026-11-01 13:4x 按 D-52 与 B3j 加提交处的锁检查与余锁扩大；v1.6 起于 05:5x，06:3x 按 D-46、07:1x 按 D-47 与 D-48 改两个钩子的拦法与第二段标题检查；09:5x 按 D-49 与 B3g 加改锁的准入与回执；10:5x 按 D-50 与 B3h 加改锁的先后；11:5x 按 D-51 与 B3i 加完整覆盖才取代；主会话按 D-44、D-45 与 A7 交回改 3.6：固定钟、`check_answer` 四条新检查与 `passed`、`latest_departure` 的 `candidates` 与 `resolved`、`open_places`、地点营业时间、两个钩子；第 4 节 settings 带钩子）；v1.5（2026-11-01 04:11，主会话按 D-43 与 A6 交回在 3.6 加 check_answer 与 latest_departure 精简）；v1.4（2026-11-01 03:12，主会话按 D-42 与 A5 交回加 3.6 查询与证据）；v1.3（2026-11-01 01:4x，主会话按质量判词与 A2 交回加“现在”：包头可选 `now`；turn 结果与 state 每个视图顶层带 `now` `{at, date, time, weekday, timezone, text}`；硬节点记录带 `deadline`（external、plan、unmarked；计划里的锚点 `Anchor.deadline` 可选 external 或 plan，锁的字段表归“用户选择”，A3；锚点标了就照用，没标时有核验证据的算 external，其余 unmarked）、`stored` `{version, at}`、`checked` `{source, at, evidence_id, valid_until, stale}`、`refresh_hours`、`may_have_changed`，turn 的 `hard_nodes` 另带 `passed`；见 `docs/DECISIONS-impl.md` 的 A2 节）；v1.2（2026-11-01 00:24，主会话按用户 Q11 与 D-39 加用户仓 `.claude/settings.json`，生成者 C）；v1.1（2026-10-31 18:5x，主会话按 B、C 交回与用户 Q4、Q6 改：交通行与地名行带搜索词、记忆数据 3.5、宽包原句带时刻与性质、快问预算 1,800、生成说明两条例外、用户仓带 skill；v1 由接缝负责人 contracts_opus_20261031-1730 冻结）｜依据：设计 v0.2.1 第 5、6、8.4、9、13 节，反方审查第 1、4 节｜机读件：`tests/contracts/schema.json`（形状）、`tests/contracts/manifest.json`（写域）｜样本：`tests/fixtures/contract_sample/`（虚构行程，逐字预期）｜改约定：本页、机读件、样本在同一提交里改，具名通知受影响的块（经主会话）。

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
- `omitted`：`{id, reason}`，reason 为 `out_of_scope:people|date|time|place|channel` 或 `over_budget`（只给非必需的；v2.0 起窄包里 agent 层的软条目也算非必需，见 3.10）。
- `details`：`{path, ids}`，细节去哪读。`candidate`：宽包里的候选草稿号，不计预算。
- 宽包另带 `full_quotes`（每条必需记录所出原话的全文；v1.1 起每项是对象 `{event, text, said_at, speaker, nature}`，A 编包时带上，渲染器不另外取原话，过渡期 `render_pack(pack, sources=…)` 仍可用）与 `dispositions`（每个必需条目一行，初值 `unaddressed`）。处置填进作业结果的 `usages`†，格式沿用 `usage.py`：used、host_reported、traded_off、not_applicable（后两种写理由），used 与 host_reported 带 `block_ids`。两条用户本人的偏好没有用户定的次序时，不许 traded_off：各自落进不同方案，或留待定。
- `digest`：`sections`（设计第 6 节 11 节的固定顺序；宽包后加 `full_quotes`、`dispositions`）、`budget_tokens`、`writing_rules`（zh、en）。
- 宿主收到渲染好的 Markdown 加 `pack_meta`（包去掉 `records`、`full_quotes`），全量数据不发两遍。

**3.2 窄包与宽包。**
- 窄包（快答、第一段）：人是发问的人；时段从现在到这次回答管到的时刻（最晚的硬节点，或用户问到的时刻）；本地点；通道 conversation。按范围收窄，不按种类丢：适用的偏好与实况照带（v2.0：agent 层的软条目放不下时先走，见 3.10）。预算推荐默认：快问 1,800（v1.1 由 1,000 调上：写法片段约 560、给宿主的 JSON 约 620，固定部分约 1,545，见 D-33）、上报实况 500、第一段 3,000 token。现状差（A 改）：`compile(scenario="quick")` 只留硬类和 `focus_keys`。
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

**3.6 查询与证据**（七个查询工具、固定钟、改锁的准入与先后、完整覆盖、提交处的锁检查、两个钩子）→ 现行全文见 `docs/CONTRACTS-queries.md`（11-04 自本页拆出，内容原样）。

### 3.9 v1.8—v1.9 增补（11-02，A8、B3k、R1、v2g、M1、A9、A10）→ 原文见 `history/2026-1102-CONTRACTS-3.9.md`（现行有效，内容原样）

### 3.10 v2.0 增补（11-03，S5：方法论 v0.5 的用户层与 agent 层、按编号取单条、云端写回）

- **两层与取代（S5 第 1 条；METHOD 2.9、4）：** 用户层＝`source.basis` 为 `user_explicit` 或 `direction_set`（`consider.USER_LAYER`）；其余（`recommended_default`、`agent_inference`，以及没填 basis 的默认值）是 agent 层。同一对象（同 key 或声明，人与范围相交）上，agent 层的新条目不取代、不作例外、不使用户条目待定：两条都 `active`，agent 那条 `relations.conflicts_with_user: [用户条目]`，不开问题；同一条消息里先记 agent 的、后记用户的，也是 agent 那条带 `conflicts_with_user`。用户层的新条目照旧取代用户层与 agent 层的旧条目。跨层的一对不再生成 `possible_contradiction_with`。“定了基本不变”的种类照旧走 `stable_refusal`（agent 的推断碰上就进 `not_recorded`）。实况（facts）的取代不变。
- **`consider` 每条带来源层（S5 第 2 条；METHOD 3.2）：** 每条加 `layer`（短词：用户说的、我的推断、我的默认、我定的、计划里的；英文 user said、my inference、my default、my decision、in the plan；产品地区表的写“我的默认”）；agent 条目与用户条目冲突时带 `conflicts_with_user`。截断：必须项与用户层条目不计入条数上限、永不截；其余按种类次序填到上限，`more` 只数截掉的。列出 agent 层条目时 `next` 加一句“不说成用户要的”，有冲突时加一句“照用户说的”。`more_how` 指向 `explain(record_id)`。窄包与它的预算不变。
- **按编号取单条（S5 第 3 条）：** `explain(record_id)` 的 `record_id` 是条目、实况、决定、待核或待定问题的编号时，结果加 `record {id, table, status, text, quote, basis, layer, said {event, text, said_at, speaker, nature}, subject, scope, superseded_by[], current, relations, conflicted_by[]?, …按表的字段}`（`current` 是取代链末端、现在生效的那条）；`version_usage` 不给 `version` 时只回最新一版，每版只带这一条的 `usages`、`carried`（那版的约束包带没带它）、点名它的 `changelog` 行，不带整份 `constraints`。编号不是账本记录（例如硬节点）时照旧。只经 MCP，不读行程仓文件，远程同样可用。
- **云端写回（S5 第 4 条；METHOD 6.1 第 3 步）：** `agents/core.yaml` 第 6 步改为“用户这句定了或改了的，和你推断或替用户选了的（`basis agent_inference`），放进同一份 `propose` 再 `commit`；两者之间不另记”，随生成物进 instructions、`steps`、主 SKILL 与 AGENTS.md；instructions 3,586 字节（≤3,600），主 SKILL 12,217 字节（≤12,225）。`turn` 的结果加 `write_back {say, record}`：一句提醒与要发回的原形（`items[0].source = {event: 本问事件号, basis: agent_inference}`，`quote` 为空）。
- **agent 层的软条目不是必需条目：** `ledger.offered(item)`＝agent 层且强度 `prefer` 或 `optional`。这样的条目在计划提案的 `usages` 里可给可不给（给了照常核，不给不报 `usage:<id>` 冲突）；宽包里标 `non_required`，不进 `dispositions`，但宽包仍不因预算省去任何记录。窄包里它们是 3.1 的第三种非必需记录：包放不下时先整批走（`omitted` 记 `over_budget`），再轮到用户条目缩成编号行；两条都是这种条目的“可能矛盾”问题同样非必需；它们不进偏好次序（`orders`）的配对。must、forbid 无论谁记都是必需的。
- **`turn.record` 出错不拒整条 `turn`：** 形状错（`checked_record`、`Changes` 校验）、多给了 `turn` 不收的部分（如 `decisions`）、账本拒（引句不在消息里、人不在行程里等）时，原话照收、第一段照出，`record` 整份不记（账本写入在存档点里回滚，不留半条），`recorded.not_recorded` 加 `{part: "record", records, reason, fix}`：`reason` 是原来的拒绝原文（字段路径照旧），`fix` 写改好后放进这一问的 `propose`（`source.event` 写本问事件号），不要用同一个 `request_id` 重发；`next` 加一句。同一 `request_id` 带同一份坏 `record` 重发，取回原收件。

### 3.11 v2.1 增补（11-04：服务写的句子说全说准，检查分轻重；见 `docs/DECISIONS-impl.md` 的 11-04 节）

- **计划车的句子写整段走法：** `plan_rides[].sentence` 查到那班车时写“计划里的 HH:MM 线路 赶得上：”加从查询起点出发的整段走法：出门（起点先关门时“HH:MM 关门出来（按存的营业时间），走到 …”）、换几次车、每一程“HH:MM 线路（往 终点牌）上车站 → 下车站 HH:MM”（没有终点牌的不写方向）、“HH:MM 到”。各段首尾接不上时（`query.chain_break`），只写接得上的几程，后接“；之后怎么换车没查到，在 <App> 搜 <断处> → <下一程上车站>”，不写换车次数与到达。其余交通句不带方向，照旧。
- **“夜里一直有车”只在没有空档时说：** 今晚最后一班的查询里，查到的、坐车的出发（含今晚截止之后的第一班）相邻两班相隔都不超过 60 分钟（`query.SERVICE_HOLE`），才写“今晚不用赶：夜里一直有车到 X”。否则（相隔不到 2 小时 `NIGHT_GAP` 的末尾不算夜断）首行写最长那段空档：“今晚到 X 的车夜里有空档：火车最后一班 HH:MM 线路（最晚 … 出门）；之后要等到 凌晨 HH:MM 出门（线路 接 线路，HH:MM 到）”；空档前的不是火车时写“空档前最后一班”。火车那行照“这班火车怎么坐：…”只说一次。框的 `told` 加 `gap {before {线路、上车站、时刻、leave、rail}, after {…}}`（无空档为 null），之后 `turn` 第一段的去处行照它写“夜里有空档，火车最后一班 HH:MM 线路，站 开；之后要等到 HH:MM 的 线路 才再有车（HH:MM 查过）”。
- **首行括号里的上车站是哪一程（11-04 质量判）：** 首行点名的车（“火车最后一班 / 空档前最后一班 HH:MM 线路”，含“今晚不用赶”句末的火车最后一班）括号里写的是首程的上车站（起点先关门时“最晚 HH:MM 在 <站> 上车”“HH:MM 关门出来（按存的营业时间），走到 <站>”），首程又不是点名的那班车时，后接“：线路 接 线路，<点名那班车的上车站> 开”（英文“: U4 then FlixBus N877, which leaves <站>”；`query.leave_words` 的 `named`）。空档前那班不是火车时（没有别的行说怎么坐它），“最晚 HH:MM 出门”后面也接这串（`way`）；火车那支有“这班火车怎么坐”，照旧。框的时刻（`frame.times`）含空档前后两班，服务的首行原样抄过 `check_answer` 不再报 `frame_matches_server`。要换车的一趟不用首程线路叫“最后一班”：期限查询写“赶得上 HH:MM 的最后一班是 HH:MM 出发：线路 接 线路，<站> 开（HH:MM 出门）”；`told.last` 加 `lines`、`last_from`，第一段写“今晚最后一班 HH:MM 在 <首程站> 上车：线路 接 线路，<站> 开”。不换车的照旧。
- **`check_answer` 的严重度：** `problems[]` 每条加 `severity`：`block` 或 `hint`。`ok` 只看 `block`；只剩 `hint` 的第二段照“过核”记下（`passed`），Stop 钩子照它比对。`hint` 的 `fix` 以“可以改得更好（不改也能发）：”开头。`summary`：有 `block` 写“要改 N 处再发：…”（另有提示时加“另有 M 处可以改得更好，不改也行”）；只剩提示写“可以照原样发；M 处可以改得更好，不改也行：…”；末尾都加“别向用户提这次检查、核对或工具回执。”
  - 拦（`render.check_answer.BLOCKING`，部分只拦一支）：`clock_in_first_part`（只拦写的“现在”和钟差 5 分钟以上；前两行没钟是提示）、`assumed_place_matches_plan`、`checked_has_evidence`（“核过”行有任何证据里都没有、又不是“约 HH:MM”的时刻，或点名不算数的证据号；推出来的“约”时刻、没有时刻的行是提示）、`only_inside_window`（“首班”没写前一班是提示）、`direct_needs_one_ride`（说直达、查到要换车；新加一支：说某程车“到 X”，而查到的同一程只到别处、X 要换车后才到；对不上查询是提示）、`unreachable_needs_lookup`（没有最晚出发查询是提示）、`denied_needs_coverage`、`fallback_later`（两个时刻都是查到的出发时才拦）、`missing_ride_found`（说没查到的是那班车的站台、实时一类时是提示）、`leave_before_closing`。
  - 其余都是提示：`no_tags`、`internal_words`、`no_process_notes`、`answers_each_point`、`place_in_first_lines`、`archived_marked`、`latest_needs_lookup`、`per_person_alone`、`no_status_line`、`second_heading`、`frame_matches_server`、`count_needs_continuous`、`meals_named`、`title_time_not_later`、`search_matches_main`、`coverage_said_short`、`first_times_followed`、`timetable_said`。
  - 删掉：`first_part_lines`、`ends_with_later`、`plan_heading`、`second_part_lines`（`scripts/answer_checks.py` 的同名分项一并删）。“later 那一行之后还有字”只由 Stop 钩子管（`later_ending`）。
- **钩子：** PreToolUse 钩子认第一段只看形状（`first_part_shape`：前两行有钟、没有标签），和 Stop 钩子同；`first_part_problems` 删掉。Stop 钩子认第二段：有“第二段”或“完整安排”标题，或等于这一问某份过核草稿（`sha256`）；没标题、没过核的第二段照旧被推去核。
- **给宿主的话：** 服务端 instructions 与主 SKILL 的常驻第一条加“nor talk of checks”（为守住两处字节上限，同句“only when you send it”缩成“once sent”，开头一句缩成“Do the steps in order; go on when its check holds.”）；第 7 步不改字：只剩提示时 `ok` 就是 true，提示的 `fix` 自己说“不改也能发”。`turn.next` 的“第二段过了 check_answer 才发”加“（ok:true；只剩提示就照原样发）……不向用户提检查、核对或工具回执”；`do_first` 在“继续”那一轮（`DO_DUE`）加“不提检查和工具回执”；`check_answer.summary` 末尾加“别向用户提这次检查、核对或工具回执。”；`query` 的工具说明照严重度改写；单 agent（l0）角色那一行改为按 `block` 的 `fix` 改。

## 4 源文件与生成文件

写域的机读件是 `tests/contracts/manifest.json`；契约测试核“仓里每个文件恰好属于一个写域”“生成文件在生成者的写域里”“`.tmpl` 只在 C”“机读件里的每个路径式都写在本页”。生成文件首行写“由 <生成器> 生成，源：<路径>；不要手改”（Markdown 用 HTML 注释，TOML 用 `#`）。两个例外（10-31 主会话定）：带 YAML frontmatter 的文件（`SKILL.md`、`.claude/agents/*.md`）把这句写成 frontmatter 里第一行的 YAML 注释，因为宿主只在第 1 行认 frontmatter；用户仓的 `CLAUDE.md` 只有 `@AGENTS.md` 一行，不带这句。生成者自己的测试核“重新生成＝已提交”。

**写域（文件与目录）：**
- A：`src/tripwright/repo/**`（仓格式、正本分支、发件箱、重放、同步、外部改动、索引与快照）、`src/tripwright/packs.py`（约束的语义筛选与完整性标记）、`src/tripwright/storage.py`、`src/tripwright/events.py`、`src/tripwright/ledger.py`、`src/tripwright/consider.py` 与 `src/tripwright/consider.yaml`（每一问该考虑的记忆、来源与须知的合并、改动频率表；M1）、`src/tripwright/versions.py`、`src/tripwright/jobs.py`、`src/tripwright/service.py`、`src/tripwright/cli.py`、`src/tripwright/demo.py`、`src/tripwright/adapters/__init__.py`、`src/tripwright/adapters/mcp.py`、`src/tripwright/remote/**`（远程入口：OAuth 代理、账号名单、单人租户、写回用户仓；R1）、`examples/**`、`docs/FORMAT.md`、`tests/repo/**`、`tests/core/**`、`tests/remote/**`。
- B：`src/tripwright/render/**`（包与回答渲染、交通行与地图链接、地区表 `regions.yaml`、查询触发、机械检查、写法规则源片段 `fragments/*.md`、首屏与视图）、`src/tripwright/site/**`、`src/tripwright/adapters/web.py`、`tests/render/**`、`tests/site/**`。
- C：`src/tripwright/agents/**`（角色源 `roles/*.md`、单 agent 步骤源 `steps/*.md`、模板 `templates/*.tmpl`、生成器与生成件 `generated/**`）、`src/tripwright/adapters/prompts.py`（MCP prompts 注册）、`skills/**`（主 skill 只由 C 装配）、`tests/agents/**`。
- 冻结核心（三块都不写，要改经主会话）：`src/tripwright/__init__.py`、`src/tripwright/checks.py`、`src/tripwright/trip.py`、`src/tripwright/usage.py`、`src/tripwright/evidence.py`、`src/tripwright/locks.py`、`src/tripwright/query.py`、`src/tripwright/providers/**`、`src/tripwright/solver/**`、`tests/providers/**`、`tests/solver/**`。
- 主会话与接缝负责人：`pyproject.toml`、`uv.lock`、`uv.toml`、`.python-version`、`.gitignore`、`.github/**`、`README.md`、`README.zh-CN.md`、`LICENSE`、`homepage/**`、`scripts/**`、`deploy/**`、`docs/CONNECT.md`、`docs/DATA_SOURCES.md`、`docs/CONTRACTS.md`、`tests/contracts/**`、`tests/fixtures/contract_sample/**`。
- 只追加：`docs/DECISIONS-impl.md`，各块只在末尾写自己的节。
- 整理拆出的文件（11-04 起）：`docs/history/**`（DECISIONS-impl 与本页 3.9 的旧卷，主会话与接缝负责人维护）、`docs/CONTRACTS-queries.md`（3.6 拆出的现行查询约定，主会话与接缝负责人维护）。

**本仓里的生成文件：** `skills/tripwright-main/SKILL.md` 由 C 装配，源是 C 的角色源与步骤源；写法页 `references/writing.md` 由角色源的 `reference: writing.md` 生成（v1.8 起，v2g；B 的 `render/fragments/*.md` 不再整段嵌入主 skill，公共片段接口照留，供 B 自己的渲染用）；其他 `skills/tripwright-*/SKILL.md` 与 `src/tripwright/agents/generated/**`（`.claude/agents`、`.codex/agents` 格式、MCP prompts、L3 提示词、L0 步骤稿、服务端 instructions）也由 C 生成。

**用户行程仓里的最终路径**（运行时由 A 的提交器写盘；“生成者”指决定字节的块）：
- A 生成：`tripwright.yaml`、`profile/**`、`trips/*/trip.yaml`、`trips/*/raw/**`、`trips/*/ledger/**`、`trips/*/plan/**`、`trips/*/evidence/**`、`.gitignore`、`.gitattributes`（布局由代码写，不用模板）、`.tripwright/**`（不进 Git）。
- B 生成：`README.md` 与 `trips/*/README.md`（首屏）、`trips/*/views/**`（今天、每天、预编包、索引、决定、记忆 `memory.md`）。A 在同一次提交里写盘，不改字节。
- C 生成：`AGENTS.md`、`CLAUDE.md`（源是 `templates/*.tmpl`）、`.claude/agents/*.md`、`.codex/agents/*.toml`、`.mcp.json`（把 MCP 服务注册为 `tripwright`，Claude 子代理的禁令按这个名字写）、`.claude/settings.json`（预先放行 tripwright 的工具、网页搜索与抓取、子代理和只读 Bash，免得手机与云端会话逐次问；不放行写、删、推送类；v1.6 起带 3.6 的两个钩子；源是 `templates/settings.json.tmpl`，D-39、D-45）、`.claude/skills/*/**` 与 `.agents/skills/*/**`（四个角色的 skill，Claude Code 与 Codex 各自的仓内位置，让 L1 宿主不连 MCP 也能发现工作法；源是 `generated/**` 与 `skills/**`）。A 的 init 与升级写盘。
- 用户：`trips/*/research/**` 是原件，工具只追加。

**跨块的接线（要对方改的一行，经主会话转达）：** `adapters/mcp.py`（A）调 C 的 `register_prompts(server)`，并从 C 的生成件读服务端 instructions；`service.py`（A）调 B 的 `render_pack` 与 `render_answer`；A 的提交器调 B 的视图函数取首屏与视图的字节。

样本的时间线与各文件用途见 `tests/fixtures/contract_sample/README.md`。契约测试 `tests/contracts/` 只核样本自洽与写域，不实现 A、B、C。
