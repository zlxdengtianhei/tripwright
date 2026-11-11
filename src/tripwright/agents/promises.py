"""What the generated role texts must keep saying (block C's promises), checked on every build.

Each promise names the outputs it covers, the statements that must be there and the wording that must not be.
A match proves only that the words are present; whether a host model acts on them is measured by cold reads and
replay, not here. The checks exist so that an edit to a role or step source cannot silently drop a promise.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass

MAIN = "skills/tripwright-main/SKILL.md"
GENERATED = "src/tripwright/agents/generated"
MCP_MAIN, L0_MAIN = f"{GENERATED}/mcp/tripwright-main.md", f"{GENERATED}/l0/tripwright-main.md"
MAIN_TEXTS = (MAIN, MCP_MAIN)
ALL_MAIN = MAIN_TEXTS + (L0_MAIN,)
# The skill keeps the answer and the steps for every message; records, relations, locks and write states moved to
# this page, which the MCP prompt and the L0 text still carry inline.
RECORDS = "skills/tripwright-main/references/records.md"
SINGLE = "skills/tripwright-main/references/single-agent.md"
# S4a: the server instructions are core.yaml's steps; the rules below them live in the MCP prompt (MCP_MAIN), which the
# instructions point to, so a promise names SERVER only for what the core steps themselves say.
SERVER = f"{GENERATED}/server/instructions.md"
AGENTS_MD = f"{GENERATED}/repo/AGENTS.init.md"
CORE_TEXTS = (MAIN, MCP_MAIN, SERVER, AGENTS_MD)
VERIFIER = ("skills/tripwright-verifier/SKILL.md", f"{GENERATED}/claude/tripwright-verifier.md",
            f"{GENERATED}/codex/tripwright_verifier.toml", f"{GENERATED}/mcp/tripwright-verifier.md")
RESEARCHER = ("skills/tripwright-researcher/SKILL.md", f"{GENERATED}/claude/tripwright-researcher.md",
              f"{GENERATED}/codex/tripwright_researcher.toml", f"{GENERATED}/mcp/tripwright-researcher.md",
              f"{GENERATED}/l3/tripwright-researcher.md")
# Mentioning a forbidden word in quotes, to forbid it, is allowed; using it is not.
QUOTED = re.compile(r"“[^”\n]*”")


@dataclass(frozen=True)
class Promise:
    id: str
    paths: tuple[str, ...]
    must: tuple[str, ...] = ()
    must_not: tuple[str, ...] = ()
    bad_sample: str = ""


PROMISES = (
    Promise("turn-first", MAIN_TEXTS + (SERVER,),
            must=(r"[Cc]all `?turn`? first for every (?:user|trip) message",)),
    Promise("order-only-from-user", ALL_MAIN + (SERVER,),
            must=(r"(?:order between preferences comes only from the user|Only the user sets precedence between preferences)",),
            must_not=(r"\bmore important than\b", r"\boutweighs?\b", r"by default,? prefer", r"优先于", r"更重要"),
            bad_sample="By default, prefer the cheaper option when money and comfort compete."),
    # The full rule lives with the records page; the skill body keeps the user-only order and points there.
    Promise("competing-preferences-get-options", (RECORDS, MCP_MAIN, L0_MAIN),
            must=(r"Give 2–3 complete, feasible options", r"never traded off against each other", r"over the whole day",
                  r"Recommend only for a factual reason", r"“默认” only when a stored user decision already chose it", r"“默认”, not “推荐”")),
    Promise("baggage-holder", (RECORDS, MCP_MAIN, L0_MAIN), must=(r"`?baggage_holder`?",)),
    Promise("declare-relations-in-propose", (RECORDS, MCP_MAIN),
            must=(r"Declare it on the items, in `record` or `propose`", r"`same_object_as`", r"`exclusive_with` only when one statement has two readings")),
    # A (abb09fc): turn records what this message states in the same step, so the answer already uses it.
    Promise("turn-records-this-message", MAIN_TEXTS + (SINGLE,),
            must=(r"`record`", r'`source\.event: "this"`')),
    # S4 after the third verdict: declarations hold only on overlap; undeclared overlap gives a non-blocking notice.
    Promise("declarations-hold-only-on-overlap", (RECORDS, MCP_MAIN, L0_MAIN, "skills/tripwright-requirements/SKILL.md"),
            must=(r"[Aa] declaration holds only where (?:the two records' )?people, dates and time windows overlap",
                  r"(?:both stay active|both stay)", r"both readings and the default", r"`?compatible_with`?"),
            must_not=(r"[Uu]ndeclared[^.\n]*(?:become|are made|turn) pending", r"[Uu]ndeclared[^.\n]*(?:\b(?<!non-)(?<!never )(?<!not )block(?:s|ed)?\b|refused)"),
            bad_sample="Undeclared overlapping records become pending until the user answers."),
    # B3d names (fe1e5632): the notice and the after-the-fact declaration; no placeholder may remain.
    Promise("possible-contradictions-named", (RECORDS, MCP_MAIN),
            must=(r"`?possible_contradictions`?", r"`?changes\.relate`?"), must_not=(r"field names pending",),
            bad_sample="The service returns a notice (field names pending)."),
    Promise("new-trip-calls", (RECORDS, MCP_MAIN, SINGLE),
            must=(r'turn\(new_trip="YYYY-MM-name"\)', r'state\(view="memory"\)', r'record=\{"carry"', r"`?not_used`?", r"tripwright sync")),
    Promise("new-trip-without-tools", (L0_MAIN, SINGLE),
            must=(r"trips/<trip>/ledger/carry/<ID>\.yaml",)),
    Promise("verifier-gets-the-wide-pack", VERIFIER, must=(r'state\(view="pack", draft_id',)),
    Promise("locks-follow-identity", (RECORDS, MCP_MAIN, L0_MAIN) + VERIFIER,
            must=(r"(?:plan\.id|plan IDs?|plan ID)", r"(?:never display names|display name alone is not enough)")),
    Promise("transport-text-search-term-and-triggers", ALL_MAIN,
            must=(r"(?:exact term to type into the named app, in its original spelling|在哪个 App 搜什么（地名照原文）)",
                  r"(?:The user must not need the link|不点链接也能照着做)", r"(?:[Ll]ook up departures only under the triggers|查不查班次（默认不查)")),
    Promise("write-state-words", (RECORDS, MCP_MAIN),
            must=(r"`received` 已收件", r"`pending_sync` 待同步", r"`effective` 已生效", r"`conflict` 冲突",
                  r"Only now say “记下了”", r"“（草稿）”", r"“核过的安排” only when the deep check is `passed` and the commit was accepted",
                  r"tell the user only a conflict",
                  r"otherwise “安排（没核完）”", r"“已过期，需重算”")),
    Promise("never-call-a-write-success", ALL_MAIN + tuple(f"skills/tripwright-main/references/{name}" for name in ("single-agent.md", "tools.md", "records.md")),
            must_not=(r"成功", r"\bsuccess(?:ful(?:ly)?)?\b", r"\bsucceed"),
            bad_sample="Tell the user the commit succeeded and the plan was saved successfully."),
    Promise("six-single-agent-steps", (SINGLE, MCP_MAIN, L0_MAIN),
            must=(r"Quick question: 1 → 2", r"Status report only: 1 → 5", r"Replan or plan from scratch: 1 → 6",
                  r"large batch of new requirements: 1 → 2")),
    Promise("multi-agent-optional", (MAIN, AGENTS_MD),
            must=(r"background `tripwright-researcher`", r"later message", r"same reply")),
    # Q8 (E3 sent back): two real parts, the second backed by live checks with sources; no write states for the user.
    Promise("two-parts-checked-live", ALL_MAIN,
            must=(r"second (?:part|message)", r"[Ss]ynthetic or demo", r"(?:its|with its) source", r"(?:first two lines|前两行|第二行写“按现在 HH:MM 算”|Line 2 says \"As of HH:MM\")"),
            must_not=(r"(?<![Nn]ever )(?<!not )[Tt]ell the user (?:the )?write state", r"(?<![Nn]ever )(?<!not )[Ss]how the user (?:the )?write state"),
            bad_sample="Tell the user the write state of every message."),
    # Answer layer v2b (verdict on r7—r11): the clock comes from turn or date, and the second part is complete.
    Promise("clock-not-guessed", ALL_MAIN, must=(r"按现在 HH:MM", r"(?:先跑 date|run `?date`?)", r"不编“几点查”")),
    Promise("second-part-complete", ALL_MAIN, must=(r"完整安排", r"和第一段不同")),
    Promise("no-write-state-for-the-user", MAIN_TEXTS,
            must=(r"(?:Say nothing about the write|[Nn]ever tell the user write states)",)),
    Promise("food-has-a-common-option", ALL_MAIN,
            must=(r"(?:麦当劳|common,? reliable chain|common chain)",)),
    # Second quality verdict (D-42): 核过 rests on a tool result; the plan heading stands out; fallbacks leave later;
    # no shop of unknown hours late at night; transfer stations get a Chinese name.
    Promise("checked-only-from-tools", ALL_MAIN, must=(r"网上说法", r"搜索摘要"),
            # Quoted words are stripped before must_not runs, so the old rule shows as "事实标或".
            must_not=(r"影响行动的事实标或", r"[Mm]ark each fact the user will act on \"checked"),
            bad_sample="- 影响行动的事实标“核过（来源）”或“没核：什么”。"),
    Promise("plan-under-a-heading", ALL_MAIN, must=(r"## 完整安排",)),
    Promise("fallback-leaves-later", ALL_MAIN, must=(r"(?:后备要?比主选晚开|leaves later than the main choice)",)),
    Promise("no-shop-of-unknown-hours", ALL_MAIN, must=(r"(?:营业时间不明或(?:此刻)?已关的不推荐|never one of unknown hours or closed now)",)),
    Promise("transfer-stations-named", ALL_MAIN, must=(r"换乘站、上车站在句中第一次出现时写“原文（中文名，是什么）”",)),
    Promise("researcher-labels-each-claim", RESEARCHER,
            must=(r"`?status`?", r"snippet", r"only for a time inside the result's `window`", r"web_evidence", r"[Nn]ever read raw timetable data yourself")),
    Promise("lookups-from-the-tools", ALL_MAIN + RESEARCHER, must=(r"latest_departure", r"web_evidence")),
    # v2d replays: the main agent that read the command in its own skill ran it before the first part, which was then lost.
    Promise("command-line-only-in-the-researcher", RESEARCHER, must=(r"tripwright --repo \. query", r"starts with `uvx --from \./tools/<wheel> tripwright --repo \. query`")),
    # The main agent's one command line is the second part's check_answer (D-43); lookups stay with the researcher. A16:
    # and the first part's draft (`first-part`, no lookup) when the tools are down.
    Promise("main-agent-never-runs-the-command-line", ALL_MAIN, must=(r"(?:主 agent 只跑第二段的 `check_answer`|the main agent runs only the second part's `check_answer`)",),
            # S4a2: and the share page's publish after an effective commit (step 6), which looks nothing up
            must_not=(r"uvx --from (?![^\n]*?(?:query check_answer|tripwright --repo \. first-part|tripwright --repo \. share publish))",),
            bad_sample="Without the MCP tools run `uvx --from ./tools/x.whl tripwright --repo . query transit`."),
    # Third quality verdict (D-44): the tools' words leaked into answers; a way not looked up was called impossible.
    Promise("tool-words-stay-out", ALL_MAIN, must=(r"(?:工具里的说法不进回答|The tools' words stay out of the answer)", r"(?:不写证据号|never the evidence ID)"),
            must_not=(r"(?:写明|标成)过了计划目标", r"say \"past the plan's target\""), bad_sample="`later` 标成过了计划目标。"),
    Promise("unlooked-ways-are-not-impossible", ALL_MAIN + RESEARCHER, must=(r"(?:这次只查了|this time only X was looked up|a way you did not look up is not)",)),
    Promise("stored-places-not-addresses", ALL_MAIN + RESEARCHER, must=(r"(?:不用街道地址|never a street address)",)),
    # A7: comparable routes, stored opening hours, and the sent text bound to the passed draft.
    Promise("routes-from-candidates", ALL_MAIN + RESEARCHER, must=(r"`?candidates`?", r"`?via_station`?")),
    Promise("food-from-stored-hours", ALL_MAIN + RESEARCHER, must=(r'kind=\\?"open_places\\?"', r'`?type`?: \\?"opening_hours\\?"')),
    Promise("first-part-before-tools", (MAIN, MCP_MAIN, AGENTS_MD),
            must=(r"first_part\.text`? before any other tool", r"first-part heading")),
    Promise("one-reply-no-continuation", (MAIN, MCP_MAIN, AGENTS_MD),
            must=(r"without asking the user to reply",),
            must_not=(r"first_part\.later", r"second_part_due"), bad_sample="first_part.later and second_part_due"),
    # S4a2 (D-89, s4c): after an effective commit the share page is published again; a blocked scan is fixed by category,
    # never by writing the value; the first public repository is published to others, so the user is asked first.
    Promise("share-page-after-commit", (SINGLE, MCP_MAIN),
            must=(r"stop and ask first", r"run `tripwright share publish`", r"`share_blocked`", r"never write the value",
                  r"GitHub Free serves Pages only from a public repository", r"holds only the pages")),
    # D-48: the host sent true_from "this" (copied from source.event) and turn refused the record.
    Promise("record-times-with-offset", (MAIN, MCP_MAIN), must=(r'`true_from` an ISO time with offset, never "this"',)),
    Promise("send-the-passed-draft", ALL_MAIN, must=(r"(?:照抄那份草稿发出，一字不改|send that draft exactly)",)),
    Promise("claude-check-optional", MAIN_TEXTS, must=(r"check_answer", r"(?:may check|可以用|可以留|可以核)")),
    Promise("no-tool-steps-end-with-write-back", ("skills/tripwright-main/references/single-agent.md", f"{GENERATED}/l0/tripwright-main.md"),
            must=tuple(rf"### {number}\. [^\n]+\n(?:(?!\n### )[\s\S])*?写回仓" for number in range(0, 7))),
    Promise("new-trip-carry-over", (RECORDS, MCP_MAIN, L0_MAIN, SINGLE),
            must=(r"[Ll]ist them (?:grouped|in the conversation, grouped)", r"the trip it came from", r"[Aa]sk which still apply to this trip",
                  r"never (?:by reading files|ask the user to (?:open or )?read a file)", r"“待确认”"),
            must_not=(r"(?:open|read|check) (?:the )?(?:profile )?files? (?:yourself )?to decide", r"待 A 定", r"［待[^］\n]*定"),
            bad_sample="Ask the user to open the profile files to decide which records apply."),
    Promise("verifier-independent-input", VERIFIER + (f"{GENERATED}/l3/tripwright-verifier.md",),
            must=(r"full text of every applicable source sentence", r"[Tt]he scenario checklist", r"complete candidate",
                  r"not only what the first response listed as unchecked", r"已过期，需重算",
                  r"`passed`: the result was fresh, every wide-pack group is `complete`, no disposition is `unaddressed`")),
    Promise("verifier-stale-gives-no-conclusion", VERIFIER,
            must=(r"`stale`: report only “已过期，需重算”", r"Give no conclusion from the old draft")),
    Promise("researcher-saved-first-with-provenance", RESEARCHER,
            must=(r"1\. The user's saved material, when it can hold the answer[\s\S]*2\. Live sources: (?:Tripwright's `query` for transport and weather, and )?the host's web search and fetch[\s\S]*3\. Only then the open web",
                  r"synthetic or demo is not a source", r"source", r"valid", r"Never fill a gap with a guess")),
    # v2g: these rules travel in both the skill and MCP prompts, without a host-specific hook.
    Promise("server-transport-sentences", ALL_MAIN + RESEARCHER + VERIFIER,
            must=(r"sentences", r"how_to_check", r"coverage")),
    Promise("must-only-and-change-reasons", ALL_MAIN + (RECORDS, SINGLE),
            must=(r"must", r"changes\.revisions", r"changelog", r"这次没改成")),
    Promise("final-check-and-inquiry-section", ALL_MAIN,
            must=(r"怎么查、怎么问", r"我可以帮你起草询问内容", r"英语", r"当地语言")),
    Promise("unchecked-latest-example-defers-answer", ("skills/tripwright-main/references/examples.md", MCP_MAIN, L0_MAIN),
            must=(r"Good first part, before lookups:\n```text\n按现在 \d\d:\d\d 算：几点出发要查，第二段给。",)),
    # A10 (D-67): the service drafts the first part and frames “最晚几点出发去 X”; part 2 goes once, after check_answer.
    Promise("first-part-from-turn", MAIN_TEXTS + (SERVER,), must=(r"first_part",)),
    Promise("last-connection-from-the-server", MAIN_TEXTS + RESEARCHER,
            must=(r"(?:只给 `origin`、`destination`|origin and destination only)",)),
    Promise("second-part-differences-first", ALL_MAIN + (SERVER,),
            must=(r"(?:what differs|和第一段不同|和第一段的不同)",)),
    Promise("no-status-lines", ALL_MAIN, must=(r"[Ss]end no status line|Keep progress notes",)),
    # a10d (R2's fields): a write the server cannot push is said with its hint; local storage needs the user's backup
    Promise("blocked-write-and-local-backup", MAIN_TEXTS + (RECORDS,),
            must=(r"write\.blocked", r"hint", r"storage\.kind", r"back up the data directory")),
    # A11: an online check-in stored as a node at the airport became “要 05:45 前到”; the plan's writers mark it
    Promise("online-deadline-anchor", (RECORDS, MCP_MAIN, L0_MAIN, "skills/tripwright-requirements/SKILL.md"),
            must=(r"`where: online`", r"no latest departure")),
    Promise("subagents-never-commit", VERIFIER + RESEARCHER + ("skills/tripwright-requirements/SKILL.md",),
            must=(r"`commit`",), must_not=(r"[Yy]ou (?:may|can|should) call `commit`",),
            bad_sample="When the check passes, you may call `commit` yourself."),
    # A12 (quality judge 6): the first part was the plan's template whatever was asked (A); a stored ticket went unused
    # (E); part 2 ran to about 20 body lines (F)
    Promise("first-part-answers-the-points", MAIN_TEXTS + (SERVER,), must=(r"`?points`?",)),
    Promise("second-part-twelve-lines", ALL_MAIN, must=(r"(?:正文最多 12 行|at most 12 body lines)",)),
    Promise("tickets-as-stored", ALL_MAIN, must=(r"(?:票照 `tickets`|tickets as the result's stored tickets)",)),
)


def _matches(path: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(path, pattern)


def promise_problems(outputs: dict[str, str], promises: tuple[Promise, ...] = PROMISES) -> list[str]:
    problems = []
    for promise in promises:
        for pattern in promise.paths:
            paths = [path for path in outputs if _matches(path, pattern)]
            if not paths:
                problems.append(f"{promise.id}: no output matches {pattern}")
            for path in paths:
                text = outputs[path]
                problems += [f"{promise.id}: {path} lacks /{must}/" for must in promise.must if not re.search(must, text)]
                unquoted = QUOTED.sub("", text)
                problems += [f"{promise.id}: {path} says /{bad}/" for bad in promise.must_not if re.search(bad, unquoted, re.IGNORECASE)]
    return problems
