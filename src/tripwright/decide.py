"""A conflict found after the plan was decided goes to the user as one question (Q19, 2026-11-23).

When something learned after a plan or a decision was settled contradicts it (a train cancelled, a place closed at
the planned hour, a rule that forbids what the plan does, something the user reports), the agent does not change the
plan by itself and does not ask piece by piece. It calls `query(kind="conflict")` once with what the plan says, what
was found and from where, two or three options and the recommended one. The service checks that the plan object or
decision exists, stores the question in the user's repository (ledger/questions, kind `after_decision`) and returns
`say`: the question ready to send, with each option, the recommendation and why, what happens without an answer, and
by when it must be decided. The user's next message is taken as the answer (as for a draft's unchecked facts,
versions.unanswered): the turn that receives it names the question in `decided`, and the agent changes the plan as the
user chose, or keeps the default when the message does not choose; it does not ask again.

One question is open at a time (11-23, after the first review: two questions were open, the user answered one of them,
and both were marked answered — the agent then applied the default to a question the user never answered). A second
conflict found while one is open is stored as `pending`: its `say` and options are ready, the agent holds it, and the
turn that receives the answer to the open question moves the earliest pending one to open (`opened`) so the agent asks
it in that same answer. The message that answers one question is read as the answer to that question only.
"""
from __future__ import annotations

import re

from tripwright.storage import identifier, now

KIND = "after_decision"
TARGETS = ("block_id", "anchor_id", "booking_id", "decision_id")
WORDS = {
    "zh": dict(say="之前定的：{plan_says}。刚{how}：{found}{source}。要怎么办？{options}推荐{recommended}：{why}。{default}{by}",
               how_user="你说", how_found="查到", option="{mark}{text}；", marks="①②③", default_keep="不回话就先照原计划，什么都不改",
               default_option="不回话就按{mark}", by="；最晚 {by} 前要定", source_ev="（{source}，{when} 查）", source_user="",
               question="方案定了之后发现的冲突：{plan_says} ↔ {found}"),
    "en": dict(say="The plan says: {plan_says}. {how}: {found}{source}. What should we do? {options}I recommend {recommended}: {why}. {default}{by}",
               how_user="You said", how_found="Just found", option="{mark} {text}; ", marks=("(1)", "(2)", "(3)"),
               default_keep="Without an answer the plan stays as it is", default_option="Without an answer: {mark}",
               by="; decide by {by}", source_ev=" ({source}, checked {when})", source_user="",
               question="Conflict found after the plan was decided: {plan_says} <-> {found}"),
}


def target_of(arguments: dict) -> tuple[str, str]:
    named = [(key, arguments["against"][key]) for key in TARGETS if (arguments.get("against") or {}).get(key)]
    if len(named) != 1:
        raise ValueError("Invalid query arguments: arguments.against names exactly one of block_id, anchor_id, booking_id "
                         "or decision_id (the decided thing the finding contradicts)")
    return named[0]


def locate(plan: dict | None, store, owner: str, key: str, value: str, date: str | None) -> dict:
    """The decided thing: {kind, id, date, time, text, subject}; ValueError naming what exists when it does not."""
    if key == "decision_id":
        record = store.get("decisions", owner, value)
        return dict(kind="decision", id=value, date=None, time=None, text=record.get("choice") or record.get("question") or value,
                    subject=record.get("subject") or ["*"])
    if plan is None:
        raise ValueError("Invalid query arguments: arguments.against: there is no decided plan yet; a conflict needs one")
    if key == "booking_id":
        for booking in plan.get("bookings", []):
            if booking["id"] == value:
                return dict(kind="booking", id=value, date=booking.get("date"), time=None, text=booking.get("what") or value,
                            subject=booking.get("subject") or ["*"])
        raise ValueError(f"Invalid query arguments: arguments.against.booking_id: {value!r} is not in the plan's bookings "
                         f"({', '.join(booking['id'] for booking in plan.get('bookings', [])) or 'none'})")
    for day in plan.get("days", []):
        if date and day["date"] != date:
            continue
        if key == "anchor_id":
            for anchor in day.get("anchors") or []:
                if anchor["id"] == value:
                    return dict(kind="anchor", id=value, date=day["date"], time=anchor.get("time"), text=anchor.get("text") or value,
                                subject=anchor.get("subject") or ["*"])
        for option in day.get("plans") or []:
            for track in option.get("tracks") or []:
                for block in track.get("blocks") or []:
                    if block["id"] == value:
                        return dict(kind="block", id=value, date=day["date"], time=block.get("start"), text=block.get("text") or value,
                                    subject=track.get("subject") or ["*"])
    raise ValueError(f"Invalid query arguments: arguments.against.{key}: {value!r} is not in the current plan"
                     + (f" on {date}" if date else "") + "; state(view='day') lists the IDs")


def same(first: str, second: str) -> bool:
    letters = lambda text: re.sub(r"\W+", "", str(text).casefold())
    return letters(first) == letters(second)


def record(store, owner: str, plan: dict | None, arguments: dict, *, evidence: dict | None, source_event: str | None,
           language: str = "zh", local=lambda value: value) -> dict:
    """Store one after-decision question (or return the open one for the same thing and finding) and its `say`."""
    w = WORDS["en" if language == "en" else "zh"]
    key, value = target_of(arguments)
    found_at = locate(plan, store, owner, key, value, (arguments.get("against") or {}).get("date"))
    options = [str(option).strip() for option in arguments.get("options") or [] if str(option).strip()]
    if not 2 <= len(options) <= 3:
        raise ValueError("Invalid query arguments: arguments.options: give two or three short options (e.g. keep the plan, the "
                         "best alternative); the user picks one")
    recommended = arguments.get("recommended")
    if not isinstance(recommended, int) or not 0 <= recommended < len(options):
        raise ValueError(f"Invalid query arguments: arguments.recommended: the index (0 to {len(options) - 1}) of the option you recommend")
    default = arguments.get("default")
    if default is not None and (not isinstance(default, int) or not 0 <= default < len(options)):
        raise ValueError(f"Invalid query arguments: arguments.default: the index (0 to {len(options) - 1}) of what holds until the "
                         "user answers, or leave it out to keep the plan as it is")
    found = str(arguments.get("found") or "").strip()
    why = str(arguments.get("why") or "").strip()
    if not found or not why:
        raise ValueError("Invalid query arguments: a conflict needs `found` (what was learned) and `why` (why the recommended option)")
    plan_says = str(arguments.get("plan_says") or found_at["text"]).strip()
    for question in store.all("undecided", owner):
        conflict = question.get("conflict") or {}
        if question.get("kind") == KIND and question.get("status") in ("open", "pending") and conflict.get("target") == {key: value} \
                and same(conflict.get("found", ""), found):
            return dict(question, reused=True)
    by = arguments.get("by") or (f"{found_at['date'][5:]} {found_at['time']}" if found_at.get("date") and found_at.get("time") else None)
    marks = w["marks"]
    listed = "".join(w["option"].format(mark=marks[index], text=text) for index, text in enumerate(options))
    source = ""
    if evidence:
        source = w["source_ev"].format(source=evidence.get("source"), when=local(evidence.get("fetched_at")))
    say = w["say"].format(plan_says=plan_says, how=w["how_found"] if evidence or not arguments.get("said_by_user") else w["how_user"],
                          found=found, source=source, options=listed, recommended=marks[recommended], why=why,
                          default=w["default_keep"] if default is None else w["default_option"].format(mark=marks[default]),
                          by=w["by"].format(by=by) if by else "")
    waiting = open_questions(store, owner)
    question = dict(id=identifier("Q"), kind=KIND, subject=found_at["subject"], scope=dict(start=found_at.get("date"), end=found_at.get("date")),
                    item_ids=[], question=w["question"].format(plan_says=plan_says, found=found), say=say, options=options,
                    recommendation=f"{options[recommended]}: {why}", recommended=recommended,
                    default=w["default_keep"] if default is None else options[default], default_index=default, by=by,
                    source_event=source_event, asked=dict(at=None if waiting else now(), via="query"), since=now(),
                    status="pending" if waiting else "open", **({"queued_after": waiting[0]["id"]} if waiting else {}),
                    conflict=dict(target={key: value}, date=found_at.get("date"), plan_says=plan_says, found=found,
                                  evidence_ids=[evidence["id"]] if evidence else [], said_by_user=bool(arguments.get("said_by_user"))))
    store.put("undecided", owner, question, insert=True)
    return question


def open_questions(store, owner: str) -> list[dict]:
    return [question for question in store.all("undecided", owner) if question.get("kind") == KIND and question.get("status") == "open"]


def pending_questions(store, owner: str) -> list[dict]:
    return sorted((question for question in store.all("undecided", owner)
                   if question.get("kind") == KIND and question.get("status") == "pending"),
                  key=lambda question: question.get("since") or "")


def answer(store, owner: str, event_id: str, received_at: str, aware) -> dict:
    """The user's next message after a question was asked is the answer to the one question that is open: mark that
    question answered by this message, and move the earliest pending question to open so the same turn asks it — one
    question at a time, so a message that answers one thing does not settle another. Returns
    {answered: [...], opened: [...]} (for the turn's `decided` and `ask`)."""
    answered, opened = [], []
    for question in open_questions(store, owner):
        asked = (question.get("asked") or {}).get("at") or question.get("since")
        if asked and aware(asked) < aware(received_at):
            question.update(status="answered", answer_event=event_id, answered_at=received_at)
            store.put("undecided", owner, question)
            answered.append(question)
    if answered:
        waiting = pending_questions(store, owner)
        if waiting:
            question = waiting[0]
            question.update(status="open", asked=dict(at=received_at, via="turn"), since=question.get("since") or received_at)
            question.pop("queued_after", None)
            question.update(opened_at=received_at)
            store.put("undecided", owner, question)
            opened.append(question)
    return dict(answered=answered, opened=opened)
