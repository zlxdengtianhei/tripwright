---
step: 0
title: Start a new trip
summary: Ask which long-term records still apply; until answered, use them marked “待确认”.
---

## With tools

- Open: `turn(new_trip="YYYY-MM-name")` with the user's message. It syncs first; while writes are not in the canonical branch it refuses, so run `tripwright sync` (the remote must be reachable) and call again. The new trip imports every long-term entry as pending.
- Read: `state(view="memory")`: each long-term entry (preference, companion, standing rule, material) with its words, source trip and message, status here and last use.
- Do: list them in the conversation, grouped, one line each: the gist of the source sentence and the trip it came from. Ask which still apply to this trip; one answer may cover a group (“偏好都照旧，这次没有 u2”). Record the answer with `turn(text=<the user's answer>, request_id=..., record={"carry": [{"id": "<entry ID>", "status": "confirmed" | "not_used", "source": {"event": "this"}}]})`.
- Show the user: the grouped list and one question. Never ask the user to open or read a file.
- Until the user answers, every long-term record is pending: use it and mark it “待确认” in the line of conditions used. A record the user keeps is confirmed for this trip; one the user drops is not used this trip and stays in history.

## Without tools

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
