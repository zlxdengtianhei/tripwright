<!-- 由 python -m tripwright.agents build 生成，源：src/tripwright/agents/single-agent.md；不要手改 -->
# One agent, the roles in turn

Tripwright is a multi-agent system: the main agent answers, `tripwright-requirements` records, `tripwright-researcher` looks things up and `tripwright-verifier` checks a draft. When your host cannot dispatch subagents (a chat app, or a host without workers), you do each role yourself, one after another, in the order below. The service returns this page as `state(view="role", role="single")`.

How to do a role:
- Read its steps once in the conversation with `state(view="role", role="<role>")` (in a repository: `skills/<role>/SKILL.md`). Read them again after the conversation was compacted. They are the same steps a subagent follows.
- Hand yourself what `flow.dispatch` lists for it (`read`: event ID, `ctx_id`, `base_version`, `draft_id`, `job_id`), do its steps to the end, and keep what it `returns`. Then go on with the next step here.
- Where `flow.dispatch` names a role this page does not, do that role too; where it names none, skip it. An entry marked `optional` is yours to skip.
- Keep each role's notes out of the answer: the user reads only the main agent's answer.

## On the road (`scenario: "quick"`)

1. Main: `turn` with the user's words, a stable `request_id`, `scenario: "quick"`, the named things in `points`, and in `record` what the message states. Send `first_part` as `next` says and end the reply.
   - A voice-typed message can mishear a word: when one makes no sense in context, do not act on it as written; guess the word meant and ask one short question (“你说的‘X’是指‘Y’吗？”).
   - Leave out the dimensions the user said not to consider (`exclude_keys` in `turn`). When conditions contradict each other or the new message, go on with the default reading, say so and list it as a question.
2. Main: check what the user will act on yourself, with `query` and the host's web search. Here the researcher is optional by design (the user asks and gets an answer directly); read its steps only when a lookup is unclear. What you cannot check goes into the same reply as an unchecked limit with its consequence; do not ask for another message to finish it.
3. Main: the second part under `second_part_title`, first line what differs from the first part.
4. When the message also changes the plan, go on with “Change the plan” from step 4.

## Change the plan (a replan, planning from scratch, or one change such as “move dinner to 19:00”)

1. Main: `turn` with the words only (no `record`).
2. Requirements: record and link what the message says, in one ledger-only `propose`; `commit` that draft first, so the request is kept even if the plan change is not. Keep the questions it lists for the user.
3. Researcher, when the plan needs facts nobody stored: `query(kind="saved")` first, then the lookups its steps name.
4. Main: `propose` the change on the same `ctx_id` and `base_version` (`blocks` or `days` for a small change).
5. Verifier, when `propose` names it: check the draft from `state(view="pack", draft_id=...)` and hand the verdict back with `result(job_id, report=...)`; `report.next` says whether to commit.
6. Main: `commit` as the save mode says. Cautious (`ask_user`): ask once with its options and commit after the user's answer. Proactive (`tell_user`): commit now. Then answer: what changed and why, from the receipt's `changelog`, and end with the receipt's `tell_user.say` when there is one. The service updates the repository and the page in the background; do not wait for them. A commit refused because the version or the ledger changed: start again from step 1 with a new `turn`; never force it.

## Undo (“撤回”, “undo”, “改回去”)

1. Main: `turn` with the words; it returns `undo.restore_version`.
2. Main: `propose(ctx_id, restore_version=...)`, then `commit`, and end the answer with the receipt's `tell_user.say`. No other role. A decision those turns locked that the restored plan changes goes with the undo; `tell_user.say` names it. Everything else they recorded stays: an allergy, and a must the service cannot test against the plan, which `tell_user.say` names in the user's words and asks about (`kept_musts`, `next`).
3. If `propose` returns `undo_blocked`, a requirement set before holds it: tell the user its `say` (the plan was not changed back; drop that one too?) and wait. On a yes or “drop it”, propose again in that turn with `restore_version` and `changes.withdraw`, as its `next` says. On a no, keep the plan. Never call a refused undo done.

## Screenshots or a batch of new requirements

1. Main: `turn` with the words, `scenario: "requirements"`.
2. Requirements: transcribe each screenshot or split the batch into points, mark conflicts and questions; then `turn(text=<the transcription>, nature="screenshot_extraction", record=...)`.
3. Main: answer with what was recorded and what is asked. Change the plan only when the user asked for it (“Change the plan”, from step 3).

## A status report (“I am alone today”)

Main: `turn` with what the message states in `record`, then answer. No other role.

## A new trip

Main: open it with `turn(new_trip=...)` and ask which long-term records still apply, as the main role's “Starting a new trip” says (`state(view="role", role="tripwright-main/records.md")`). Then the scenario of the message.
