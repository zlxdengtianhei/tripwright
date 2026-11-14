"""“撤回” takes back what the undone turns recorded when it stands in the way (D-20, 2026-11-14). In ChatGPT on the web
the user moved dinner to 19:00; the host recorded a must (“晚饭必须 19:00”) in a ledger-only commit, then the plan
(version 1 → 3). The undo then could not be saved: the restored plan broke that must, and the host made up an “undo
draft”. Now the version to go back to is the one before that message's first commit, and a requirement those turns
recorded that the restored plan breaks is taken back with it, in the same commit; one that does not conflict (an
allergy said in the same message) stays."""
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
    back = call(server, "propose", ctx_id=turn["ctx_id"], restore_version=turn["undo"]["restore_version"])
    return turn, back


def test_undo_goes_back_before_the_messages_first_commit_and_takes_its_must_along(trip):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    first, second = as_chatgpt(server, trip)
    assert (first["number"], second["number"]) == (2, 3) and dinner(trip) == "19:00"
    turn, back = undo(server)
    assert turn["undo"]["restore_version"] == 1
    assert back["status"] == "proposed", back
    version = call(server, "commit", draft_id=back["draft_id"], accept_unverified=True)
    assert version["number"] == 4 and dinner(trip) == "17:00"
    active = [entry for entry in trip.store.all("decisions", "alice") if entry["status"] == "active" and entry.get("must")]
    assert not any("/blocks/d1-dinner/start" in entry["locked_paths"] for entry in active)
    taken = version["tell_user"]["taken_back"]
    assert [entry["text"] for entry in taken] == ["第一天晚饭几点：19:00"]
    assert "随撤回一起撤掉" in version["tell_user"]["say"] and "第一天晚饭几点：19:00" in version["tell_user"]["say"]


def test_an_allergy_said_in_the_same_message_stays(trip):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    as_chatgpt(server, trip, text=MOVE + "我对花生过敏。", items=[allergy()])
    _, back = undo(server)
    call(server, "commit", draft_id=back["draft_id"], accept_unverified=True)
    assert dinner(trip) == "17:00"
    assert [item["key"] for item in trip.ledger.current("alice")["items"] if item["key"] == "diet:peanut"] == ["diet:peanut"]


def older_must(server, service):
    """The must comes from one message (version 2, ledger only), the move from the next (version 3)."""
    turn = call(server, "turn", text="第一天晚饭必须 19:00。", scenario="replan", speaker="p1")
    ledger = call(server, "propose", ctx_id=turn["ctx_id"], changes=dict(decisions=[must_dinner(turn)]))
    call(server, "commit", draft_id=ledger["draft_id"])
    turn = call(server, "turn", text=MOVE, scenario="replan", speaker="p1")
    plan = call(server, "propose", ctx_id=turn["ctx_id"], days=[day_with(service, d1_dinner=("19:00", "20:00"), d1_coffee=("20:30", "21:30"))])
    assert call(server, "commit", draft_id=plan["draft_id"], accept_unverified=True)["number"] == 3


def test_a_must_from_an_earlier_message_still_holds_the_undo_and_the_refusal_says_what_to_ask(trip):
    from mcp.server.mcpserver.exceptions import ToolError

    server = create_server(trip, local_user="alice", manage_jobs=False)
    older_must(server, trip)
    turn, back = undo(server)
    assert turn["undo"]["restore_version"] == 2
    with pytest.raises(ToolError) as refusal:
        call(server, "commit", draft_id=back["draft_id"], accept_unverified=True)
    text = str(refusal.value)
    assert "Not undone: the plan is still the current version" in text and "第一天晚饭几点: 19:00" in text
    assert "ask whether to drop that requirement too" in text and "withdraw" in text
    assert dinner(trip) == "19:00"


def test_when_the_user_says_to_drop_it_too_withdraw_and_restore_go_through(trip):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    older_must(server, trip)
    lock = next(entry for entry in trip.store.all("decisions", "alice") if entry["status"] == "active" and entry.get("must"))
    turn = call(server, "turn", text="对，把晚饭必须 19:00 那条也撤了，恢复原来的 17:00。", scenario="replan", speaker="p1")
    back = call(server, "propose", ctx_id=turn["ctx_id"], restore_version=2,
                changes=dict(withdraw=[dict(id=lock["id"], reason="用户说那条也撤了")]))
    version = call(server, "commit", draft_id=back["draft_id"], accept_unverified=True)
    assert dinner(trip) == "17:00" and trip.store.get("decisions", "alice", lock["id"])["status"] == "superseded"
    assert trip.store.get("decisions", "alice", lock["id"])["withdrawn"]["reason"] == "用户说那条也撤了"
    assert [entry["id"] for entry in version["tell_user"]["taken_back"]] == [lock["id"]]


def test_a_users_must_is_not_withdrawn_on_words_that_do_not_say_so_or_without_the_speaker(trip):
    from mcp.server.mcpserver.exceptions import ToolError

    server = create_server(trip, local_user="alice", manage_jobs=False)
    older_must(server, trip)
    lock = next(entry for entry in trip.store.all("decisions", "alice") if entry["status"] == "active" and entry.get("must"))
    for words, speaker, message in (("好的。", "p1", "does not take it back for certain: ask the user"), ("把那条撤了。", None, "only the user's own message")):
        turn = call(server, "turn", text=words, scenario="replan", **({"speaker": speaker} if speaker else {}))
        with pytest.raises(ToolError, match=message):
            call(server, "propose", ctx_id=turn["ctx_id"], changes=dict(withdraw=[dict(id=lock["id"], reason="x")]))
    assert trip.store.get("decisions", "alice", lock["id"])["status"] == "active"


def test_a_must_item_stays_after_the_undo_and_is_named_with_the_allergy_and_asked_about(trip):
    """GLM, 2026-11-14: the must was recorded as an item (“Day 1 dinner is at 19:00”, strength must) and the undo turn
    had no speaker; the answer said the item went when it had not. D-21: the service does not test an item's words
    against the plan, so the item stays; the receipt names it in the user's words and asks, and names the allergy as kept."""
    server = create_server(trip, local_user="alice", manage_jobs=False)
    turn = call(server, "turn", text=MOVE + "我对花生过敏。", scenario="replan", speaker="p1")
    must = dict(kind="constraint", key="d1-dinner-time", text="Day 1 dinner is at 19:00.", quote="第一天的晚饭挪到 19:00",
                subject=["p1"], strength="must", source={"event": "this", "basis": "user_explicit"})
    ledger = call(server, "propose", ctx_id=turn["ctx_id"], changes=dict(items=[must, allergy()]))
    call(server, "commit", draft_id=ledger["draft_id"])
    plan = call(server, "propose", ctx_id=turn["ctx_id"], days=[day_with(trip, d1_dinner=("19:00", "20:00"), d1_coffee=("20:30", "21:30"))])
    call(server, "commit", draft_id=plan["draft_id"], accept_unverified=True)
    back_turn = call(server, "turn", text="撤回", scenario="replan")  # no speaker, as GLM sent it
    back = call(server, "propose", ctx_id=back_turn["ctx_id"], restore_version=back_turn["undo"]["restore_version"])
    version = call(server, "commit", draft_id=back["draft_id"], accept_unverified=True)
    active = {item["key"] for item in trip.ledger.current("alice")["items"]}
    assert dinner(trip) == "17:00" and "d1-dinner-time" in active and "diet:peanut" in active
    say = version["tell_user"]["say"]
    assert "随撤回一起撤掉" not in say and "照旧留着的必须项：“第一天的晚饭挪到 19:00”。要不要也撤掉？" in say
    assert "照旧留着：对花生过敏。" in say and "。。" not in say
    assert [entry["key"] for entry in version["tell_user"]["kept_musts"]] == ["d1-dinner-time"]


def test_a_host_cannot_mark_its_own_withdrawal_as_the_services(trip):
    from mcp.server.mcpserver.exceptions import ToolError

    server = create_server(trip, local_user="alice", manage_jobs=False)
    older_must(server, trip)
    lock = next(entry for entry in trip.store.all("decisions", "alice") if entry["status"] == "active" and entry.get("must"))
    turn = call(server, "turn", text="把那条撤了。", scenario="replan")  # no speaker
    with pytest.raises(ToolError, match="only the user's own message"):
        call(server, "propose", ctx_id=turn["ctx_id"], changes=dict(withdraw=[dict(id=lock["id"], reason="x", filled_by="service")]))


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
    back_turn, back = undo(server)
    assert back_turn["undo"]["restore_version"] == 1
    call(server, "commit", draft_id=back["draft_id"], accept_unverified=True)
    assert dinner(trip) == "17:00"


def test_a_turn_sent_again_with_the_same_words_is_the_same_message_for_the_undo(trip):
    """Should a host still call turn again with the same words between the two commits, the undo takes both back."""
    server = create_server(trip, local_user="alice", manage_jobs=False)
    turn = call(server, "turn", text=MOVE, scenario="replan", speaker="p1")
    ledger = call(server, "propose", ctx_id=turn["ctx_id"], changes=dict(decisions=[must_dinner(turn)]))
    call(server, "commit", draft_id=ledger["draft_id"])
    again = call(server, "turn", text=MOVE, scenario="replan", speaker="p1")
    plan = call(server, "propose", ctx_id=again["ctx_id"], days=[day_with(trip, d1_dinner=("19:00", "20:00"), d1_coffee=("20:30", "21:30"))])
    assert call(server, "commit", draft_id=plan["draft_id"], accept_unverified=True)["number"] == 3
    back_turn, back = undo(server)
    assert back_turn["undo"]["restore_version"] == 1
    call(server, "commit", draft_id=back["draft_id"], accept_unverified=True)
    assert dinner(trip) == "17:00"
