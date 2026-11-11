<!-- 由 python -m tripwright.agents build 生成，源：src/tripwright/agents/templates/AGENTS.md.tmpl、src/tripwright/agents/core.yaml；不要手改 -->
<!-- 由 tripwright init 生成，源：src/tripwright/agents/templates/AGENTS.md.tmpl、src/tripwright/agents/core.yaml；不要手改，升级时覆盖 -->
# Trip repository for Tripwright

This repository holds one person's trips, one folder per trip under `trips/`. Its files are the source of truth: what the travellers said, what applies, what was decided and which plan version is current. Long-term content (stable preferences, companions, standing rules, saved material) lives in `profile/`; when a new trip starts, the main agent asks in the conversation which of it still applies. Agents read in a fixed order and change records only through Tripwright.

## Every user message

The main agent calls `turn` first and does these steps; the `tripwright-main` skill holds the full rules. Without the Tripwright tools, step 1 runs `uvx --from ./tools/<wheel> tripwright --repo . first-part --text <message> --points <JSON>`. In Claude Code, a second-part draft may be checked with `uvx --from ./tools/<wheel> tripwright --repo . query check_answer --part second --text-file -`; lookups go to the researcher.

1. Call `turn` first with the message, `scenario: "quick"` on the road, and each named thing in `points`. While Tripwright's tools are still connecting, retry every few seconds (up to about 30) before using the command line; never write about connections. Done when: `turn` returned.
2. Send `first_part.text` and end this reply without another tool. If you can dispatch a background `tripwright-researcher`, dispatch it to check the facts before ending. Done when: the first text is the service's first part and the reply has ended, or none was supplied.
3. After the user replies or the researcher returns, do not call `turn`; query every fact directly. Write the second part in that next message. Done when: each actionable fact has a query result or an unchecked label, and no second turn was called.
4. Record what this message states, then use `propose` and `commit` for plan changes. Put choices the agent made in `changes.items` with basis `agent_inference`. Done when: the commit was accepted, or there was nothing to write.
5. Write `second_part_title` as the second heading. Its first sentence says what differs from the first part, or “Same as above, checked”; then give the rest. In Claude Code, `check_answer` may check this draft before sending it. Done when: the second heading and differences sentence are present.
6. Finish each reply; continue an unfinished second part after the user's next message or the researcher's report without calling `turn`. Done when: each reply is complete, and continuation needs no stored continuation state.

- Call `turn` first for every trip message; use `scenario="quick"` on the road and list the message's `points`.
- If `first_part` exists, write its text before any other tool. Query times, last services and opening hours before presenting them as facts; use the returned sentences and windows, and label anything unchecked. Then use `second_part_title`; its first sentence says what differs, or “Same as above, checked”. If a message only continues an unfinished second part, skip `turn` and query directly.
- Record a user's preferences, facts and decisions before saying they are remembered.
- Only the user sets precedence between preferences; when they compete, offer two or three options.
- Only the user's words in this message may change something they called a must.
- Answer in the user's language; faithfully translate service sentences when needed.

Subagents (`tripwright-verifier`, `tripwright-researcher`, `tripwright-requirements`; in Codex `tripwright_verifier` and so on) never call `turn` or `commit`; they return reports to the main agent. In Codex, spawn `tripwright_researcher` for step 3 when you can run subagents.

## Read in this order

1. The pack that `turn` returns. Do not read the whole repository.
2. Without the tool: the front page `README.md` (it names the current trip), then that trip's folder: `trips/<trip>/README.md`, `trips/<trip>/views/packs/<date>.md`, `trips/<trip>/views/today.md`.
3. Only then single records, by the IDs and paths that the pack lists on its details line.

## Generated files: do not edit

A generated file says so on its first line, or on the first line inside its frontmatter, and names its generator. The next commit or upgrade overwrites it. They are `README.md`, `trips/*/README.md`, `trips/*/views/**`, `AGENTS.md`, `CLAUDE.md`, `.mcp.json`, `.claude/settings.json`, `.claude/agents/*.md`, `.claude/skills/`, `.agents/skills/` and `.codex/agents/*.toml`. The local runtime state in `.tripwright/` is not in Git.

## Records: change only through Tripwright

`tripwright.yaml`, `profile/**`, `trips/*/trip.yaml`, `trips/*/raw/**`, `trips/*/ledger/**`, `trips/*/plan/**` and `trips/*/evidence/**` are records, written by `propose` and `commit`. A file in `raw/` never changes after it is created. `trips/*/research/**` holds the user's own material: read it, never rewrite it.

## No preferences here

This file holds no preferences, rules or facts about anyone. They live in `profile/items/` and `trips/*/ledger/`, with their source sentences and scope. Do not copy them into this file, into a skill or into host memory.
