"""Which role the main agent hands this step to, and a short reminder of the flow on every tool result (2026-11-12).

The user: a multi-agent system with clear jobs, where the server reminds the agent of the flow when a host has no hooks,
"only as a reminder, without repeating it too often". Roles that were written but never dispatched were removed on
2026-11-11 (5e37b5c); here the service names the role at the moment it is needed, so dispatch no longer depends on the
host remembering a long guide.

Three roles since round 4 (2026-11-19): the requirements role is gone. The main agent records what the message says
itself, in `turn`'s `record`, so a new message no longer stops at a second role before the plan can change; only the
researcher and the verifier are dispatched.

Per user, in memory: a restart only means the next turn gives the whole flow again. Nothing here refuses a call."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

PLANNING = ("plan", "replan", "compare", "requirements")
FULL_AFTER_TURNS = 10
FULL_AFTER_IDLE = timedelta(hours=6)
TURN_FIRST_AFTER = timedelta(minutes=30)
MISSED_FULL_EVERY = 3  # a missed step gives the whole flow again at most once in this many turns
SAME_LINE_EVERY = 3  # the same reminder line at most once in this many results

WHY = {
    "zh": dict(researcher_second="第二段要核的事实交查询员（后台）",
               researcher_latest="今晚最晚的车交查询员查",
               researcher_unknown="有没存的地方：交查询员查",
               researcher_quick="路上快问：你自己查（设计如此）；宿主能开后台 worker 时也可以交查询员",
               verifier="草稿改了计划：提交前交核对员从完整资料包核一遍",
               fares="有要买的票：另派一次查询员只查票价与优惠"),
    "en": dict(researcher_second="The facts the second part checks go to the researcher (in the background)",
               researcher_latest="Tonight's latest connection goes to the researcher",
               researcher_unknown="A place is not stored: the researcher looks it up",
               researcher_quick="On the road you look it up yourself (by design); a host with background workers may hand it to the researcher",
               verifier="The draft changes the plan: before commit, the verifier checks it from the wide pack",
               fares="Tickets still to buy: dispatch the researcher once more for fares and discounts only"),
}
# Which version a user who wants one back means is the host's call (2026-11-28): the service lists the versions in
# `turn`'s `versions` and does what `propose(restore_version=N)` says; it reads no word of the user's for it.
UNDO_HOST = dict(
    zh="用户想退回之前的某一版时（不论怎样说），看 versions 和前几轮在做什么，定退到第几版：例如刚撤回过又说“撤回”，要看他是想再往前退（只有前几轮他明说了在一步步往回找，才这样判：退到比现在更早、他还没看过的那一版，刚恢复来的那版和它恢复的那版是同一个样子，要越过它们；前文没有这条线索，就问一句），还是想把刚撤掉的拿回来；"
       "说的是某一次具体改动（“不要改晚饭那次”），就退到那次之前。拿得准就 propose(ctx_id, restore_version=N)，一次做完，不用 commit。"
       "拿不准就问一句，说清两版各是什么样（第几版、和现在差在哪），答之前不存。只撤掉某一条要求、不是退整版的，照普通改动走。",
    en="When the user wants an earlier version back, in whatever words, read `versions` and what the last turns were doing, and decide which version: "
       "“undo” right after an undo may mean going further back (only when the last turns say they are stepping back one by one: then to a version earlier than any they have seen, skipping a restored version and the one it restored, which look the same; without that clue, ask) or getting back what was just taken back; a named change (“not that dinner change”) means the version before it. "
       "When sure, propose(ctx_id, restore_version=N): it saves in this one call, no commit. When not, ask one question naming both versions "
       "(the number, and how each differs from now) and save nothing until the answer. Taking back only one requirement, not a whole version, is an ordinary change.")
UNDO_SHORT = dict(
    zh="想退回某一版（不论怎样说）：看 versions 与前几轮定第几版（只有前文明说在一步步往回找，再撤回才是退到更早、没看过的样子，恢复来的版和它恢复的版算同一个样子；没有这线索就问），拿得准 propose(restore_version=N) 一次做完；拿不准问一句说清两版，答之前不存。",
    en="To go back to a version (whatever the words): read versions and the last turns (only when they said they are stepping back one by one does it mean a look not seen yet, and a restored version and the one it restored look the same; without that clue, ask), then propose(restore_version=N) in one call; when unsure ask one question naming both versions, save nothing before the answer.")
SHORT = {
    "zh": dict(quick="路上急问：先发 first_part，要核的自己查（宿主能开后台 worker 时才派查询员），第二段先说不同。",
               plan="做计划：把这句的要求、事实、决定放进 record（引文照抄）；要查的交查询员；改计划后交核对员，再提交。",
               turn_first="先用 turn 记下用户这条消息（原话），再接着做。",
               unturned="这条用户消息还没经 turn：先调 turn(text=用户原话, scenario='quick')，再接着查。"),
    "en": dict(quick="On the road: send first_part now; check the facts yourself (the researcher only when the host runs background workers); the second part leads with what differs.",
               plan="Planning: put what this message states in record (quotes copied); lookups to the researcher; after a plan edit, the verifier, then commit.",
               turn_first="Record the user's message with turn (their words) first, then go on.",
               unturned="This user message has not gone through turn: call turn(text=the user's words, scenario='quick') first, then go on with the lookup."),
}
# A road turn the service drafted no first part for (round 4 review; baseline 2026-11-19: 13 of 15 real questions): the
# user gets a short message before any lookup, not silence for 4 to 19 minutes. Review 4 gives the host the service's
# stored `opening_material`, so that short message needs no lookup.
SHORT_ROAD = dict(
    zh="路上急问，这次服务没给可发的 first_part：先不调任何工具，用 opening_material 和常识写一两句能照着走的话发出去"
       "（时刻写“存档写 X，还没核”），再去查；第二段另发一条。",
    en="On the road with no first part to send: first call no tool, write one or two lines you can act on from "
       "opening_material and common sense and send them (times go as “the plan stores X, not checked yet”), then check; "
       "the second part is its own message.")
FULL = {
    "zh": dict(quick="路上即时问答：①turn 记下原话；②照 turn 的 next 先写第一条消息（first_part 是材料），只放第一段；③要核的事实自己查，宿主能开后台 worker 时才派查询员；"
                     "④第二段另发一条，标题照给的写，先说和第一段的不同；⑤不派核对员。flow.dispatch 点了谁就派谁；不能派子代理时，先取 state(view=\"role\", role=\"single\") 的编排，按它依次取各角色的步骤自己做。",
               quick_road="路上即时问答（这次服务没给可发的 first_part）：①turn 记下原话；②先不调任何工具，用 opening_material 和常识写一两句能照着走的话"
                           "发出去（时刻写“存档写 X，还没核”），再去查；只放第一段，第二段另发一条，每条消息都是答话本身；"
                           "③要核的事实自己查，宿主能开后台 worker 时才派查询员；"
                           "④查完另发一条第二段，先说和那条短消息的不同，两段标题不并进一条；⑤不派核对员。"
                           "flow.dispatch 点了谁就派谁；不能派子代理时，先取 state(view=\"role\", role=\"single\") 的编排，按它依次取各角色的步骤自己做。",
               plan="做计划：①turn 带原话，把这句里的要求、事实、决定放进 record（引文照抄这条消息、source.event 用 'this'），要求先落账；"
                    "照 turn 的 next 先写第一条消息（一到五行答最急的事），再往下做；②先 query(kind='saved') 用存的资料，缺的派查询员查；③propose 计划改动（同一个 ctx_id 与 base_version；这条消息的条目已经在 record 里，不要在 propose 里再带一遍）；"
                    "④草稿改了计划就派核对员核，通过才 commit；propose 返回 ask_user（谨慎模式，新带来的没核事实）时照它的 options 问用户（每个一句后果、标推荐、说不回按哪个），等回话再 commit；"
                    "主动模式直接 commit，不等仓库与页面更新；⑤回答里说改了什么、为什么，commit 返回 tell_user 时末尾原样带上它的 say。turn 返回的 records 里有现行条目、决定、依赖与 review，不用再取 state(view='ledger')、state(view='graph')；review 里的决定自己核依赖，要问的列进回答。"
                    "flow.dispatch 点了谁就派谁；不能派子代理时，先取 state(view=\"role\", role=\"single\") 的编排，按它依次取各角色的步骤自己做。"),
    "en": dict(quick="On the road: (1) record the words with turn; (2) write the first message as turn's next says (first_part is material), the first part only; (3) check the facts yourself, and dispatch the researcher only "
                     "when the host runs background workers; (4) send the second part as its own message, with the given heading and what differs first; (5) no verifier. Dispatch whom flow.dispatch names; "
                     "without subagents, read the order with state(view=\"role\", role=\"single\"), then do each role's steps yourself in turn.",
               quick_road="On the road (the service gave no first_part to send): (1) record the words with turn; (2) first call no tool: write one or two "
                           "lines you can act on from opening_material and common sense and send them (times go as “the plan stores X, not checked yet”), "
                           "then check; the first part only, the second part its own message, every message the answer itself; (3) check the facts yourself, "
                           "and dispatch the researcher only when the host runs background workers; (4) after checking, send the second part as its own "
                           "message, leading with what differs, never both headings in one; (5) no verifier. Dispatch whom flow.dispatch names; without "
                           "subagents, read the order with state(view=\"role\", role=\"single\"), then do each role's steps yourself in turn.",
               plan="Planning: (1) turn with the words, and put what this message states in record (quotes copied from it, source.event 'this'), so the "
                    "requirements are recorded first, then write the first message as turn's next says (one to five lines on the most urgent thing); (2) use query(kind='saved') first and dispatch the researcher for what is missing; (3) propose the plan "
                    "change (same ctx_id and base_version; this message's entries are already in record, so do not send them again in propose); (4) when the draft changes the plan, "
                    "dispatch the verifier and commit only after it passes; when propose returns ask_user (cautious mode, unverified facts the change brings in), "
                    "ask with its options (one consequence each, the recommended one marked, what happens without an answer) and commit after the answer; in the "
                    "proactive mode commit at once, without waiting for the repository or the page; (5) say what changed and why, and end with the tell_user.say "
                    "commit returns. turn's records hold the entries, decisions, links and review in force, so no state(view='ledger') or state(view='graph') is "
                    "needed; check the dependencies of what review lists yourself and put its questions in the answer. Dispatch whom flow.dispatch names; without "
                    "subagents, read the order with state(view=\"role\", role=\"single\"), then do each role's steps yourself in turn."),
}

for _language in ("zh", "en"):  # the undo sentence goes on every planning and road line (2026-11-28)
    for _mode in ("quick", "plan"):
        SHORT[_language][_mode] += " " + UNDO_SHORT[_language]
    for _mode in ("quick", "quick_road", "plan"):
        FULL[_language][_mode] += " " + UNDO_HOST[_language]

# A minimal task packet on every named role (2026-11-13, analysis 5.3): the IDs to hand over, the one thing it returns
# and where its steps are, so a host without the skill files can still do the role, and a subagent needs no long brief.
READS = {"tripwright-researcher": ("ctx_id", "draft_id"),
         "tripwright-verifier": ("draft_id", "job_id", "ctx_id")}
RETURNS = {
    "zh": {"tripwright-researcher": "每件事实一行：结论、出处、核没核",
           "tripwright-verifier": "判词（passed、conflict、incomplete），每项检查一行"},
    "en": {"tripwright-researcher": "one line per fact: the finding, its source, checked or not",
           "tripwright-verifier": "a verdict (passed, conflict or incomplete) and one line per check"},
}


def packet(entry: dict, ids: dict | None, language: str) -> dict:
    """The named role with what to hand it (`read`), what it returns and the call that gives its steps."""
    role = entry["role"]
    read = {key: (ids or {})[key] for key in READS.get(role, ()) if (ids or {}).get(key) is not None}
    return dict(entry, read=read, returns=RETURNS["en" if language == "en" else "zh"][role],
                steps=f'state(view="role", role="{role}")')


@dataclass
class Seen:
    last_turn: datetime | None = None
    last_full: datetime | None = None
    turns_since_full: int = 0
    mode: str | None = None
    named: set = field(default_factory=set)
    did: set = field(default_factory=set)  # steps seen since the last turn: query, verify
    committed_since_turn: bool = False
    lines: list = field(default_factory=list)


def mode_of(scenario: str | None) -> str:
    return "plan" if scenario in PLANNING else "quick"


class Flow:
    def __init__(self):
        self.seen: dict[str, Seen] = {}

    def _line(self, seen: Seen, line: str) -> str | None:
        """The line, unless it was given in the last SAME_LINE_EVERY - 1 results. A line left out is recorded as None,
        not as given, so it comes back on every SAME_LINE_EVERY-th result."""
        given = line not in seen.lines[-(SAME_LINE_EVERY - 1):]
        seen.lines = [*seen.lines[-10:], line if given else None]
        return line if given else None

    def turn(self, owner: str, at: datetime, *, scenario: str, language: str,
             second: bool, latest: bool, unknown: bool, ids: dict | None = None, line: str | None = None,
             full_line: str | None = None) -> dict:
        """flow for a turn. Nothing here names a recorder: since round 4 the main agent records what the message states
        itself, in `turn`'s `record`, and only a lookup or a check of a draft is dispatched. `line` replaces this turn's
        short reminder, `full_line` the whole flow."""
        language = "en" if language == "en" else "zh"
        seen = self.seen.setdefault(owner, Seen())
        mode = mode_of(scenario)
        why = WHY[language]
        dispatch = []
        for reason, needed in (("researcher_second", second), ("researcher_latest", latest), ("researcher_unknown", unknown)):
            if needed:
                # On the road the main agent looks things up itself (2026-11-12 live runs: 3 of 3 right in 15-20 s; the
                # user: "直接询问、直接回答"); the researcher is an option for hosts with background workers, not a step.
                dispatch.append(dict(role="tripwright-researcher", why=why["researcher_quick" if mode == "quick" else reason],
                                     **({"optional": True} if mode == "quick" else {})))
                break
        missed = (("tripwright-researcher" in seen.named and "query" not in seen.did)
                  or ("tripwright-verifier" in seen.named and "verify" not in seen.did and seen.committed_since_turn))
        # a missed step brings the whole flow back, but not on every turn of a host that never dispatches
        full = (seen.last_turn is None or at - seen.last_turn > FULL_AFTER_IDLE or seen.mode != mode
                or seen.turns_since_full >= FULL_AFTER_TURNS or missed and seen.turns_since_full >= MISSED_FULL_EVERY - 1)
        seen.last_turn, seen.mode = at, mode
        seen.named = {entry["role"] for entry in dispatch if not entry.get("optional")}  # an optional role is never missed
        dispatch = [packet(entry, ids, language) for entry in dispatch]
        seen.did, seen.committed_since_turn = set(), False
        if full:
            seen.last_full, seen.turns_since_full = at, 0
            return dict(dispatch=dispatch, full=full_line or FULL[language][mode])
        if not full:
            seen.turns_since_full += 1
        line = line or SHORT[language][mode]  # a turn always carries its line; other tools skip it when just said
        seen.lines = [*seen.lines[-10:], line]
        return dict(dispatch=dispatch, reminder=line)

    def tool(self, owner: str, at: datetime, tool: str, *, language: str, extra: list[dict] | None = None) -> dict:
        """flow for any other tool: one short line, left out when just given; "turn first" when the last turn is old."""
        language = "en" if language == "en" else "zh"
        seen = self.seen.setdefault(owner, Seen())
        if tool == "query":
            seen.did.add("query")
        # "turn first" only when a turn is known here and was long ago: a process that never saw a turn (the command
        # line a researcher uses) and the requirements-first commit before a plan draft are not missed messages
        stale = seen.last_turn is not None and at - seen.last_turn > TURN_FIRST_AFTER
        if tool == "commit":
            seen.committed_since_turn = True
        if stale:
            line = SHORT[language]["turn_first"]
        else:
            line = self._line(seen, SHORT[language][seen.mode or "plan"])
        for entry in extra or []:
            seen.named.add(entry["role"])
        out = dict(reminder=line)
        if extra:
            out["dispatch"] = extra
        return out

    def unturned(self, owner: str, at: datetime, language: str) -> str | None:
        """The `next` a host's lookup leads with when this service has seen no turn for the owner (a new conversation,
        or the first call after a restart) or the last one is older than TURN_FIRST_AFTER (2026-11-13: a weak model on
        WorkBuddy went straight to query on the road, so the words were not stored and no first part came). Only for
        calls that reach the service from a host (the MCP tools): the command-line process a researcher runs has
        never seen a turn and is not told to call one. Reading what is stored (state) needs no turn and gets none."""
        seen = self.seen.get(owner)
        last = seen.last_turn if seen else None
        if last is not None and at - last <= TURN_FIRST_AFTER:
            return None
        return SHORT["en" if language == "en" else "zh"]["unturned"]

    def verified(self, owner: str):
        """The verifier handed its verdict back (result with report). Reading the wide pack no longer counts as checked
        (analysis G4, 2026-11-13: the verifier found 3 drafts of 3 incomplete and 2 were saved all the same)."""
        self.seen.setdefault(owner, Seen()).did.add("verify")

    def after_propose(self, draft: dict, scenario: str, language: str, ids: dict | None = None) -> list[dict]:
        """Whom to dispatch once a draft exists: the verifier when it changes the plan while planning; the researcher
        once more for fares when it holds tickets still to buy."""
        why = WHY["en" if language == "en" else "zh"]
        if mode_of(scenario) != "plan" or draft.get("ledger_only") or draft.get("refused") or draft.get("status") == "rejected":
            return []
        dispatch = [dict(role="tripwright-verifier", why=why["verifier"])]
        unpriced = [booking for booking in (draft.get("plan") or {}).get("bookings", [])
                    if booking.get("status") in ("to_buy", "to_book")
                    and not any(fare.get("amount_per_person") is not None and not fare.get("estimated") for fare in booking.get("fares", []))]
        if unpriced:
            dispatch.append(dict(role="tripwright-researcher", focus="fares", why=why["fares"]))
        return [packet(entry, ids, language) for entry in dispatch]
