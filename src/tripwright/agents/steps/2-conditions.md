---
step: 2
title: Take the applicable conditions
summary: Each with its source sentence and object; competing preferences noted, not ranked.
---

## With tools

- Read: the pack from `turn` (applicable items, scoped facts, user decisions, hard nodes, open checks, answer rules) and each group's status.
- Do: list the conditions this answer must use, each with its source sentence, object (who, which day, which leg) and strength. List separately the dimensions the user said not to consider. Note each pair of the user's preferences that compete without a user order: step 3 gives options for them.
- Show the user: nothing.
- If conditions contradict each other or the new message: continue with the default reading, say so in the first response and list it as a question. Never invent a numeric threshold.

## Without tools

- Read: `trips/<trip>/views/packs/<date>.md` if it exists; it is compiled for that day, all people and the conversation, one level less precise than a tool pack. Otherwise only the records for the people, date and place involved: `profile/items/`, the trip's `ledger/items/`, `ledger/facts/`, `ledger/decisions/` and `ledger/checks/`, `plan/days/<date>.yaml` and `plan/bookings.yaml` under `trips/<trip>/`. Ask the user to paste a missing file by its path.
- Do: the same list as with tools.
- Show the user: nothing.
- On contradictions: the same as with tools.

写回仓: this step adds nothing; the list stays in the conversation.
