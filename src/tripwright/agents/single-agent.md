# One agent, the roles in turn

Tripwright runs three roles: the main agent answers and records what the message states, `tripwright-researcher` looks things up and `tripwright-verifier` checks a draft. When your host cannot dispatch subagents (a chat app, or a host without workers), you do each role yourself, one after another, in the order below. The service returns this page as `state(view="role", role="single")`.

How to do a role:
- Read its steps once in the conversation with `state(view="role", role="<role>")` (in a repository: `skills/<role>/SKILL.md`). Read them again after the conversation was compacted. They are the same steps a subagent follows.
- Hand yourself what `flow.dispatch` lists for it (`read`: event ID, `ctx_id`, `base_version`, `draft_id`, `job_id`), do its steps to the end, and keep what it `returns`. Then go on with the next step here.
- Where `flow.dispatch` names a role this page does not, do that role too; where it names none, skip it. An entry marked `optional` is yours to skip.
- Keep each role's notes out of the answer: the user reads only the main agent's answer.

## On the road (`scenario: "quick"`)

1. Main: `turn` with the user's words, a stable `request_id`, `scenario: "quick"`, the named things in `points`, and in `record` what the message states. Send `first_part` as `next` says and end the reply. With no `first_part`, first call no tool: write one or two lines you can act on from `opening_material` and common sense and send them (times go as “the plan stores X, not checked yet”), then check. It is the first part only, and the second part is its own message.
   - A voice-typed message can mishear a word: when one makes no sense in context, do not act on it as written; guess the word meant and ask one short question (“你说的‘X’是指‘Y’吗？”).
   - Leave out the dimensions the user said not to consider (`exclude_keys` in `turn`). When conditions contradict each other or the new message, go on with the default reading, say so and list it as a question.
2. Main: check what the user will act on yourself, with `query` and the host's web search. Here the researcher is optional by design (the user asks and gets an answer directly); read its steps only when a lookup is unclear. What you cannot check goes into the same reply as an unchecked limit with its consequence; do not ask for another message to finish it.
3. Main: the second part under `second_part_title`, first line what differs from the first part (or from the road's short message).
4. When the message also changes the plan, go on with “Change the plan” from step 2.

## Change the plan (a replan, planning from scratch, or one change such as “move dinner to 19:00”)

1. Main: `turn` with the words, and in `record` what the message states — exact quotes, one record per point, linked to what `turn.records` lists (`depends_on`, `same_object_as`, `exclusive_with`). The requirements are in the ledger before the plan changes, so a refused plan change does not lose them. Keep the questions in the answer.
2. Researcher, when the plan needs facts nobody stored: `query(kind="saved")` first, then the lookups its steps name.
3. Main: first give the walk or the answer in the conversation (what you are changing and why), then `propose` the change on the same `ctx_id` and `base_version` (`blocks` or `days` for a small change); nothing is saved by a proposal.
4. Verifier, when `propose` names it: check the draft from `state(view="pack", draft_id=...)` and hand the verdict back with `result(job_id, report=...)`; `report.next` says whether to commit.
5. Main: `commit` after the answer, as the save mode says; the page update follows the answer. Cautious (`ask_user`): ask once with its options and commit after the user's answer. Proactive (`tell_user`): commit now, after the answer. Read the receipt's `changelog` and `tell_user.say` and say them right after the commit (a short follow-up when the answer is already sent). The service updates the repository and the page in the background; do not wait for them. A commit refused because the version or the ledger changed: start again from step 1 with a new `turn`; never force it.

## Undo (“撤回”, “undo”, “改回去”)

1. Main: `turn` with the words. The service goes back itself, in this one call: nothing holds an undo, and everything those turns recorded goes with it.
2. Main: write the answer from `undo.say` as it is: the version back, and each withdrawn entry named; when the user says one should stay, record it again. No other role, and no `propose` or `commit` — `undo.done` says it is done.

## Screenshots or a batch of new requirements

1. Main: transcribe each screenshot in order, or split the batch into points, and mark conflicts and questions.
2. Main: for a transcription, `turn(text=<the transcription>, nature="screenshot_extraction", record=...)`; for the user's own message, `turn` with the words and its records. Saved material goes into the ledger, never into the plan.
3. Main: answer with what was recorded and what is asked. Change the plan only when the user asked for it (“Change the plan”, from step 2).

## A status report (“I am alone today”)

Main: `turn` with what the message states in `record`, then answer. No other role.

## A new trip

Main: open it with `turn(new_trip=...)` and ask which long-term records still apply, as the main role's “Starting a new trip” says (`state(view="role", role="tripwright-main/records.md")`). Then the scenario of the message.
