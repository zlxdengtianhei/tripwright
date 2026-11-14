"""The reviewer's probes of round 3 (2026-11-14), kept as regressions (D-21: the service decides only what it can tell for
certain). P2: a must item the restored plan does not break stays. P3: an unrelated sentence with a take-back word does
not drop a must. P4: an older must holds the undo, and propose already says which one and what to ask."""
import pytest

from tripwright.adapters.mcp import create_server
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


def try_undo(server):
    turn = call(server, "turn", text="撤回", scenario="replan", speaker="p1")
    if "undo" not in turn:
        return turn, None, "no undo in turn"
    try:
        back = call(server, "propose", ctx_id=turn["ctx_id"], restore_version=turn["undo"]["restore_version"])
        if back.get("status") != "proposed":
            return turn, None, f"propose {back.get('status')}: {back.get('refused')}"
        return turn, call(server, "commit", draft_id=back["draft_id"], accept_unverified=True), None
    except Exception as error:  # noqa: BLE001
        return turn, None, f"{type(error).__name__}: {str(error)[:300]}"


def test_p1_chatgpt_path(trip):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    chatgpt_move(server, trip)
    turn, version, error = try_undo(server)
    lock = [d for d in trip.store.all("decisions", "alice") if d.get("question") == "晚饭时间"]
    print("P1 restore_version:", turn.get("undo", {}).get("restore_version"), "error:", error,
          "dinner:", dinner(trip), "lock:", [(d["status"], d.get("withdrawn")) for d in lock],
          "say:", (version or {}).get("tell_user", {}).get("say"))
    assert error is None and dinner(trip) == "17:00"
    assert lock and lock[0]["status"] == "superseded" and lock[0]["withdrawn"]
    assert "晚饭时间" in version["tell_user"]["say"]


def test_p2_a_must_item_in_the_same_message_that_the_restored_plan_keeps(trip):
    """D-20: only what conflicts with the restored plan goes. "第二天 8 点前必须出发" has nothing to do with the dinner."""
    server = create_server(trip, local_user="alice", manage_jobs=False)
    leave = dict(kind="constraint", key="d2-leave-by-0800", text="第二天 8 点前必须出发", quote="第二天 8 点前必须出发",
                 subject=["p1"], strength="must", source={"event": "this", "basis": "user_explicit"})
    allergy = dict(kind="constraint", key="diet:peanut", text="对花生过敏", quote="我对花生过敏", subject=["p1"], strength="forbid",
                   source={"event": "this", "basis": "user_explicit"})
    # the words of both items are in the message (the ledger checks each quote against it)
    chatgpt_move(server, trip, items=[leave, allergy], text=MOVE + "第二天 8 点前必须出发，我对花生过敏。")
    _, version, error = try_undo(server)
    active = {i["key"] for i in trip.ledger.current("alice")["items"]}
    print("P2 error:", error, "active keys:", sorted(k for k in active if k in ("d2-leave-by-0800", "diet:peanut")),
          "say:", (version or {}).get("tell_user", {}).get("say"))
    assert error is None and "diet:peanut" in active
    assert "d2-leave-by-0800" in active, "a must item that the restored plan does not break was taken back"


def test_p3_an_older_must_is_not_dropped_by_an_unrelated_sentence_with_a_take_back_word(trip):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    turn = call(server, "turn", text="第一天晚饭必须 19:00。", scenario="replan", speaker="p1")
    ledger = call(server, "propose", ctx_id=turn["ctx_id"],
                  changes=dict(decisions=[must_decision("晚饭时间", "19:00", "/blocks/d1-dinner/start", "19:00")]))
    call(server, "commit", draft_id=ledger["draft_id"])
    plan = call(server, "propose", ctx_id=turn["ctx_id"], days=[day_with(trip, d1_dinner=("19:00", "20:00"), d1_coffee=("20:30", "21:30"))])
    call(server, "commit", draft_id=plan["draft_id"], accept_unverified=True)
    lock = next(d for d in trip.store.all("decisions", "alice") if d.get("question") == "晚饭时间")
    other = call(server, "turn", text="明天不要安排太累的行程。", scenario="replan", speaker="p1")
    try:
        draft = call(server, "propose", ctx_id=other["ctx_id"], changes=dict(withdraw=[dict(id=lock["id"], reason="agent 自己决定")]),
                     days=[day_with(trip, d1_dinner=("17:00", "18:00"), d1_coffee=("18:30", "19:30"))])
        result = draft.get("status")
        if result == "proposed":
            result += " / commit " + str(call(server, "commit", draft_id=draft["draft_id"], accept_unverified=True).get("number"))
    except Exception as error:  # noqa: BLE001
        result = f"refused: {str(error)[:200]}"
    print("P3:", result, "lock status:", trip.store.get("decisions", "alice", lock["id"])["status"], "dinner:", dinner(trip))
    assert trip.store.get("decisions", "alice", lock["id"])["status"] == "active", "an unrelated sentence dropped a must"


def test_p4_older_must_refusal_names_it_and_does_not_drop_it(trip):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    turn = call(server, "turn", text="第一天晚饭必须 19:00。", scenario="replan", speaker="p1")
    ledger = call(server, "propose", ctx_id=turn["ctx_id"],
                  changes=dict(decisions=[must_decision("晚饭时间", "19:00", "/blocks/d1-dinner/start", "19:00")]))
    call(server, "commit", draft_id=ledger["draft_id"])
    turn = call(server, "turn", text="第一天的咖啡改到 20:30。", scenario="replan", speaker="p1")
    plan = call(server, "propose", ctx_id=turn["ctx_id"], days=[day_with(trip, d1_dinner=("19:00", "20:00"), d1_coffee=("20:30", "21:30"))])
    call(server, "commit", draft_id=plan["draft_id"], accept_unverified=True)
    _, version, error = try_undo(server)
    lock = next(d for d in trip.store.all("decisions", "alice") if d.get("question") == "晚饭时间")
    print("P4 error:", error, "lock:", lock["status"], "dinner:", dinner(trip))
    assert version is None and lock["status"] == "active" and dinner(trip) == "19:00"


def test_p4b_propose_itself_says_what_holds_the_undo_and_what_to_ask(trip):
    """The refusal reaches the agent where the draft is refused, not only by committing a refused draft."""
    server = create_server(trip, local_user="alice", manage_jobs=False)
    turn = call(server, "turn", text="第一天晚饭必须 19:00。", scenario="replan", speaker="p1")
    ledger = call(server, "propose", ctx_id=turn["ctx_id"],
                  changes=dict(decisions=[must_decision("晚饭时间", "19:00", "/blocks/d1-dinner/start", "19:00")]))
    call(server, "commit", draft_id=ledger["draft_id"])
    turn = call(server, "turn", text="第一天的咖啡改到 20:30。", scenario="replan", speaker="p1")
    plan = call(server, "propose", ctx_id=turn["ctx_id"], days=[day_with(trip, d1_dinner=("19:00", "20:00"), d1_coffee=("20:30", "21:30"))])
    call(server, "commit", draft_id=plan["draft_id"], accept_unverified=True)
    undo = call(server, "turn", text="撤回", scenario="replan", speaker="p1")
    back = call(server, "propose", ctx_id=undo["ctx_id"], restore_version=undo["undo"]["restore_version"])
    blocked = back["undo_blocked"]
    assert back["status"] != "proposed" or back.get("refused")
    assert [entry["text"] for entry in blocked["held_by"]] == ["晚饭时间：19:00"]
    assert blocked["held_by"][0]["quote"] == "第一天晚饭必须 19:00。"
    assert "计划没撤回去" in blocked["say"] and "要不要" in blocked["say"]
    assert "changes.withdraw" in blocked["next"] and "restore_version" in blocked["next"]


# --- what each opening of the gate must still refuse (D-21) ---------------------------------------------------------

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


def test_a_yes_to_the_services_own_question_naming_it_takes_it_back(trip):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    lock = older_lock(server, trip)
    turn = call(server, "turn", text="第一天的咖啡改到 20:30。", scenario="replan", speaker="p1")
    plan = call(server, "propose", ctx_id=turn["ctx_id"], days=[day_with(trip, d1_dinner=("19:00", "20:00"), d1_coffee=("20:30", "21:30"))])
    call(server, "commit", draft_id=plan["draft_id"], accept_unverified=True)
    undo = call(server, "turn", text="撤回", scenario="replan", speaker="p1")
    back = call(server, "propose", ctx_id=undo["ctx_id"], restore_version=undo["undo"]["restore_version"])
    assert back["undo_blocked"]["held_by"][0]["id"] == lock["id"]
    answer = call(server, "turn", text="好的。", scenario="replan", speaker="p1")
    again = call(server, "propose", ctx_id=answer["ctx_id"], restore_version=undo["undo"]["restore_version"],
                 changes=dict(withdraw=[dict(id=lock["id"], reason="用户同意一起撤")]))
    call(server, "commit", draft_id=again["draft_id"], accept_unverified=True)
    assert trip.store.get("decisions", "alice", lock["id"])["status"] == "superseded" and dinner(trip) == "17:00"


@pytest.mark.parametrize("answer", ["不用了。", "不，留着。", "先这样吧。"])
def test_an_answer_to_that_question_that_says_no_or_says_nothing_clear_is_refused(trip, answer):
    from mcp.server.mcpserver.exceptions import ToolError

    server = create_server(trip, local_user="alice", manage_jobs=False)
    lock = older_lock(server, trip)
    turn = call(server, "turn", text="第一天的咖啡改到 20:30。", scenario="replan", speaker="p1")
    plan = call(server, "propose", ctx_id=turn["ctx_id"], days=[day_with(trip, d1_dinner=("19:00", "20:00"), d1_coffee=("20:30", "21:30"))])
    call(server, "commit", draft_id=plan["draft_id"], accept_unverified=True)
    undo = call(server, "turn", text="撤回", scenario="replan", speaker="p1")
    call(server, "propose", ctx_id=undo["ctx_id"], restore_version=undo["undo"]["restore_version"])
    reply = call(server, "turn", text=answer, scenario="replan", speaker="p1")
    with pytest.raises(ToolError, match="ask the user"):
        call(server, "propose", ctx_id=reply["ctx_id"], changes=dict(withdraw=[dict(id=lock["id"], reason="x")]))
    assert trip.store.get("decisions", "alice", lock["id"])["status"] == "active"


def test_a_yes_to_a_question_about_another_record_does_not_take_this_one(trip):
    """The service asked about the dinner lock; a yes does not reach a must it never named (the allergy here is forbid,
    the must item was recorded in an earlier message and never asked about)."""
    from mcp.server.mcpserver.exceptions import ToolError

    server = create_server(trip, local_user="alice", manage_jobs=False)
    first = call(server, "turn", text="第二天 8 点前必须出发。", scenario="replan", speaker="p1")
    leave = dict(kind="constraint", key="d2-leave-by-0800", text="第二天 8 点前必须出发", quote="第二天 8 点前必须出发",
                 subject=["p1"], strength="must", source={"event": "this", "basis": "user_explicit"})
    call(server, "commit", draft_id=call(server, "propose", ctx_id=first["ctx_id"], changes=dict(items=[leave]))["draft_id"])
    item = next(i for i in trip.store.all("items", "alice") if i["key"] == "d2-leave-by-0800")
    older_lock(server, trip)
    turn = call(server, "turn", text="第一天的咖啡改到 20:30。", scenario="replan", speaker="p1")
    plan = call(server, "propose", ctx_id=turn["ctx_id"], days=[day_with(trip, d1_dinner=("19:00", "20:00"), d1_coffee=("20:30", "21:30"))])
    call(server, "commit", draft_id=plan["draft_id"], accept_unverified=True)
    undo = call(server, "turn", text="撤回", scenario="replan", speaker="p1")
    call(server, "propose", ctx_id=undo["ctx_id"], restore_version=undo["undo"]["restore_version"])
    reply = call(server, "turn", text="好的。", scenario="replan", speaker="p1")
    with pytest.raises(ToolError, match="ask the user"):
        call(server, "propose", ctx_id=reply["ctx_id"], changes=dict(withdraw=[dict(id=item["id"], reason="x")]))
    assert trip.store.get("items", "alice", item["id"])["status"] == "active"


def test_undo_names_the_must_items_it_kept_and_asks(trip):
    """P2's other half: the must item stays, and the answer says so with its words and asks whether to drop it too."""
    server = create_server(trip, local_user="alice", manage_jobs=False)
    leave = dict(kind="constraint", key="d2-leave-by-0800", text="第二天 8 点前必须出发", quote="第二天 8 点前必须出发",
                 subject=["p1"], strength="must", source={"event": "this", "basis": "user_explicit"})
    chatgpt_move(server, trip, items=[leave], text=MOVE + "第二天 8 点前必须出发。")
    _, version, error = try_undo(server)
    assert error is None
    tell = version["tell_user"]
    assert "照旧留着的必须项" in tell["say"] and "第二天 8 点前必须出发" in tell["say"] and "要不要" in tell["say"]
    assert [entry["key"] for entry in tell["kept_musts"]] == ["d2-leave-by-0800"] and "changes.withdraw" in tell["next"]
    # and the user's yes to that question takes it back
    item = next(i for i in trip.store.all("items", "alice") if i["key"] == "d2-leave-by-0800")
    reply = call(server, "turn", text="好，也撤了吧。", scenario="replan", speaker="p1")
    draft = call(server, "propose", ctx_id=reply["ctx_id"], changes=dict(withdraw=[dict(id=item["id"], reason="用户同意")]))
    call(server, "commit", draft_id=draft["draft_id"])
    assert trip.store.get("items", "alice", item["id"])["status"] == "superseded"


# --- round 4 (2026-11-14): a value names a record only with its object; a yes is read as a whole ----------------------

def asked_about(server, service):
    """An older lock holds an undo; propose's undo_blocked asks about it (the question the next answer replies to)."""
    lock = older_lock(server, service)
    turn = call(server, "turn", text="第一天的咖啡改到 20:30。", scenario="replan", speaker="p1")
    plan = call(server, "propose", ctx_id=turn["ctx_id"], days=[day_with(service, d1_dinner=("19:00", "20:00"), d1_coffee=("20:30", "21:30"))])
    call(server, "commit", draft_id=plan["draft_id"], accept_unverified=True)
    undo = call(server, "turn", text="撤回", scenario="replan", speaker="p1")
    back = call(server, "propose", ctx_id=undo["ctx_id"], restore_version=undo["undo"]["restore_version"])
    assert back["undo_blocked"]["held_by"][0]["id"] == lock["id"]
    return lock, undo["undo"]["restore_version"]


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


@pytest.mark.parametrize("answer", ["好的，不过晚饭那条先留着。", "可以，但先别撤。", "对，留着吧。", "OK, but keep it."])
def test_a_yes_with_a_but_or_a_keep_is_no_yes(trip, answer):
    from mcp.server.mcpserver.exceptions import ToolError

    server = create_server(trip, local_user="alice", manage_jobs=False)
    lock, restore = asked_about(server, trip)
    reply = call(server, "turn", text=answer, scenario="replan", speaker="p1")
    with pytest.raises(ToolError, match="ask the user"):
        call(server, "propose", ctx_id=reply["ctx_id"], restore_version=restore,
             changes=dict(withdraw=[dict(id=lock["id"], reason="x")]))
    assert trip.store.get("decisions", "alice", lock["id"])["status"] == "active"


def test_a_plain_yes_that_says_to_drop_it_still_takes_it_back(trip):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    lock, restore = asked_about(server, trip)
    reply = call(server, "turn", text="好，撤掉吧。", scenario="replan", speaker="p1")
    draft = call(server, "propose", ctx_id=reply["ctx_id"], restore_version=restore,
                 changes=dict(withdraw=[dict(id=lock["id"], reason="用户说撤掉")]))
    call(server, "commit", draft_id=draft["draft_id"], accept_unverified=True)
    assert trip.store.get("decisions", "alice", lock["id"])["status"] == "superseded" and dinner(trip) == "17:00"


def test_words_that_say_to_drop_it_but_keep_something_are_refused(trip):
    from mcp.server.mcpserver.exceptions import ToolError

    server = create_server(trip, local_user="alice", manage_jobs=False)
    lock = older_lock(server, trip)
    with pytest.raises(ToolError, match="ask the user"):
        withdraw_on(server, trip, "撤掉晚饭 19:00 那条，不过先留着计划。", lock)
    assert trip.store.get("decisions", "alice", lock["id"])["status"] == "active"
