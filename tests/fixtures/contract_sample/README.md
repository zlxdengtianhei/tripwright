# 契约样本（虚构）

本目录是产品行为的虚构样本；改它要同时改 `tests/contracts/` 与使用它的产品行为测试。

`tests/fixtures/contract_sample/`：两人（u1、u2）两天的虚构行程。一个硬节点（07-15 19:30 晚饭，最晚 18:05 出发）；一条用户锁定的决定（晚饭地点）；u1 在同一条消息里说“今天我一个人，没有箱子”又问怎么走；两条互相挤的偏好（省钱、轻松）没有用户定的次序。
- 正本：`ledger.json`（原话、记录、流水、版本）、`trip.json`（V3）、`candidate.json`（草稿 DR-300715-b8d 的那一天，两套方案）。
- `snapshots.json`：S1—S4。`pack_narrow.*` 绑 S1，`pack_wide.*` 绑 S2。
- `answer_{quick,first,second}.*`：三种骨架的数据与逐字预期。
- `answer_en.*`：英文第一段（同一情形）。交通行与地名行都写明在哪个 App 搜什么（search），链接只是方便（用户 10-31 新要求）。
- `pack_partial.*`、`pack_over_budget.*`、`pack_unknown.*`：由 `pack_narrow` 派生，记录不变。partial 预算 1,690，偏好降成编号行，条目组降为 partial；over_budget 预算 1,610，能降的都降了仍超，包头写“硬约束过多”；unknown 的条目组适用集没定，包里写原因。`*.required.json` 是 `render_pack` 返回的 required 与 token 估算（估算不冻结，改估算法时一起重生成）。
- `pack_omitted.*`：由 `pack_narrow` 派生的快问包（S1，时段 13:00—15:00，预算 1,695）；非必需只有两种：窄包里到期晚于时段末的待核、快问与上报实况里的目标；整包放不下时它们先整批省掉、记 `over_budget`，这里是 15:30 到期的 P-300714-w6t，其余记录不降级。
- `write_states.json`：一个操作走到已生效，一个走到冲突并发通知。
- `sequence.json`：深核期间只改实况 → 取回过期；只多一条不带账的原话 → 未定 → ack 后才交；已展示的草稿冲突 → 下一次 turn 先给通知。
- `tests/contracts/` 核样本自洽；`tests/repo/test_packs.py` 等测试把同一样本交给产品实现核行为。
