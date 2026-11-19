"""“撤回” takes back everything the undone turns recorded, in the same call (round 4, 2.1, 2026-11-19).

In ChatGPT on the web (2026-11-14) the user moved dinner to 19:00; the host recorded a must (“晚饭必须 19:00”) in a
ledger-only commit, then the plan (version 1 → 3). The undo then could not be saved: the restored plan broke that must,
and the host made up an “undo draft”. Round 3 took back only what the service could tell broke something; round 4 takes
back every requirement, decision and fact of those turns — a judgement made on a wrong plan must not stand, and the
answer names each one so the user can say which to keep. The version to go back to is still the one before that
message's first commit."""
import pytest

from tripwright.adapters.mcp import create_server

from test_ask_before_saving import call, day_with, trip  # noqa: F401  (trip is a fixture)

MOVE = "第一天的晚饭挪到 19:00。"


@pytest.fixture(autouse=True)
def default_mode(monkeypatch):
    monkeypatch.delenv("TRIPWRIGHT_SAVE_UNVERIFIED", raising=False)


def must_dinner(turn):
    return dict(question="第一天晚饭几点", options=["19:00"], choice="19:00", decided_by="user", must=True, subject=["p1"],
                source={"event": "this", "basis": "user_explicit"}, reason="用户说晚饭挪到 19:00",
                locked_paths={"/blocks/d1-dinner/start": "19:00"})


def allergy():
    return dict(kind="constraint", key="diet:peanut", text="对花生过敏", quote="我对花生过敏", subject=["p1"], strength="forbid",
                source={"event": "this", "basis": "user_explicit"})


def as_chatgpt(server, service, text=MOVE, items=()):
    """Requirements first, as a ledger-only commit (version 2), then the plan (version 3)."""
    turn = call(server, "turn", text=text, scenario="replan", speaker="p1")
    ledger = call(server, "propose", ctx_id=turn["ctx_id"], changes=dict(decisions=[must_dinner(turn)], items=list(items)))
    assert ledger["status"] == "proposed", ledger
    first = call(server, "commit", draft_id=ledger["draft_id"])
    plan = call(server, "propose", ctx_id=turn["ctx_id"], days=[day_with(service, d1_dinner=("19:00", "20:00"), d1_coffee=("20:30", "21:30"))])
    assert plan["status"] == "proposed", plan
    second = call(server, "commit", draft_id=plan["draft_id"], accept_unverified=True)
    return first, second


def dinner(service):
    day = service.state("alice", view="day", date="2030-06-10")["json"]
    return next(block["start"] for option in day["plans"] for track in option["tracks"] for block in track["blocks"]
                if block["id"] == "d1-dinner")


def undo(server):
    turn = call(server, "turn", text="撤回", scenario="replan", speaker="p1")
    assert turn["undo"]["done"], turn["undo"]
    return turn, turn["undo"]


def test_undo_goes_back_before_the_messages_first_commit_and_takes_its_requirements_along(trip):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    first, second = as_chatgpt(server, trip, text=MOVE + "我对花生过敏。", items=[allergy()])
    assert (first["number"], second["number"]) == (2, 3) and dinner(trip) == "19:00"
    turn, back = undo(server)
    assert back["restore_version"] == 1 and back["version"] == 4 and dinner(trip) == "17:00"
    active = [entry for entry in trip.store.all("decisions", "alice") if entry["status"] == "active" and entry.get("must")]
    assert not any("/blocks/d1-dinner/start" in entry["locked_paths"] for entry in active)
    assert sorted(entry["text"] for entry in back["taken_back"]) == sorted(["第一天晚饭几点：19:00", "对花生过敏"])
    assert "随撤回一起撤掉" in back["say"] and "第一天晚饭几点：19:00" in back["say"] and "对花生过敏" in back["say"]
    assert "其中哪条要留，再说一次就行" in back["say"]
    # no propose and no commit: the turn did it
    assert "不要再调 propose 或 commit" in turn["next"]


def test_the_answer_names_one_taken_entry_without_a_list_separator(trip):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    turn = call(server, "turn", text="第一天晚饭挪到 19:00。我对花生过敏。", scenario="replan", speaker="p1")
    ledger = call(server, "propose", ctx_id=turn["ctx_id"], changes=dict(items=[allergy()]))
    call(server, "commit", draft_id=ledger["draft_id"])
    plan = call(server, "propose", ctx_id=turn["ctx_id"], days=[day_with(trip, d1_dinner=("19:00", "20:00"), d1_coffee=("20:30", "21:30"))])
    call(server, "commit", draft_id=plan["draft_id"], accept_unverified=True)
    _, back = undo(server)
    assert back["say"].startswith("已撤回：计划回到第 1 版。随撤回一起撤掉：对花生过敏。")
    assert "。。" not in back["say"] and "；" not in back["say"].split("随撤回一起撤掉：")[1].split("。")[0]


def test_an_earlier_must_no_longer_holds_the_undo_and_is_named_as_still_in_force(trip):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    turn = call(server, "turn", text="第一天晚饭必须 19:00。", scenario="replan", speaker="p1")
    ledger = call(server, "propose", ctx_id=turn["ctx_id"], changes=dict(decisions=[must_dinner(turn)]))
    call(server, "commit", draft_id=ledger["draft_id"])
    turn = call(server, "turn", text=MOVE, scenario="replan", speaker="p1")
    plan = call(server, "propose", ctx_id=turn["ctx_id"], days=[day_with(trip, d1_dinner=("19:00", "20:00"), d1_coffee=("20:30", "21:30"))])
    assert call(server, "commit", draft_id=plan["draft_id"], accept_unverified=True)["number"] == 3
    _, back = undo(server)
    lock = next(entry for entry in trip.store.all("decisions", "alice") if entry.get("must"))
    assert back["restore_version"] == 2 and dinner(trip) == "17:00" and lock["status"] == "active"
    # round 4 review: not taken back, but not silent either: named as still in force, the user decides whether to change it
    assert "第一天晚饭几点：19:00" in back["say"] and "仍然有效" in back["say"] and "随撤回一起撤掉" not in back["say"]


def test_a_fact_from_the_undone_turn_goes_with_the_undo_too(trip):
    """The design's list is requirements, decisions and facts: a fact recorded with the wrong plan is a judgement too."""
    server = create_server(trip, local_user="alice", manage_jobs=False)
    fact = dict(key="energy", value="low", text="今天一个人", subject=["p1"],
                true_from="2030-06-10T09:00:00+02:00", invalidated_by="明天两个人",
                source={"event": "this", "basis": "user_explicit"})
    turn = call(server, "turn", text=MOVE + "今天一个人。", scenario="replan", speaker="p1")
    ledger = call(server, "propose", ctx_id=turn["ctx_id"], changes=dict(facts=[fact]))
    call(server, "commit", draft_id=ledger["draft_id"])
    plan = call(server, "propose", ctx_id=turn["ctx_id"], days=[day_with(trip, d1_dinner=("19:00", "20:00"), d1_coffee=("20:30", "21:30"))])
    call(server, "commit", draft_id=plan["draft_id"], accept_unverified=True)
    _, back = undo(server)
    assert any(entry["text"] == "今天一个人" for entry in back["taken_back"])
    assert [entry["status"] for entry in trip.store.all("facts", "alice") if entry["key"] == "energy"] == ["superseded"]


def test_the_plan_may_name_the_version_its_own_ledger_commit_made_and_the_undo_goes_back_before_both(trip):
    """GLM, 2026-11-14: after committing the message's ledger-only draft it proposed the plan with the new version
    number, got “Stale version”, called turn again, and the undo then went back to the ledger-only version only."""
    server = create_server(trip, local_user="alice", manage_jobs=False)
    turn = call(server, "turn", text=MOVE, scenario="replan", speaker="p1")
    ledger = call(server, "propose", ctx_id=turn["ctx_id"], base_version=turn["base_version"], changes=dict(decisions=[must_dinner(turn)]))
    assert call(server, "commit", draft_id=ledger["draft_id"])["number"] == 2
    plan = call(server, "propose", ctx_id=turn["ctx_id"], base_version=2,
                days=[day_with(trip, d1_dinner=("19:00", "20:00"), d1_coffee=("20:30", "21:30"))])
    assert plan["status"] == "proposed", plan
    assert call(server, "commit", draft_id=plan["draft_id"], accept_unverified=True)["number"] == 3
    turn, back = undo(server)
    assert back["restore_version"] == 1 and dinner(trip) == "17:00"


def test_a_turn_sent_again_with_the_same_words_is_the_same_message_for_the_undo(trip):
    """Should a host still call turn again with the same words between the two commits, the undo takes both back."""
    server = create_server(trip, local_user="alice", manage_jobs=False)
    turn = call(server, "turn", text=MOVE, scenario="replan", speaker="p1")
    ledger = call(server, "propose", ctx_id=turn["ctx_id"], changes=dict(decisions=[must_dinner(turn)]))
    call(server, "commit", draft_id=ledger["draft_id"])
    again = call(server, "turn", text=MOVE, scenario="replan", speaker="p1")
    plan = call(server, "propose", ctx_id=again["ctx_id"], days=[day_with(trip, d1_dinner=("19:00", "20:00"), d1_coffee=("20:30", "21:30"))])
    assert call(server, "commit", draft_id=plan["draft_id"], accept_unverified=True)["number"] == 3
    turn, back = undo(server)
    assert back["restore_version"] == 1 and dinner(trip) == "17:00"


def test_a_booked_item_from_the_undone_turn_does_not_hold_the_undo_and_is_named(trip):
    """Boundary of the design: a ticket bought in those turns goes with the undo; the answer says the record went and
    that the ticket itself is the user's to cancel or keep."""
    server = create_server(trip, local_user="alice", manage_jobs=False)
    before = [booking["id"] for booking in trip.versions.current("alice")["plan"]["bookings"]]
    turn = call(server, "turn", text="我买了 6 月 10 日晚上 19:00 那班车的票。", scenario="replan", speaker="p1")
    plan = call(server, "propose", ctx_id=turn["ctx_id"], days=[day_with(trip, d1_dinner=("19:00", "20:00"), d1_coffee=("20:30", "21:30"))],
                bookings=[dict(id="b1", kind="transport", what="19:00 的车", date="2030-06-10", subject=["p1"], status="bought",
                               fares=[dict(id="f1", segment="d1", ticket="t1", subject=["p1"], amount_per_person=12.5, currency="EUR")])])
    assert plan["status"] == "proposed", plan
    call(server, "commit", draft_id=plan["draft_id"], accept_unverified=True)
    assert any(booking["status"] == "bought" for booking in trip.versions.current("alice")["plan"]["bookings"])
    _, back = undo(server)
    assert dinner(trip) == "17:00"
    assert [booking["id"] for booking in trip.versions.current("alice")["plan"]["bookings"]] == before
    # the ticket itself is not the service's to cancel: the answer names it and says so
    assert "19:00 的车" in back["say"] and "票本身要你自己去退或留" in back["say"]
