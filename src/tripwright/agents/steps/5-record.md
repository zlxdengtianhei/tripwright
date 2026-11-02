---
step: 5
title: Record
summary: Propose, then commit; describe accepted plan changes and reasons, or say the change was refused.
---

## With tools

- Do: what the message states went into step 1's `record`. Call `propose` with the `ctx_id` and `base_version` from `turn` for decisions and plans (with declarations and usages), read its checks, then call `commit` with a stable idempotency key. A one-off question becomes no record. Only explicit must decisions lock. To revise a non-must user decision, give `changes.revisions [{decision, reason}]`; use `changes.why` for the proposal. A must changes only by an admitted new user decision, report or confirmed correction (see “Split, label, declare”); bought tickets and booked times still need a source.
- Show the user: accepted plan changes and their reasons from the commit's `changelog`, in the second part before “怎么查、怎么问”. An agent revision with `revises` says “你之前定的 X，我改成了 Y，因为 Z；要改回就说一声”. Never tell the user write states; a conflict's `user_line` opens the answer. On `not recorded (reason)`, add the reason and resubmit or ask; on `locked:`, ask rather than rephrase and retry. A refused commit or authorization `used` says “这次没改成”; do not claim the change happened. Points waiting for the user are asked in the answer.
- If the commit is refused because the version or the ledger changed: go back to step 2 with a new `turn`. Never force it.

## Without tools

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
