# Implementation decisions — tripwright（各块实现决定；只追加，新节写在文件末尾）

> 11-04 照 doc-governance 整理拆卷（原文件 326 KB，超 50 KB 红线）：11-03 及以前的节按日原样搬进本目录 `history/`，一字未改、现行有效；本文件照旧是各块追加自己节的地方（见 `CONTRACTS.md` 第 4 节“只追加”）。留在本卷的旧节只有 B3f（锁字段表，`tests/core/test_b3f_field_table.py` 机读）与回答层 v2。

## 旧卷索引（内容原样，按日期）

- `history/2026-1031-DECISIONS-impl.md`：core v0.1、B3a、视图、B3c、接缝约定（10-31 第一版）、C 角色与装配、B 渲染与输出、B3d、A 仓与快照、B3e
- `history/2026-1101-DECISIONS-impl.md`：回答层 v2b、A2、A3、A5、A6、A7、B3g、B3h、B3i、B3j、B3k、A8、v2g、R1
- `history/2026-1102-DECISIONS-impl.md`：M1、A9、A10、R2、A11、R2d、A12（含 a12b）、r2e、A13、A14、A15、A16
- `history/2026-1103-DECISIONS-impl.md`：A17、A17b、S4a、s4c、S4a2、s4b、S4e、S4h-a、S4h-b、S4h2、S4h3、S5
- 留在本文件：B3f、回答层 v2（均 10-31，见下）、11-04 节（最新）

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

## 11-04：服务写的句子说全说准，检查分轻重（2026-11-04，worker impl-content；主会话 191cd345 派）

依据：用户 11-04 01:26 改的判法（看内容对不对、引导对不对、防没防住内容错，不做死板的规则匹配、不要固定格式）；判卡 v2 的两份非作者判词（判词 a 的 A、B 两段）；检查器盘点 `checker-audit.md`（32 个问题码的逐条建议与探针）。

- **一、计划车的句子写整段走法（`query.plan_rides`、`query.way_sentence`、`providers.connections.leg_line` 的 `toward`）。**
  - 现象：“计划里的 22:42 RJX 19959 赶得上：22:00 关门出来（按存的营业时间），走到 Wien Brigittaplatz 坐 22:16 的 5B”。12 轮第 1 问都受影响，9 轮补成“5B 到 Hauptbahnhof”；5B 只跑 Heiligenstadt—Praterstern，要在 Praterstern 换 U1。
  - 为什么没有分段：分段一直在。`plan_rides` 匹配到的是这次查询里的整条连接（`match[0]`，步行、5B、U1、RJX 都在），句子却只取了 `leave_words(..., ride=True)`，即出门与第一程上车；精简结果又把这条连接删掉（`plan_rides` 里的 `itinerary`），`connections` 也只在 `detail: full` 里有。所以宿主手里只有那半句。
  - 改：句子写 `way_sentence`：出门（起点先关门时照 A16 写关门出来）、换几次车、每一程线路加终点牌方向（“5B（往 Praterstern）”，只在这一句里写，别的交通句不动，免得全仓句子和黄金样本都变）、上下车站与时刻、到达。腿首尾接不上时只写接得上的几程，接“之后怎么换车没查到，在 <App> 搜 <断处> → <下一程上车站>”。真实 Transitous 数据的腿都接得上；这一支是保底，测试用缺 U1 的假数据。
- **二、“夜里一直有车”只在没有空档时说（`query._last_connection`、`SERVICE_HOLE`、`render.first_part.go_line`、`packs.stored_frame`）。**
  - 现象：首行“今晚不用赶：夜里一直有车到机场”；00:53 末班火车之后到 02:52 才有 U1 接机场大巴。原来的判法只看“今晚截止前最后一班”和“截止后第一班”隔没隔 2 小时（`NIGHT_GAP`），不看窗口里面。
  - 改：把今晚查到的、坐车的出发（加截止后第一班）排好，相邻两班隔超过 60 分钟就算空档；有空档不说“一直有车”，首行写最长的那段：空档前最后一班（是火车就写“火车最后一班”）与空档后第一班的出门、线路、到达。取最长而不是最早的一段，是因为一晚上可能早早有一段 70 分钟的小空档，读的人最要知道的是夜里那段。空档之后的那班要用 `leave_words`（起点关门时写上车）；第一段（`turn`）照存的框写“夜里有空档，……之后要等到 HH:MM 的 线路 才再有车”。
  - 顺手查了服务写的其它“直达、只剩、没有车了”：期限框的“之后没有赶得上的班次”“误了就赶不上”都只在 `covers_target` 时说；“不用换车”按程数生成；第一段的“今晚最后一班”只在夜断（`night_break`）时说。没有找到同类说过头的。期限框的“赶得上 HH:MM 的最后一班是 HH:MM 线路”点名的是第一程的线路，读起来像那一程是最后一班；不是事实错，没改，记在这里。
- **三、检查分轻重（`render.check_answer`：`problem(..., severity)`、`BLOCKING`、`graded`、`blocking`；`service.check_answer`）。**
  - 每条问题带 `severity`。照盘点：留着拦 10 个（8 个收窄，收窄掉的那一支在函数里自己标 `HINT`），改提示 18 个，删 4 个（`first_part_lines`、`ends_with_later`、`plan_heading`、`second_part_lines`，连 `scripts/answer_checks.py` 的同名分项）。`ok` 只看 `block`；只剩提示的第二段照样写 `passed` 与草稿指纹。提示的 `fix` 前加“可以改得更好（不改也能发）：”。
  - 收窄怎么判：`checked_has_evidence` 漏掉的时刻都带“约、大约、about”一类（`approximate_times`）或行里没有时刻时算提示，证据号不算数、写成核过的起点关门时刻仍拦；`fallback_later` 两个时刻都是这一问查到的出发（`departures`）才拦；`missing_ride_found` 在“的站台没查到”“查不到实时晚点”这类说的是车的某样东西时算提示（`ABOUT`）；其余照盘点。
  - 跟盘点不同的一处：`direct_needs_one_ride` 加了一支“某程车到 X，查到的同一程只到别处、X 要换车后才到”（`reach_problems`）。盘点里没有这一条；它防的正是判词 a 的 A 段（“坐 22:16 的 5B 到 Wien Hauptbahnhof”），属于“把要换乘说成直达”，所以归在同一个码下，拦。站名按时刻表原名、去掉城市名的短名和地点表里同名地点的中文名（去括号）认；有任何一条连接里这程车自己就到 X，就不拦。
- **四、钩子（`stop_decision`、`first_part_decision`）。** Stop 钩子认第二段：标题，或等于这一问某份过核草稿的指纹；所以 `second_heading` 能降成提示。没标题也没过核的第二段，钩子认不出是第二段，照旧推“在后台派 researcher 核查，再写第二段”。PreToolUse 钩子改用 `first_part_shape`（和 Stop 钩子同），`first_part_problems` 删掉。
- **五、给宿主的话。** 常驻第一条、`turn.next`、`DO_DUE`、`check_answer.summary`、`query` 工具说明、l0 角色那一行。两处字节上限卡得很紧：instructions ≤3,600（改前 3,586；`test_a_changed_step_reaches_every_place_only_through_the_build` 还要往里加 11 字节，所以实际只能到 3,589），主 SKILL ≤12,225（改前 12,217）。所以常驻一条只加“nor talk of checks”，同句“only when you send it”缩成“once sent”，开头一句缩成“go on when its check holds”；第 7 步不改字（只剩提示 `ok` 就是 true，提示的 `fix` 自己写“不改也能发”）；“工具回执”写在 `next` 与 `summary` 里。改后 instructions 3,587，主 SKILL 12,225。
- **测试。** `tests/core/test_v2_content.py`（4 条）、`tests/core/test_v2_checker.py`（8 条）；坏样本在改前的代码上红，记录见交回的 REPLY。改了的旧测试只改断言里的新句子、新字段和被删的检查。
- **限制。** 检查仍靠字面找句子，过了不等于说对了，内容质量照判卡 v2 判。`reach_problems` 只认“到、→、开往、去往、to”后面紧跟的站名，换一种说法（“5B 一直坐到总站”）认不出；也认不出没在任何连接里的线路。盘点里的漏判 H（“唯一能坐的是 22:16 S7”而查到 22:42 RJX）没修。
