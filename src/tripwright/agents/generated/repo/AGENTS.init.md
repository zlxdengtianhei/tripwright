<!-- 由 python -m tripwright.agents build 生成，源：src/tripwright/agents/templates/AGENTS.md.tmpl、src/tripwright/agents/core.yaml；不要手改 -->
<!-- 由 tripwright init 生成，源：src/tripwright/agents/templates/AGENTS.md.tmpl、src/tripwright/agents/core.yaml；不要手改，升级时覆盖 -->
# Trip repository for Tripwright

This repository holds one person's trips, one folder per trip under `trips/`. Its files are the source of truth: what the travellers said, what applies, what was decided and which plan version is current. Long-term content (stable preferences, companions, standing rules, saved material) lives in `profile/`; when a new trip starts, the main agent asks in the conversation which of it still applies. Agents read in a fixed order and change records only through Tripwright.

## Every user message

The main agent calls `turn` first and does these steps; the skill or MCP prompt `tripwright-main` holds the full rules, to read when writing the second part. Without the Tripwright tools, step 1 runs `uvx --from ./tools/<wheel> tripwright --repo . first-part --text <message> --points <JSON>` and step 7 runs `uvx --from ./tools/<wheel> tripwright --repo . query check_answer --part second --text-file -`; the lookups of step 5 go to the researcher.

1. Call `turn` first for every user message, before writing any text: the text as received, a new `request_id`, `scenario: "quick"` for a question on the road, and `points`, each thing the message names as `{"kind", "said", "place"}` in the user's words. While Tripwright's tools are still connecting, retry every few seconds (up to about 30) before using the command line; never write about connections. Done when: `turn` returned.
2. If your app runs a subagent in the background and tells you when it reports (Claude Code), dispatch one `tripwright-researcher` there now, with each fact for step 5 and what the plan stores of it. Otherwise skip this step. Done when: a researcher runs in the background, or your app has none.
3. Write `first_part.text` as it is (only the form of address and the language may change), before any lookup. Then continue with the lookups and the second part in this same reply; never ask the user to reply “continue”. Done when: the first text the user sees in this reply is the first part.
4. Check each fact the user will act on (times and hard nodes, what still runs after now, opening hours, events) with `query`: `latest_departure` with origin and destination only for the latest or last departure, `open_places` for food, `web_evidence` for what a web page says. A subagent may do the lookups (`tripwright-researcher`; in Codex `tripwright_researcher`); without one, do them yourself, one after another. Done when: each fact has a `query` result or a report line, or a reason it stays unchecked.
5. Record in one `propose`, then `commit`, what the message decided or changed and what you inferred or chose (basis `agent_inference`); record nothing in between. Done when: `commit` accepted it, or there was nothing to record.
6. Write the second part from the results and pass it to `query(kind="check_answer", arguments={"text": <the draft>, "part": "second", "request_id": <the question's>})`; rewrite what each `fix` says and check again, at most twice. Done when: `check_answer` returned `ok: true`.
7. Send part 2 once: the draft that passed, unchanged. Done when: the text sent is the draft that passed.

- The user sees one reply with only the two parts: send no status line before the first or between the two, nor talk of checks. No tool result or draft is shown; a part is out once written.
- 核过 only for a Tripwright tool result, never synthetic or demo data; what a web page says is 网上说法 with its source, a search snippet is 搜索摘要, and what stays unchecked is 没核, with why.
- The order between preferences comes only from the user.
- Stop for the user only before a commit with `accept_unverified`, a change to what the user called a must, or anything sent or published to others; every other step goes on without asking.

Subagents (`tripwright-verifier`, `tripwright-researcher`, `tripwright-requirements`; in Codex `tripwright_verifier` and so on) never call `turn` or `commit`; they return reports to the main agent. In Codex, spawn `tripwright_researcher` for step 5 when you can run subagents.

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
