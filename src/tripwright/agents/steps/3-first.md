---
step: 3
title: Give the first part
summary: Within about two minutes, from stored conditions before any lookup; assumptions first.
---

## With tools

- Read: the list from step 2. No query lookup or web search before the first part. Do not reuse hard-node claims marked “存档”; quote them only as “计划里写过…（存档，没核）”. Stored “only”, “last” or “no later service” is not a current fact; mark stored times unchecked.
- Before planning days or choosing places, ask whether there are must-go places or fixed times; copy the tool's question when present, otherwise ask “有没有必须去的地方或必须守的时间？”. If already answered, use that answer; otherwise plan provisionally with no musts. Set `must: true` only when the user said so, never from an inferred preference.
- Do: for latest-departure, last-service or can-I-make-it questions before any lookup, open with “计划里是 HH:MM 出门（存档，没核）；几点出发要查，第二段给”, with the stored way when there is one; omit the stored time when none exists, then give an actionable next step. No 最晚, 末班 or 最后一班 time before a lookup. A stored time must not lead as the answer, even with “按计划” or “没核”. For other questions, line 1 answers directly; a condition the answer depends on and the user did not give (where they are, the time) is an assumption in the first two lines. Start from where the user says they are. Where the user's own preferences compete, give the options from the order rules. For a replan, after sending it call `propose` with the complete candidate and a usage for every applicable item; keep `draft_id` and `job_id`.
- Show the user: the first part, as its own message, ending with what the second part will check.
- Then go on to step 4 in the same turn. If the host cannot keep working after showing text, end with “第二段要核 N 项，回“继续”我接着核。”

## Without tools

- Read: the list from step 2. No search yet.
- Do: the same first part (`tripwright first-part` drafts it from the repository when it can run), including the deferred latest answer, must-go question when planning and no stored negative transport claims. For a replan, then write the complete candidate day in the conversation; step 4 checks it.
- Show the user: the first part.
- Then step 4, as with tools.

写回仓: this step adds nothing yet; the candidate is not saved before step 5.
