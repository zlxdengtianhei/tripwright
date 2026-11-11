from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from tripwright import consider, packs
from tripwright.checks import check_plan, departure_clock
from tripwright.events import Events
from tripwright.jobs import Jobs
from tripwright.ledger import Carry, Changes, Fact, Item, Ledger, PendingCheck, Proposal, Relation
from tripwright.query import QueryRouter, checked_arguments, lookup_failed
from tripwright.render import first_part
from tripwright.repo import files as repo_files, memory, notices, replay
from tripwright.repo.outbox import Outbox
from tripwright.storage import Store, encode, identifier, now
from tripwright.trip import Trip, aware, input_problems, json_type, local_instant
from tripwright.usage import checked_usages
from tripwright.versions import Versions, ledger_only

# What a turn may record from the message it receives (CONTRACTS 1.3): first-hand facts, items, their relations and the
# answer about imported long-term entries. Decisions lock plan parts and go through propose and commit.
TURN_RECORDS = ("items", "facts", "relate", "carry", "pending_checks")
SKELETON = {"quick": "quick", "status": "quick"}
FRAME_MINUTES = packs.STEP_MINUTES  # S4h-a: a quick latest question's frame holds for lookups this long after its turn
FIRST_TITLES = dict(zh="按存的计划先说（时刻还没核）", en="From the stored plan first (times not checked yet)")
SECOND_TITLES = dict(zh="核过之后", en="After checking")
MEMORY_ASK = dict(zh="长期条目待确认：在对话里问用户这趟还用不用；按回答调 turn(record={carry:[{id,status}]})。",
                  en="Long-term entries need confirmation: ask whether they still apply to this trip; record the answer with "
                     "turn(record={carry:[{id,status}]}).")


def message_language(text: str) -> str:
    return "zh" if re.search(r"[\u4e00-\u9fff]", text) else "en"


# Stored plans and quotes keep the language they were written in, and a host answers in that language unless told: with
# a Chinese trip GPT answered an English question in Chinese in 3 of 5 runs, and in 0 of 5 with this sentence first in
# `next` (2026-11-11). The opposite direction needs no sentence: on an English trip GLM, Kimi and DeepSeek answered
# Chinese questions in Chinese in all 26 conversations (2026-11-11).
REPLY_IN_ENGLISH = "Reply in English: translate the stored text you use; keep place names as written."


# Reply flow "two" (TRIPWRIGHT_REPLY_FLOW): the one sentence a host follows reliably is "send this and end the reply"
# with the first part's own closing line; anything about the second part in the same field makes it go on (replays
# of 2026-11-11). What comes after waits in after_first_part.
SEND_AND_END = dict(
    zh="先把 first_part.text 原样发出，作为这一条回复的第一条、也是唯一一条消息，末行是 first_part.later，然后结束这一条；"
       "发之前不调别的工具、不写别的字。能在后台派 researcher 的：唯一可以先做的是在后台派它去查，然后照样原样发出 "
       "first_part.text（不加 later 那一行），不另写“正在查”这类话；它的报告回来后写第二段。",
    en="Send first_part.text as it is as this reply's first and only message, its last line first_part.later, then end the "
       "reply; before sending it call no other tool and write nothing else. If you can dispatch a background researcher: "
       "the one thing you may do first is dispatch it in the background; then send first_part.text as it is all the same "
       "(without the later line) and add no “still checking” sentence; when its report returns, write the second part.")
# A first part quotes stored text as written. On a trip stored in Chinese an English one came out half Chinese
# (2026-11-11), so the host translates that one; every other first part is still sent as it is.
SEND_IN_ENGLISH = SEND_AND_END["en"].replace("as it is all the same", "that way all the same").replace(
    "as it is", "in English (translate its stored text; keep names, times and numbers as written)")
AFTER_FIRST = dict(zh="用户回了话、或 researcher 的报告回来之后照这里做，不再调 turn：",
                   en="After the user replies or the researcher's report returns, do this without calling turn again: ")
# explain(record_id) (S5): the ledger tables a record ID is looked up in, and the fields each adds to the record
RECORD_TABLES = (("items", "item"), ("facts", "fact"), ("decisions", "decision"), ("pending_checks", "pending_check"),
                 ("undecided", "question"))
RECORD_FIELDS = dict(item=("kind", "key", "strength", "condition", "valid_until", "reason", "always", "long_term"),
                     fact=("key", "value", "true_from", "valid_until"),
                     decision=("decided_by", "must", "reason", "depends_on", "revisit_when", "locked_paths"),
                     pending_check=("due_at", "responsible", "consequence", "alternative", "result"),
                     question=("options", "default", "item_ids"))


class RecordEntry(BaseModel):
    """What placeholder_sources reads of a record entry: an object whose source, when sent, is an object too. The rest is
    checked with the ledger's models after the placeholder is filled in."""
    model_config = ConfigDict(extra="allow")
    source: dict[str, Any] = Field(default_factory=dict)


RecordShape = create_model("RecordShape", __config__=ConfigDict(extra="allow"), **{part: (list[RecordEntry], []) for part in TURN_RECORDS})
RECORD_MODELS = dict(items=Item, facts=Fact, pending_checks=PendingCheck, relate=Relation, carry=Carry)
# The keys a wrong record entry is told it needs, e.g. `record.facts.0 must be an object {key, value, …}, got array`.
RECORD_SHAPES = {f"record.{part}.*": "{" + ", ".join(name for name, field in model.model_fields.items() if field.is_required()) + "}"
                 for part, model in RECORD_MODELS.items()} | {
                 f"record.{part}.*.source": '{event ("this" for this message), optional nature and basis}' for part in TURN_RECORDS}


def checked_record(record) -> dict:
    """The shape of turn.record, checked before anything reads it (B3g): the record and each of its parts' entries and
    their sources must be objects in arrays. A wrong one is refused with its field path, what it needs and what was sent
    (the X1a form), before placeholder_sources fills in the message and the ledger's models check the rest."""
    if not isinstance(record, dict):
        raise ValueError(f"Invalid record: record must be an object, got {json_type(record)}")
    try:
        RecordShape.model_validate(record)
    except ValidationError as error:
        raise ValueError("Invalid record: " + "; ".join(input_problems(error, "record", RECORD_SHAPES))) from None
    return encode_copy(record)


def placeholder_sources(record: dict, event_id: str) -> dict:
    """Records sent with a turn name their message as "this" (its ID does not exist yet). `record` has passed
    checked_record."""
    record = encode_copy(record)
    for part in TURN_RECORDS:
        for entry in record.get(part, []):
            source = entry.setdefault("source", {})
            if source.get("event") in (None, "", "this"):
                source["event"] = event_id
    return record


def person_id(value, plan: dict | None, field: str):
    """Resolve a trip person by ID or display name, without guessing partial matches."""
    if value is None or plan is None:
        return value
    if not isinstance(value, str):
        return value
    if value == "*":
        return value
    people = plan.get("people", [])
    folded = value.strip().casefold()
    matches = [person for person in people if folded in (person["id"].casefold(), person["name"].casefold())]
    if len(matches) == 1:
        return matches[0]["id"]
    choices = ", ".join(f"{person['id']} ({person['name']})" for person in people) or "no people in this trip"
    raise ValueError(f"{field}: unknown person {value!r}; this trip's people are {choices}")


def person_list(value, plan: dict | None, field: str):
    if not isinstance(value, list) or plan is None:
        return value
    return [person_id(entry, plan, f"{field}.{index}") for index, entry in enumerate(value)]


def normalize_record_people(record, plan: dict | None, root: str):
    if not isinstance(record, dict):
        return record
    result = encode_copy(record)
    for part, entries in result.items():
        if not isinstance(entries, list):
            continue
        for index, entry in enumerate(entries):
            if isinstance(entry, dict) and "subject" in entry:
                entry["subject"] = person_list(entry["subject"], plan, f"{root}.{part}.{index}.subject")
    return result


def merged_plan(current: dict | None, days=None, places=None, bookings=None) -> dict:
    """Merge whole-day and by-ID patches, then let the normal Trip and lock checks judge the result."""
    if current is None:
        raise ValueError("Partial plan changes need an existing plan; use state(view='blank') and propose(plan=...)")
    if not all(isinstance(value, list) for value in (days, places, bookings)):
        raise ValueError("days, places and bookings must be arrays")
    result = encode_copy(current)
    for name, values, key in (("days", days, "date"), ("places", places, "id"), ("bookings", bookings, "id")):
        for index, value in enumerate(values):
            if not isinstance(value, dict) or not isinstance(value.get(key), str) or not value[key]:
                raise ValueError(f"{name}.{index}.{key} is required and must be a string")
            found = next((position for position, entry in enumerate(result[name]) if str(entry[key]) == value[key]), None)
            if found is None:
                result[name].append(encode_copy(value))
            else:
                result[name][found] = encode_copy(value)
    result["days"].sort(key=lambda day: day["date"])
    return result


def checked_plan(plan) -> None:
    """A plan of the wrong shape is refused with one line per field path, what it needs and what was sent (B3f X1a),
    e.g. `plan.days.0.anchors.1.deadline must be 'external' or 'plan', got string`."""
    try:
        Trip.model_validate(plan)
    except ValidationError as error:
        raise ValueError("Invalid plan: " + "; ".join(input_problems(error, "plan"))) from None


def encode_copy(value):
    import json

    return json.loads(encode(value))


class Tripwright:
    def __init__(self, store: Store, *, query_router: QueryRouter | None = None, query_timeout: float = 30,
                 deep_timeout: float = 120, repository=None, commit_wait: float = 5.0, language: str = "zh"):
        self.store = store
        self.events = Events(store)
        self.ledger = Ledger(store)
        self.jobs = Jobs(store)
        self.versions = Versions(store, self.jobs)
        self.outbox = Outbox(store)
        self.jobs.handlers["deep_check"] = self.versions.deep_check
        self.jobs.handlers["plan_day"] = self._plan_day
        self.query_router = query_router or QueryRouter(store)
        self.query_timeout = query_timeout
        self.deep_timeout = deep_timeout
        self.commit_wait = commit_wait
        self.language = language
        self.versions.requery = self._requery
        self.jobs.async_cleanup = lambda: self.query_router.aclose()
        self.repository = repository
        self.committer = None
        if repository is not None:
            from tripwright.repo.committer import Committer

            self.committer = Committer(self, repository)
            self.committer.attach()

    # --- repository wiring (used by the committer) -------------------------------------

    def bound(self, owner: str):
        if self.repository is not None and owner != self.repository.owner:
            raise ValueError(f"This trip repository is bound to the local identity {self.repository.owner!r}")

    def current_trip(self, owner: str) -> str | None:
        if self.committer is not None:
            return (self.committer.head_record() or {}).get("trip")
        plan = self.versions.current(owner)["plan"]
        return plan["id"] if plan else None

    def trip_folder(self, owner: str) -> str | None:
        from tripwright.repo.layout import trip_folder

        trip = self.current_trip(owner)
        return trip_folder(trip) if trip and self.repository is not None else None

    def repository_files(self, tables, *, trip, upto, repo, at, notices):
        return repo_files.repository_files(self, tables, trip=trip, upto=upto, repo=repo, at=at, notices=notices)

    def replay(self, scratch, owner, op):
        return replay.replay(self, scratch, owner, op)

    def conflict_notice(self, owner, op, reason, canonical):
        return notices.conflict(self.store, owner, op, reason, canonical)

    def open_notices(self, owner):
        return notices.open_notices(self.store, owner)

    def settle_notices(self, owner, op):
        notices.settle(self.store, owner, op)

    def adopt_manual_edits(self):
        from tripwright.repo import manual

        return manual.adopt(self) if self.committer is not None else None

    def take_in(self):
        """Before a write: plan files edited without the tool become a version first (A15), so this write builds on
        them and the commit that follows does not write the previous version's plan over them."""
        if self.committer is not None:
            self.committer.adopt()

    def _has(self, table: str, owner: str, record_id: str) -> bool:
        try:
            self.store.get(table, owner, record_id)
        except ValueError:
            return False
        return True

    def write_ref(self, owner: str, op: dict | None) -> dict:
        return self.outbox.ref(owner, op)

    # --- tools -------------------------------------------------------------------------

    def turn(self, owner: str, text: str, request_id: str | None = None, *, nature: str = "host_restatement",
             native_ref: str | None = None, channel: str = "conversation", subject: list[str] | None = None,
             scope: dict | None = None, scenario: str = "plan", focus_keys: list[str] | None = None,
             exclude_keys: list[str] | None = None, speaker: str | None = None, record: dict | None = None,
             new_trip: str | None = None, points: list | None = None) -> dict:
        self.bound(owner)
        language = message_language(text)
        current_plan = self.versions.current(owner)["plan"]
        speaker = person_id(speaker, current_plan, "speaker")
        subject = person_list(subject, current_plan, "subject")
        record = normalize_record_people(record, current_plan, "record")
        # S5: a record of the wrong shape (or one the ledger refuses, below) never refuses the turn: the message is
        # received and the first part drafted as without it; recorded.not_recorded says what was left out and where to
        # send it again
        refused_record = None
        try:
            record_input = (checked_record(record) if record is not None else None) or None
        except ValueError as error:
            record_input, refused_record = None, str(error)
        points = self._checked_places(owner, first_part.checked_points(points))
        request_id = request_id or identifier("REQ")
        prior = self.outbox.find(owner, "turn", request_id)
        nature_key = "host_restatement" if nature == "user_verbatim" and not native_ref else nature
        incoming = dict(text=text, nature=nature_key, channel=channel, native_ref=native_ref,
                        speaker=speaker, suspicious_words=[])
        collision = next((event for event in self.store.all("events", owner)
                          if event.get("request_id") == request_id and encode(event["received"]) != encode(incoming)), None)
        if collision is not None or (prior is not None and encode(prior["replay"].get("input")) != encode(record_input)):
            request_id = identifier("REQ")
            prior = None
        self.take_in()
        if new_trip and prior is None:
            memory.prepare_new_trip(self, owner, new_trip)
        with self.store.transaction():
            opened = memory.switch(self, owner, new_trip) if new_trip and prior is None else None
            receipt = self.events.receive(owner, text, request_id, nature=nature, native_ref=native_ref, channel=channel, speaker=speaker)
            event_id = receipt["event_id"]
            recorded = dict(changed=[], not_recorded=[], warnings=[], needs_replan=False)
            if prior is None:
                imported = memory.import_entries(self, owner, opened, new_trip, event_id) if opened else []
                imports = dict(items=[self.store.get("items", owner, entry) for entry in imported if self._has("items", owner, entry)],
                               carry=[self.store.get("carry", owner, entry) for entry in imported]) if opened else None
                changes = None
                if record_input:
                    try:
                        with self.store.attempt():
                            changes, recorded = self._record(owner, placeholder_sources(record_input, event_id), event_id,
                                                            language=language)
                    except ValueError as error:
                        refused_record = str(error)
                if refused_record:
                    recorded = dict(recorded, not_recorded=[*recorded["not_recorded"],
                                                            self._refused_record(record, refused_record, event_id, language=language)])
                # What the last turn asked and this message leaves unanswered takes its default (M1, R-58, R-67).
                silent = consider.defaults(self.store, owner, self._asked(owner, event_id), record_input, at=now(), language=language)
                if silent:
                    recorded = dict(recorded, defaulted=silent)
                self.ledger.ensure_profile(owner)
                event = self.store.get("events", owner, event_id)
                op = self.outbox.add(owner, kind="turn", tool="turn", key=request_id, event=event_id, records=recorded["changed"],
                                     replay=dict(event=event, input=record_input, changes=changes, new_trip=new_trip, imports=imports),
                                     state="received" if self.committer else "effective", recorded=recorded)
            else:
                op = prior
                recorded = prior.get("recorded", recorded)
            context_id = identifier("CTX")
            context = self.versions.context(owner, event_id, subject=subject, scope=dict(scope or {}, channel=channel),
                                            scenario=scenario, focus_keys=focus_keys, exclude_keys=exclude_keys, context_id=context_id)
            # The memory this question must weigh, what it must ask and the offer to draft a question (M1). What
            # silence settled is stored with this context first, so this turn does not ask it again.
            context.update(language=language, settled=recorded.get("defaulted", []))
            self.store.put("contexts", owner, context)
            shown = self.present_pack(owner, kind="narrow", scenario=scenario, pack_id=context_id, snapshot=context["snapshot"],
                                      at=context["snapshot"]["taken_at"], subject=subject, scope=dict(scope or {}, channel=channel),
                                      live=True)
            current = self.versions.current(owner)
            plan = current["plan"] or {}
            at = context["snapshot"]["taken_at"]
            known = consider.sources(self.store, owner, plan or None, at=at, language=language)
            asked = consider.asks(self.store, owner, plan or None, scenario=scenario, scope=scope, at=at, language=language, known=known)
            considered = consider.build(self.store, owner, pack=shown["pack"], plan=plan or None, scenario=scenario, at=at, language=language)
            context.update(pack=shown["pack"], asked=[dict(id=question["id"], scope=question.get("scope", {})) for question in asked],
                           options_pending=bool(recorded.get("crowding")))
            self.store.put("contexts", owner, context)
            anchors = self._hard_nodes(owner, plan, subject, scope, at, language=language)
            write = self.write_ref(owner, op)
            pending = memory.pending_entries(self.store, owner)
            possible = self.ledger.possible_contradictions(owner, context["bundle"]["items"])
            response = dict(now=self._clock(owner, at, language), request_id=receipt.get("request_id", request_id),
                            receipt=receipt, ctx_id=context_id, base_version=current["number"],
                            snapshot=context["snapshot"],
                            write=write, notices=self.open_notices(owner),
                            pack=dict(markdown=shown["markdown"], meta=shown["meta"], tokens=shown["tokens"]),
                            recorded=recorded, possible_contradictions=possible,
                            current_summary=dict(trip=self.current_trip(owner), title=plan.get("title"), days=[day["date"] for day in plan.get("days", [])],
                                                 needs_replan=current.get("needs_replan", False) or recorded["needs_replan"],
                                                 warnings=current.get("warnings", [])),
                            hard_nodes=anchors,
                            inbox=[dict(job_id=message["job_id"], state=message["state"]) for message in self.store.all("inbox", owner)
                                   if message["state"] in ("generated", "available") and message["base_version"] == current["number"]])
            if pending:
                response["memory"] = dict(pending=pending, ask=MEMORY_ASK[language])
                asked.append(dict(id="memory", say=response["memory"]["ask"], default=packs.WORDS[language]["memory_default"],
                                  record=dict(carry=[dict(id=entry["id"], status="confirmed or not_used") for entry in pending])))
            told = packs.point_facts(self.store, owner, plan or None, points, anchors, at, language,
                                     person=packs.asker(plan, speaker, subject)) if points is not None and plan else None
            first = self._quick_first(text, scenario, response["now"], anchors, plan or None, told, language=language)
            second_title = SECOND_TITLES[language] if first else None
            context.update(points=packs.point_record(told),
                           frame=dict(scenario=scenario, asks_latest=first_part.asks_latest(text)),
                           request_id=request_id, promises_second=bool(first), first_text=first["text"] if first else None,
                           continues=None)
            self.store.put("contexts", owner, context)
            instructions = self._next(considered, asked, recorded, first=first, second_title=second_title,
                                      latest=first_part.asks_latest(text),
                                      unknown=self._unknown_places(plan, told, language=language), language=language,
                                      scenario=scenario, options=bool(possible or recorded.get("crowding")))
            # Claude reads the first fields first: the immediate action and both service-written headings lead.
            if first and self._reply_flow() == "two":
                first = dict(first, later=first_part.LATER[language])
                send = SEND_IN_ENGLISH if language == "en" and message_language(first["text"]) == "zh" else SEND_AND_END[language]
                response = dict(next=send, first_part=first, second_part_title=second_title,
                                after_first_part=AFTER_FIRST[language] + instructions, **response)
            else:
                response = dict(next=instructions, first_part=first, second_part_title=second_title, **response)
            response.update(consider={key: considered[key] for key in ("entries", "more", "more_how") if key in considered},
                            ask=asked, offer=consider.offer(known["languages"], language))
            if told is not None:
                response["points"] = told
        if self.committer is not None:
            response["sync"] = self.committer.status()
            self.committer.poke()
        return response

    def _quick_first(self, text: str, scenario: str, clock: dict, anchors: list[dict], plan: dict | None,
                     told: dict | None, *, language: str | None = None) -> dict | None:
        """A quick first part only when stored data answers at least one named point."""
        if scenario != "quick" or told is None or not plan:
            return None

        def useful(point):
            facts = point["facts"]
            if point["kind"] == "go":
                return any(facts.get(key) for key in ("frame", "train", "way", "nodes"))
            if point["kind"] == "eat":
                return bool(facts.get("stored"))
            if point["kind"] == "do":
                return bool(facts.get("places"))
            if point["kind"] == "ticket":
                return bool(facts.get("tickets"))
            return False

        if not any(useful(point) for point in told["points"]):
            return None
        first = first_part.draft(text, clock, anchors, plan, language or self.language, told)
        kept = []
        for line in first["lines"]:
            if line.startswith(("没核：", "Not checked:")):
                continue
            promised = "第二段" in line or "part 2" in line.lower()
            stored = any(mark in line for mark in ("存档", "查过", "现在关着", "archived", "checked at", "closed now"))
            if promised and not stored:
                continue
            kept.append(line)
        if not kept:
            return None
        language = message_language(text)
        lines = [FIRST_TITLES[language], *kept[:7]]
        return dict(first, text="\n".join(lines), lines=lines)

    def _refused_record(self, record, reason: str, event_id: str, *, language: str) -> dict:
        """recorded.not_recorded for a turn.record left out as a whole (S5), with where the corrected entries go: the same
        request_id now holds this receipt, so they go into this question's propose (or the next turn's record)."""
        count = sum(len(value) for value in record.values() if isinstance(value, list)) if isinstance(record, dict) else 0
        en = language == "en"
        return dict(part="record", records=count, reason=reason,
                    fix=(f"Nothing of record was kept; the message was received. Send the corrected entries in this question's propose "
                         f"(source.event \"{event_id}\"), not again with this request_id." if en else
                         f"record 一条没记，原话已收。改好后放进这一问的 propose（source.event 写 \"{event_id}\"），不要用同一个 request_id 重发 turn。"))

    def _hard_nodes(self, owner: str, plan: dict, subject: list[str] | None, scope: dict | None, at: str, *,
                    language: str) -> list[dict]:
        """turn's hard nodes: the plan's anchors for the subject and scope, each with its latest departure clock, where
        its time comes from, its archived claims and whether it has passed (passed ones last)."""
        anchors = [dict(date=day["date"], **anchor) for day in plan.get("days", []) for anchor in day.get("anchors", [])
                   if (not subject or "*" in subject or set(subject) & set(anchor["subject"]))
                   and (not scope or (not scope.get("start") or day["date"] >= scope["start"]) and (not scope.get("end") or day["date"] <= scope["end"]))]
        versions = packs.committed(self.store.all("versions", owner))
        for anchor in anchors:
            anchor["latest_departure"] = departure_clock(anchor["time"], anchor["buffer_minutes"], anchor["travel_minutes"], day=anchor["date"], zone=plan["timezone"])
            # Where the time comes from and whether it is already behind us (A2): a passed node's consequence
            # ("only the 23:53 is left") is history, not tonight's options.
            anchor.update(packs.node_timing(self.store, owner, versions, anchor["date"], anchor, at))
            # A9 (D-64): a stored "末班 / 只剩 / 最晚" sentence is marked as archive (when written, source), never a
            # current fact; a later departure found this session voids it.
            anchor.update(packs.archived_claims(self.store, owner, plan or None, anchor["date"], anchor, anchor, at,
                                                language=language))
            instant = local_instant(anchor["date"], anchor["time"], plan["timezone"])
            anchor["passed"] = instant is not None and instant <= aware(at)
        anchors.sort(key=lambda anchor: anchor["passed"])
        return anchors

    def draft_first_part(self, owner: str, text: str, *, points: list | None = None, speaker: str | None = None,
                         subject: list[str] | None = None) -> dict:
        """turn's first part without turn, for a host whose Tripwright tools are down (A16, quality judge 8, off_r1: the
        host wrote its own from the plan's files, with the stored “最晚 21:50 出门” and “23:53 末班” on its first line). It
        reads the trip and records nothing: the message is not received, no context is kept. Returns the clock, the
        draft (`first_part`, as turn's) and what to do with it (`next`)."""
        self.bound(owner)
        language = message_language(text)
        points = self._checked_places(owner, first_part.checked_points(points))
        plan = self.versions.current(owner)["plan"] or {}
        at = now()
        anchors = self._hard_nodes(owner, plan, subject, None, at, language=language) if plan else []
        told = packs.point_facts(self.store, owner, plan, points, anchors, at, language,
                                 person=packs.asker(plan, speaker, subject)) if points is not None and plan else None
        clock = self._clock(owner, at, language)
        first = self._quick_first(text, "quick", clock, anchors, plan or None, told, language=language)
        en = language == "en"
        return dict(now=clock, first_part=first, second_part_title=SECOND_TITLES["en" if en else "zh"] if first else None,
                    **(dict(points=told) if told is not None else {}),
                    next=("Write first_part.text before any other tool. After checking, use second_part_title and say differences first."
                          if en else "先把 first_part.text 写出来，再调其他工具；核完用 second_part_title，先讲和第一段的不同。"))

    def _checked_places(self, owner: str, points: list[dict] | None) -> list[dict] | None:
        """Points whose place and words name two different stored places are refused before anything is recorded
        (S4h-a, ns_on_r1 question 2), with what to send instead; the same request_id may be sent again."""
        conflicts = packs.place_conflicts(self.versions.current(owner)["plan"], points) if points else []
        if conflicts:
            raise ValueError("Invalid points: " + "; ".join(conflicts))
        return points

    def _unknown_places(self, plan: dict | None, told: dict | None, *, language: str | None = None) -> list[str]:
        """What `next` says of the places in points that are no stored place and no coordinates (S4h-a, mcponly_r1
        question 1: “airport” was left unread and the first part said only that the time needs checking): which, that
        the first part names none of them and never the plan's place in their stead, and the stored IDs to send."""
        unknown = list(dict.fromkeys([point["unknown"] for point in (told or {}).get("points", []) if point.get("unknown")]
                                     + ([told["at"]["unknown"]] if (told or {}).get("at") and told["at"].get("unknown") else [])))
        if not unknown:
            return []
        en = (language or self.language) == "en"
        said = (", " if en else "、").join(f"“{value}”" for value in unknown)
        return [f"These places in points were not recognized: {said}. Continue without another turn; query by coordinates "
                f"or stop name." if en else
                f"points 里这些地方没认出：{said}。不用再调 turn，按坐标或站名查。"]

    def _asked(self, owner: str, event_id: str) -> list[dict]:
        """What the previous turn asked (the latest context of another message): must-go places, real-time data."""
        earlier = [context for context in self.store.all("contexts", owner) if context.get("source_event") != event_id]
        latest = max(earlier, key=lambda context: aware(context["created_at"]), default=None)
        return (latest or {}).get("asked") or []

    def _reply_flow(self) -> str:
        return "one" if os.environ.get("TRIPWRIGHT_REPLY_FLOW", "two").lower() == "one" else "two"

    def _next(self, considered: dict, asked: list[dict], recorded: dict, *, first: dict | None,
              second_title: str | None, latest: bool = False, unknown: list[str] = (), language: str | None = None,
              scenario: str | None = None, options: bool = False) -> str:
        """Only this message's next actions, bounded for a chat tool result."""
        en = (language or self.language) == "en"
        parts = []
        if first and self._reply_flow() == "one":
            parts.append("Send first_part.text to the user now (they are waiting for this first part); call query only "
                         "after sending it." if en else
                         "现在就把 first_part.text 发给用户（用户在等这第一段）；发出去之后再调 query。")
        if en:
            parts.append(REPLY_IN_ENGLISH)
        if first:
            parts.append(f"After checking, write {second_title!r} as the second heading; under it, first say what differs "
                         "from first_part, or 'Same as above, checked.'" if en else
                         f"核完后写第二段标题“{second_title}”；标题下先讲和第一段的不同，没有不同就写“和上面一样，已核”。")
            # Blind comparison of 2026-11-11: without these two, hosts stated web findings they had not fetched through
            # the service and told the user what they had looked up or not recorded.
            parts.append("Something found online: fetch its sentence with query(kind='web_evidence') before stating it, or "
                         "label it unchecked. Leave out process remarks such as 'checked again' or 'nothing was recorded'."
                         if en else
                         "网上查到的说法先用 query(kind='web_evidence') 取到原句再写，取不到就标没核。"
                         "不写“重新查过了”“没写入记录”这类过程话。")
        else:
            parts.append("Answer this message directly after its necessary lookups and writes; do not invent a first part."
                         if en else "完成必要的查询和写入后直接答这一问，不造第一段。")
        if latest:
            parts.append("For tonight's latest, call query(kind='latest_departure', arguments={origin,destination}) and use "
                         "its first_line, sentences and window." if en else
                         "问今晚最晚：query(kind='latest_departure', arguments={origin,destination})，照它的 first_line、sentences 和 window 说。")
        if options and scenario in ("plan", "compare"):
            parts.append("These preferences compete: record the user's words, but do not change the plan until the user chooses "
                         "an option." if en else "这些偏好互相挤：这一条只记用户说的偏好，不改计划；等用户选了一套再改。")
        elif scenario in ("plan", "replan"):
            parts.append("For an actual plan edit, read state(view='day',date=...), edit its json, then propose(days=[json]) "
                         "and commit; do not resend the whole trip." if en else
                         "真要改计划：state(view='day',date=...)，改返回的 json，propose(days=[json])，再 commit；不重发整份行程。")
        refused = [line["fix"] for line in recorded.get("not_recorded", []) if line.get("part") == "record"]
        if refused:
            parts.append(("record: " if en else "record 没记：") + refused[0])
        if recorded.get("replaced"):
            w = consider.words(language or self.language)
            parts.append(("Say what changed: " if en else "回答里说一句这次改了什么：")
                         + w["join"].join(f"{line['old']} → {line['new']}" for line in recorded["replaced"]) + ("." if en else "。"))
        optional = [*unknown, considered["next"]]
        if asked:
            optional.append(consider.words(language or self.language)["ask_next"])
        if considered.get("more_how"):
            optional.append(considered["more_how"])
        separator = " " if en else ""
        for part in optional:
            if len(separator.join([*parts, part])) <= 600:
                parts.append(part)
        # The first/title/refusal instructions are mandatory; a pathological server-side refusal is still bounded.
        return separator.join(parts)[:600]

    def _record(self, owner: str, record: dict, event_id: str | None = None, *, language: str) -> tuple[dict, dict]:
        """Apply what a message states in the same transaction as its receipt, so this turn's package already has it.
        Facts never wait for the rest of the message (B3c): an invalid other part leaves them recorded alone. An item
        that would replace a stable entry without the user's own words in this message is left out and listed in
        not_recorded with what to ask (M1); `replaced` lists the entries the others replaced."""
        extra = [part for part, value in record.items() if value and part not in TURN_RECORDS]
        if extra:
            raise ValueError(f"turn records {', '.join(TURN_RECORDS)}; send {', '.join(extra)} with propose and commit")
        try:
            changes = Changes.model_validate(record)
        except ValidationError as error:
            # Each problem with its path in the record, what it needs and what was sent (B3f X1a), e.g.
            # `record.facts.0.true_from: must be an ISO time with a timezone offset (…), got 'this'`.
            raise ValueError("Invalid record: " + "; ".join(input_problems(error, "record"))) from None
        plan = self.versions.current(owner)["plan"]
        if plan is not None:
            people = {person["id"] for person in plan["people"]} | {"*"}
            for entry in [*changes.items, *changes.facts, *changes.pending_checks]:
                if not set(entry.subject) <= people:
                    raise ValueError("Ledger subject is outside trip people")
        not_recorded = []
        refused = [(item, self.ledger.stable_refusal(owner, item, Proposal(answered=event_id))) for item in changes.items]
        refused = [(item, reason) for item, reason in refused if reason]
        if refused:
            changes = changes.model_copy(update=dict(items=[item for item in changes.items if item not in [entry for entry, _ in refused]]))
            not_recorded.append(dict(part="items", records=len(refused), ids=[item.id for item, _ in refused],
                                     reason="; ".join(reason for _, reason in refused),
                                     ask="这条定了基本不变，用户本轮没说要改：先问用户，用户说了再记。" if language != "en" else
                                         "This entry is set once and the user did not say to change it this turn: ask the user first."))
        dumped = changes.model_dump(mode="json")
        try:
            with self.store.attempt():
                applied = self.ledger.apply(owner, dumped, answered=event_id)
        except ValueError as error:
            others = [part for part in TURN_RECORDS if part != "facts" and dumped.get(part)]
            if not (changes.facts and others):
                raise
            not_recorded += [dict(part=part, records=len(dumped[part]), reason=str(error)) for part in others]
            dumped = Changes(facts=changes.facts).model_dump(mode="json")
            applied = self.ledger.apply(owner, dumped, answered=event_id)
        warnings = []
        if plan is not None and (changes.facts or changes.items):
            report = check_plan(self.store, owner, Trip.model_validate(plan))
            warnings = [check for check in report["checks"] if check["status"] == "conflict" and check["critical"]]
        replaced = []
        for item in changes.items:
            for old_id in self.store.get("items", owner, item.id).get("relations", {}).get("supersedes", []) if item.id in applied["changed"] else []:
                old = self.store.get("items", owner, old_id)
                kind = consider.kind_of(old)
                replaced.append(dict(id=item.id, replaces=old_id, kind=kind, stability=consider.stability(kind), old=old["text"], new=item.text))
        return dumped, dict(changed=applied["changed"], not_recorded=not_recorded, warnings=warnings, needs_replan=bool(warnings),
                            crowding=applied["crowding"], **(dict(replaced=replaced) if replaced else {}))

    def present_pack(self, owner: str, **arguments) -> dict:
        pack = packs.build(self.store, owner, trip_folder=self.trip_folder(owner), language=self._reply_language(owner), **arguments)
        return packs.present(pack)

    def clock(self, owner: str, at: str | None = None) -> dict:
        return self._clock(owner, at, self._reply_language(owner))

    def _clock(self, owner: str, at: str | None, language: str) -> dict:
        """What "now" is for an answer (A2): the moment of the read in the trip's time zone (the plan's, else the
        repository's default, else UTC), with the sentence the pack's first line uses."""
        from tripwright.render.pack import now_line

        plan = self.versions.current(owner)["plan"]
        zone = plan["timezone"] if plan else (self.repository.timezone() if self.repository is not None else None) or "UTC"
        found = packs.clock(at or now(), zone)
        return dict(found, text=now_line(found, language))

    def _reply_language(self, owner: str) -> str:
        contexts = self.store.all("contexts", owner)
        latest = max(reversed(contexts), key=lambda context: aware(context["created_at"]), default=None)
        return (latest or {}).get("language", self.language)

    def _answer_instruction(self, owner: str) -> dict | None:
        """The first text itself for one-reply hosts, never a second-message flow's state."""
        if self._reply_flow() == "two":
            return None
        contexts = [context for context in self.store.all("contexts", owner) if context.get("first_text")]
        latest = max(contexts, key=lambda context: aware(context["created_at"]), default=None)
        if latest is None:
            return None
        text = self.store.get("events", owner, latest["source_event"])["received"]["text"]
        instruction = ("还没把第一段发给用户的话，最终回答以这段开头，再写 second_part_title 和第二段。"
                       if message_language(text) == "zh" else
                       "If you have not sent the first part, start the final response with this text, then write "
                       "second_part_title and the second part.")
        return dict(text=latest["first_text"], instruction=instruction)

    async def query(self, owner: str, kind: str, arguments: dict) -> dict:
        self.bound(owner)
        language = self._reply_language(owner)
        plan = self.versions.current(owner)["plan"]
        arguments = checked_arguments(kind, arguments, plan)
        if kind == "transit" and arguments.get("action") == "plan_day":
            from tripwright.solver import prepare

            day = prepare(arguments["day"])
            job = self.jobs.enqueue(owner, "plan_day", dict(day=day, timeout=min(300, max(0.01, float(arguments.get("timeout", 120))))),
                                    self.versions.current(owner)["number"], arguments["idempotency_key"])
            return dict(job_id=job["id"], status=job["status"], notice="Retrieve with result; cancel with result(cancel=true). Solver results require a separate plan proposal.")
        draft_id = arguments.get("target", {}).get("draft_id")
        if draft_id:
            draft = self.store.get("drafts", owner, draft_id)
            if draft["base_version"] != self.versions.current(owner)["number"] or draft["revision"] != self.ledger.revision(owner):
                raise ValueError("Fact target draft is stale; propose again")
            plan = draft["plan"]

        # Local times in the answer are the trip's (A5): the plan's zone, else the repository's default.
        zone = (plan or {}).get("timezone") or (self.repository.timezone() if self.repository is not None else None)
        if kind == "latest_departure":
            self._keep_frame(owner, arguments, plan, zone)

        async def lookup():
            async with asyncio.timeout(self.query_timeout):
                return await self.query_router.query(owner, kind, arguments, plan, zone=zone, at=now(), language=language)

        before = {record["id"] for record in self.store.all("evidence", owner)}
        try:
            result = await asyncio.wrap_future(self.jobs.submit(lookup()))
        except TimeoutError:
            # A11: a timed-out departure lookup says nothing was checked; “retain the plan” sent a host to its stored “末班”
            return {**dict(status="unverified", reason="The data-source query exceeded its time budget.",
                           consequence="This fact could not be verified in time.", alternative="Confirm locally or retain the previous safe plan."),
                    **(lookup_failed("timeout", language) if kind in ("latest_departure", "transit") else {})}
        self.record_evidence(owner, before, key=f"query:{identifier('Q')}")
        instruction = self._answer_instruction(owner)
        return dict(reply_begins_with=instruction, **result) if instruction else result

    def _keep_frame(self, owner: str, arguments: dict, plan: dict | None, zone: str | None) -> None:
        """A quick question whose turn asked for the latest departure is framed by the service (A10, D-67): its lookups
        name no hard node (S4h-a, stage 4 exit judge, codex_r1 question 1: the host added the plan's 05:45 queue as
        anchor_id after the frame's lookup, and its second part moved the user's night at the airport to leaving at
        dawn). Within FRAME_MINUTES of that turn (a “继续” turn carries it), latest_departure with anchor_id, or with
        `by` or `board_at` at a hard node's own time, is refused with what to send. A time the user names is not a hard
        node's and passes, as do other scenarios and lookups after the half hour."""
        from tripwright.query import anchors as plan_anchors, moment

        named = [key for key in ("anchor_id", "by", "board_at") if arguments.get(key)]
        if not named:
            return
        at = aware(now())
        written = [context for context in self.store.all("contexts", owner) if aware(context["created_at"]) <= at + timedelta(minutes=1)]
        latest = max(reversed(written), key=lambda context: aware(context["created_at"]), default=None)  # the later written of a tie
        frame = (latest or {}).get("frame") or {}
        if not frame.get("asks_latest") or SKELETON.get(frame.get("scenario")) != "quick" or \
                aware(latest["created_at"]) < at - timedelta(minutes=FRAME_MINUTES):
            return
        if "anchor_id" in named:
            key, node = "anchor_id", arguments["anchor_id"]
        else:
            key = named[0]
            try:
                instant = moment(arguments[key], arguments.get("date"), ZoneInfo(arguments.get("timezone") or zone or "UTC"), key)
            except ValueError:
                return  # its own check says what is wrong with it
            node = next((anchor["id"] for _, anchor, found in plan_anchors(plan) if found == instant), None)
            if node is None:
                return
        request = latest.get("continues") or latest.get("request_id")
        raise ValueError(f"Invalid query arguments: arguments.{key}: this question (turn request_id {request}) asks for the latest "
                         f"departure, and the service frames it: send origin and destination only (no anchor_id, by or board_at"
                         f"{f'; {arguments[key]!r} is hard node {node!r}' if key != 'anchor_id' else ''}). The result gives "
                         f"tonight's last connection and the next time to keep after it with when to leave for it (hard_node); "
                         f"the second part opens with its first_line.")

    def record_evidence(self, owner: str, before: set[str], *, key: str):
        """Evidence written by a lookup or a deep job is canonical (evidence/); an outbox operation carries it so a
        rejected push can replay it."""
        if self.committer is None:
            return
        self.take_in()
        with self.store.transaction():
            added = [record for record in self.store.all("evidence", owner) if record["id"] not in before]
            if added:
                self.outbox.add(owner, kind="job", tool="job", key=key, records=[record["id"] for record in added],
                                replay=dict(evidence=added))

    async def _requery(self, owner: str, plan: dict):
        before = {record["id"] for record in self.store.all("evidence", owner)}
        try:
            async with asyncio.timeout(self.deep_timeout):
                return await self.query_router.recheck(owner, plan)
        except TimeoutError:
            raise ValueError("Deep verification exceeded its time budget; unresolved facts remain unverified") from None
        finally:
            self.record_evidence(owner, before, key=f"deep:{identifier('Q')}")

    async def _plan_day(self, owner: str, payload: dict):
        from tripwright.providers.common import request_scope
        from tripwright.solver import solve_day

        if not all(hasattr(self.query_router.transit, method) for method in ("resolve", "day")):
            return dict(status="unverified", reason="Day solving requires a configured whole-day transit provider.")
        with request_scope(client=owner):
            return await solve_day(payload["day"], provider=self.query_router.transit, timeout=payload["timeout"])

    def propose(self, owner: str, **arguments) -> dict:
        self.bound(owner)
        partial = [name for name in ("days", "places", "bookings") if arguments.get(name) is not None]
        for name in ("days", "places", "bookings"):
            if name not in partial:
                arguments.pop(name, None)
        if partial:
            if arguments.get("plan") is not None:
                raise ValueError("Send either the complete plan or days/places/bookings patches, not both")
            patch = {name: arguments.pop(name) for name in partial}
            current = self.versions.current(owner)["plan"]
            arguments["plan"] = merged_plan(current, days=patch.get("days") or [], places=patch.get("places") or [],
                                            bookings=patch.get("bookings") or [])
        problems = []
        if arguments.get("plan") is not None:
            try:
                checked_plan(arguments["plan"])
            except ValueError as error:
                problems.append(str(error))
            for index, place in enumerate(arguments["plan"].get("places") or []):
                cited = ((place or {}).get("hours_source") or {}).get("evidence_id")
                if cited and not self._has("evidence", owner, cited):
                    problems.append(f"Invalid plan: plan.places.{index}.hours_source.evidence_id: no evidence {cited!r} in this trip; "
                                    "record the page with query(kind=\"web_evidence\") first")
        arguments["changes"] = normalize_record_people(arguments.get("changes") or {}, arguments.get("plan") or
                                                         self.versions.current(owner)["plan"], "changes")
        try:
            Changes.model_validate(arguments["changes"])
        except ValidationError as error:
            problems.append("Invalid changes: " + "; ".join(input_problems(error, "changes")))
        if arguments.get("usages") is not None:
            try:
                arguments["usages"] = checked_usages(arguments["usages"])
            except ValueError as error:
                problems.append(str(error))
        if problems:
            raise ValueError("Invalid proposal: " + " | ".join(problems))
        trip = self.current_trip(owner)
        if self.repository is not None and arguments.get("plan") is not None and trip and arguments["plan"].get("id") != trip:
            raise ValueError(f"plan.id must be the current trip {trip!r}; open another trip with turn(new_trip=...)")
        self.take_in()
        draft = self.versions.propose(owner, **arguments)
        context = self.store.get("contexts", owner, draft["ctx_id"])
        scenario = context["bundle"]["scenario"]
        if not ledger_only(draft) and scenario in ("plan", "compare") and (context.get("options_pending") or draft["checks"].get("crowding")):
            # Keep both preferences on record only through a separate ledger draft; never choose a plan for the user.
            draft.update(options_pending=True, status="rejected",
                         refused=[*draft.get("refused", []),
                                  "Competing preferences need the user's option; commit preferences without a plan, then change the plan after the user chooses"])
            self.store.put("drafts", owner, draft)
        if "verify" not in draft:
            with self.store.transaction():
                draft = self.store.get("drafts", owner, draft["id"])
                pack_id = identifier("CTX")
                shown = self.present_pack(owner, kind="wide", scenario=self.store.get("contexts", owner, draft["ctx_id"])["bundle"]["scenario"],
                                          pack_id=pack_id, snapshot=draft["snapshot"], at=draft["snapshot"]["taken_at"], candidate=draft["id"])
                draft["verify"] = dict(pack=shown["pack"], markdown=shown["markdown"], meta=shown["meta"], tokens=shown["tokens"])
                self.store.put("drafts", owner, draft)
        unchanged_plan = ledger_only(draft)
        notice = None
        if draft.get("refused"):
            # D-51: a user decision that only touches an old lock refuses the proposal; lock_changes says where to decide again.
            notice = "Refused, nothing will be recorded: " + "; ".join(draft["refused"]) + "."
        elif draft.get("not_recorded"):
            notice = ("Facts will be recorded; not recorded: " + "; ".join(
                f"{part['part']} ({part['records']}): {part['reason']}" for part in draft["not_recorded"])
                + ". Correct those parts and propose them again.")
        elif unchanged_plan and draft["checks"]["conflicts"]:
            notice = "The ledger change will be recorded; the unchanged plan conflicts with it and will be marked needs_replan."
        elif draft["status"] == "rejected" and draft["changes"].get("facts"):
            notice = "A rejected plan proposal records nothing, including its facts; propose the facts without a plan to record them now."
        # What the proposal does to user locks, said in the answer (D-36, D-49): made from the operations the ledger
        # admitted in the preview (ledger.lock_receipt); a correction waits for the user.
        locks = draft.get("lock_changes")
        return dict(draft_id=draft["id"], base_version=draft["base_version"], status=draft["status"],
                    checks=draft["checks"], job_id=draft["job_id"], usages=draft["usages"], snapshot=draft["snapshot"],
                    **(dict(lock_changes=locks) if locks else {}), changelog=draft.get("changelog", []),
                    warnings=draft["checks"]["conflicts"] if unchanged_plan else [],
                    not_recorded=draft.get("not_recorded", []), notice=notice, crowding=draft["checks"].get("crowding", []),
                    possible_contradictions=draft["checks"].get("possible_contradictions", []),
                    verify=dict(pack_id=draft["verify"]["pack"]["pack_id"], tokens=draft["verify"]["tokens"],
                                how=f"state(view='pack', draft_id='{draft['id']}') returns the wide pack and the complete candidate for review"))

    def commit(self, owner: str, draft_id: str, idempotency_key: str, *, accept_unverified: bool = False,
               wait: float | None = None) -> dict:
        self.bound(owner)
        with self.store.transaction():
            prior = self.outbox.find(owner, "commit", idempotency_key)
            version = self.versions.commit(owner, draft_id, idempotency_key, accept_unverified=accept_unverified)
            if prior is None:
                draft = self.store.get("drafts", owner, draft_id)
                context = self.store.get("contexts", owner, draft["ctx_id"])
                prior = self.outbox.add(owner, kind="commit", tool="commit", key=idempotency_key, draft_id=draft_id,
                                        version_id=version["id"], records=list(draft["changes"].get("items", []) and
                                                                               [item["id"] for item in draft["changes"]["items"]]),
                                        replay=dict(draft=draft, context=context, accept_unverified=accept_unverified,
                                                    version_id=version["id"]),
                                        state="received" if self.committer else "effective")
                notices.settle(self.store, owner, prior) if self.committer is None else None
        timeout = self.commit_wait if wait is None else wait
        if self.committer is not None and prior["state"] == "received" and timeout > 0:
            self.committer.wait(prior["id"], timeout)
            # Replayed on a newer head while we waited: the version as it now stands, with `redone` when it was redone (D-75).
            try:
                version = self.store.get("versions", owner, version["id"])
            except ValueError:
                pass
        # The user locks this commit changed, from the operations the ledger admitted and applied in it (D-49).
        locks = self.store.get("drafts", owner, draft_id).get("committed_lock_changes")
        return dict(version, accepted=True, write=self.write_ref(owner, prior), **(dict(lock_changes=locks) if locks else {}))

    def result(self, owner: str, job_id: str, *, cancel: bool = False, ack_events: list[str] | None = None) -> dict:
        self.bound(owner)
        if cancel:
            return self.jobs.cancel(owner, job_id)
        return self.jobs.result(owner, job_id, ack_events=ack_events, body=lambda job, message: self._verdict(owner, job, message))

    def _verdict(self, owner: str, job: dict, message: dict) -> dict:
        """CONTRACTS 1.4: passed only without conflicts, unverified critical facts and unaddressed dispositions, and with
        every group of the wide pack complete; it says nothing about the answer the host writes."""
        if job["kind"] != "deep_check":
            return {}
        value = message["value"]
        checks = value.get("checks", {})
        usages = checks.get("usages", [])
        draft = self.store.get("drafts", owner, value["draft_id"])
        groups = ((draft.get("verify") or {}).get("meta") or {}).get("required", {}).values()
        if checks.get("conflicts"):
            verdict = "conflict"
        elif any(check.get("critical") for check in checks.get("unverified", [])) or any(
                usage.get("status") in ("unaddressed", "invalid") for usage in usages) or any(group["status"] != "complete" for group in groups):
            verdict = "incomplete"
        else:
            verdict = "passed"
        return dict(draft_id=value["draft_id"], verdict=verdict, usages=usages,
                    conflicts=[check["id"] for check in checks.get("conflicts", [])],
                    unverified=[check["id"] for check in checks.get("unverified", [])])

    def state(self, owner: str, view: str = "current", date: str | None = None, draft_id: str | None = None) -> dict:
        """Every view carries `now` (A2): what is current is read against the moment of the read."""
        self.bound(owner)
        return dict(self._state(owner, view, date, draft_id), now=self.clock(owner))

    def _state(self, owner: str, view: str, date: str | None, draft_id: str | None) -> dict:
        current = self.versions.current(owner)
        if view in ("current", "plan"):
            return current
        if view == "blank":
            blank = Trip(id="new-trip", title="New trip", start="2030-01-01", end="2030-01-01", timezone="UTC",
                         areas=["area"], people=[dict(id="p1", name="Traveler")])
            return dict(plan=blank.model_dump(mode="json"))
        if view == "day":
            # The text view remains; json is the same day object that propose(days=[...]) accepts.
            text = repo_files.day_text(self, owner, date or "")
            day = next((day for day in (current["plan"] or {}).get("days", []) if str(day["date"]) == date), None)
            if day is None:
                raise ValueError(f"Unknown plan day: {date!r}")
            return dict(version=current["number"], needs_replan=current.get("needs_replan", False),
                        warnings=current.get("warnings", []), text=text, json=day)
        if view == "ledger":
            return self.ledger.current(owner)
        if view == "profile":
            return dict(profile=self.ledger.profile(owner), preferences=[item for item in self.ledger.current(owner)["items"] if item["kind"] == "preference"],
                        memory=memory.memory_data(self.store, owner, trip=self.current_trip(owner), at=now()))
        if view == "memory":
            return dict(memory.memory_data(self.store, owner, trip=self.current_trip(owner), at=now()), **self._memory_all(owner))
        if view == "pack":
            if not draft_id:
                raise ValueError("view='pack' needs draft_id")
            draft = self.store.get("drafts", owner, draft_id)
            verify = draft.get("verify") or {}
            return dict(draft_id=draft_id, job_id=draft.get("job_id"), markdown=verify.get("markdown"), meta=verify.get("meta"),
                        candidate=draft["plan"], checks=draft["checks"], changes=draft["changes"],
                        stale=self.versions.fresh(owner, draft.get("snapshot")))
        if view == "sync":
            sync = self.committer.status() if self.committer else dict(canonical=None, unsynced=0, note="single-database mode")
            # site/publish.py records every attempt: where each trip's public page is, or why it is not there
            pages = [{key: item[key] for key in ("trip", "status", "url", "plan_version", "blocked", "error") if item.get(key) is not None}
                     for item in self.store.all("share", owner) if item.get("status") != "superseded"]
            return dict(sync, public_pages=pages) if pages else sync
        if view == "decisions":
            return self.ledger.explain(owner)
        if view == "history":
            # D-53: what each version changed, field by field, who changed it, on what basis and why.
            return dict(versions=[dict(version=version["number"], committed_at=version["committed_at"], source_event=version.get("source_event"),
                                       origin=version.get("origin"), changelog=version.get("changelog"))
                                  for version in sorted(self.store.all("versions", owner), key=lambda version: version["number"])])
        if view == "export":
            return {"format": "tripwright-state-v1", "version": current, "ledger": self.ledger.current(owner),
                    "records": {table: self.store.all(table, owner) for table in ("events", "items", "facts", "decisions", "pending_checks", "journal")},
                    "notice": "For a round-trip directory export including jobs and history, use the local export CLI."}
        raise ValueError("Unknown state view")

    def _memory_all(self, owner: str) -> dict:
        """Everything turn's consider can list, for the whole trip and everyone (M1): `consider` and `sources`."""
        at = now()
        plan = self.versions.current(owner)["plan"]
        language = self._reply_language(owner)
        listed = consider.build(self.store, owner, pack=None, plan=plan, scenario="plan", at=at, language=language, limit=None)
        return dict(consider=listed["entries"], sources=consider.sources(self.store, owner, plan, at=at, language=language))

    def explain(self, owner: str, record_id: str | None = None, version: int | None = None) -> dict:
        self.bound(owner)
        explanation = self.ledger.explain(owner, record_id)
        versions = self.store.all("versions", owner)
        if version is not None:
            versions = [candidate for candidate in versions if candidate["number"] == version]
            if not versions:
                raise ValueError("Unknown version")

        def constraints(candidate):
            # Contexts are runtime state: a version rebuilt from the repository or adopted from a hand edit has none.
            try:
                return self.store.get("contexts", owner, candidate["ctx_id"])["bundle"] if candidate.get("ctx_id") else None
            except ValueError:
                return None

        card = self._record_card(owner, record_id) if record_id else None
        if card is None:
            explanation["version_usage"] = [dict(version=candidate["number"], ctx_id=candidate.get("ctx_id"), usages=candidate.get("usages", []),
                                                  origin=candidate.get("origin"), constraints=constraints(candidate),
                                                  changelog=candidate.get("changelog"))
                                            for candidate in versions]
            return explanation
        # S5 (METHOD 2.9 second level): one record by its ID, also over MCP alone: the record itself and, unless a
        # version is asked for, only the latest version, with this record's usage, whether its pack carried it and the
        # change-log lines that name it.
        if version is None:
            versions = sorted(versions, key=lambda candidate: candidate["number"])[-1:]
        explanation["record"] = card
        explanation["version_usage"] = [dict(version=candidate["number"], ctx_id=candidate.get("ctx_id"), origin=candidate.get("origin"),
                                              usages=[usage for usage in candidate.get("usages", []) if usage.get("item_id") == record_id],
                                              carried=record_id in json.dumps(constraints(candidate) or {}),
                                              changelog=[line for line in candidate.get("changelog") or [] if record_id in json.dumps(line)])
                                        for candidate in versions]
        return explanation

    def _record_card(self, owner: str, record_id: str) -> dict | None:
        """One ledger record for explain (S5): its words, whose it is (consider's layer words), the message it comes from
        (packs.message: also an imported long-term entry's), its status, what replaced it and the record in effect now,
        its relations and the agent's entries that give way to it. None for an ID that is no ledger record."""
        for table, name in RECORD_TABLES:
            if self._has(table, owner, record_id):
                found = self.store.get(table, owner, record_id)
                break
        else:
            return None
        events = {event["id"]: event for event in self.store.all("events", owner) if "received" in event}
        basis = (found.get("source") or {}).get("basis")
        code = ("user" if found["decided_by"] == "user" else "agent") if name == "decision" else consider.basis_layer(basis) if basis else None
        items = self.store.all("items", owner) if name == "item" else []
        replaced_by = ([item["id"] for item in items if record_id in item.get("relations", {}).get("supersedes", [])] if name == "item" else
                       [found["superseded_by"]] if found.get("superseded_by") else [])
        current, seen = record_id, {record_id}
        while True:  # the end of the replacement chain: the record in effect now
            following = ([item["id"] for item in items if current in item.get("relations", {}).get("supersedes", [])] if name == "item" else
                         [self.store.get(table, owner, current).get("superseded_by")] if name == "decision" else [])
            following = [entry for entry in following if entry and entry not in seen]
            if not following:
                break
            current = following[-1]
            seen.add(current)
        text = (f"{found['question']} → {found['choice']}" if name == "decision" else found.get("text") or found.get("question"))
        card = dict(id=record_id, table=name, status=found["status"], text=text, quote=found.get("quote"), basis=basis,
                    layer=consider.words(self._reply_language(owner))["layer"][code] if code else None, said=packs.message(found, events),
                    subject=found.get("subject"), scope=packs.bare_scope(found.get("scope")), superseded_by=replaced_by,
                    current=current, relations=found.get("relations") or {})
        card.update({key: found[key] for key in RECORD_FIELDS[name] if found.get(key) is not None})
        conflicted = [item["id"] for item in items if record_id in item.get("relations", {}).get(consider.CONFLICTS_USER, [])]
        if conflicted:
            card["conflicted_by"] = conflicted
        return card
