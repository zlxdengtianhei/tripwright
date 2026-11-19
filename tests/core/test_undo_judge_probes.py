"""Round 4 (2026-11-19): nothing holds an undo. The reviewers' probes of round 3 (2026-11-14) are kept where they still
describe the service: an undo takes back everything the undone turns recorded (D-21 then kept what did not conflict), and
it goes through even when a requirement older than those turns disagrees with the restored plan; the answer names the
older one as still in force and leaves changing it to the user. Taking back a single record (`changes.withdraw`, not a
whole version) still needs the user's own words that name it, as D-21 reads them."""
import pytest

from tripwright.adapters.mcp import create_server
from tripwright.service import RESTORED_SAY, UNDO_LINE
from test_ask_before_saving import call, day_with, trip  # noqa: F401

MOVE = "把第一天的晚饭改到 19:00，晚饭必须 19:00。"


@pytest.fixture(autouse=True)
def default_mode(monkeypatch):
    monkeypatch.delenv("TRIPWRIGHT_SAVE_UNVERIFIED", raising=False)


def dinner(service):
    day = service.state("alice", view="day", date="2030-06-10")["json"]
    return next(b["start"] for o in day["plans"] for t in o["tracks"] for b in t["blocks"] if b["id"] == "d1-dinner")


def must_decision(question, choice, path, value):
    return dict(question=question, options=[choice], choice=choice, decided_by="user", must=True, subject=["p1"],
                source={"event": "this", "basis": "user_explicit"}, reason="用户原话", locked_paths={path: value})


def chatgpt_move(server, service, items=(), text=MOVE):
    turn = call(server, "turn", text=text, scenario="replan", speaker="p1")
    ledger = call(server, "propose", ctx_id=turn["ctx_id"],
                  changes=dict(decisions=[must_decision("晚饭时间", "19:00", "/blocks/d1-dinner/start", "19:00")], items=list(items)))
    assert ledger["status"] == "proposed", ledger
    call(server, "commit", draft_id=ledger["draft_id"])
    plan = call(server, "propose", ctx_id=turn["ctx_id"], days=[day_with(service, d1_dinner=("19:00", "20:00"), d1_coffee=("20:30", "21:30"))])
    assert plan["status"] == "proposed", plan
    call(server, "commit", draft_id=plan["draft_id"], accept_unverified=True)
    assert dinner(service) == "19:00"


def undo(server, words="撤回"):
    """The one call an undo takes now: turn. It returns what the service did and the answer as it goes to the user."""
    turn = call(server, "turn", text=words, scenario="replan", speaker="p1")
    assert turn.get("undo", {}).get("done"), turn.get("undo")
    return turn["undo"]


def test_p1_an_undo_goes_back_and_takes_the_must_the_undone_turns_recorded(trip):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    chatgpt_move(server, trip)
    back = undo(server)
    lock = [d for d in trip.store.all("decisions", "alice") if d.get("question") == "晚饭时间"]
    assert back["restore_version"] == 1 and dinner(trip) == "17:00"
    assert lock and lock[0]["status"] == "superseded" and lock[0]["withdrawn"]
    assert "随撤回一起撤掉" in back["say"] and "晚饭时间：19:00" in back["say"]
    assert [entry["text"] for entry in back["taken_back"]] == ["晚饭时间：19:00"]


def test_p2_a_must_item_from_the_same_message_goes_with_the_undo_too(trip):
    """Round 4 changes D-21's reading: the service no longer guesses which record the restored plan breaks. Everything
    those turns recorded goes, and the answer names each one so the user can say which to keep."""
    server = create_server(trip, local_user="alice", manage_jobs=False)
    leave = dict(kind="constraint", key="d2-leave-by-0800", text="第二天 8 点前必须出发", quote="第二天 8 点前必须出发",
                 subject=["p1"], strength="must", source={"event": "this", "basis": "user_explicit"})
    chatgpt_move(server, trip, items=[leave], text=MOVE + "第二天 8 点前必须出发。")
    back = undo(server)
    assert dinner(trip) == "17:00"
    assert trip.store.get("items", "alice", next(i["id"] for i in trip.store.all("items", "alice") if i["key"] == "d2-leave-by-0800"))["status"] == "superseded"
    assert "第二天 8 点前必须出发" in back["say"] and "其中哪条要留，再说一次就行" in back["say"]


def test_p4_a_must_from_an_earlier_message_does_not_hold_the_undo(trip):
    """Round 4: the plan goes back even when a record older than the undone turns disagrees with it; that record stays
    active, and the answer names it as still in force (not as taken back) and leaves changing it to the user."""
    server = create_server(trip, local_user="alice", manage_jobs=False)
    turn = call(server, "turn", text="第一天晚饭必须 19:00。", scenario="replan", speaker="p1")
    ledger = call(server, "propose", ctx_id=turn["ctx_id"],
                  changes=dict(decisions=[must_decision("晚饭时间", "19:00", "/blocks/d1-dinner/start", "19:00")]))
    call(server, "commit", draft_id=ledger["draft_id"])
    turn = call(server, "turn", text="第一天的咖啡改到 20:30。", scenario="replan", speaker="p1")
    plan = call(server, "propose", ctx_id=turn["ctx_id"], days=[day_with(trip, d1_dinner=("19:00", "20:00"), d1_coffee=("20:30", "21:30"))])
    call(server, "commit", draft_id=plan["draft_id"], accept_unverified=True)
    back = undo(server)
    lock = next(d for d in trip.store.all("decisions", "alice") if d.get("question") == "晚饭时间")
    assert back["restore_version"] == 2 and dinner(trip) == "17:00"
    assert lock["status"] == "active"
    assert "晚饭时间：19:00" in back["say"] and RESTORED_SAY["zh"]["still"].format(what="晚饭时间：19:00") in back["say"]
    assert "随撤回一起撤掉" not in back["say"] and not back.get("taken_back")


def test_p6_a_must_item_older_than_the_undone_turns_is_named_as_still_in_force(trip):
    """The judge's scenario (a): a must item recorded before the undone turns (no locked path) is met by the plan the
    undo takes away and not by the restored one. The undo still goes through in one call, asks nothing, and the answer
    names the item as still in force and says the user decides whether to change it."""
    server = create_server(trip, local_user="alice", manage_jobs=False)
    turn = call(server, "turn", text="晚饭必须 19:00。", scenario="replan", speaker="p1")
    ledger = call(server, "propose", ctx_id=turn["ctx_id"], changes=dict(items=[dict(
        kind="constraint", key="dinner-1900", text="晚饭必须 19:00", quote="晚饭必须 19:00", subject=["p1"], strength="must",
        source={"event": "this", "basis": "user_explicit"})]))
    call(server, "commit", draft_id=ledger["draft_id"])
    turn = call(server, "turn", text="把第一天的晚饭改到 19:00。", scenario="replan", speaker="p1")
    plan = call(server, "propose", ctx_id=turn["ctx_id"], days=[day_with(trip, d1_dinner=("19:00", "20:00"), d1_coffee=("20:30", "21:30"))])
    call(server, "commit", draft_id=plan["draft_id"], accept_unverified=True)
    assert dinner(trip) == "19:00"
    back = call(server, "turn", text="撤回", scenario="replan", speaker="p1")
    assert back["undo"]["done"] and dinner(trip) == "17:00"
    assert not back["ask"]  # nothing is asked before the undo, and nothing after it
    assert back["flow"].get("reminder") == UNDO_LINE["zh"] and "full" not in back["flow"]
    item = next(i for i in trip.store.all("items", "alice") if i["key"] == "dinner-1900")
    say = back["undo"]["say"]
    assert item["status"] == "active" and "晚饭必须 19:00" in say and "仍然有效" in say and "要不要改，你说" in say
    assert "随撤回一起撤掉" not in say and not back["undo"].get("taken_back")


def test_p3_the_same_words_sent_again_add_no_version(trip):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    chatgpt_move(server, trip)
    back = undo(server)
    numbers = [version["number"] for version in trip.store.all("versions", "alice")]
    again = undo(server)
    assert numbers == [1, 2, 3, 4] and again["version"] == back["version"]
    assert [version["number"] for version in trip.store.all("versions", "alice")] == numbers
    # the once-only cautious-mode offer is not said a second time; the rest of the answer is the first one
    offer = RESTORED_SAY["zh"]["offer"]
    assert offer in back["say"] and offer not in again["say"]
    assert again["say"] == back["say"].replace(offer, "").rstrip()


# --- taking back one record still needs the user's own words that name it (D-21) ------------------------------------

def older_lock(server, service):
    turn = call(server, "turn", text="第一天晚饭必须 19:00。", scenario="replan", speaker="p1")
    ledger = call(server, "propose", ctx_id=turn["ctx_id"],
                  changes=dict(decisions=[must_decision("晚饭时间", "19:00", "/blocks/d1-dinner/start", "19:00")]))
    call(server, "commit", draft_id=ledger["draft_id"])
    return next(d for d in service.store.all("decisions", "alice") if d.get("question") == "晚饭时间")


def withdraw_on(server, service, words, lock, **extra):
    turn = call(server, "turn", text=words, scenario="replan", speaker="p1")
    draft = call(server, "propose", ctx_id=turn["ctx_id"], changes=dict(withdraw=[dict(id=lock["id"], reason="x")]), **extra)
    return call(server, "commit", draft_id=draft["draft_id"])


def test_words_that_name_the_value_and_say_to_drop_it_take_the_must_back(trip):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    lock = older_lock(server, trip)
    withdraw_on(server, trip, "晚饭 19:00 那条必须撤掉吧。", lock)
    assert trip.store.get("decisions", "alice", lock["id"])["status"] == "superseded"


@pytest.mark.parametrize("words", ["把那条撤了。", "晚饭 19:00 不要了。", "晚饭改到 18:00 吧。", "撤掉晚饭 20:00 那条。"])
def test_words_that_do_not_both_name_this_value_and_say_to_drop_it_are_refused(trip, words):
    from mcp.server.mcpserver.exceptions import ToolError

    server = create_server(trip, local_user="alice", manage_jobs=False)
    lock = older_lock(server, trip)
    with pytest.raises(ToolError, match="ask the user"):
        withdraw_on(server, trip, words, lock)
    assert trip.store.get("decisions", "alice", lock["id"])["status"] == "active"


def test_p5_a_value_said_about_another_object_does_not_take_this_one(trip):
    """The reviewer's P5: “取消 19:00 那班去卑尔根的火车。” is about a train; the dinner lock is 19:00 too."""
    from mcp.server.mcpserver.exceptions import ToolError

    server = create_server(trip, local_user="alice", manage_jobs=False)
    lock = older_lock(server, trip)
    with pytest.raises(ToolError, match="naming it"):
        withdraw_on(server, trip, "取消 19:00 那班去卑尔根的火车。", lock)
    assert trip.store.get("decisions", "alice", lock["id"])["status"] == "active"


def test_a_value_that_other_plan_entries_share_needs_the_object_named(trip):
    """“撤掉 19:00 那条。” names no object, and 19:00 is also when the coffee after dinner ends."""
    from mcp.server.mcpserver.exceptions import ToolError

    server = create_server(trip, local_user="alice", manage_jobs=False)
    lock = older_lock(server, trip)
    with pytest.raises(ToolError, match="naming it"):
        withdraw_on(server, trip, "撤掉 19:00 那条。", lock)
    assert trip.store.get("decisions", "alice", lock["id"])["status"] == "active"


def test_the_value_with_its_own_object_still_takes_it_back(trip):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    lock = older_lock(server, trip)
    withdraw_on(server, trip, "撤掉晚饭 19:00 那条。", lock)
    assert trip.store.get("decisions", "alice", lock["id"])["status"] == "superseded"


def test_words_that_say_to_drop_it_but_keep_something_are_refused(trip):
    from mcp.server.mcpserver.exceptions import ToolError

    server = create_server(trip, local_user="alice", manage_jobs=False)
    lock = older_lock(server, trip)
    with pytest.raises(ToolError, match="ask the user"):
        withdraw_on(server, trip, "撤掉晚饭 19:00 那条，不过先留着计划。", lock)
    assert trip.store.get("decisions", "alice", lock["id"])["status"] == "active"
