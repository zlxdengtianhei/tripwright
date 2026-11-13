from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, Field, TypeAdapter, ValidationError, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema

from tripwright import consider
from tripwright.consider import CONFLICTS_USER, SETTING, USER_LAYER
from tripwright.evidence import uncounted
from tripwright.events import Events
from tripwright.locks import (EXTERNAL, MODELS, PARTIAL, amend, binding, decides, described, field_class, held, inner, lock_changes, model,
                             path_of, pointer, protects, reach, required, snapshot, user_part)
from tripwright.storage import Store, identifier, now
from tripwright.trip import Model, aware, field_type, input_problems, minutes, readable


def iso_time(value: str) -> str:
    """A moment with its offset; a wrong one is refused with what was sent (turn and propose report the field path)."""
    try:
        aware(value)
    except ValueError:
        raise ValueError(f"must be an ISO time with a timezone offset (e.g. 2030-06-10T09:00:00+02:00), got {value!r}") from None
    return value


IsoTime = Annotated[str, AfterValidator(iso_time)]


class Scope(Model):
    start: date | None = None
    end: date | None = None
    time_from: str | None = None
    time_to: str | None = None
    place: str = "*"
    channel: str = "*"

    @model_validator(mode="after")
    @readable
    def interval(self):
        if self.start and self.end and self.end < self.start:
            raise ValueError("Scope end precedes start")
        if self.time_from:
            minutes(self.time_from)
        if self.time_to:
            minutes(self.time_to)
        if self.time_from and self.time_to and minutes(self.time_to) <= minutes(self.time_from):
            raise ValueError("Scope time_to must follow time_from")
        return self


class Source(Model):
    event: str
    nature: str | None = None
    basis: Literal["user_explicit", "direction_set", "recommended_default", "agent_inference"] = "agent_inference"


class Item(Model):
    id: str = Field(default_factory=lambda: identifier("I"))
    kind: Literal["goal", "constraint", "preference", "rule", "authorization", "stop", "setting"] = Field(description=(
        "setting: this trip's sources and notes (M1), not a constraint on the plan: key source:<id> (value {on, which}), "
        "app:timetable (value a regions.yaml tool, scope.place the region), ask_languages (value language codes), "
        "note:<slug>, must_go (value none or named: the answer to 'is any place a must?'), stability:<kind> (value stable "
        "or changing). long_term keeps it for later trips."))
    key: str
    quote: str
    text: str
    value: Any = None
    subject: list[str] = Field(min_length=1)
    scope: Scope = Field(default_factory=Scope)
    strength: Literal["must", "forbid", "prefer", "optional"]
    condition: str | dict | None = None
    valid_from: IsoTime | None = None
    valid_until: IsoTime | None = None
    expires_on_event: str | None = None
    source: Source
    status: Literal["active", "pending", "superseded", "expired", "satisfied"] = "active"
    relations: dict[str, list[str]] = Field(default_factory=dict)
    reason: str | None = None
    interpretations: list[dict] = Field(default_factory=list)
    priority: int | None = None
    check: dict | None = None
    repetitions: int = 1
    same_object_as: list[str] = Field(default_factory=list, description=(
        "Keys or IDs of items (this message or earlier) that state the same object under another key; "
        "the service treats them as one key where people, dates and time windows overlap: a later message replaces, "
        "the same message needs clarification. Items in scopes that do not overlap both stay active."))
    exclusive_with: list[str] = Field(default_factory=list, description=(
        "Keys or IDs of items whose reading excludes this one: the user's words give two readings of one intention "
        "and only one can be meant (e.g. two destinations for the same evening). Holds only where people, dates and time "
        "windows overlap. Do not declare two wishes that only compete for time; those stay active and the planner offers options."))
    compatible_with: list[str] = Field(default_factory=list, description=(
        "Keys or IDs of items in the same message that hold together with this one. Clears the non-blocking "
        "possible-contradiction notice the service otherwise returns for two undeclared overlapping items of one message."))
    always: bool = Field(default=False, description=(
        "A principle the user wants applied everywhere (e.g. 'whose task is whose'); every package carries it whatever "
        "its people, time, place or channel."))
    long_term: bool = Field(default=False, description=(
        "The user states it as lasting beyond this trip (e.g. a standing preference or rule); it is also kept in the "
        "repository's profile and offered to the next trip, which asks the user whether to keep it."))
    origin: SkipJsonSchema[dict | None] = None

    @field_validator("check")
    @classmethod
    def predicate(cls, value):
        return check_shape(value)


CHECK_FIELDS = {"metric": str, "op": str, "value": (int, float), "currency": str}


def check_shape(value: dict | None) -> dict | None:
    """A deterministic predicate `{metric, op, value[, currency]}`; a wrong nested type is refused here with its field
    instead of failing later inside the checker."""
    if value is None:
        return value
    for key, expected in CHECK_FIELDS.items():
        if key in value and value[key] is not None and (not isinstance(value[key], expected) or isinstance(value[key], bool)):
            kind = "a number" if key == "value" else "a string"
            raise ValueError(f"check.{key} must be {kind}, got {type(value[key]).__name__}")
    return value


# Fact keys the checker compares with the plan, and the value each needs; other keys are open.
FACT_VALUES = {"equipment": TypeAdapter(list[str]), "companions": TypeAdapter(list[str]),
               "visited": TypeAdapter(str | list[str]), "baggage_holder": TypeAdapter(str)}


class Fact(Model):
    id: str = Field(default_factory=lambda: identifier("F"))
    key: str
    value: Any
    text: str
    subject: list[str] = Field(min_length=1)
    scope: Scope = Field(default_factory=Scope)
    true_from: IsoTime
    valid_until: IsoTime | None = None
    invalidated_by: str
    source: Source
    status: Literal["active", "superseded", "expired"] = "active"

    @model_validator(mode="after")
    def known_value(self):
        if self.key in FACT_VALUES:
            try:
                FACT_VALUES[self.key].validate_python(self.value)
            except ValidationError as error:
                raise ValueError(f"{self.key} fact: " + "; ".join(input_problems(error, "value"))) from None
        return self


class Decision(Model):
    id: str = Field(default_factory=lambda: identifier("D"))
    question: str
    options: list[dict | str] = Field(min_length=1)
    choice: str
    decided_by: Literal["user", "agent"]
    parts: dict[str, Literal["user", "agent"]] = Field(default_factory=dict)
    subject: list[str] = Field(min_length=1)
    scope: Scope = Field(default_factory=Scope)
    source: Source
    depends_on: list[str] = Field(default_factory=list)
    reason: str | None = None
    revisit_when: str | dict | None = None
    locked_paths: dict[str, Any] = Field(default_factory=dict)
    must: bool = Field(default=False, description=(
        "The user said this is a must (a place they must go to, a fixed node). Only then does the user's decision lock "
        "its paths: the agent cannot move or change them, and only the user's own later message can (D-53). Without it "
        "the decision is recorded and the agent may change what it decided, giving the reason in changes.revisions. "
        "Set it only when the user said so; ask whether anything is a must when planning, and when they did not say, it is not."))
    placement: SkipJsonSchema[dict[str, dict]] = Field(default_factory=dict)
    status: Literal["active", "review", "superseded"] = "active"


class PendingCheck(Model):
    id: str = Field(default_factory=lambda: identifier("P"))
    question: str
    due_at: IsoTime
    responsible: str
    subject: list[str] = Field(min_length=1)
    scope: Scope = Field(default_factory=Scope)
    source: Source
    status: Literal["open", "verified", "unverified", "revised"] = "open"
    result: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    consequence: str
    alternative: str


class Relation(Model):
    items: list[str] = Field(min_length=2, max_length=2, description="IDs of two current items from one message.")
    relation: Literal["exclusive_with", "same_object_as", "compatible_with"]
    source: Source


class Resolution(Model):
    id: str = Field(description="ID of an open pending check, e.g. P-261031-k3f9qa.")
    status: Literal["verified", "unverified", "revised"]
    result: str = Field(min_length=1, description="What was found; required to close the check.")
    evidence_ids: list[str] = Field(default_factory=list, description="Evidence IDs; verified needs current first-hand evidence.")


class Carry(Model):
    id: str = Field(description="ID of a long-term entry imported into this trip (state(view='memory') lists them).")
    status: Literal["confirmed", "not_used"] = Field(description="confirmed: the user keeps it for this trip; not_used: not on this trip.")
    source: Source


TARGET = ("'kind:id' of the plan object: person, place, fare, booking, block or anchor by ID, a day by its date "
          "('day:2031-03-01'), the trip by its ID ('trip:<id>').")


class Report(Model):
    """Path 2 for a protected field (D-36): what the user's own message says happened, e.g. "I bought the tickets"."""
    id: str = Field(default_factory=lambda: identifier("R"))
    target: str = Field(description=TARGET)
    field: str = Field(description="The field of that object, e.g. status.")
    old: Any = Field(description="The value the plan has now.")
    new: Any = Field(description="The value the user reports.")
    quote: str = Field(min_length=1, description="The user's words that say it, copied from the message.")
    source: Source = Field(description="The user's message: speaker is a person of the trip; nature user_verbatim or host_restatement.")


class Correction(Model):
    """Path 3: the agent's proposal to change a protected field. It becomes an open question and changes nothing until
    the user confirms it in a later message (confirm_corrections)."""
    id: str = Field(default_factory=lambda: identifier("C"))
    target: str = Field(description=TARGET)
    field: str
    old: Any = Field(description="The value the user's lock holds now.")
    new: Any = Field(description="The value proposed.")
    basis: str = Field(min_length=1, description="Why the agent thinks it should change.")
    ask: str = Field(min_length=1, description="What the user is to answer.")
    evidence_ids: list[str] = Field(default_factory=list, description=(
        "Evidence from a real source (query results, not synthetic), for an external fact only; the user still confirms."))
    source: Source = Field(description="The message the agent proposes it in; any nature.")


class Confirmation(Model):
    id: str = Field(description="ID of an open correction (C-…), as listed in the package's questions.")
    answer: Literal["confirmed", "rejected"]
    quote: str = Field(min_length=1, description="The user's words that answer it, copied from the message.")
    source: Source = Field(description="The user's later message: speaker is a person of the trip; nature user_verbatim or host_restatement.")


class Revision(Model):
    """The agent's reason for changing what the user decided earlier without marking it a must (D-53)."""
    decision: str = Field(description="ID of the user's earlier decision this plan changes (the package lists them), e.g. D-261101-k3f9qa.")
    reason: str = Field(min_length=1, description="Why the plan no longer follows it, in words the answer can show the user.")
    filled_by: Literal["service"] | None = Field(default=None, description="service: omitted by the host; records contact, not precedence.")


class Changes(Model):
    items: list[Item] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    pending_checks: list[PendingCheck] = Field(default_factory=list)
    resolve_pending: list[Resolution] = Field(default_factory=list, description="Close open pending checks: {id, status, result, evidence_ids}.")
    retract_interpretation: list[str] = Field(default_factory=list)
    relate: list[Relation] = Field(default_factory=list, description=(
        "Declare after the fact how two recorded items of one message relate, e.g. answering a possible-contradiction "
        "notice: exclusive_with or same_object_as makes overlapping items pending with one question; compatible_with "
        "keeps both active and clears the notice."))
    carry: list[Carry] = Field(default_factory=list, description=(
        "The user's answer about long-term entries imported into a new trip: keep them (confirmed) or not on this trip "
        "(not_used). Until answered they apply and are marked pending."))
    reports: list[Report] = Field(default_factory=list, description=(
        "A field changed because the user said so (\"I bought the tickets\" → booking status bought): {target, field, old, "
        "new, quote, source}. A must lock holding it takes the new value from this message on; a bought or booked booking "
        "or a hard node changes only in the fields a report or a confirmed correction names (D-53)."))
    corrections: list[Correction] = Field(default_factory=list, description=(
        "The agent proposes to change a field of a must lock, a bought or booked booking or a hard node (coordinates, hours, "
        "a price, a deadline, a channel): {target, field, old, new, basis, ask, evidence_ids, source}. It becomes an open "
        "question for the user and changes nothing until confirmed; a plan with the new value is refused meanwhile. What "
        "the user decided without a must the agent changes in the plan with changes.revisions instead."))
    confirm_corrections: list[Confirmation] = Field(default_factory=list, description=(
        "The user's answer to an open correction, from a later message: {id, answer: confirmed|rejected, quote, source}. "
        "Confirmed, the lock takes the new value; send the plan with it in the same proposal."))
    revisions: list[Revision] = Field(default_factory=list, description=(
        "The plan changes what the user decided earlier but did not mark a must: one {decision, reason} per such decision. "
        "A plan that changes it without one is refused; the change log shows the reason (D-53)."))
    why: str | None = Field(default=None, description=(
        "Why the agent changes the plan this time, in a sentence; the change log shows it for changes that no user message "
        "or revision explains."))


def overlaps(first: Scope, second: Scope) -> bool:
    if first.start and second.end and first.start > second.end:
        return False
    if second.start and first.end and second.start > first.end:
        return False
    if first.time_from and second.time_to and first.time_from >= second.time_to:
        return False
    if second.time_from and first.time_to and second.time_from >= first.time_to:
        return False
    if first.channel != "*" and second.channel != "*" and first.channel != second.channel:
        return False
    return first.place == "*" or second.place == "*" or first.place == second.place or first.place.startswith(second.place + "/") or second.place.startswith(first.place + "/")


def contains(outer: Scope, inner: Scope) -> bool:
    if outer.start and (not inner.start or outer.start > inner.start):
        return False
    if outer.end and (not inner.end or outer.end < inner.end):
        return False
    if outer.time_from and (not inner.time_from or outer.time_from > inner.time_from):
        return False
    if outer.time_to and (not inner.time_to or outer.time_to < inner.time_to):
        return False
    return (outer.channel == "*" or outer.channel == inner.channel) and (
        outer.place == "*" or outer.place == inner.place or inner.place.startswith(outer.place + "/")
    )


def declared(first, second) -> bool:
    """Whether the host declared two items to state the same object or to exclude each other; the service does not guess."""
    def field(record, name, default=None):
        return record.get(name, default) if isinstance(record, dict) else getattr(record, name, default)

    def references(record):
        return set(field(record, "same_object_as") or []) | set(field(record, "exclusive_with") or [])

    names = lambda record: {field(record, "id"), field(record, "key")}
    return bool(names(second) & references(first) or names(first) & references(second))


def compatible(first, second) -> bool:
    def field(record, name):
        return (record.get(name) if isinstance(record, dict) else getattr(record, name, None)) or []

    names = lambda record: {record["id"], record["key"]} if isinstance(record, dict) else {record.id, record.key}
    return bool(names(second) & set(field(first, "compatible_with")) or names(first) & set(field(second, "compatible_with")))


POSSIBLE = "possible_contradiction_with"
# the kinds that state a limit: a host labels one prohibition either way (2026-11-12: “constraint”, said again as “rule”)
LIMITS = {"constraint", "rule"}


WIDTH_HINT = ("; it differs from the message only in punctuation or character width (for example ， and ,): "
              "copy the quote character for character from the message text")


def quote_hint(quote: str, text: str) -> str:
    """The extra words for an absent quote that matches once full-width and half-width forms are folded (NFKC): a
    host that transcribed a screenshot into the message often re-types the punctuation in the quote."""
    fold = lambda value: unicodedata.normalize("NFKC", value)
    return WIDTH_HINT if fold(quote) in fold(text) else ""


def user_layer(basis: str | None) -> bool:
    """METHOD 2.9 and 4 (S5): the user's layer is what the user said, set or confirmed (consider.USER_LAYER); the agent's
    is its inferences and recommended defaults. On the same object (key or declaration, overlapping people and scope) an
    agent's entry never replaces, narrows or pends a user's: both stay active, the user's governs, and the agent's names
    it under `conflicts_with_user`."""
    return basis in USER_LAYER


def offered(item: dict) -> bool:
    """An entry of the agent's layer that is no hard condition (S5): the plan may use it and say so, but no usage or
    wide-pack disposition is required for it, so a proposal that also sends the agent's inferences back is not judged
    incomplete for them. Musts and forbids are required whoever recorded them."""
    return not user_layer((item.get("source") or {}).get("basis")) and item.get("strength") in ("prefer", "optional")


def contradiction_notice(first: dict, second: dict) -> dict:
    """A non-blocking notice for two undeclared, overlapping items of one message: the service cannot tell whether
    they are two readings of one intention, so both stay active (the default) until the host or user says otherwise."""
    return dict(kind="possible_contradiction", item_ids=[first["id"], second["id"]], quotes=[first["quote"], second["quote"]],
                readings=[f"Both hold together: {first['text']} / {second['text']}",
                          f"Only one is meant: {first['text']} or {second['text']}"],
                default="Keep both active (the first reading) until the user says otherwise.", blocking=False,
                resolve="Ask the user if unsure. Then propose changes.relate with relation exclusive_with (the items become pending "
                        "with one question) or compatible_with (the notice clears).",
                subject=sorted(set(first["subject"]) | set(second["subject"])), scope=first["scope"])


def audit_event(record: dict) -> str:
    """The message in this trip that a change to the record is filed under. An entry imported from an earlier trip keeps
    its original source (that trip's message) and is filed under the message that opened this trip."""
    return (record.get("origin") or {}).get("import_event") or record["source"]["event"]


def people_overlap(first: list[str], second: list[str]) -> bool:
    return "*" in first or "*" in second or bool(set(first) & set(second))


def people_contain(outer: list[str], inner: list[str]) -> bool:
    return "*" in outer or ("*" not in inner and set(inner) <= set(outer))


CONVERSATION_RULES = [
    "Answer directly in the conversation.",
    "Answer before updating publication views.",
    "Separate first and second responses with visible titles; begin the second with differences.",
    "Start from the current place and local time; put hard nodes and latest departure first.",
    "Put each local place name on its own copyable line, with a Chinese explanation and app search or purchase action.",
    "Use prose and lists in conversation; avoid tables.",
    "State which conditions were used and which were traded off, with reasons.",
]


USER_NATURES = ("user_verbatim", "host_restatement")
# Kinds a report or correction can name: objects with an identity in the trip document. Tracks have none; a plan's
# fields are display or structure.
AMENDABLE = ("person", "place", "fare", "booking", "block", "anchor", "day", "trip")


def stable_value(name: str, value):
    """What a stable plan field is compared by: the areas as a set; the people by ID and name (their other fields are
    the plan's to change)."""
    if name == "people":
        return sorted((person.get("id"), person.get("name")) for person in value or [])
    if isinstance(value, list):
        return sorted(map(str, value))
    return value


def shown(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def amended_locks(amended: list[dict]) -> list[dict]:
    """The locks a report or confirmation changed, as they were: each superseded decision with the path that held the field."""
    return [dict(decision=entry["replaces"], path=path) for entry in amended for path in entry["paths"]]


TAKES_EFFECT = dict(decisions="on commit", reports="on commit",
                    corrections="only after the user confirms it in a later message (confirm_corrections)")


def lock_receipt(operations: dict[str, list[dict]], *, committed: bool) -> dict:
    """The tool answer's lock_changes (D-36, D-49), made from the operations the ledger admitted and applied, so propose
    and commit say the same thing: each user decision with its message and the locks and decisions it replaces; each
    report and confirmation with the field, old and new value, message and the locks holding it; each correction with
    the question it opens; a decision that only touches an old lock carries `refused` (which lock, why, where to
    decide again) and the proposal is refused (D-51). propose adds when each takes effect and leaves out the amended
    decisions (their IDs exist only after the commit); commit adds them, also gathered under `amended`."""
    receipt = {}
    for part, entries in operations.items():
        lines = []
        for entry in entries:
            line = dict(entry)
            if not committed:
                line.pop("amended", None)
                line.pop("status", None)
                line["takes_effect"] = ("nothing: the proposal is refused" if entry.get("refused") else
                                        TAKES_EFFECT.get(part) or ("on commit" if entry["answer"] == "confirmed" else "nothing changes"))
            lines.append(line)
        if lines:
            receipt[part] = lines
    amended = [entry for part in ("reports", "confirmations") for operation in operations.get(part, []) for entry in operation["amended"]]
    if committed and amended:
        receipt["amended"] = amended
    return receipt


def refusals(operations: dict[str, list[dict]]) -> list[str]:
    """Why a proposal is refused as a whole: each user decision that only touches an old lock (D-51), and each change of
    a stable plan field the user did not say this turn (M1), in one line."""
    return [f"user decision {line['id']} {entry['reason']}; {entry['fix']}"
            for line in operations.get("decisions", []) for entry in line.get("refused", [])] + [
            f"{line['reason']}; {line['fix']}" for line in operations.get("stable", [])]


@dataclass(frozen=True)
class Proposal:
    """What the ledger needs to know about the proposal its changes come with (D-49, D-50): the message it answers
    (`answered`, its turn's), the server time it was made (`at`, the draft's creation time, kept when the draft is
    committed or replayed) and its candidate plan (`plan`, whose people count as the trip's only while no plan is
    committed)."""
    answered: str | None = None
    at: str | None = None
    plan: dict | None = None


class Ledger:
    def __init__(self, store: Store):
        self.store = store
        self.events = Events(store)

    def revision(self, owner: str) -> int:
        return len(self.store.all("journal", owner))

    def digest(self, owner: str) -> str:
        """Content key of the ledger (CONTRACTS 1.1): replay can keep the entry count while changing the entries."""
        ids = "\n".join(entry["id"] for entry in self.store.all("journal", owner))
        return "sha256:" + hashlib.sha256(ids.encode()).hexdigest()[:16]

    def carried(self, owner: str) -> dict[str, dict]:
        """Long-term entries imported into the current trip, by ID, with their status here."""
        return {entry["id"]: entry for entry in self.store.all("carry", owner)}

    def profile(self, owner: str) -> dict:
        profiles = self.store.all("profiles", owner)
        if profiles:
            return profiles[0]
        return dict(id="conversation-defaults", subject=["*"], scope={"channel": "conversation"},
                    rules=CONVERSATION_RULES, concession_order=[],
                    rule_items=[dict(id=f"SYS-R{index + 1}", kind="rule", subject=["*"], scope={"channel": "conversation"},
                                     text=text, strength="must", source="system_default")
                                for index, text in enumerate(CONVERSATION_RULES)])

    def ensure_profile(self, owner: str):
        with self.store.transaction():
            if not self.store.all("profiles", owner):
                self.store.put("profiles", owner, self.profile(owner), insert=True)

    def _message(self, owner: str, event_id: str) -> dict:
        """The stored message a source names; an unknown ID says which ID to send instead."""
        try:
            return self.store.get("events", owner, event_id)
        except ValueError:
            raise ValueError(f"Unknown message {event_id!r}: source.event is the event_id that turn returned for the message, "
                             'or "this" for the message of the ctx_id it is sent with') from None

    def _source(self, owner: str, source: Source, quote: str | None = None) -> Source:
        event = self._message(owner, source.event)
        if "received" not in event:
            raise ValueError("Source must reference a received message")
        received = event["received"]
        if source.nature is not None and source.nature != received["nature"]:
            raise ValueError("Source nature cannot be upgraded or rewritten")
        if quote is not None and quote not in received["text"]:
            raise ValueError("Quote is absent from the received message" + quote_hint(quote, received["text"]))
        if source.basis == "user_explicit" and received["nature"] == "agent_inference":
            raise ValueError("Agent inference cannot become user explicit")
        return source.model_copy(update={"nature": received["nature"]})

    # --- the three ways a protected field of a user lock changes (D-36) --------------------------------------------
    # 1. A new user decision (decisions above) locks the new value. 2. A report of what the user's own message says
    # happened. 3. A correction the agent proposes, which waits as a question until the user confirms it in a later
    # message. 2 and 3 record a new decision that copies each lock holding the field with the new value and supersedes
    # the old one, so records only ever change status. No other change of a protected field passes the lock. 1, 2 and
    # the confirmation of 3 pass one admission (`admit`, D-49) before anything is locked, replaced or amended, and the
    # message that authorizes a change must not be older than the one that last changed the lock (`in_order`, D-50).
    # Since D-53 a lock is a decision the user marked a must (`locks.binding`); 2 and 3 also change a fact that no lock
    # holds, a bought or booked booking or a hard node (`_fact`), which the plan check keeps otherwise.

    # --- stable entries (M1): what is set once changes only on the user's words this turn ----------------------------
    # consider.yaml classes each kind of memory as stable (目的地、人员、这趟的数据源、长期偏好…) or changing (想去的地方、
    # 每天的安排, D-53: changed with a record). A stable ledger entry is replaced, and a stable plan field (/areas,
    # /people) changes, only through the admission a must passes (`admit`): the message this turn answers, the user's
    # own words, a person of the trip. Changing ones are recorded as before and the turn says what they replaced.

    def stable_refusal(self, owner: str, item: Item, proposal: Proposal) -> str | None:
        """Why the item may not replace or contest a stable active entry, or None. It meets one where `apply` would
        supersede it or ask about it: the same key (or a declared same object), overlapping scope and people, a
        different value or text."""
        existing = [old for old in self.store.all("items", owner) if old["status"] == "active"]
        overridden = consider.overrides(existing)
        for old in existing:
            if not (old["key"] == item.key or declared(item, old)) or old["source"]["event"] == item.source.event:
                continue
            if not (overlaps(Scope.model_validate(old["scope"]), item.scope) and people_overlap(old["subject"], item.subject)):
                continue
            if old.get("value") == item.value and old["text"] == item.text:
                continue
            kind = consider.kind_of(old)
            if consider.stability(kind, overridden) != "stable":
                continue
            what = f"item {item.id} ({item.key})"
            fix = (f"fix: {old['id']} ({old['text']}) is {kind}, set once and changed only on the user's own words: ask the "
                   "user, then record their answer from that message with basis user_explicit")
            if item.source.basis != "user_explicit":
                return f"not admitted (stable): {what} would replace {old['id']} with basis {item.source.basis}; {fix}"
            try:
                self.admit(owner, item.source, proposal, what=what, quote=item.quote or None)
            except ValueError as error:
                return f"{error} ({old['id']} is {kind}, stable); {fix}"
        return None

    def _stable_plan(self, owner: str, changes: Changes, proposal: Proposal) -> list[dict]:
        """A candidate that changes a stable plan field of the committed plan (consider.yaml plan_paths: the
        destination's /areas, the /people) needs a user decision in the same proposal whose paths cover it, admitted
        from this turn's message. Returns one refusal line per field that has none."""
        before = self._plan(owner)
        if not before or proposal.plan is None or self._must_changed(owner, proposal.plan):
            return []
        overridden = consider.overrides([item for item in self.store.all("items", owner) if item["status"] == "active"])
        lines = []
        for path, kind in consider.stable_plan_paths(overridden).items():
            name = path.strip("/")
            if stable_value(name, before.get(name)) == stable_value(name, proposal.plan.get(name)):
                continue
            covering = [decision for decision in changes.decisions
                        if any((key == path or key.startswith(path + "/")) and (decision.decided_by == "user" or decision.parts.get(key) == "user")
                               for key in decision.locked_paths)]
            problem = None if covering else "no user decision of this proposal covers it"
            for decision in covering:
                try:
                    self.admit(owner, decision.source, proposal, what=f"user decision {decision.id}")
                    problem = None
                    break
                except ValueError as error:
                    problem = str(error)
            if problem:
                lines.append(dict(path=path, kind=kind, refused=True, reason=f"not admitted (stable): the plan changes {path} ({kind}, set once): {problem}",
                                  fix=f"fix: keep {path} as committed, or ask the user and record their decision from that message "
                                      f"with locked_paths {{'{path}': <new value>}}"))
        return lines

    def _must_changed(self, owner: str, plan: dict) -> bool:
        """Whether the candidate changes what a must holds: the lock then refuses it with its own, more precise reason,
        or a report or confirmed correction of this proposal (the user's words) passes it."""
        for decision in self.store.all("decisions", owner):
            if decision["status"] == "superseded":
                continue
            for path, value in decision["locked_paths"].items():
                if binding(decision, path) and lock_changes(plan, path, value, (decision.get("placement") or {}).get(path)):
                    return True
        return False

    def _plan(self, owner: str) -> dict:
        """The committed plan; the ledger reads only its people and ID."""
        versions = self.store.all("versions", owner)
        return (max(versions, key=lambda version: version["number"])["plan"] if versions else None) or {}

    def _people(self, owner: str, proposal: Proposal) -> tuple[set[str], str | None]:
        """The trip's people a speaker must be one of: the committed plan's; before any plan is committed, the people of
        the first plan this proposal commits (D-50: no earlier lock exists to overwrite). The second value says so."""
        plan = self._plan(owner)
        if not plan and proposal.plan is not None:
            return {person["id"] for person in proposal.plan.get("people", [])}, "first plan"
        return {person["id"] for person in plan.get("people", [])}, None

    def admit(self, owner: str, source: Source, proposal: Proposal, *, what: str, quote: str | None = None) -> Source:
        """The one gate a user lock is set, replaced or changed through (D-49): a user decision (or a user part of an
        agent's decision), a report and a confirmation pass it before the candidate's values become a lock or an old
        decision is superseded. In order, the message is received; it is the one this proposal answers (its turn's
        message), not an earlier one; it is the user's own words (user_verbatim or host_restatement, not an agent's
        inference or a screenshot extraction); its speaker is a person of the trip (`_people`); and it holds the quoted
        words, when there are any. The labels an agent fills in (basis, nature) authorize nothing: the received message
        decides. Whether the message may still change a given lock is `in_order`'s. Returns the source with the
        message's nature and basis user_explicit; a refusal names the check that failed."""
        def refuse(check: str, detail: str):
            raise ValueError(f"{what} not admitted ({check}): {detail}")

        answered = proposal.answered
        event = self._message(owner, source.event)
        if "received" not in event:
            refuse("received message", f"Source must reference a received message, got {source.event}")
        if answered is None:
            refuse("this turn", "a user lock changes only in a proposal that answers the user's message (propose with its turn's ctx_id)")
        if source.event != answered:
            refuse("this turn", f"source.event must be {answered}, the message this proposal answers: the user's own words in this "
                                f"turn, not an earlier message (got {source.event})")
        received = event["received"]
        if source.nature is not None and source.nature != received["nature"]:
            refuse("nature", f"Source nature cannot be upgraded or rewritten: message {source.event} is {received['nature']}, "
                             f"got {source.nature}")
        if received["nature"] not in USER_NATURES:
            refuse("nature", f"must come from the user's own message (user_verbatim or host_restatement), not {received['nature']}; "
                             "an agent proposes a change as changes.corrections")
        people, _ = self._people(owner, proposal)
        if received.get("speaker") not in people:
            refuse("speaker", f"the speaker of message {source.event} must be a person of this trip "
                              f"({', '.join(sorted(people)) or 'no plan committed or proposed yet'}), got {received.get('speaker')!r}; "
                              "pass speaker on the turn that received it")
        if quote is not None and quote not in received["text"]:
            refuse("quote", f"Quote is absent from message {source.event}: {quote!r}" + quote_hint(quote, received["text"]))
        return source.model_copy(update={"nature": received["nature"], "basis": "user_explicit"})

    def _said(self, owner: str, source: Source, proposal: Proposal) -> dict:
        """Where an admitted change comes from, for its lock_changes line: the message, its nature and speaker, and
        `people_from` when the speaker was found among the people of the first plan."""
        received = self.store.get("events", owner, source.event)["received"]
        _, first = self._people(owner, proposal)
        return dict(event=source.event, nature=received["nature"], speaker=received.get("speaker"),
                    **(dict(people_from="the first plan (no plan was committed yet)") if first else {}))

    # --- authority: which message last changed a lock (D-50) -------------------------------------------------------

    def _authority(self, owner: str, event_id: str) -> dict:
        """A user message as the authority for a lock: its event and the server's receipt time."""
        return dict(event=event_id, received_at=self.store.get("events", owner, event_id)["receipt"]["received_at"])

    def authority(self, owner: str, decision: dict) -> dict | None:
        """The user's message that last set or changed this lock. Recorded on the lock since B3h; for an older lock, the
        message of its amendment, else its own source."""
        if decision.get("authority"):
            return decision["authority"]
        event_id = (decision.get("amends") or {}).get("event") or decision["source"]["event"]
        try:
            return self._authority(owner, event_id)
        except (ValueError, KeyError):
            return None  # not a received message: any admitted message may change it

    def _moment(self, owner: str, authority: dict) -> tuple[datetime, int]:
        """Where a message stands in the order the service received it: receipt time, then its place in the log (two
        messages can share a time on a pinned clock)."""
        order = [event["id"] for event in self.store.all("events", owner)]
        place = order.index(authority["event"]) if authority["event"] in order else -1
        return aware(authority["received_at"]), place

    def in_order(self, owner: str, lock: dict, authority: dict, what: str):
        """A change of a user lock needs a message at least as recent as the one that last changed the lock (D-50). A
        retried turn returns the old receipt with a new context bound to the current version; that context does not
        make the old message newer, so it cannot undo what a later message decided. The same message may set the lock
        again: several drafts of one message, of which one commits; whether it already changed the lock in an earlier
        commit is the commit's check (`Versions._guard`, D-52), which calls this again for each lock it changes."""
        current = self.authority(owner, lock)
        if current is not None and self._moment(owner, authority) < self._moment(owner, current):
            raise ValueError(f"{what} not admitted (order): the authority is out of date: lock {lock['id']} was changed by "
                             f"message {current['event']} (received {current['received_at']}) after message "
                             f"{authority['event']} (received {authority['received_at']}); only a later message of the user changes it")

    def _field(self, owner: str, change, what: str) -> tuple[str, Any, Any]:
        """Check that a report or correction names one protected field of a plan object; returns its class and the old
        and new values in their stored (JSON) form."""
        kind, separator, object_id = change.target.partition(":")
        if not separator or not object_id or kind not in AMENDABLE:
            raise ValueError(f"{what}.target must be 'kind:id' with kind one of {', '.join(AMENDABLE)}, got {change.target!r}")
        record = MODELS[kind]
        if change.field not in record.model_fields:
            raise ValueError(f"{what}.field: {record.__name__} has no field {change.field!r}")
        annotation = field_type(record.model_fields[change.field])
        if change.field == "id" or (kind == "day" and change.field == "date"):
            raise ValueError(f"{what}.field: {record.__name__}.{change.field} names the object; the user decides again instead")
        if model(annotation) is not None or model(inner(annotation)) is not None:
            raise ValueError(f"{what}.field: {record.__name__}.{change.field} holds other plan objects; the user decides again instead")
        if described(record.__name__, change.field):
            raise ValueError(f"{what}.field: {record.__name__}.{change.field} is a description field; change it in the plan directly")
        if kind == "trip" and object_id != self._plan(owner).get("id"):
            raise ValueError(f"{what}.target: the current trip is {self._plan(owner).get('id')!r}, got {object_id!r}")
        values = []
        for name in ("old", "new"):
            try:
                typed = TypeAdapter(annotation)
                values.append(typed.dump_python(typed.validate_python(getattr(change, name)), mode="json"))
            except ValidationError as error:
                raise ValueError("; ".join(input_problems(error, f"{what}.{name}"))) from None
        if values[0] == values[1]:
            raise ValueError(f"{what}: new equals old")
        return field_class(record.__name__, change.field), values[0], values[1]

    def holders(self, owner: str, target: str, field: str, new, old=None, *, what: str = "change") -> list[dict]:
        """Every user lock (a must, D-53) that holds `field` of `target`, with the new locked value and snapshot. Refuses
        when a lock holds another value than `old` (the change is stale) or holds only a part of the field."""
        found = []
        for decision in self.store.all("decisions", owner):
            if decision["status"] == "superseded":
                continue
            for path, expected in decision["locked_paths"].items():
                if not binding(decision, path):
                    continue
                held, value, recorded = amend(path, expected, (decision.get("placement") or {}).get(path), target, field, new)
                if not held:
                    continue
                if any(entry is PARTIAL for entry in held):
                    raise ValueError(f"{what}: lock {decision['id']}:{path} holds only a part of {target}/{field}; the user decides again instead")
                if any(entry != old for entry in held):
                    raise ValueError(f"{what}: lock {decision['id']}:{path} holds {target}/{field} = "
                                     f"{shown(next(entry for entry in held if entry != old))}, not {shown(old)}; it is stale")
                found.append(dict(decision=decision, path=path, value=value, placement=recorded))
        return found

    def _amend(self, owner: str, target: str, field: str, old, new, *, via: str, event: str, change: str, what: str) -> list[dict]:
        """Record the change in every user lock that holds the field: a new decision with the new value, authorized by
        `event`, supersedes each. A lock changed by a later message than `event` refuses it (`in_order`)."""
        authority = self._authority(owner, event)
        replaced = {}
        for holder in self.holders(owner, target, field, new, old, what=change):
            self.in_order(owner, holder["decision"], authority, what)
            decision = replaced.setdefault(holder["decision"]["id"], dict(old=holder["decision"], paths={}, placement={}))
            decision["paths"][holder["path"]] = holder["value"]
            if holder["placement"] is not None:
                decision["placement"][holder["path"]] = holder["placement"]
        amended = []
        for old_id, entry in replaced.items():
            previous = entry["old"]
            derived = dict(deepcopy(previous), id=identifier("D"), status="active",
                           locked_paths=dict(previous["locked_paths"], **entry["paths"]),
                           placement=dict(previous.get("placement") or {}, **entry["placement"]),
                           amends=dict(replaces=old_id, change=change, via=via, target=target, field=field, old=old, new=new, event=event),
                           authority=authority)
            derived.pop("superseded_by", None)
            previous.update(status="superseded", superseded_by=derived["id"])
            self.store.put("decisions", owner, previous)
            self.store.put("decisions", owner, derived, insert=True)
            self._audit(owner, event, "amend_lock", derived["id"], dict(replaces=old_id, via=via, change=change, target=target,
                                                                         field=field, old=old, new=new))
            amended.append(dict(decision=derived["id"], paths=sorted(entry["paths"]), **derived["amends"]))
        return amended

    def _replace(self, owner: str, decision: Decision, paths: list[str], authority: dict, what: str,
                 proposal: Proposal) -> tuple[list[dict], list[dict]]:
        """Path 1: what the user decides replaces the active user locks of the same people and scope that it decides
        whole, judged on the comparator's objects and references (`locks.reach`, D-51), for a user decision and for the
        user part of an agent's decision alike. An old decision that holds other paths too is superseded and they stay
        locked in a remainder decision with its old authority (D-50). A lock the decision only touches (a field inside
        it, or a field of an object it reaches) is not replaced: returned as refused, with the path to decide again, and
        the proposal is refused. A lock changed by a later message than `authority` refuses it (`in_order`).
        What the replaced lock protected and the new one does not take over (the timezone a block's times are read in,
        when its day is decided again) stays locked in the remainder too, where a lock path can name it (D-52); the
        commit's conservation check refuses what cannot be kept. Returns (replaced, refused)."""
        replaced, refused = [], []
        scope = decision.scope.model_dump(mode="json")
        before, candidate = self._plan(owner) or None, proposal.plan
        claims = set().union(*(decides(path, decision.locked_paths[path], before) for path in paths))
        taken = claims | set().union(*(held(path, decision.locked_paths[path], decision.placement.get(path)) for path in paths))
        for old in self.store.all("decisions", owner):
            if not (old["status"] == "active" and set(old["subject"]) == set(decision.subject) and old["scope"] == scope
                    and old["source"]["event"] != decision.source.event):
                continue
            # Only a must locks (D-53): only its paths refuse a partial touch, keep what the new decision does not take
            # over, and need a message as recent as theirs. The user's other paths stay a record until decided whole.
            users = [path for path in old["locked_paths"] if binding(old, path)]
            met = {path: reach(claims, path, value, (old.get("placement") or {}).get(path)) for path, value in old["locked_paths"].items()}
            partial = sorted(path for path in users if met[path] == "touches")
            if partial:
                refused.append(dict(decision=old["id"], locks=partial, decides=paths,
                                    reason=f"decides only a part of what lock {old['id']} holds at {', '.join(partial)}; replacing "
                                           "the lock would release the rest of that choice",
                                    fix=f"record the decision on {', '.join(partial)}: the user decides that whole part again"))
                continue
            # An agent's path enforces nothing; it goes with the decision that meets it at all.
            covered = sorted(path for path, how in met.items() if how == "covers" or (how and not user_part(old, path)))
            if not covered:
                continue
            if set(users) & set(covered):
                self.in_order(owner, old, authority, what)
            old.update(status="superseded", superseded_by=decision.id)
            self.store.put("decisions", owner, old)
            self._audit(owner, decision.source.event, "supersede_decision", old["id"], {"replacement": decision.id, "paths": covered})
            line = dict(decision=old["id"], locks=covered)
            kept = [path for path in old["locked_paths"] if user_part(old, path) and path not in covered]
            locked = {path: value for path, value in old["locked_paths"].items() if path not in covered}
            placement = {path: value for path, value in (old.get("placement") or {}).items() if path not in covered}
            parts = {path: who for path, who in (old.get("parts") or {}).items() if path not in covered}
            if candidate is not None and decision.must:  # a decision without a must releases the lock's protection (D-53)
                self._keep_unclaimed(old, [path for path in covered if path in users], taken, candidate, locked, placement, parts, kept)
            if kept:
                remainder = dict(deepcopy(old), id=identifier("D"), status="active", locked_paths=locked, placement=placement, parts=parts,
                                 authority=self.authority(owner, old), remainder_of=dict(decision=old["id"], replaced_by=decision.id))
                remainder.pop("superseded_by", None)
                self.store.put("decisions", owner, remainder, insert=True)
                self._audit(owner, decision.source.event, "keep_lock", remainder["id"], {"remainder_of": old["id"], "paths": sorted(kept)})
                line["kept"] = dict(decision=remainder["id"], locks=sorted(kept))
            replaced.append(line)
        return replaced, refused

    def _meets_must(self, owner: str, decision: Decision, paths: list[str]) -> bool:
        """Whether a user decision reaches any part of an active must (D-53), whoever's it is: then it must pass the
        admission like a must does, since it may replace or release one."""
        before = self._plan(owner) or None
        claims = set().union(*(decides(path, decision.locked_paths[path], before) for path in paths))
        return any(reach(claims, path, value, (old.get("placement") or {}).get(path)) is not None
                   for old in self.store.all("decisions", owner) if old["status"] == "active"
                   for path, value in old["locked_paths"].items() if binding(old, path))

    def _fact(self, owner: str, target: str) -> dict | None:
        """The committed object behind a target that is a fact rather than a choice (D-53): a bought or booked booking, a
        fare of one, a hard node (anchor). Changing it needs the user's report or a confirmed correction, must or not."""
        kind, _, object_id = target.partition(":")
        plan = self._plan(owner)
        for booking in plan.get("bookings", []):
            if booking["status"] in ("bought", "booked"):
                if kind == "booking" and booking["id"] == object_id:
                    return booking
                if kind == "fare" and any(fare["id"] == object_id for fare in booking.get("fares", [])):
                    return booking
        if kind == "anchor":
            return next((anchor for day in plan.get("days", []) for anchor in day.get("anchors", []) if anchor["id"] == object_id), None)
        return None

    @staticmethod
    def _keep_unclaimed(old: dict, covered: list[str], taken: set[str], candidate: dict, locked: dict, placement: dict, parts: dict,
                        kept: list[str]):
        """Add to a remainder what the replaced paths protected, still in the candidate, that the new decision does not
        take over: the old value, by the path that names it (`/timezone`, `/places/<id>`)."""
        for path in covered:
            recorded = (old.get("placement") or {}).get(path)
            before = held(path, old["locked_paths"][path], recorded)
            for name in sorted(required(candidate, path)):
                where_to = path_of(candidate, name)
                if not protects(before, name) or protects(taken, name) or where_to is None or where_to in locked:
                    continue
                references = (recorded or {}).get("references") or {}
                locked[where_to] = (recorded or {}).get("timezone") if name == "trip/timezone" else references.get(name, pointer(candidate, where_to))
                try:
                    placement[where_to] = snapshot(candidate, where_to)
                except ValueError:
                    pass
                if old["decided_by"] != "user":
                    parts[where_to] = "user"
                kept.append(where_to)

    def _report(self, owner: str, report: Report, proposal: Proposal) -> dict:
        """Path 2; returns the admitted operation for lock_changes."""
        what = f"report {report.id}"
        source = self.admit(owner, report.source, proposal, what=what, quote=report.quote)
        _, old, new = self._field(owner, report, what)
        amended = self._amend(owner, report.target, report.field, old, new, via="user_report", event=source.event, change=report.id, what=what)
        self._audit(owner, source.event, "user_report", report.id, dict(target=report.target, field=report.field, old=old, new=new,
                                                                        quote=report.quote, decisions=[entry["decision"] for entry in amended]))
        return dict(id=report.id, target=report.target, field=report.field, old=old, new=new, **self._said(owner, source, proposal),
                    quote=report.quote, locks=amended_locks(amended), amended=amended)

    def _correction(self, owner: str, correction: Correction, proposal: Proposal) -> dict:
        """Path 3's question. It is asked by this proposal, whatever message its source cites: `asked` holds the message
        the proposal answers and the server time it was made (D-50), and only a user message received after that time
        can answer it."""
        source = self._source(owner, correction.source)
        what = f"correction {correction.id}"
        kind, old, new = self._field(owner, correction, what)
        if correction.evidence_ids and kind != EXTERNAL:
            raise ValueError(f"{what}.evidence_ids: {correction.target}/{correction.field} is the user's choice, not an external "
                             "fact; only the user's answer can change it")
        for evidence_id in correction.evidence_ids:
            evidence = self.store.get("evidence", owner, evidence_id)
            if evidence.get("stale") or evidence.get("synthetic") or evidence["source"].startswith("demo:") or not evidence.get("valid_until"):
                raise ValueError(f"{what}.evidence_ids: {evidence_id} is stale, synthetic or has unknown validity")
            if uncounted(evidence):
                raise ValueError(f"{what}.evidence_ids: {evidence_id} does not count as checked: {uncounted(evidence)}")
        holders = self.holders(owner, correction.target, correction.field, new, old, what=what)
        fact = self._fact(owner, correction.target)
        if not holders and fact is None:
            raise ValueError(f"{what}: no must lock, bought or booked booking or hard node holds {correction.target}/{correction.field}; "
                             "change it in a plan proposal (with changes.revisions when it changes what the user decided)")
        if any(question["id"] == correction.id for question in self.store.all("undecided", owner)):
            raise ValueError(f"{what}: this ID already exists")
        subject = sorted({person for holder in holders for person in holder["decision"]["subject"]} | set((fact or {}).get("subject", []))) or ["*"]
        evidence = f" Evidence: {', '.join(correction.evidence_ids)}." if correction.evidence_ids else ""
        question = (f"Correction of what the user decided: {correction.target}/{correction.field} from {shown(old)} to "
                    f"{shown(new)}. Basis: {correction.basis}.{evidence} Ask: {correction.ask}")
        self.store.put("undecided", owner, dict(
            id=correction.id, kind="correction", subject=subject, scope=Scope().model_dump(mode="json"), item_ids=[],
            question=question, source_event=source.event, asked=dict(event=proposal.answered or source.event, at=proposal.at or now()),
            options=[f"Confirm {shown(new)}", f"Keep {shown(old)}"],
            recommendation="Ask the user; record the answer with changes.confirm_corrections",
            default=f"Keep {shown(old)}: nothing changes until the user confirms", since=now(), status="open",
            correction=dict(target=correction.target, field=correction.field, old=old, new=new, basis=correction.basis,
                            ask=correction.ask, evidence_ids=correction.evidence_ids, field_class=kind,
                            locks=sorted({holder["decision"]["id"] for holder in holders}))), insert=True)
        self._audit(owner, source.event, "propose_correction", correction.id, dict(target=correction.target, field=correction.field,
                                                                                  old=old, new=new))
        return dict(id=correction.id, target=correction.target, field=correction.field, old=old, new=new, event=source.event,
                    locks=[dict(decision=holder["decision"]["id"], path=holder["path"]) for holder in holders],
                    status="open", question=question)

    def _confirm(self, owner: str, answer: Confirmation, proposal: Proposal) -> dict:
        """Path 3's answer; returns the admitted operation for lock_changes."""
        what = f"confirmation of {answer.id}"
        question = self.store.get("undecided", owner, answer.id)
        if question.get("kind") != "correction":
            raise ValueError(f"{what}: {answer.id} is not a correction")
        if question["status"] != "open":
            raise ValueError(f"{what}: it was already answered ({question.get('answer')})")
        source = self.admit(owner, answer.source, proposal, what=what, quote=answer.quote)
        # When the question was asked: by the proposal that opened it (B3h); a question opened before that, when it was
        # stored. Never the time of the message its source cites, which the agent chooses.
        asked = question.get("asked") or dict(event=question["source_event"], at=question["since"])
        said = self.store.get("events", owner, source.event)["receipt"]["received_at"]
        if source.event in (asked["event"], question["source_event"]) or aware(said) <= aware(asked["at"]):
            raise ValueError(f"{what}: the user answers in a later message than the proposal that opened the correction "
                             f"(answering {asked['event']} at {asked['at']}); message {source.event} was received at {said}")
        correction = question["correction"]
        amended = []
        if answer.answer == "confirmed":
            amended = self._amend(owner, correction["target"], correction["field"], correction["old"], correction["new"],
                                  via="confirmed_correction", event=source.event, change=answer.id, what=what)
        question.update(status="answered", answer=answer.answer, answer_event=source.event)
        self.store.put("undecided", owner, question)
        self._audit(owner, source.event, "answer_correction", answer.id, dict(answer=answer.answer, decisions=[entry["decision"] for entry in amended]))
        return dict(id=answer.id, answer=answer.answer, target=correction["target"], field=correction["field"], old=correction["old"],
                    new=correction["new"], **self._said(owner, source, proposal), quote=answer.quote, locks=amended_locks(amended), amended=amended)

    def _audit(self, owner: str, source: str, action: str, record_id: str, details: dict | None = None):
        details = details or {}
        event_id = self.events.append_change(owner, source, action, dict(record_id=record_id, **details))
        self.store.put("journal", owner, dict(id=identifier("L"), event=event_id, source_event=source,
                       action=action, record_id=record_id, details=details, at=now()), insert=True)

    def _question(self, owner: str, item: Item, reason: str, related: list[str], *, options: list[str] | None = None):
        item_ids = sorted(set(related + [item.id]))
        if any(sorted(question["item_ids"]) == item_ids and question["status"] == "open" for question in self.store.all("undecided", owner)):
            return
        fresh = options is not None
        self.store.put("undecided", owner, dict(id=identifier("Q"), question=reason, subject=item.subject,
                       scope=item.scope.model_dump(mode="json"), item_ids=item_ids,
                       source_event=item.source.event, options=options or ["Keep the previous condition", "Confirm the new condition"],
                       recommendation="Clarify which reading applies" if fresh else "Keep the safer existing condition until clarified",
                       default="Await clarification; neither new reading is selected" if fresh else "Keep existing", since=now(), status="open"), insert=True)

    def apply(self, owner: str, changes: Changes | dict, *, answered: str | None = None, at: str | None = None,
              plan: dict | None = None) -> dict:
        """The proposal the changes come with (`Proposal`): `answered`, the message it answers, from which alone user
        decisions, reports and confirmations are admitted; `at`, when it was made, which a correction it opens records
        as asked; `plan`, its candidate, whose people count only while no plan is committed. Returns `locks`, the
        admitted lock operations that lock_receipt turns into lock_changes."""
        proposal = Proposal(answered=answered, at=at, plan=plan)
        changes = Changes.model_validate(changes).model_copy(deep=True)
        changed = []
        operations = dict(decisions=[], reports=[], corrections=[], confirmations=[], stable=[])
        with self.store.transaction():
            contradictory = set()
            pairs = []
            crowding = []
            alongside = []
            batch = {item.id for item in changes.items}
            for index, first in enumerate(changes.items):
                for second in changes.items[index + 1:]:
                    if first.source.event != second.source.event or (first.value == second.value and first.text == second.text):
                        continue
                    if SETTING in (first.kind, second.kind) and first.key != second.key:
                        continue  # a setting says how to look things up, not what the user wants: it reads no other item
                    # Method §4: object, people, scope and time first. A declared relation or a shared key only makes two
                    # readings of one statement where people and scope overlap; elsewhere each item holds in its own scope.
                    if not (overlaps(first.scope, second.scope) and people_overlap(first.subject, second.subject)):
                        continue
                    if declared(first, second) or first.key == second.key:
                        if user_layer(first.source.basis) != user_layer(second.source.basis):
                            # The user's words and the agent's own reading: no two readings to clarify; the user's governs.
                            agent, user = (second, first) if user_layer(first.source.basis) else (first, second)
                            agent.relations.setdefault(CONFLICTS_USER, []).append(user.id)
                            continue
                        # Two readings of one statement: neither is used until the user clarifies.
                        contradictory.update((first.id, second.id))
                        pairs.append((first, second))
                        continue
                    if not compatible(first, second) and user_layer(first.source.basis) == user_layer(second.source.basis):
                        # the agent's own reading beside the user's words is no second reading of the message (S5)
                        first.relations.setdefault(POSSIBLE, []).append(second.id)
                        second.relations.setdefault(POSSIBLE, []).append(first.id)
                    if all((first.scope.time_from, first.scope.time_to, second.scope.time_from, second.scope.time_to)):
                        # Two wishes for the same people and time window: both stay active; the planner offers options.
                        first.relations.setdefault("competes_with", []).append(second.id)
                        second.relations.setdefault("competes_with", []).append(first.id)
                        crowding.append(dict(item_ids=[first.id, second.id], texts=[first.text, second.text],
                                             scope=first.scope.model_dump(mode="json"),
                                             notice="Both stay active and compete for this time; offer options with their differences and let the user choose."))
            for item in changes.items:
                item.origin = None
                item.source = self._source(owner, item.source, item.quote)
                if item.id in contradictory:
                    item.status = "pending"
                for timestamp in (item.valid_from, item.valid_until):
                    if timestamp:
                        aware(timestamp)
                if item.check and isinstance(item.check.get("value"), (int, float)):
                    numbers = [float(number) for number in re.findall(r"(?<!\w)\d+(?:\.\d+)?", self.store.get("events", owner, item.source.event)["received"]["text"])]
                    if float(item.check["value"]) not in numbers:
                        raise ValueError("A numeric limit must occur in the source message")
                existing = self.store.all("items", owner)
                if any(old["id"] == item.id for old in existing):
                    raise ValueError("Item id already exists; submit a replacement with a new id")
                refused = self.stable_refusal(owner, item, proposal)
                if refused:
                    raise ValueError(refused)
                for old in existing:
                    if SETTING in (old["kind"], item.kind) and old["key"] != item.key:
                        continue
                    if (old["status"] == "active" and old["id"] not in batch and old["source"]["event"] == item.source.event
                            and old["key"] != item.key and not declared(item, old) and not compatible(item, old)
                            and user_layer(old["source"]["basis"]) == user_layer(item.source.basis)
                            and (old.get("value") != item.value or old["text"] != item.text)
                            and overlaps(Scope.model_validate(old["scope"]), item.scope) and people_overlap(old["subject"], item.subject)):
                        # The same message recorded over two proposals: the same non-blocking notice as within one batch.
                        item.relations.setdefault(POSSIBLE, []).append(old["id"])
                        old.setdefault("relations", {}).setdefault(POSSIBLE, []).append(item.id)
                        self.store.put("items", owner, old)
                        continue
                    if old["status"] != "active" or not (old["key"] == item.key or declared(item, old)):
                        continue
                    if old["id"] in batch and old["source"]["event"] == item.source.event:
                        continue
                    old_scope = Scope.model_validate(old["scope"])
                    if not overlaps(old_scope, item.scope) or not people_overlap(old["subject"], item.subject):
                        continue
                    different = old.get("value") != item.value or old["text"] != item.text
                    if item.id in contradictory and old["source"]["event"] != item.source.event:
                        continue
                    if user_layer(old["source"]["basis"]) and not user_layer(item.source.basis):
                        if different:
                            item.relations.setdefault(CONFLICTS_USER, []).append(old["id"])
                        continue
                    if user_layer(item.source.basis) and not user_layer(old["source"]["basis"]) and old["source"]["event"] == item.source.event:
                        if different:  # the agent's reading of this message came first: it gives way the same
                            old.setdefault("relations", {}).setdefault(CONFLICTS_USER, []).append(item.id)
                            self.store.put("items", owner, old)
                        continue
                    if old["source"]["event"] == item.source.event and different:
                        item.status = "pending"
                        old["status"] = "pending"
                        self.store.put("items", owner, old)
                        self._question(owner, item, "Contradictory statements in one message require clarification", [old["id"]])
                    elif (different and old["strength"] == item.strength == "forbid" and {old["kind"], item.kind} <= LIMITS
                          and old["key"] == item.key and old.get("value") == item.value
                          and not ({old["id"], old["key"]} & set(item.exclusive_with))):
                        # Two prohibitions always hold together (avoid both), so neither gives way and no question is
                        # asked; both stay active (deny-overrides). The receipt quotes the one in force, so a host that
                        # meant to replace or narrow it can record again with exclusive_with and ask (D-20, 2026-11-12).
                        alongside.append(dict(id=item.id, alongside=old["id"], old=old["text"]))
                    elif different and old["strength"] in ("must", "forbid"):
                        item.status = "pending"
                        self._question(owner, item, "New statement conflicts with an existing hard condition", [old["id"]])
                    elif old_scope == item.scope and set(old["subject"]) == set(item.subject):
                        old["status"] = "superseded"
                        item.relations.setdefault("supersedes", []).append(old["id"])
                        self.store.put("items", owner, old)
                    elif contains(old_scope, item.scope) and people_contain(old["subject"], item.subject):
                        item.relations.setdefault("exception_of", []).append(old["id"])
                    elif contains(item.scope, old_scope) and people_contain(item.subject, old["subject"]):
                        old.setdefault("relations", {}).setdefault("exception_of", []).append(item.id)
                        self.store.put("items", owner, old)
                self.store.put("items", owner, item.model_dump(mode="json"), insert=True)
                self._audit(owner, item.source.event, "record_item", item.id, {"status": item.status, "relations": item.relations})
                changed.append(item.id)
            for first, second in pairs:
                self._question(owner, first, "Two readings in one message (same key or declared by the host): clarify which one is meant",
                               [second.id], options=[first.text, second.text])
            for fact in changes.facts:
                fact.source = self._source(owner, fact.source)
                aware(fact.true_from)
                if fact.valid_until:
                    aware(fact.valid_until)
                for old in self.store.all("facts", owner):
                    if old["id"] == fact.id:
                        raise ValueError("Fact id already exists")
                    if old["status"] == "active" and old["key"] == fact.key and old["subject"] == fact.subject and old["scope"] == fact.scope.model_dump(mode="json"):
                        old["status"] = "superseded"
                        self.store.put("facts", owner, old)
                self.store.put("facts", owner, fact.model_dump(mode="json"), insert=True)
                self._audit(owner, fact.source.event, "record_fact", fact.id)
                changed.append(fact.id)
            for decision in changes.decisions:
                if decision.status != "active":
                    raise ValueError("New decisions must start active")
                if any(old["id"] == decision.id for old in self.store.all("decisions", owner)):
                    raise ValueError("Decisions are immutable; record a new decision")
                what = f"user decision {decision.id}"
                paths = sorted(path for path in decision.locked_paths if decision.decided_by == "user" or decision.parts.get(path) == "user")
                if decision.decided_by == "user" and decision.source.basis != "user_explicit":
                    raise ValueError("A user decision needs an explicit user source")
                # Path 1: what the user decides (a whole decision or a part of an agent's) is recorded and replaces the
                # older decisions it covers. A must locks (D-53): it passes the admission and changes a lock only from a
                # message no older than theirs, and so does any decision that meets a must, so that "not a must" is no
                # way around one. The user's other decisions are recorded as the message says (`_source`). An agent's
                # part locks nothing and replaces nothing.
                record, replaced, refused = None, [], []
                if paths:
                    if decision.must or self._meets_must(owner, decision, paths):
                        decision.source = self.admit(owner, decision.source, proposal, what=what)
                    else:
                        decision.source = self._source(owner, decision.source)
                    authority = self._authority(owner, decision.source.event)
                    replaced, refused = self._replace(owner, decision, paths, authority, what, proposal)
                    record = dict(decision.model_dump(mode="json"), authority=authority)
                else:
                    decision.source = self._source(owner, decision.source)
                self.store.put("decisions", owner, record or decision.model_dump(mode="json"), insert=True)
                self._audit(owner, decision.source.event, "record_decision", decision.id)
                changed.append(decision.id)
                if record:
                    operations["decisions"].append(dict(id=decision.id, **self._said(owner, decision.source, proposal), must=decision.must,
                                                        paths=paths, locks=paths if decision.must else [], replaces=replaced,
                                                        **(dict(refused=refused) if refused else {})))
            operations["stable"] = self._stable_plan(owner, changes, proposal)
            for report in changes.reports:
                operations["reports"].append(self._report(owner, report, proposal))
                changed += [entry["decision"] for entry in operations["reports"][-1]["amended"]]
            for correction in changes.corrections:
                operations["corrections"].append(self._correction(owner, correction, proposal))
                changed.append(correction.id)
            for answer in changes.confirm_corrections:
                operations["confirmations"].append(self._confirm(owner, answer, proposal))
                changed += [answer.id, *(entry["decision"] for entry in operations["confirmations"][-1]["amended"])]
            for pending in changes.pending_checks:
                pending.source = self._source(owner, pending.source)
                if pending.status != "open":
                    raise ValueError("New pending checks start open; close them with evidence")
                self.store.put("pending_checks", owner, pending.model_dump(mode="json"), insert=True)
                self._audit(owner, pending.source.event, "record_pending", pending.id)
                changed.append(pending.id)
            for resolution in changes.resolve_pending:
                pending = self.store.get("pending_checks", owner, resolution.id)
                status = resolution.status
                if pending["status"] != "open":
                    raise ValueError(f"Pending check {resolution.id} is already closed as {pending['status']}")
                evidence_ids = resolution.evidence_ids
                if status == "verified":
                    if not evidence_ids:
                        raise ValueError("Verification requires evidence")
                    for evidence_id in evidence_ids:
                        evidence = self.store.get("evidence", owner, evidence_id)
                        if evidence.get("stale") or not evidence.get("valid_until") or aware(evidence["valid_until"]) <= aware(now()) or evidence["source"].startswith("demo:"):
                            raise ValueError("Evidence is stale, synthetic, or has unknown validity")
                        if uncounted(evidence):
                            raise ValueError(f"Evidence {evidence_id} does not count as checked: {uncounted(evidence)}")
                pending.update(status=status, result=resolution.result, evidence_ids=evidence_ids)
                self.store.put("pending_checks", owner, pending)
                self._audit(owner, pending["source"]["event"], "close_pending", pending["id"], {"status": status})
            for relation in changes.relate:
                self._relate(owner, relation)
            for answer in changes.carry:
                source = self._source(owner, answer.source)
                entry = self.carried(owner).get(answer.id)
                if entry is None:
                    raise ValueError(f"{answer.id} is not a long-term entry imported into this trip")
                entry.update(status=answer.status, status_event=source.event)
                self.store.put("carry", owner, entry)
                self._audit(owner, source.event, "carry_over", answer.id, {"status": answer.status})
                changed.append(answer.id)
            for root_id in changes.retract_interpretation:
                root = self.store.get("items", owner, root_id)
                if root["source"]["basis"] != "agent_inference":
                    raise ValueError("Only an agent interpretation can be retracted this way")
                affected = {root_id}
                growing = True
                while growing:
                    growing = False
                    for item in self.store.all("items", owner):
                        if item["id"] not in affected and affected.intersection(item.get("relations", {}).get("derived_from", [])):
                            affected.add(item["id"])
                            growing = True
                for item_id in affected:
                    item = self.store.get("items", owner, item_id)
                    item["status"] = "superseded"
                    self.store.put("items", owner, item)
                    self._audit(owner, audit_event(root), "retract_interpretation", item_id)
        return {"changed": changed, "revision": self.revision(owner), "crowding": crowding, "alongside": alongside,
                "possible_contradictions": self.possible_contradictions(owner),
                "locks": {part: entries for part, entries in operations.items() if entries}}

    def _relate(self, owner: str, relation: Relation):
        source = self._source(owner, relation.source)
        first, second = (self.store.get("items", owner, item_id) for item_id in relation.items)
        if first["id"] == second["id"]:
            raise ValueError("relate needs two different items")
        if first["status"] != "active" or second["status"] != "active":
            raise ValueError("relate applies to active items; pending ones wait for the user's clarification")
        if first["source"]["event"] != second["source"]["event"]:
            raise ValueError("relate declares items of one message; for a later message set same_object_as or exclusive_with on the new item")
        overlapping = overlaps(Scope.model_validate(first["scope"]), Scope.model_validate(second["scope"])) and people_overlap(first["subject"], second["subject"])
        for record, other in ((first, second), (second, first)):
            relations = record.setdefault("relations", {})
            if other["id"] in relations.get(POSSIBLE, []):
                relations[POSSIBLE] = [item_id for item_id in relations[POSSIBLE] if item_id != other["id"]]
                if not relations[POSSIBLE]:
                    relations.pop(POSSIBLE)
            record.setdefault(relation.relation, [])
            if other["id"] not in record[relation.relation]:
                record[relation.relation].append(other["id"])
            if relation.relation != "compatible_with" and overlapping:
                record["status"] = "pending"
            self.store.put("items", owner, record)
        self._audit(owner, source.event, "relate_items", first["id"], {"other": second["id"], "relation": relation.relation,
                                                                        "pending": relation.relation != "compatible_with" and overlapping})
        if relation.relation != "compatible_with" and overlapping:
            self._question(owner, Item.model_validate(first), "Two readings in one message (declared by the host): clarify which one is meant",
                           [second["id"]], options=[first["text"], second["text"]])

    def possible_contradictions(self, owner: str, items: list[dict] | None = None) -> list[dict]:
        """Open possible-contradiction notices among active items, or those touching the given items."""
        active = {item["id"]: item for item in self.store.all("items", owner) if item["status"] == "active"}
        notices, seen = [], set()
        for item in items if items is not None else active.values():
            for other_id in item.get("relations", {}).get(POSSIBLE, []):
                pair = frozenset((item["id"], other_id))
                if item["id"] in active and other_id in active and pair not in seen:
                    seen.add(pair)
                    first, second = sorted((active[item["id"]], active[other_id]), key=lambda record: list(active).index(record["id"]))
                    notices.append(contradiction_notice(first, second))
        return notices

    def current(self, owner: str, *, at: str | None = None) -> dict:
        at = at or now()
        current = {}
        unused = {entry_id for entry_id, entry in self.carried(owner).items() if entry["status"] == "not_used"}
        for table in ("items", "facts", "decisions", "pending_checks", "undecided"):
            records = self.store.all(table, owner)
            current[table] = [record for record in records if record.get("status") not in ("superseded", "expired", "satisfied")
                              and record["id"] not in unused
                              and (not record.get("valid_from") or aware(record["valid_from"]) <= aware(at))
                              and (not record.get("valid_until") or aware(record["valid_until"]) > aware(at))]
        current["profile"] = self.profile(owner)
        return current

    def compile(self, owner: str, *, subject: list[str] | None = None, scope: Scope | dict | None = None,
                scenario: str = "plan", at: str | None = None, focus_keys: list[str] | None = None,
                exclude_keys: list[str] | None = None) -> dict:
        subject = subject or ["*"]
        scope = Scope.model_validate(scope or {})
        at = at or now()
        view = self.current(owner, at=at)

        def relevant(record):
            return people_overlap(record["subject"], subject) and overlaps(Scope.model_validate(record.get("scope", {})), scope)

        # CONTRACTS 3.2: every scenario narrows by people, time, place and channel, never by kind; a quick answer needs the
        # applicable preferences and facts as much as the hard conditions. focus_keys is kept for callers but no longer filters.
        selected = [item for item in view["items"] if item["status"] == "active" and item["kind"] != SETTING and (relevant(item) or item.get("always"))
                    and (item["key"] not in (exclude_keys or []) or item["strength"] in ("must", "forbid"))]
        hidden = set()
        for exception in selected:
            if contains(Scope.model_validate(exception["scope"]), scope) and people_contain(exception["subject"], subject):
                hidden.update(exception.get("relations", {}).get("exception_of", []))
        items = [item for item in selected if item["id"] not in hidden]
        facts = [fact for fact in view["facts"] if fact["status"] == "active" and relevant(fact) and aware(fact["true_from"]) <= aware(at)]
        pending = [pending for pending in view["pending_checks"] if pending["status"] == "open" and relevant(pending)]
        pending.sort(key=lambda pending: (aware(pending["due_at"]) > aware(at), pending["due_at"]))
        profile = view["profile"]
        return dict(subject=subject, scope=scope.model_dump(mode="json"), scenario=scenario, compiled_at=at,
                    revision=self.revision(owner), items=items, facts=facts,
                    decisions=[decision for decision in view["decisions"] if relevant(decision)], pending_checks=pending,
                    overdue=[pending["id"] for pending in pending if aware(pending["due_at"]) <= aware(at)],
                    undecided=[question for question in view["undecided"] if question["status"] == "open" and relevant(question)],
                    conversation_rules=profile["rules"] if scope.channel in ("*", "conversation") else [],
                    concession_order=profile["concession_order"], excluded_keys=exclude_keys or [])

    def scan_due(self, owner: str, *, at: str | None = None) -> dict:
        at = at or now()
        expired = []
        with self.store.transaction():
            events = {event["id"] for event in self.store.all("events", owner)}
            for table in ("items", "facts"):
                for record in self.store.all(table, owner):
                    if record["status"] == "active" and ((record.get("valid_until") and aware(record["valid_until"]) <= aware(at)) or record.get("expires_on_event") in events):
                        record["status"] = "expired"
                        self.store.put(table, owner, record)
                        self._audit(owner, audit_event(record), "expire", record["id"])
                        expired.append(record["id"])
        return dict(expired=expired, overdue=[pending for pending in self.store.all("pending_checks", owner)
                                           if pending["status"] == "open" and aware(pending["due_at"]) <= aware(at)])

    def _record(self, owner: str, record_id: str) -> dict | None:
        for table in ("decisions", "items", "facts"):
            try:
                return self.store.get(table, owner, record_id)
            except ValueError:
                continue
        return None

    def needs_review(self, owner: str) -> list[dict]:
        """Live decisions that depend on a record no longer in force (2026-11-12): the user's dependency chain says
        they may not hold any more. Computed when asked, so every way a record is replaced or expires counts; a
        decision leaves this list once it is decided again. Commit still rechecks every hard constraint."""
        found = []
        for decision in self.store.all("decisions", owner):
            if decision["status"] not in ("active", "review"):
                continue
            for upstream_id in decision.get("depends_on", []):
                upstream = self._record(owner, upstream_id)
                if upstream is not None and upstream.get("status") in ("superseded", "expired"):
                    found.append(dict(decision=decision["id"], question=decision["question"], choice=decision["choice"],
                                      upstream=upstream_id, upstream_status=upstream["status"],
                                      replaced_by=upstream.get("superseded_by")))
        return found

    def graph(self, owner: str) -> dict:
        """The dependency view (2026-11-12): items and decisions in force as nodes; depends_on, the declared item
        relations and replacements as edges; plus an indented text a person or an agent can read."""
        items = [item for item in self.store.all("items", owner) if item["status"] in ("active", "pending")]
        decisions = [decision for decision in self.store.all("decisions", owner) if decision["status"] in ("active", "review")]
        nodes = ([dict(id=item["id"], kind=item["kind"], key=item["key"], text=item["text"], status=item["status"]) for item in items]
                 + [dict(id=decision["id"], kind="decision", text=f"{decision['question']} → {decision['choice']}",
                         decided_by=decision["decided_by"]) for decision in decisions])
        edges = [dict(source=decision["id"], target=target, relation="depends_on")
                 for decision in decisions for target in decision.get("depends_on", [])]
        for item in items:
            declared = {name: item.get(name) or [] for name in ("same_object_as", "exclusive_with")}
            for name, targets in {**declared, **(item.get("relations") or {})}.items():
                edges += [dict(source=item["id"], target=target, relation=name) for target in targets if isinstance(target, str)]
        review = self.needs_review(owner)
        names = {node["id"]: node["text"] for node in nodes}
        lines = []
        for node in nodes:
            outgoing = [edge for edge in edges if edge["source"] == node["id"]]
            if not outgoing and not any(edge["target"] == node["id"] for edge in edges):
                continue
            lines.append(f"{node['id']} {node['text']}")
            lines += [f"  {edge['relation']} → {edge['target']} {names.get(edge['target'], '(not in force)')}" for edge in outgoing]
        lines += [f"! review {entry['decision']}: depends on {entry['upstream']} ({entry['upstream_status']})" for entry in review]
        return dict(nodes=nodes, edges=edges, review=review, text="\n".join(lines),
                    note="Links help find what a change touches; every commit still rechecks all hard constraints.")

    def explain(self, owner: str, record_id: str | None = None) -> dict:
        journal = self.store.all("journal", owner)
        if record_id:
            journal = [entry for entry in journal if entry["record_id"] == record_id or record_id in str(entry["details"])]
        decisions = self.store.all("decisions", owner)
        if record_id:
            decisions = [decision for decision in decisions if decision["id"] == record_id or record_id in decision["depends_on"]]
        return dict(journal=journal, decisions=decisions, impact_scope=[decision["scope"] for decision in decisions],
                    note="Dependencies explain recorded links; every commit rechecks all hard constraints even if a link is missing.")
