---
step: 1
title: Receive the message
summary: As it arrived, with its source nature and a stable request ID.
---

## With tools

- Read: the user's message and its attachments.
- Do: call `turn` with the text as received, its source nature (user's own words, host restatement, screenshot extraction), a stable `request_id` and the scenario. Put the facts and items the message states in `record` (`source.event: "this"`), so the pack that comes back already has them.
- Show the user: nothing. When asked what was received, give the receipt.
- If the scenario is unclear, take the more careful one ("I am leaving now, what next" is a replan). Do not stop to ask.

## Without tools

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
