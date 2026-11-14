"""Which role the main agent hands this step to, and a short reminder of the flow on every tool result (2026-11-12).

The user: a multi-agent system with clear jobs, where the server reminds the agent of the flow when a host has no hooks,
"only as a reminder, without repeating it too often". Roles that were written but never dispatched were removed on
2026-11-11 (5e37b5c); here the service names the role at the moment it is needed, so dispatch no longer depends on the
host remembering a long guide.

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
    "zh": dict(requirements_shots="消息附了截图：交需求整理员逐张转写、入库（截图资料进账本，不进计划）",
               requirements_many="这条有几件事：交需求整理员拆成要点、连上旧条目、写进账本",
               requirements_changed="改了已有的决定：交需求整理员连上依赖并标出要重看的",
               requirements_plan="做计划：需求整理员把这条记进账本、连上依赖（turn 不带 record）",
               review="上游改了，有决定要重看：交需求整理员核依赖",
               researcher_second="第二段要核的事实交查询员（后台）",
               researcher_latest="今晚最晚的车交查询员查",
               researcher_unknown="有没存的地方：交查询员查",
               researcher_quick="路上快问：你自己查（设计如此）；宿主能开后台 worker 时也可以交查询员",
               verifier="草稿改了计划：提交前交核对员从完整资料包核一遍",
               fares="有要买的票：另派一次查询员只查票价与优惠",
               unrecorded="这条消息还没进账本：先派需求整理员记下、提交它的草稿（计划没提交也不能丢），再给核对员"),
    "en": dict(requirements_shots="The message has screenshots: hand them to the requirements role to transcribe and record (saved material goes to the ledger, not the plan)",
               requirements_many="This message holds several things: the requirements role splits, links and records them",
               requirements_changed="It changes a decision: the requirements role links the dependency and marks what to review",
               requirements_plan="Planning: the requirements role records this message and links it (turn without record)",
               review="Something upstream changed and decisions need review: the requirements role checks the links",
               researcher_second="The facts the second part checks go to the researcher (in the background)",
               researcher_latest="Tonight's latest connection goes to the researcher",
               researcher_unknown="A place is not stored: the researcher looks it up",
               researcher_quick="On the road you look it up yourself (by design); a host with background workers may hand it to the researcher",
               verifier="The draft changes the plan: before commit, the verifier checks it from the wide pack",
               fares="Tickets still to buy: dispatch the researcher once more for fares and discounts only",
               unrecorded="Nothing of this message is in the ledger: dispatch the requirements role and commit its draft first (the request must not be lost if the plan is held back), then the verifier"),
}
SHORT = {
    "zh": dict(quick="路上急问：先发 first_part，要核的自己查（宿主能开后台 worker 时才派查询员），第二段先说不同。",
               plan="做计划：新要求交整理员记、连依赖，先提交它的草稿；要查的交查询员；改计划后交核对员，再提交。",
               turn_first="先用 turn 记下用户这条消息（原话），再接着做。",
               unturned="这条用户消息还没经 turn：先调 turn(text=用户原话, scenario='quick')，再接着查。"),
    "en": dict(quick="On the road: send first_part now; check the facts yourself (the researcher only when the host runs background workers); the second part leads with what differs.",
               plan="Planning: the requirements role records and links new requirements, commit its draft first; lookups to the researcher; after a plan edit, the verifier, then commit.",
               turn_first="Record the user's message with turn (their words) first, then go on.",
               unturned="This user message has not gone through turn: call turn(text=the user's words, scenario='quick') first, then go on with the lookup."),
}
FULL = {
    "zh": dict(quick="路上即时问答：①turn 记下原话；②原样发 first_part；③要核的事实自己查，宿主能开后台 worker 时才派查询员（有截图或几件事时派需求整理员）；"
                     "④第二段标题照给的写，先说和第一段的不同；⑤不派核对员。flow.dispatch 点了谁就派谁；不能派子代理时，先取 state(view=\"role\", role=\"single\") 的编排，按它依次取各角色的步骤自己做。",
               plan="做计划：①turn 只带原话（不带 record）；②派需求整理员记进账本、连依赖（depends_on 等）、列要问的，先提交它交回的只改账本的草稿；"
                    "③先 query(kind='saved') 用存的资料，缺的派查询员查；④propose（同一个 ctx_id 与 base_version）；⑤草稿改了计划就派核对员核，"
                    "通过才 commit；propose 返回 ask_user（谨慎模式，新带来的没核事实）时照它的 options 问用户（每个一句后果、标推荐、说不回按哪个），等回话再 commit；主动模式直接 commit，不等仓库与页面更新；⑥回答里说改了什么、为什么，commit 返回 tell_user 时末尾原样带上它的 say。flow.dispatch 点了谁就派谁；不能派子代理时，先取 state(view=\"role\", role=\"single\") 的编排，按它依次取各角色的步骤自己做。"),
    "en": dict(quick="On the road: (1) record the words with turn; (2) send first_part as given; (3) check the facts yourself, and dispatch the researcher only "
                     "when the host runs background workers (the requirements role when there are screenshots or several things); (4) write the given second heading and lead with "
                     "what differs; (5) no verifier. Dispatch whom flow.dispatch names; without subagents, read the order with state(view=\"role\", role=\"single\"), then do each role's steps yourself in turn.",
               plan="Planning: (1) turn with the words only (no record); (2) dispatch the requirements role to record, link (depends_on and "
                    "relations) and list questions, and commit the ledger-only draft it returns first; (3) use query(kind='saved') first and "
                    "dispatch the researcher for what is missing; (4) propose (same ctx_id and base_version); (5) when the draft changes the "
                    "plan, dispatch the verifier and commit only after it passes; when propose returns ask_user (cautious mode, unverified facts "
                    "the change brings in), ask with its options (one consequence each, the recommended one marked, what happens "
                    "without an answer) and commit after the answer; in the proactive mode commit at once, without waiting for the "
                    "repository or the page; (6) say what changed and why, and end with the tell_user.say commit returns. Dispatch whom flow.dispatch "
                    "names; without subagents, read the order with state(view=\"role\", role=\"single\"), then do each role's steps yourself in turn."),
}

# A minimal task packet on every named role (2026-11-13, analysis 5.3): the IDs to hand over, the one thing it returns
# and where its steps are, so a host without the skill files can still do the role, and a subagent needs no long brief.
READS = {"tripwright-requirements": ("event_id", "ctx_id", "base_version"),
         "tripwright-researcher": ("ctx_id", "draft_id"),
         "tripwright-verifier": ("draft_id", "job_id", "ctx_id")}
RETURNS = {
    "zh": {"tripwright-requirements": "只改账本的草稿（draft_id）与要问用户的问题",
           "tripwright-researcher": "每件事实一行：结论、出处、核没核",
           "tripwright-verifier": "判词（passed、conflict、incomplete），每项检查一行"},
    "en": {"tripwright-requirements": "a ledger-only draft (draft_id) and the questions for the user",
           "tripwright-researcher": "one line per fact: the finding, its source, checked or not",
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
    did: set = field(default_factory=set)  # steps seen since the last turn: record, query, verify
    committed_since_turn: bool = False
    text: str | None = None
    reviewed: set = field(default_factory=set)
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

    def turn(self, owner: str, at: datetime, *, text: str, scenario: str, language: str, nature: str, recorded: bool,
             changed: bool, review: list, second: bool, latest: bool, unknown: bool, answers_draft: bool = False,
             ids: dict | None = None) -> dict:
        """flow for a turn. A turn that records for the message just received is that message's requirements step,
        not a new message: it names nobody again. `answers_draft`: the message is the user's answer to a draft that
        waited for it ("yes, save it"), so planning alone is no reason to name the requirements role."""
        language = "en" if language == "en" else "zh"
        seen = self.seen.setdefault(owner, Seen())
        transcribed = (nature == "screenshot_extraction" and "tripwright-requirements" in seen.named
                       and "record" not in seen.did)  # the transcription the named requirements role returned
        if recorded and (text == seen.text or transcribed):
            # the requirements step of the message just received, not a new message: it names nobody again
            seen.did.add("record")
            seen.last_turn = at
            return dict(dispatch=[], reminder=SHORT[language][seen.mode or mode_of(scenario)])
        mode = mode_of(scenario)
        why = WHY[language]
        dispatch = []
        if nature == "screenshot_extraction" and not recorded:  # recorded: the main agent transcribed and recorded them
            dispatch.append(dict(role="tripwright-requirements", why=why["requirements_shots"]))
        elif changed:
            dispatch.append(dict(role="tripwright-requirements", why=why["requirements_changed"]))
        elif set(review) - seen.reviewed:
            seen.reviewed |= set(review)
            dispatch.append(dict(role="tripwright-requirements", why=why["review"]))
        elif len([line for line in text.splitlines() if line.strip()]) >= 3 and not recorded:
            dispatch.append(dict(role="tripwright-requirements", why=why["requirements_many"]))
        elif mode == "plan" and not answers_draft:  # while planning, the requirements role records and links every new message (one recorder)
            dispatch.append(dict(role="tripwright-requirements", why=why["requirements_plan"]))
        for reason, needed in (("researcher_second", second), ("researcher_latest", latest), ("researcher_unknown", unknown)):
            if needed:
                # On the road the main agent looks things up itself (2026-11-12 live runs: 3 of 3 right in 15-20 s; the
                # user: "直接询问、直接回答"); the researcher is an option for hosts with background workers, not a step.
                dispatch.append(dict(role="tripwright-researcher", why=why["researcher_quick" if mode == "quick" else reason],
                                     **({"optional": True} if mode == "quick" else {})))
                break
        missed = (("tripwright-requirements" in seen.named and "record" not in seen.did)
                  or ("tripwright-researcher" in seen.named and "query" not in seen.did)
                  or ("tripwright-verifier" in seen.named and "verify" not in seen.did and seen.committed_since_turn))
        # a missed step brings the whole flow back, but not on every turn of a host that never dispatches
        full = (seen.last_turn is None or at - seen.last_turn > FULL_AFTER_IDLE or seen.mode != mode
                or seen.turns_since_full >= FULL_AFTER_TURNS or missed and seen.turns_since_full >= MISSED_FULL_EVERY - 1)
        seen.last_turn, seen.mode, seen.text = at, mode, text
        seen.named = {entry["role"] for entry in dispatch if not entry.get("optional")}  # an optional role is never missed
        dispatch = [packet(entry, ids, language) for entry in dispatch]
        seen.did, seen.committed_since_turn = {"record"} if recorded else set(), False
        if full:
            seen.last_full, seen.turns_since_full = at, 0
            return dict(dispatch=dispatch, full=FULL[language][mode])
        seen.turns_since_full += 1
        line = SHORT[language][mode]  # a turn always carries its line; other tools skip it when just said
        seen.lines = [*seen.lines[-10:], line]
        return dict(dispatch=dispatch, reminder=line)

    def tool(self, owner: str, at: datetime, tool: str, *, language: str, extra: list[dict] | None = None) -> dict:
        """flow for any other tool: one short line, left out when just given; "turn first" when the last turn is old."""
        language = "en" if language == "en" else "zh"
        seen = self.seen.setdefault(owner, Seen())
        if tool == "query":
            seen.did.add("query")
        if tool == "propose":
            seen.did.add("record")  # the requirements role records through a ledger-only draft
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

    def unrecorded(self, language: str, ids: dict | None = None) -> dict:
        return packet(dict(role="tripwright-requirements", why=WHY["en" if language == "en" else "zh"]["unrecorded"]), ids, language)

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
