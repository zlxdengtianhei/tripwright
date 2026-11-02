"""B3g (D-49): the three ways a user lock changes pass one admission, say where each change came from, and turn.record
is checked for shape before anything reads it.

Admission (`Ledger.admit`): a user decision, a report and a confirmation are admitted only from the message their
proposal answers, in the user's own words (user_verbatim or host_restatement) and written by a person of the trip. The
judge's counterexample A put a screenshot extraction, a missing or outside speaker, or an earlier user message into a
new user decision and replaced the lock; here the same seven samples run through all three ways. B: each legal way's
propose and commit answers carry lock_changes made from the admitted operation. C: a record entry or its source sent as
an array was answered with a generic error; now each of the ten shapes is refused with its field path and the type it
needs, and the same part sent well-formed is recorded. Fictional people, places and tickets only."""
from __future__ import annotations

import pytest

from test_b3e_reference_graph import GROUP, LOCKED, agent, lock_conflicts, node
from test_b3f_three_paths import (ENTRY, HALL, NEAR, active_decisions, commit, correction, locked, message, propose,  # noqa: F401
                                  report, with_entry, with_hall)
from tripwright import locks
from tripwright.service import TURN_RECORDS
from tripwright.storage import now

LOCK = f"/blocks/{LOCKED}"

# label, the cited message's nature and speaker, whether it is an earlier message than the one the proposal answers,
# and the admission check that refuses it (None: admitted).
SAMPLES = [
    ("user_verbatim/u1", "user_verbatim", "u1", False, None),
    ("host_restatement/u1", "host_restatement", "u1", False, None),
    ("agent_inference/u1", "agent_inference", "u1", False, "nature"),
    ("screenshot_extraction/u1", "screenshot_extraction", "u1", False, "nature"),
    ("user_verbatim/missing-speaker", "user_verbatim", None, False, "speaker"),
    ("user_verbatim/stranger", "user_verbatim", "stranger", False, "speaker"),
    ("earlier-user-event", "user_verbatim", "u1", True, "this turn"),
]


def open_correction(service) -> str:
    """Path 3 starts with the agent's correction of the hall's coordinates, an open question."""
    turn = message(service, "agent checks the entrance on the map", nature="agent_inference", speaker=None)
    commit(service, propose(service, turn, corrections=[correction(turn)]))
    return next(question["id"] for question in service.store.all("undecided", "alice") if question.get("kind") == "correction")


def new_decision(plan, cited):
    return dict(question="门票怎么买", options=["已经买好"], choice="已经买好", decided_by="user", must=True, subject=GROUP,
                source=dict(event=cited["receipt"]["event_id"], basis="user_explicit"), locked_paths={LOCK: locks.pointer(plan, LOCK)})


def decision_change(service, cited, opened):
    plan = with_entry(service, status="bought")
    return plan, dict(decisions=[new_decision(plan, cited)])


def report_change(service, cited, opened):
    return with_entry(service, status="bought"), dict(reports=[report(cited, quote="我买了票")])


def confirmation_change(service, cited, opened):
    return with_hall(service, coords=NEAR), dict(confirm_corrections=[dict(
        id=opened, answer="confirmed", quote="确认北门", source=dict(event=cited["receipt"]["event_id"]))])


# Each way: the user's words, what to prepare, the change it sends with the plan, and the locked value it changes.
WAYS = {
    "decision": dict(text="门票我们改成已经买好，定了。", prepare=lambda service: None, change=decision_change,
                     value=lambda plan: node(plan, ENTRY)["status"], new="bought"),
    "report": dict(text="我买了票。", prepare=lambda service: None, change=report_change,
                   value=lambda plan: node(plan, ENTRY)["status"], new="bought"),
    "confirmation": dict(text="对，确认北门。", prepare=open_correction, change=confirmation_change,
                         value=lambda plan: node(plan, HALL)["coords"], new=NEAR),
}


@pytest.mark.parametrize("label,nature,speaker,earlier,check", SAMPLES, ids=[sample[0] for sample in SAMPLES])
@pytest.mark.parametrize("way", WAYS)
def test_counterexample_a_one_admission_for_the_three_ways_a_user_lock_changes(locked, way, label, nature, speaker, earlier, check):
    path = WAYS[way]
    opened = path["prepare"](locked)
    before, decisions = locked.state("alice")["plan"], active_decisions(locked)
    cited = message(locked, path["text"], nature=nature, speaker=speaker)
    # The earlier sample: the user's message is cited from a later turn the agent answers.
    turn = message(locked, "agent records what was said earlier", nature="agent_inference", speaker=None) if earlier else cited
    plan, changes = path["change"](locked, cited, opened)
    if check is None:
        draft = propose(locked, turn, plan=plan, **changes)
        assert draft["status"] == "proposed" and lock_conflicts(draft) == [], [entry["details"] for entry in lock_conflicts(draft)]
        version = commit(locked, draft, accept_unverified=True)
        assert path["value"](locked.state("alice")["plan"]) == path["new"]
        for answer in (draft, version):
            [line] = next(lines for part, lines in answer["lock_changes"].items() if part != "amended")
            # The message's nature is kept as received: a host's restatement is not raised to verbatim (S5).
            assert (line["event"], line["nature"], line["speaker"]) == (cited["receipt"]["event_id"], nature, speaker)
        return
    with pytest.raises(ValueError, match=rf"not admitted \({check}\)"):
        propose(locked, turn, plan=plan, **changes)
    assert locked.state("alice")["plan"] == before and active_decisions(locked) == decisions
    if opened:
        assert locked.store.get("undecided", "alice", opened)["status"] == "open"
    # The lock still holds the old value: the same plan from the agent is refused naming the field.
    assert lock_conflicts(agent(locked, plan))


@pytest.mark.parametrize("way", WAYS)
def test_counterexample_b_each_legal_way_says_in_propose_and_commit_which_message_changed_which_lock(locked, way):
    path = WAYS[way]
    opened = path["prepare"](locked)
    [old] = active_decisions(locked)
    turn = message(locked, path["text"])
    event = turn["receipt"]["event_id"]
    plan, changes = path["change"](locked, turn, opened)
    draft = propose(locked, turn, plan=plan, **changes)
    version = commit(locked, draft, accept_unverified=True)
    [new] = active_decisions(locked)
    said = dict(event=event, nature="user_verbatim", speaker="u1")
    if way == "decision":
        line = dict(id=new["id"], **said, must=True, paths=[LOCK], locks=[LOCK], replaces=[dict(decision=old["id"], locks=[LOCK])])
        assert draft["lock_changes"] == dict(decisions=[dict(line, takes_effect="on commit")])
        assert version["lock_changes"] == dict(decisions=[line])
        assert new["source"] == dict(event=event, nature="user_verbatim", basis="user_explicit")
        return
    sent = locked.store.get("drafts", "alice", draft["draft_id"])["changes"]
    if way == "report":
        part, change, via = "reports", sent["reports"][0]["id"], "user_report"
        line = dict(id=change, target=ENTRY, field="status", old="on_site", new="bought", **said, quote="我买了票")
    else:
        part, change, via = "confirmations", opened, "confirmed_correction"
        line = dict(id=opened, answer="confirmed", target=HALL, field="coords", old=[47.80, 13.04], new=NEAR, **said, quote="确认北门")
    line["locks"] = [dict(decision=old["id"], path=LOCK)]
    amended = dict(decision=new["id"], paths=[LOCK], replaces=old["id"], change=change, via=via, target=line["target"],
                   field=line["field"], old=line["old"], new=line["new"], event=event)
    assert draft["lock_changes"] == {part: [dict(line, takes_effect="on commit")]}
    assert version["lock_changes"] == {part: [dict(line, amended=[amended])], "amended": [amended]}
    assert new["amends"] == {key: value for key, value in amended.items() if key not in ("decision", "paths")}


def test_counterexample_b_a_correction_says_what_it_opens_and_that_it_waits(locked):
    turn = message(locked, "agent checks the entrance on the map", nature="agent_inference", speaker=None)
    draft = propose(locked, turn, corrections=[correction(turn)])
    version = commit(locked, draft)
    [old] = active_decisions(locked)
    [opened] = [question for question in locked.store.all("undecided", "alice") if question.get("kind") == "correction"]
    line = dict(id=opened["id"], target=HALL, field="coords", old=[47.80, 13.04], new=NEAR, event=turn["receipt"]["event_id"],
                locks=[dict(decision=old["id"], path=LOCK)], question=opened["question"])
    assert draft["lock_changes"] == dict(corrections=[dict(line, takes_effect="only after the user confirms it in a later message (confirm_corrections)")])
    assert version["lock_changes"] == dict(corrections=[dict(line, status="open")])


def test_an_agents_decision_with_a_part_the_user_decided_is_admitted_like_a_user_decision(locked):
    """A user part of an agent's decision becomes a user lock too: the same admission, not a way around it."""
    turn = message(locked, "agent decides the ticket", nature="agent_inference", speaker=None)
    plan = with_entry(locked, status="bought")
    part = dict(new_decision(plan, turn), decided_by="agent", parts={LOCK: "user"},
                source=dict(event=turn["receipt"]["event_id"], basis="agent_inference"))
    with pytest.raises(ValueError, match=r"not admitted \(nature\)"):
        propose(locked, turn, plan=plan, decisions=[part])
    said = message(locked, "门票这部分我们定了。")
    draft = propose(locked, said, plan=plan, decisions=[dict(part, source=dict(event=said["receipt"]["event_id"], basis="agent_inference"))])
    assert draft["lock_changes"]["decisions"][0]["locks"] == [LOCK]


def test_a_user_decision_labelled_as_the_agents_inference_is_refused_even_from_the_users_message(locked):
    turn = message(locked, "门票我们改成已经买好，定了。")
    plan = with_entry(locked, status="bought")
    decision = dict(new_decision(plan, turn), source=dict(event=turn["receipt"]["event_id"], basis="agent_inference"))
    with pytest.raises(ValueError, match="A user decision needs an explicit user source"):
        propose(locked, turn, plan=plan, decisions=[decision])


def test_a_user_lock_changes_only_in_a_proposal_that_answers_a_message(locked):
    turn = message(locked, "门票我们改成已经买好，定了。")
    with pytest.raises(ValueError, match=r"not admitted \(this turn\): a user lock changes only in a proposal"):
        locked.ledger.apply("alice", dict(reports=[report(turn, quote="我买了票")]))


# --- counterexample C: turn.record's shape before anything reads it ---------------------------------------------------

SAID = "我带着外套。慢慢走。想早点到。"


def valid_record(part: str) -> dict:
    """A well-formed record of each part for the message SAID, by u1."""
    item = lambda key, quote, **extra: dict(dict(kind="preference", key=key, quote=quote, text=quote, subject=["u1"], strength="prefer",
                                                 source=dict(event="this")), **extra)
    return dict(
        items=dict(items=[item("pace", "慢慢走")]),
        facts=dict(facts=[dict(key="equipment", value=["coat"], text="我带着外套", subject=["u1"], true_from=now(),
                               invalidated_by="The user says otherwise", source=dict(event="this"))]),
        pending_checks=dict(pending_checks=[dict(question="Does hall A open early?", due_at="2031-05-01T08:00:00+02:00", responsible="agent",
                                                 subject=["u1"], consequence="No early start.", alternative="Start at the café.",
                                                 source=dict(event="this"))]),
        relate=dict(items=[item("pace", "慢慢走", id="I-shape-a"), item("start", "想早点到", id="I-shape-b")],
                    relate=[dict(items=["I-shape-a", "I-shape-b"], relation="compatible_with", source=dict(event="this"))]),
        carry=dict(carry=[dict(id=CARRIED, status="confirmed", source=dict(event="this"))]))[part]


CARRIED = "I-shape-carried"


def carried(store, owner: str):
    """A long-term entry imported into this trip, as memory.import_entries stores it, waiting for the user's answer."""
    store.put("carry", owner, dict(id=CARRIED, kind="preference", text="Prefers trains", path="profile/items/I-shape-carried.yaml",
                                   from_trip="2030-01-earlier", status="pending", status_event=None, imported_at=now(), last_used=None),
              insert=True)


SHAPES = [(part, shape) for part in TURN_RECORDS for shape in ("entry", "source")]


def bad_record(part: str, shape: str) -> tuple[dict, str]:
    """The judge's two shapes per part: the entry an array, or its source an array; and the field path refused."""
    return ({part: [[]]}, f"record.{part}.0 must be an object {{") if shape == "entry" else \
        ({part: [dict(source=[])]}, f"record.{part}.0.source must be an object {{event")


def recorded(service, part: str, result: dict) -> bool:
    if part == "carry":
        return service.store.get("carry", "alice", CARRIED)["status"] == "confirmed"
    return bool(result["recorded"]["changed"]) and not result["recorded"]["not_recorded"]


@pytest.mark.parametrize("part,shape", SHAPES, ids=[f"{part}-{shape}" for part, shape in SHAPES])
def test_counterexample_c_a_wrong_record_shape_is_refused_with_its_path_and_the_right_one_is_recorded(locked, part, shape):
    record, refused = bad_record(part, shape)
    events = len(locked.store.all("events", "alice"))
    with pytest.raises(ValueError) as error:
        locked.turn("alice", SAID, f"bad-{part}-{shape}", nature="user_verbatim", native_ref="fixture:bad", speaker="u1", record=record)
    assert str(error.value).startswith(f"Invalid record: {refused}") and str(error.value).endswith("got array"), str(error.value)
    assert len(locked.store.all("events", "alice")) == events  # refused before the message was received
    if part == "carry":
        carried(locked.store, "alice")
    result = locked.turn("alice", SAID, f"good-{part}-{shape}", nature="user_verbatim", native_ref=f"fixture:good-{shape}", speaker="u1",
                         record=valid_record(part))
    assert recorded(locked, part, result), result["recorded"]
