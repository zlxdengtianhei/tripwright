from __future__ import annotations

import asyncio
import json
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
from tripwright.repo.snapshot import received_events
from tripwright.agents import core_steps
from tripwright.storage import Store, answers_path, encode, identifier, now, remember_answer
from tripwright.trip import Trip, aware, input_problems, json_type, local_instant
from tripwright.usage import checked_usages
from tripwright.versions import Versions, ledger_only

# What a turn may record from the message it receives (CONTRACTS 1.3): first-hand facts, items, their relations and the
# answer about imported long-term entries. Decisions lock plan parts and go through propose and commit.
TURN_RECORDS = ("items", "facts", "relate", "carry", "pending_checks")
SKELETON = {"quick": "quick", "status": "quick"}
# check_answer (A6): evidence from this long before the question's turn backs a draft; a timetable older than this may
# have changed (A2's refresh time), and the earlier questions of a conversation fall inside it.
SESSION_HOURS = 6
RECENT_QUESTION_MINUTES = 30  # check_answer without question or request_id: a user message this recent is the question
FRAME_MINUTES = packs.STEP_MINUTES  # S4h-a: a quick latest question's frame holds for lookups this long after its turn
# S4a: an app that may not show text written between tool calls ends its reply with the first part and this line (core
# step 4); the user's next message, sent to turn with points [], brings the second part (second_part_due)
LATER = first_part.LATER
# a first part that says a second part follows (the hooks' word list, render.check_answer.PROMISE)
PROMISES_SECOND = re.compile(r"第二段|核完再发|second part", re.I)
UNKNOWN_QUESTION = "本问原话不明，吃的检查没做。"
UNKNOWN_POINTS = "这一问的 turn 没给 points（或没给 request_id），没核第一段有没有逐件回应、写没写在哪。"  # A12
UNKNOWN_LISTED = 8  # stored place IDs `next` names when a place in points is not recognized (S4h-a)
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

    def turn(self, owner: str, text: str, request_id: str, *, nature: str = "host_restatement",
             native_ref: str | None = None, channel: str = "conversation", subject: list[str] | None = None,
             scope: dict | None = None, scenario: str = "plan", focus_keys: list[str] | None = None,
             exclude_keys: list[str] | None = None, speaker: str | None = None, record: dict | None = None,
             new_trip: str | None = None, points: list | None = None) -> dict:
        self.bound(owner)
        # S5: a record of the wrong shape (or one the ledger refuses, below) never refuses the turn: the message is
        # received and the first part drafted as without it; recorded.not_recorded says what was left out and where to
        # send it again
        refused_record = None
        try:
            record_input = (checked_record(record) if record is not None else None) or None
        except ValueError as error:
            record_input, refused_record = None, str(error)
        points = self._checked_places(owner, first_part.checked_points(points))
        prior = self.outbox.find(owner, "turn", request_id)
        if prior is not None and encode(prior["replay"].get("input")) != encode(record_input):
            raise ValueError("request_id was reused with different content")
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
                            changes, recorded = self._record(owner, placeholder_sources(record_input, event_id), event_id)
                    except ValueError as error:
                        refused_record = str(error)
                if refused_record:
                    recorded = dict(recorded, not_recorded=[*recorded["not_recorded"], self._refused_record(record, refused_record, event_id)])
                # What the last turn asked and this message leaves unanswered takes its default (M1, R-58, R-67).
                silent = consider.defaults(self.store, owner, self._asked(owner, event_id), record_input, at=now(), language=self.language)
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
            shown = self.present_pack(owner, kind="narrow", scenario=scenario, pack_id=context_id, snapshot=context["snapshot"],
                                      at=context["snapshot"]["taken_at"], subject=subject, scope=dict(scope or {}, channel=channel),
                                      live=True)
            current = self.versions.current(owner)
            plan = current["plan"] or {}
            at = context["snapshot"]["taken_at"]
            # The memory this question must weigh, what it must ask and the offer to draft a question (M1). What
            # silence settled is stored with this context first, so this turn does not ask it again.
            context.update(settled=recorded.get("defaulted", []))
            self.store.put("contexts", owner, context)
            known = consider.sources(self.store, owner, plan or None, at=at, language=self.language)
            asked = consider.asks(self.store, owner, plan or None, scenario=scenario, scope=scope, at=at, language=self.language, known=known)
            considered = consider.build(self.store, owner, pack=shown["pack"], plan=plan or None, scenario=scenario, at=at, language=self.language)
            context.update(pack=shown["pack"], asked=[dict(id=question["id"], scope=question.get("scope", {})) for question in asked])
            self.store.put("contexts", owner, context)
            anchors = self._hard_nodes(owner, plan, subject, scope, at)
            write = self.write_ref(owner, op)
            pending = memory.pending_entries(self.store, owner)
            # S5b: do_first is the first field the host reads (set below, once the first part and second_part_due are known)
            response = dict(do_first=None, now=self.clock(owner, at), receipt=receipt, ctx_id=context_id, base_version=current["number"],
                            snapshot=context["snapshot"],
                            write=write, notices=self.open_notices(owner),
                            pack=dict(markdown=shown["markdown"], meta=shown["meta"], tokens=shown["tokens"]),
                            recorded=recorded, possible_contradictions=self.ledger.possible_contradictions(owner, context["bundle"]["items"]),
                            current_summary=dict(trip=self.current_trip(owner), title=plan.get("title"), days=[day["date"] for day in plan.get("days", [])],
                                                 needs_replan=current.get("needs_replan", False) or recorded["needs_replan"],
                                                 warnings=current.get("warnings", [])),
                            hard_nodes=anchors, answer=dict(skeleton=SKELETON.get(scenario, "first")),
                            inbox=[dict(job_id=message["job_id"], state=message["state"]) for message in self.store.all("inbox", owner)
                                   if message["state"] in ("generated", "available") and message["base_version"] == current["number"]])
            response["write_back"] = self._write_back(event_id)
            if pending:
                response["memory"] = dict(pending=pending, ask="长期条目待确认：在对话里问用户这趟还用不用；按回答调 turn(record={carry:[{id,status}]})。")
                asked.append(dict(id="memory", say=response["memory"]["ask"], default=packs.WORDS["zh"]["memory_default"],
                                  record=dict(carry=[dict(id=entry["id"], status="confirmed or not_used") for entry in pending])))
            # A10 (D-67): the first part, drafted from what is stored, for every host to send before any lookup; A12: from
            # this question's points when the host gives them, with what the trip stores for each
            # S4a: a message that names nothing (points []) after a question whose promised second part never passed
            # check_answer asks for that second part; no new first part is drafted for it
            continued = self._unanswered(owner, event_id, at) if points == [] else None
            due = self._second_part_due(owner, continued) if continued else None
            told = packs.point_facts(self.store, owner, plan or None, points, anchors, at, self.language,
                                     person=packs.asker(plan, speaker, subject)) if points is not None and plan and due is None else None
            first = None if due else self._with_later(first_part.draft(text, response["now"], anchors, plan or None, self.language, told))
            # S4h-a (stage 4 exit judge, mcponly_r1 question 2): the message asking for an earlier second part is that
            # question's turn for the lookups that follow: its points (where the user is, where they go next) and its frame
            # go with it, and the lookups start from its next step as they would have without the “继续”
            context.update(points=(continued.get("points") or packs.point_record(None, anchors)) if continued else
                           packs.point_record(told, anchors),  # what check_answer holds this question's first part to
                           frame=continued.get("frame") if continued else
                           dict(scenario=scenario, asks_latest=bool(first and first["asks_latest"])),
                           request_id=request_id, promises_second=bool(first and PROMISES_SECOND.search(first["text"])),
                           first_text=first["text"] if first else None, continues=due["request_id"] if due else None)
            self.store.put("contexts", owner, context)
            response.update(consider={key: considered[key] for key in ("entries", "more", "more_how") if key in considered},
                            ask=asked, offer=consider.offer(known["languages"], self.language), first_part=first,
                            next=self._next(considered, asked, recorded, archived=any(anchor.get("archived_claims") for anchor in anchors),
                                            latest=bool(first and first["asks_latest"]), points=(points is not None) if plan and not due else None,
                                            due=due, unknown=self._unknown_places(plan, told)),
                            **core_steps())
            key = "en" if self.language == "en" else "zh"
            unknown = self._unknown_places(plan, told)
            response["do_first"] = (first_part.DO_DUE if due else first_part.DO_POINTS if unknown or (plan and points is None)
                                    else first_part.DO_FIRST)[key]
            if due:
                response["second_part_due"] = due
            if told is not None:
                response["points"] = told
        if self.committer is not None:
            response["sync"] = self.committer.status()
            self.committer.poke()
        return response

    def _refused_record(self, record, reason: str, event_id: str) -> dict:
        """recorded.not_recorded for a turn.record left out as a whole (S5), with where the corrected entries go: the same
        request_id now holds this receipt, so they go into this question's propose (or the next turn's record)."""
        count = sum(len(value) for value in record.values() if isinstance(value, list)) if isinstance(record, dict) else 0
        en = self.language == "en"
        return dict(part="record", records=count, reason=reason,
                    fix=(f"Nothing of record was kept; the message was received. Send the corrected entries in this question's propose "
                         f"(source.event \"{event_id}\"), not again with this request_id." if en else
                         f"record 一条没记，原话已收。改好后放进这一问的 propose（source.event 写 \"{event_id}\"），不要用同一个 request_id 重发 turn。"))

    def _write_back(self, event_id: str) -> dict:
        """What turn says about the agent's own inferences and choices (S5, METHOD 6.1 three): send them back, marked as
        the agent's, in this question's one proposal, never between propose and commit; the shape to send, with this
        message as source."""
        en = self.language == "en"
        say = ("What you inferred or chose that the user did not say (a route, a place, a default): put it in this question's one "
               "propose as changes.items with source.basis agent_inference (the user's own words: user_explicit), then commit; record "
               "nothing between propose and commit." if en else
               "这一问里你推断或替用户选了用户没说的（走法、店、默认）：放进这一问的同一份 propose 的 changes.items，source.basis 写 "
               "agent_inference（用户的原话写 user_explicit），再 commit；不在 propose 与 commit 之间另记。")
        return dict(say=say, record=dict(items=[dict(kind="preference", key="<the user's key when it is about the same thing>", quote="",
                                                     text="<one sentence: what you inferred or chose>", subject=["*"], strength="prefer",
                                                     source=dict(event=event_id, basis="agent_inference"))]))

    def _hard_nodes(self, owner: str, plan: dict, subject: list[str] | None, scope: dict | None, at: str) -> list[dict]:
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
                                                language=self.language))
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
        points = self._checked_places(owner, first_part.checked_points(points))
        plan = self.versions.current(owner)["plan"] or {}
        at = now()
        anchors = self._hard_nodes(owner, plan, subject, None, at) if plan else []
        told = packs.point_facts(self.store, owner, plan, points, anchors, at, self.language,
                                 person=packs.asker(plan, speaker, subject)) if points is not None and plan else None
        clock = self.clock(owner, at)
        first = self._with_later(first_part.draft(text, clock, anchors, plan or None, self.language, told))
        en = self.language == "en"
        return dict(now=clock, first_part=first, **(dict(points=told) if told is not None else {}),
                    next=(" " if en else "").join(self._unknown_places(plan, told) + self._send_first(en)), **core_steps())

    def _checked_places(self, owner: str, points: list[dict] | None) -> list[dict] | None:
        """Points whose place and words name two different stored places are refused before anything is recorded
        (S4h-a, ns_on_r1 question 2), with what to send instead; the same request_id may be sent again."""
        conflicts = packs.place_conflicts(self.versions.current(owner)["plan"], points) if points else []
        if conflicts:
            raise ValueError("Invalid points: " + "; ".join(conflicts))
        return points

    def _unknown_places(self, plan: dict | None, told: dict | None) -> list[str]:
        """What `next` says of the places in points that are no stored place and no coordinates (S4h-a, mcponly_r1
        question 1: “airport” was left unread and the first part said only that the time needs checking): which, that
        the first part names none of them and never the plan's place in their stead, and the stored IDs to send."""
        unknown = list(dict.fromkeys([point["unknown"] for point in (told or {}).get("points", []) if point.get("unknown")]
                                     + ([told["at"]["unknown"]] if (told or {}).get("at") and told["at"].get("unknown") else [])))
        if not unknown:
            return []
        en = self.language == "en"
        stored = [place for place in (plan or {}).get("places", []) if place.get("transit_id") or place.get("coords")]
        named = (", " if en else "、").join(f"{place['id']} {packs.short_name(place, self.language, plan)}" for place in stored[:UNKNOWN_LISTED])
        said = (", " if en else "、").join(f"“{value}”" for value in unknown)
        return [f"These places in points are no stored place and were not recognized: {said}. The first part says nothing "
                f"stored for them and does not put the plan's place in their stead. If one is a stored place, send its ID "
                f"({named}) and call turn again with the same request_id; if not, answer as it is and look it up by "
                f"coordinates or stop name." if en else
                f"points 里这些地方不是这趟存的地点，也认不出：{said}。第一段没给它们存的车和时刻，也没拿计划里的地点顶替。"
                f"是存的地点就改成地点编号（{named}），用同一个 request_id 再调一次 turn；不是就照这样答，第二段按坐标或站名查。"]

    def _with_later(self, first: dict) -> dict:
        """The first part and the line that ends it in an app that cannot show text between tool calls (S4a)."""
        return dict(first, later=LATER["en" if self.language == "en" else "zh"])

    def _unanswered(self, owner: str, event_id: str, at: str) -> dict | None:
        """The context of the question before this message, when its first part said a second part follows and no second
        part passed check_answer for its request_id (the answers file the Stop hook reads), within SESSION_HOURS (S4a).
        An app that cannot show text between tool calls ended its reply with that first part; a host that stopped early
        did too. None without an answers file: nothing tells whether the second part went out."""
        # in the order they were written (one clock moment can hold two), the latest last
        earlier = sorted((context for context in self.store.all("contexts", owner)
                          if context.get("source_event") != event_id and context.get("request_id")),
                         key=lambda context: aware(context["created_at"]))
        last = earlier[-1] if earlier else None
        path = self.answers_path()
        if last is None or path is None:
            return None
        if last.get("continues"):  # the message before asked for it too, and it has still not passed
            last = next((context for context in reversed(earlier) if context["request_id"] == last["continues"]), None)
        if last is None or not last.get("promises_second") or aware(at) - aware(last["created_at"]) > timedelta(hours=SESSION_HOURS):
            return None
        try:
            passed = json.loads(path.read_text(encoding="utf-8")).get("answers", {}) if path.is_file() else {}
        except (OSError, ValueError):
            passed = {}
        return None if last["request_id"] in passed else last

    def _second_part_due(self, owner: str, last: dict) -> dict:
        """turn's `second_part_due` for the unanswered question's context (`last`, _unanswered)."""
        event = self.store.get("events", owner, last["source_event"])
        kept = last.get("points") or {}
        # what step 5 looks up from: each point with its place, and where the user is (or was assumed to be)
        return dict(request_id=last["request_id"], said=event["receipt"]["received_text"], first_part=last.get("first_text"),
                    points=[dict(kind=point["kind"], said=point["said"], place=point.get("place")) for point in kept.get("points", [])],
                    at=kept.get("at"))

    def _send_first(self, en: bool) -> list[str]:
        """What `next` says of the first part (A10, A14, A16), for turn and for the command line's first part."""
        return ["Send first_part.text to the user as it is (only the form of address and the language may change), then "
                "follow steps." if en else "先把 first_part.text 原样发给用户（只可改称呼和语言），再照 steps 往下做。",
                # A10 replay: “Following instructions: …” before the first part, “还在核…马上发第二段” between the two
                "The user sees two messages only, the first and the second part: send no status line before the first "
                "or between the two." if en else "用户只看到第一段和第二段两条消息：第一段之前、两段之间不发任何状态句。",
                # A14 (quality judge 7): “第一部分已经在上面了”, “上面这段是按存的计划写的” sent while nothing was above
                "The user sees no tool result: the first part is there only once you send it; never write that something is "
                "above." if en else "用户看不到工具结果：第一段要你发出来才算，不写“上面已经……”。"]

    def _asked(self, owner: str, event_id: str) -> list[dict]:
        """What the previous turn asked (the latest context of another message): must-go places, real-time data."""
        earlier = [context for context in self.store.all("contexts", owner) if context.get("source_event") != event_id]
        latest = max(earlier, key=lambda context: aware(context["created_at"]), default=None)
        return (latest or {}).get("asked") or []

    def _next(self, considered: dict, asked: list[dict], recorded: dict, *, archived: bool = False, latest: bool = False,
              points: bool | None = None, due: dict | None = None, unknown: list[str] = ()) -> str:
        """What to do before answering (design v0.3.1 2.1 three). First (A10, D-67): send `first_part` as it is, then
        look things up; a latest/last-service question goes to latest_departure without a deadline, whose first line
        the second part opens with; the second part is sent once, after check_answer passes. Then: weigh the listed
        memory, ask what ask holds, say what this message replaced. An archived last/only/late claim in the hard nodes
        (A9) is quoted only as first_part quotes it, and said as a fact only after a lookup."""
        w = consider.words(self.language)
        en = self.language == "en"
        # A12: `points` given (True) or not (False) with a plan; None without a plan, where there is nothing to answer from.
        # Without them the draft is the stored plan's and answers nothing the user named.
        parts = [] if points is not False else ["No points were given: first_part follows the stored plan and answers nothing this question "
                                   "names. Write each thing it names in points (where the user is, where to, food, an event, "
                                   "tickets; in the user's words) and call turn again with the same request_id, then follow "
                                   "that result." if en else
                                   "这一问没给 points：first_part 只按存的计划写，不回应这一问点名的事。先把这一问点名的每件事"
                                   "（人在哪、去哪、吃、活动、票，用用户的话）写进 points，用同一个 request_id 再调一次 turn，再照它做。"]
        parts = list(unknown) + parts  # S4h-a: places in points the service did not recognize come first
        if due:
            # S4a: the user asks for the earlier question's second part (it was promised, none passed check_answer)
            parts.append(f"This message asks for the second part of request {due['request_id']} (“{due['said']}”): send no new "
                         f"first part; check its facts and run check_answer with request_id {due['request_id']}, then send it." if en else
                         f"这一句是要上一问（request_id {due['request_id']}，“{due['said']}”）的第二段：不再发第一段，核查它的事，"
                         f"check_answer 用 request_id {due['request_id']}，过了再发。")
        else:
            parts += self._send_first(en)
        if latest:
            parts.append("For the latest departure or the last service: query latest_departure with origin and destination "
                         "only (no anchor_id, by or board_at); the service searches tonight's last connection and the next "
                         "time to keep, and the second part opens with its first_line." if en else
                         "问最晚、末班：query latest_departure 只给 origin 和 destination（不给 anchor_id、by、board_at），"
                         "服务端连续查到今晚最后一班并给之后要守的时间；第二段首行照抄它的 first_line。")
        parts.append("Send the second part once, only after check_answer passes it." if en else "第二段过了 check_answer 才发，只发一次。")
        if points:
            parts.append("The second part answers each point too; tickets as the stored tickets say (points[].facts.tickets)." if en
                         else "第二段也逐件回应 points；讲票照存的票说（points 里的 tickets）。")
        parts.append(considered["next"])
        if archived:
            parts.append("硬节点里标“存档”的“末班、只剩、最晚”是计划里存的、没核：只照 first_part 的写法引用；要说成事实就先查（latest_departure 只给起终点，或 transit 加 until）。"
                         if not en else
                         "Hard-node lines marked archived (末班/只剩/最晚) are stored, not checked: quote them only as first_part "
                         "does; state one as a fact only after a lookup (latest_departure with origin and destination only, or "
                         "transit with until).")
        if asked:
            parts.append(w["ask_next"])
        refused = [line["fix"] for line in recorded.get("not_recorded", []) if line.get("part") == "record"]
        if refused:
            parts.append(("record: " if en else "record 没记：") + refused[0])
        if recorded.get("replaced"):
            parts.append(("回答里说一句这次改了什么：" if self.language != "en" else "Say in the answer what this message changed: ")
                         + w["join"].join(f"{line['old']} → {line['new']}" for line in recorded["replaced"]) + ("。" if self.language != "en" else "."))
        if considered.get("more_how"):
            parts.append(considered["more_how"])
        return " ".join(parts) if self.language == "en" else "".join(parts)

    def _record(self, owner: str, record: dict, event_id: str | None = None) -> tuple[dict, dict]:
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
                                     ask="这条定了基本不变，用户本轮没说要改：先问用户，用户说了再记。" if self.language != "en" else
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
        pack = packs.build(self.store, owner, trip_folder=self.trip_folder(owner), language=self.language, **arguments)
        return packs.present(pack)

    def clock(self, owner: str, at: str | None = None) -> dict:
        """What "now" is for an answer (A2): the moment of the read in the trip's time zone (the plan's, else the
        repository's default, else UTC), with the sentence the pack's first line uses."""
        from tripwright.render.pack import now_line

        plan = self.versions.current(owner)["plan"]
        zone = plan["timezone"] if plan else (self.repository.timezone() if self.repository is not None else None) or "UTC"
        found = packs.clock(at or now(), zone)
        return dict(found, text=now_line(found, self.language))

    async def query(self, owner: str, kind: str, arguments: dict) -> dict:
        self.bound(owner)
        arguments = checked_arguments(kind, arguments)
        if kind == "check_answer":
            return self.check_answer(owner, arguments)
        if kind == "transit" and arguments.get("action") == "plan_day":
            from tripwright.solver import prepare

            day = prepare(arguments["day"])
            job = self.jobs.enqueue(owner, "plan_day", dict(day=day, timeout=min(300, max(0.01, float(arguments.get("timeout", 120))))),
                                    self.versions.current(owner)["number"], arguments["idempotency_key"])
            return dict(job_id=job["id"], status=job["status"], notice="Retrieve with result; cancel with result(cancel=true). Solver results require a separate plan proposal.")
        plan = self.versions.current(owner)["plan"]
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
                return await self.query_router.query(owner, kind, arguments, plan, zone=zone, at=now(), language=self.language)

        before = {record["id"] for record in self.store.all("evidence", owner)}
        try:
            result = await asyncio.wrap_future(self.jobs.submit(lookup()))
        except TimeoutError:
            # A11: a timed-out departure lookup says nothing was checked; “retain the plan” sent a host to its stored “末班”
            return {**dict(status="unverified", reason="The data-source query exceeded its time budget.",
                           consequence="This fact could not be verified in time.", alternative="Confirm locally or retain the previous safe plan."),
                    **(lookup_failed("timeout", self.language) if kind in ("latest_departure", "transit") else {})}
        self.record_evidence(owner, before, key=f"query:{identifier('Q')}")
        return result

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

    def check_answer(self, owner: str, arguments: dict) -> dict:
        """Check a host's draft of the first or second part before it is sent (A6, D-43): against the clock in the
        trip's zone and the evidence of this conversation: from SESSION_HOURS before the turn named by request_id (else
        before now) until now. Returns ok and one problem per failing line, each with how to rewrite it.

        This question's words (for meals_named): `question` when given; else the message of the turn named by
        request_id; else the latest message a user sent (not channel system) in the last RECENT_QUESTION_MINUTES. A host
        cut off from the tools never recorded its turn, and the latest message may be a setup message or one of an
        earlier conversation (D-48). With none, the meal check is not made and the summary says so."""
        from tripwright.render import check_answer as checks

        plan = self.versions.current(owner)["plan"]
        name = (plan or {}).get("timezone") or (self.repository.timezone() if self.repository is not None else None) or "UTC"
        at = aware(now())
        local = at.astimezone(ZoneInfo(name))
        start = at
        # messages only: the change records a turn writes share the events table and carry no receipt (D-47)
        events = received_events(self.store, owner)
        question = arguments.get("question") if str(arguments.get("question") or "").strip() else None
        points, speaker, first = None, None, None
        if arguments.get("request_id"):
            event = next((event for event in events if event.get("request_id") == arguments["request_id"]), None)
            if event is None:
                raise ValueError(f"Invalid query arguments: arguments.request_id: no turn with request_id {arguments['request_id']!r}")
            start = aware(event["receipt"]["received_at"])
            question = event["receipt"]["received_text"] if question is None else question
            speaker = event["received"].get("speaker")
            # A12: the points this question's turn kept (its latest context, a retried turn's included)
            contexts = self.store.all("contexts", owner)
            kept = [context for context in contexts if context.get("source_event") == event["id"] and "points" in context]
            latest = sorted(kept, key=lambda context: aware(context["created_at"]))[-1] if kept else {}
            points = latest.get("points")
            # S4h2: the first part this question's turn drafted (a “继续” turn's is the continued question's)
            first = latest.get("first_text") or next((context.get("first_text") for context in reversed(contexts)
                                                      if latest.get("continues") and context.get("request_id") == latest["continues"]
                                                      and context.get("first_text")), None)
        elif question is None:  # the latest user message of the last minutes (the later recorded of two at one moment)
            recent = [event for event in events if event["received"].get("channel") != "system"
                      and aware(event["receipt"]["received_at"]) >= at - timedelta(minutes=RECENT_QUESTION_MINUTES)]
            event = max(reversed(recent), key=lambda event: aware(event["receipt"]["received_at"]), default=None)
            question = event["receipt"]["received_text"] if event is not None else None
        since = start - timedelta(hours=SESSION_HOURS)
        found = checks.evidence_found(self.store.all("evidence", owner), ZoneInfo(name), since, at + timedelta(minutes=1))
        problems = checks.check_draft(arguments["text"], arguments["part"], local.hour * 60 + local.minute, found,
                                      question=question, places=(plan or {}).get("places", []), points=points,
                                      here=packs.plan_here(plan, local, packs.asker(plan, speaker), self.language), first=first)
        count = len(problems)
        summary = ("草稿可以发。" if not problems else f"要改 {count} 处再发：" + "；".join(
            f"第 {entry['line']} 行 {entry['why']}" if entry["line"] else entry["why"] for entry in problems))
        if question is None and arguments["part"] == "second":
            summary = summary.rstrip("。") + "。" + UNKNOWN_QUESTION
        if arguments["part"] == "first" and not (points or {}).get("given"):
            summary = summary.rstrip("。") + "。" + UNKNOWN_POINTS
        result = dict(kind="check_answer", ok=not problems, part=arguments["part"], problems=problems, summary=summary,
                      now=self.clock(owner, at.isoformat()),
                      evidence=dict(since=since.isoformat(), ids=sorted(found["ids"]), windows=[checks.hhmm(end) for end in sorted(set(found["windows"]))]))
        if not problems and arguments["part"] == "second":
            # What passed is what must be sent (A7): the Stop hook compares the text the host sends with this one.
            digest = checks.draft_digest(arguments["text"])
            remembered = remember_answer(self.answers_path(), arguments.get("request_id"), arguments["text"], digest, at.isoformat(), zone=name)
            result["passed"] = dict(sha256=digest, remembered=remembered,
                                    notice="Send exactly this text: a Stop hook blocks a second part that differs from the one that passed.")
        return result

    def answers_path(self):
        return answers_path(self.repository.root if self.repository is not None else None, None if self.repository is not None else self.store.path)

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
        if arguments.get("plan") is not None:
            checked_plan(arguments["plan"])
            for index, place in enumerate(arguments["plan"].get("places") or []):
                cited = ((place or {}).get("hours_source") or {}).get("evidence_id")
                if cited and not self._has("evidence", owner, cited):
                    raise ValueError(f"Invalid plan: plan.places.{index}.hours_source.evidence_id: no evidence {cited!r} in this trip; "
                                     "record the page with query(kind=\"web_evidence\") first")
        if arguments.get("usages") is not None:
            arguments["usages"] = checked_usages(arguments["usages"])
        trip = self.current_trip(owner)
        if self.repository is not None and arguments.get("plan") is not None and trip and arguments["plan"].get("id") != trip:
            raise ValueError(f"plan.id must be the current trip {trip!r}; open another trip with turn(new_trip=...)")
        self.take_in()
        draft = self.versions.propose(owner, **arguments)
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
                                how=f"state(view='pack', draft_id='{draft['id']}') returns the wide pack and the complete candidate for the verifier"))

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
        if view == "current":
            return current
        if view == "day":
            # The day view (block B) writes the replan line itself from the version's needs_replan and warnings.
            text = repo_files.day_text(self, owner, date or "")
            return dict(version=current["number"], needs_replan=current.get("needs_replan", False), warnings=current.get("warnings", []), text=text)
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
            return self.committer.status() if self.committer else dict(canonical=None, unsynced=0, note="single-database mode")
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
        listed = consider.build(self.store, owner, pack=None, plan=plan, scenario="plan", at=at, language=self.language, limit=None)
        return dict(consider=listed["entries"], sources=consider.sources(self.store, owner, plan, at=at, language=self.language))

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
                    layer=consider.words(self.language)["layer"][code] if code else None, said=packs.message(found, events),
                    subject=found.get("subject"), scope=packs.bare_scope(found.get("scope")), superseded_by=replaced_by,
                    current=current, relations=found.get("relations") or {})
        card.update({key: found[key] for key in RECORD_FIELDS[name] if found.get(key) is not None})
        conflicted = [item["id"] for item in items if record_id in item.get("relations", {}).get(consider.CONFLICTS_USER, [])]
        if conflicted:
            card["conflicted_by"] = conflicted
        return card
