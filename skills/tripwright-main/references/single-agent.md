<!-- 由 python -m tripwright.agents build 生成，源：src/tripwright/agents/steps/*.md；不要手改 -->
# Single-agent steps

Read this from `SKILL.md` when the host cannot dispatch subagents, or when a step is unclear. Each step has a version for hosts with the Tripwright tools and one for chat apps without them. Without tools, every reply ends with one 写回仓 block that collects what its steps add.

Choose the steps by scenario:
- Starting a new trip: 0, then the steps of the scenario.
- Quick question: 1 → 2 (only the people and time involved) → 3 → 4 (the facts the user will act on) → 5 (record only what the message carried).
- Status report only: 1 → 5.
- Replan or plan from scratch: 1 → 6.
- A large batch of new requirements: 1 → 2 → split into points and mark conflicts → 5 (proposals only).

## With tools

### 0. Start a new trip

- Open: `turn(new_trip="YYYY-MM-name")` with the user's message. It syncs first; while writes are not in the canonical branch it refuses, so run `tripwright sync` (the remote must be reachable) and call again. The new trip imports every long-term entry as pending.
- Read: `state(view="memory")`: each long-term entry (preference, companion, standing rule, material) with its words, source trip and message, status here and last use.
- Do: list them in the conversation, grouped, one line each: the gist of the source sentence and the trip it came from. Ask which still apply to this trip; one answer may cover a group (“偏好都照旧，这次没有 u2”). Record the answer with `turn(text=<the user's answer>, request_id=..., record={"carry": [{"id": "<entry ID>", "status": "confirmed" | "not_used", "source": {"event": "this"}}]})`.
- Show the user: the grouped list and one question. Never ask the user to open or read a file.
- Until the user answers, every long-term record is pending: use it and mark it “待确认” in the line of conditions used. A record the user keeps is confirmed for this trip; one the user drops is not used this trip and stays in history.

### 1. Receive the message

- Read: the user's message and its attachments.
- Do: call `turn` with the text as received, its source nature (user's own words, host restatement, screenshot extraction), a stable `request_id` and the scenario. Put the facts and items the message states in `record` (`source.event: "this"`), so the pack that comes back already has them.
- Show the user: nothing. When asked what was received, give the receipt.
- If the scenario is unclear, take the more careful one ("I am leaving now, what next" is a replan). Do not stop to ask.

### 2. Take the applicable conditions

- Read: the pack from `turn` (applicable items, scoped facts, user decisions, hard nodes, open checks, answer rules) and each group's status.
- Do: list the conditions this answer must use, each with its source sentence, object (who, which day, which leg) and strength. List separately the dimensions the user said not to consider. Note each pair of the user's preferences that compete without a user order: step 3 gives options for them.
- Show the user: nothing.
- If conditions contradict each other or the new message: continue with the default reading, say so in the first response and list it as a question. Never invent a numeric threshold.

### 3. Give the first part

- Read: the list from step 2. No query lookup or web search before the first part. Do not reuse hard-node claims marked “存档”; quote them only as “计划里写过…（存档，没核）”. Stored “only”, “last” or “no later service” is not a current fact; mark stored times unchecked.
- Before planning days or choosing places, ask whether there are must-go places or fixed times; copy the tool's question when present, otherwise ask “有没有必须去的地方或必须守的时间？”. If already answered, use that answer; otherwise plan provisionally with no musts. Set `must: true` only when the user said so, never from an inferred preference.
- Do: for latest-departure, last-service or can-I-make-it questions before any lookup, open with “真正最晚要查（第二段给）；计划里留的是 HH:MM 出门（存档，没核）”; omit the stored time when none exists, then give an actionable next step. A stored time must not lead as the answer, even with “按计划” or “没核”. For other questions, line 1 answers directly; a condition the answer depends on and the user did not give (where they are, the time) is an assumption in the first two lines. Start from where the user says they are. Where the user's own preferences compete, give the options from the order rules. For a replan, after sending it call `propose` with the complete candidate and a usage for every applicable item; keep `draft_id` and `job_id`.
- Show the user: the first part, as its own message, ending with what the second part will check.
- Then go on to step 4 in the same turn. If the host cannot keep working after showing text, end with “第二段要核 N 项，回“继续”我接着核。”

### 4. Check from new input

- Change the input: do not check only the first part's unchecked lines. List every fact the user will act on: opening hours now, the last departure and the night fallback, an event tonight, whether a building is open at night, ticket validity. For a replan also re-read the complete candidate and the full text of every applicable source sentence (`state(view="pack", draft_id=...)`, the wide pack), the checks from `result(job_id)`, and the scenario checklist: who each block belongs to and who carries the luggage; the time chain from now to each hard node with buffers; exact branch, date and opening; booked tickets, their holder and validity; hard budget and stamina.
- Sources: the host's web search and fetch (the operator's or venue's own page), and `query` where it returns live data. Synthetic or demo evidence is not a source.
- Freshness: on `stale` say only “已过期，需重算” and start again from step 1's `turn`; on `unsettled` record what the newer messages carry, or acknowledge them with `ack_events`.
- Do: give every fact a conclusion: checked (source), conflict, or not checked with its consequence. Copy transport `sentences` and window or `coverage` text verbatim; follow `note` once for the transport section (timetable, not live), not every departure, keeping per-leg live markings. For latest/last/remaining-service questions, use transit `until` continuously through next-day 03:00 for tonight; report incomplete coverage and gaps. In latest_departure, `by` is an arrival deadline; to catch a service use `board_at` with its boarding station as destination. Empty results never imply no service. A conflict that needs a change means a new `propose`.
- Before sending a changed plan as the final second part, run step 5's commit, read its accepted `changelog`, and include what changed and why (agent revisions: “你之前定的 X，我改成了 Y，因为 Z；要改回就说一声”). A refused change or already-used authorization says “这次没改成”. Then run `check_answer` on the complete final draft and send that passed draft exactly.
- Show the user: the second part, as a new message. For a latest-departure question, its first body line gives the latest departure actually found, then differences; other questions give differences first. Then the full plan and remaining unknowns. End with “## 怎么查、怎么问”: copy `how_to_check.text` and `link` with the app, search and steps for unavailable facts. Stable facts are checked by you. If a person must be asked, the section's last line is only “我可以帮你起草询问内容（英语、<当地语言>）”; draft on request, with English and the local language. Any source or site link goes before this final section.
- If a critical fact that affects a hard node cannot be checked: say so in the second part's title, with the consequence and the fallback. Do not commit before the user answers.

### 5. Record

- Do: what the message states went into step 1's `record`. Call `propose` with the `ctx_id` and `base_version` from `turn` for decisions and plans (with declarations and usages), read its checks, then call `commit` with a stable idempotency key. A one-off question becomes no record. Only explicit must decisions lock. To revise a non-must user decision, give `changes.revisions [{decision, reason}]`; use `changes.why` for the proposal. A must changes only by an admitted new user decision, report or confirmed correction (see “Split, label, declare”); bought tickets and booked times still need a source.
- Show the user: accepted plan changes and their reasons from the commit's `changelog`, in the second part before “怎么查、怎么问”. An agent revision with `revises` says “你之前定的 X，我改成了 Y，因为 Z；要改回就说一声”. Never tell the user write states; a conflict's `user_line` opens the answer. On `not recorded (reason)`, add the reason and resubmit or ask; on `locked:`, ask rather than rephrase and retry. A refused commit or authorization `used` says “这次没改成”; do not claim the change happened. Points waiting for the user are asked in the answer.
- If the commit is refused because the version or the ledger changed: go back to step 2 with a new `turn`. Never force it.

### 6. Update the site

- Do: only after the write state is `effective`. The service regenerates the site and views from the new version; agents do not edit pages. Give the result page link, if there is one, before the final “怎么查、怎么问” section; that section stays last.
- If generation fails: report it in one line. Do not change the answer already given.

## Without tools

### 0. Start a new trip

- Read: `trips/<trip>/views/memory.md` (each long-term record with its status for this trip) if it exists; otherwise the files in `profile/` that the user pastes. Ask for them by path.
- Do: the same grouped list and the same question in the conversation.
- Show the user: the list and one question; never ask the user to read a file to decide.
- Until the user answers: the same as with tools.

写回仓, this step adds the carry-over answer once the user gives it:

```
file: trips/<trip>/ledger/carry/<ID>.yaml (one per long-term entry)
id: <entry ID>
status: confirmed | not_used
status_event: <ID of the message that answered>
```

### 1. Receive the message

- Read: the user's message and its attachments.
- Do: copy the text exactly as it arrived. Mark it as the user's own words only when it is; a screenshot transcription or your summary is marked as such.
- Show the user: nothing. When asked what was received, quote the raw file you wrote.
- If the scenario is unclear, take the more careful one. Do not stop to ask.

写回仓, this step adds a new raw-message file that never changes afterwards:

```
file: trips/<trip>/raw/<local date>/<UTC time HHMMSS>-<speaker>.md (new)
received_at: <local time with zone>
speaker: <person ID>
nature: user_verbatim | host_restatement | screenshot_extraction
---
<the text exactly as received>
```

### 2. Take the applicable conditions

- Read: `trips/<trip>/views/packs/<date>.md` if it exists; it is compiled for that day, all people and the conversation, one level less precise than a tool pack. Otherwise only the records for the people, date and place involved: `profile/items/`, the trip's `ledger/items/`, `ledger/facts/`, `ledger/decisions/` and `ledger/checks/`, `plan/days/<date>.yaml` and `plan/bookings.yaml` under `trips/<trip>/`. Ask the user to paste a missing file by its path.
- Do: the same list as with tools.
- Show the user: nothing.
- On contradictions: the same as with tools.

写回仓: this step adds nothing; the list stays in the conversation.

### 3. Give the first part

- Read: the list from step 2. No search yet.
- Do: the same first part, including the deferred real-latest answer, must-go question when planning and no stored negative transport claims. For a replan, then write the complete candidate day in the conversation; step 4 checks it.
- Show the user: the first part.
- Then step 4, as with tools.

写回仓: this step adds nothing yet; the candidate is not saved before step 5.

### 4. Check from new input

- Change the input the same way: list the facts the user will act on; for a replan re-read the candidate you wrote in step 3, the source sentences from the files, and the same checklist.
- Do: search the web for each fact if the app can. There is no deep-check verdict and no commit: a changed plan is titled “安排（没核完）”, with “（草稿）” until the user confirms the commit.
- Show the user: the second part, checked latest departure first when found, otherwise the remaining uncertainty; then differences and draft changes with reasons, ending with “怎么查、怎么问” as above. Use only verified app steps or say they are unknown; there is no server `sentences` or accepted `changelog` without tools.
- On a critical fact you cannot check: the same as with tools.

写回仓, this step adds one file per check you could not close:

```
file: trips/<trip>/ledger/checks/<ID>.yaml (new)
question: <what is not checked>
due_at: <local time with zone>
consequence: <what happens if it is wrong>
fallback: <how to confirm on site, or what to do instead>
```

### 5. Record

- Do: write the 写回仓 block. Ask the user to commit it and to reply “已提交”.
- Show the user: one line: what the block records and which points wait for the user. Until the user replies, it is not saved: say “写回仓块在下面，提交后才算存好”.
- If the user's files changed since you read them: read them again and rewrite the block. Keep explicit musts until the user changes them; describe proposed non-must changes and their reasons as drafts.

写回仓, this step adds every record and the plan, then the commit message:

```
file: trips/<trip>/ledger/items/<ID>.yaml (new; facts and decisions likewise), one record per file
file: trips/<trip>/ledger/journal/<local date>.jsonl (append one line: which message changed which IDs)
file: trips/<trip>/plan/days/<date>.yaml (the whole new content)
file: trips/<trip>/plan/versions.jsonl (append one line: version, base version, records used)
commit message: tripwright: <one line>
```

New IDs are type, date and a random part, for example `I-301031-c7d`. Use the fields of the existing files in the same folder. A long-term preference goes into `profile/items/`, so the next trip can carry it over after the user confirms.

### 6. Update the site

- Do: only after the user said the block is committed. The repository's Pages or Actions build the site from the files; do not edit `README.md` or `views/`.
- If the build fails: report it in one line. Do not change the answer already given.

写回仓: this step adds nothing.
