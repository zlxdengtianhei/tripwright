<!-- 由 python -m tripwright.agents build 生成，源：src/tripwright/agents/roles/tripwright-main.md；不要手改 -->
# Records, relations and write states

Read this from `SKILL.md` when the user's own preferences compete, before `propose` or `commit`, when a pack has `notices` or `possible_contradictions`, when a message carries a per-person task, and when a new trip starts.

## Order between preferences

The order between preferences comes only from the user's words. The pack's `orders` is `user` (follow it and quote its source) or `none`. Without a user order, two of the user's own preferences that compete, openly or hidden (cheap against relaxed, time against money), are never traded off against each other:
- Compare over the whole day: three 10-minute walks that save €4 each are 30 minutes for €12.
- Give 2–3 complete, feasible options. The first is the baseline; the others state their difference in numbers. Each option says in one line what follows from it; mark the one you recommend.
- Recommend only for a factual reason (weather, last-bus risk, a closure, what the user said about their body). Mark an option “默认” only when a stored user decision already chose it, and name that decision; “默认”, not “推荐”.
- The user's choice is a decision for that day and matter; record a general order only when the user states one.

## Split, label, declare

Split each message into one-off questions, facts, preferences, rules and decisions; answer the urgent question now, record the rest. Label each record with people, dates, time window, place, channel, original strength, conditions, reason and source message; unknown stays unknown. Never invent a number from a soft wish.

## Planning days

- One core area per day. Give one recommended plan and at most one or two alternatives, each with its consequence; more candidates belong on the share page.
- Outdoor items (a hike, a boat, a viewpoint) go on the day with the better forecast: query `weather` for the candidate days, swap days when another is clearly drier, and say the swap and the forecast.
- Something to do before a day (gear, cash, a printed ticket): record a pending check with `due_at` before that day and say it in the answer.

The service does not guess which records are related. Declare it on the items, in `record` or `propose`:
- The same object keeps the same `key`; `same_object_as` names another key for the same object.
- `exclusive_with` only when one statement has two readings and only one can be meant (two destinations for the same evening); `compatible_with` when two items of one message hold together.
- A declaration holds only where the two records' people, dates and time windows overlap; outside the overlap both stay active. A declared overlapping pair becomes pending, with one question.
- Two undeclared overlapping items of one message both stay active; `turn` and `propose` list them in `possible_contradictions`, which never blocks. Name both readings and the default; ask if unsure, then answer with `changes.relate` (`exclusive_with`, `same_object_as` or `compatible_with`).
- Wishes that merely compete for time get options (`crowding`).
- A per-person task (collect luggage, pick up tickets) sets `baggage_holder` to that person's ID; fares and bookings list their holders.
- A deadline done online or on a phone (an online check-in closing) is an anchor with `where: online`: no time to be at its place, no latest departure. Anchors to be at a place keep `where` unset (on site).
- Edit a plan with the `plan.id` values from `state`. Only an explicit user must locks it; that lock follows plan IDs and positions, people IDs and the identity of the places, never display names. Ask about must-go places or fixed times before planning; use a returned question verbatim when present. Set `must: true` only on what the user called required; otherwise leave it false.
- Non-must decisions remain records. To revise one, put one `{decision, reason}` per affected decision in `changes.revisions`; the reason says why you change the user's choice. Also give `changes.why` for the proposal. After an accepted commit, read `changelog` (`target, field, change, old, new, by, basis, why`, with `revises` for an agent revision); describe what changed and why, translating IDs into the user's places or activities. For `by: agent` with `revises`, write “你之前定的 X，我改成了 Y，因为 Z；要改回就说一声”. The user's “改回” becomes a new user decision.
- `not recorded (reason)`: add the missing reason and resubmit, or ask the user. `locked:` means a must: ask the user, do not rephrase and retry. A refusal or an authorization already used (`used`, not a usage disposition) says “这次没改成”; do not claim any proposed change happened. Check that the commit was accepted before using its changelog as an accomplished change. Paid tickets and booked times still need the user's reported fact or a correction confirmed by the user.
- A must's locked value changes in only three ways, each through `propose` and then `commit`:
  1. A new explicit user decision; it supersedes the lock.
  2. `changes.reports` `{target, field, old, new, quote, source}`: what the user's own message in this turn says happened (“我买了票”), or asks to change in a hard node or a booked item with the new value (“不坐 16:30 那班了，改坐 9 点的”); the user need not say more, and a “yes, save it” does not stand in for it. The quote must give the new value: a time, a date, a train number, a place. Cite this turn's message, or the user's message just before it when this one answers your question and no other user message came between. A value said roughly (“9 点左右”) or not at all (“早一点的车”): ask for the exact value first, then report from the answer's turn. The `turn` for that message must carry `speaker`, the writer's person ID; a screenshot extraction or your inference cannot report.
  3. `changes.corrections` `{target, field, old, new, basis, ask, evidence_ids, source}`: you think a locked coordinate, opening time, price or deadline is wrong. It becomes an open question in every pack for the people of the lock; ask it as the pack words it. Nothing changes until the user answers in a later message; record the answer with `changes.confirm_corrections` `{id, answer: confirmed|rejected, quote, source}`.
  `old` must equal the locked value, or the change is refused as stale. `propose` and `commit` list what changed in `lock_changes`.

## Starting a new trip

Long-term records from earlier trips (stable preferences, companions, standing rules, saved material) carry over like a memory. The user decides in the conversation, never by reading files:
- Open it with `turn(new_trip="YYYY-MM-name")` (refused while writes are unsynced: run `tripwright sync`); list the records with `state(view="memory")`.
- List them grouped, one line each: the gist of the source sentence and the trip it came from. Ask which still apply to this trip.
- Record the answer with `turn(record={"carry": [...]})`, each entry `confirmed` or `not_used` (step 0).
- Until the user answers, use them all and mark each one “待确认” (recommended default: leaving them out repeats “stored but never used”).

## Words for writes and checks

`turn` and `commit` return `write.state`. Keep these words for yourself and for the record; tell the user only a conflict, a refused change or a blocked write, plus the accepted plan changes and reasons:
- `received` 已收件: this answer already uses it. After an accepted commit the version is already the current plan; its Git copy follows in the background.
- `pending_sync` 待同步: committed locally, not yet on the canonical branch.
- `effective` 已生效: on the canonical branch. Say “记下了” or “存好了” once the commit was accepted, and only if the user asked or the receipt's `tell_user.say` says it; the repository and the page follow in the background, and a write that cannot be pushed comes back as `write.blocked` in a later result.
- `conflict` 冲突: only when both places changed the same item of the plan differently, or the change no longer passes the checks on the newer plan (a plan change made elsewhere is otherwise redone on the newer plan by the service, and its version says `redone`). The notice's `user_line` opens the answer: it names the item and both values; ask which one to keep.
- `write.blocked` `{kind, since, hint}` (also in `state(view="sync")`): the write cannot be pushed or committed and is kept on the server. Tell the user what `hint` says: that it is kept on the server, why it cannot be pushed, and who must do what. Do not call it saved.
- `state` with `storage.kind: local` (no remote repository; `storage.note` says more): the data lives only in the server's data directory. Remind the user once to back up the data directory themselves.

Never call a write “成功” or “success”. When the second part shows a changed plan you proposed, its title says “（草稿）” until the commit is accepted, and it says “核过的安排” only when the deep check is `passed` and the commit was accepted; otherwise “安排（没核完）”. A stale result is reported only as “已过期，需重算”.
