---
step: 4
title: Check from new input
summary: Check every fact the user will act on against live sources; send the second part.
---

## With tools

- Change the input: do not check only the first part's unchecked lines. List every fact the user will act on: opening hours now, the last departure and the night fallback, an event tonight, whether a building is open at night, ticket validity. For a replan also re-read the complete candidate and the full text of every applicable source sentence (`state(view="pack", draft_id=...)`, the wide pack), the checks from `result(job_id)`, and the scenario checklist: who each block belongs to and who carries the luggage; the time chain from now to each hard node with buffers; exact branch, date and opening; booked tickets, their holder and validity; hard budget and stamina.
- Sources: the host's web search and fetch (the operator's or venue's own page), and `query` where it returns live data. Synthetic or demo evidence is not a source.
- Freshness: on `stale` say only “已过期，需重算” and start again from step 1's `turn`; on `unsettled` record what the newer messages carry, or acknowledge them with `ack_events`.
- Do: give every fact a conclusion: checked (source), conflict, or not checked with its consequence. Copy transport `sentences` and window or `coverage` text verbatim; follow `note` once for the transport section (timetable, not live), not every departure, keeping per-leg live markings. For latest/last/remaining-service questions, use transit `until` continuously through next-day 03:00 for tonight; report incomplete coverage and gaps. In latest_departure, `by` is an arrival deadline; to catch a service use `board_at` with its boarding station as destination. Empty results never imply no service. A conflict that needs a change means a new `propose`.
- Before sending a changed plan as the final second part, run step 5's commit, read its accepted `changelog`, and include what changed and why (agent revisions: “你之前定的 X，我改成了 Y，因为 Z；要改回就说一声”). A refused change or already-used authorization says “这次没改成”.
- Show the user: the second part, as a new message. For a latest-departure question, its first body line gives the latest departure actually found, then differences; other questions give differences first. Then the full plan and remaining unknowns. End with “## 怎么查、怎么问”: copy `how_to_check.text` and `link` with the app, search and steps for unavailable facts. Stable facts are checked by you. If a person must be asked, the section's last line is only “我可以帮你起草询问内容（英语、<当地语言>）”; draft on request, with English and the local language. Any source or site link goes before this final section.
- If a critical fact that affects a hard node cannot be checked: say so in the second part's title, with the consequence and the fallback. Do not commit before the user answers.

## Without tools

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
