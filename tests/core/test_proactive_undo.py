"""The proactive mode as the user set it on 2026-11-14: the default when nobody chose; a saved plan change ends the
answer with “当前是主动模式……想撤回，直接回复‘撤回’” from the commit receipt; “撤回” restores the version before, and the
first time this user undoes a change the answer offers the cautious mode once. Both sentences come from the service,
so they do not rest on the model remembering them."""
import pytest

from tripwright.adapters.mcp import create_server
from tripwright.service import PROACTIVE_SAY, RESTORED_SAY, UNDO_WORDS

from test_ask_before_saving import MOVE, call, day_with, moved, trip  # noqa: F401  (trip is a fixture)

SAVED = "当前是主动模式：改动已存；想撤回，直接回复“撤回”。"
OFFER = RESTORED_SAY["zh"]["offer"]


@pytest.fixture(autouse=True)
def default_mode(monkeypatch):
    monkeypatch.delenv("TRIPWRIGHT_SAVE_UNVERIFIED", raising=False)


def host_change(server, service, key, dinner=("19:00", "20:00"), text=MOVE):
    turn = call(server, "turn", text=text, scenario="replan")
    draft = call(server, "propose", ctx_id=turn["ctx_id"], base_version=turn["base_version"], idempotency_key=key,
                 days=[day_with(service, d1_dinner=dinner, d1_coffee=("20:30", "21:30"))])
    return turn, draft, call(server, "commit", draft_id=draft["draft_id"], idempotency_key=key, accept_unverified=True)


def host_undo(server, words="撤回"):
    turn = call(server, "turn", text=words, scenario="replan")
    back = call(server, "propose", ctx_id=turn["ctx_id"], restore_version=turn["undo"]["restore_version"])
    return turn, back, call(server, "commit", draft_id=back["draft_id"], accept_unverified=True)


def test_without_a_choice_a_host_change_is_saved_in_its_turn_and_the_receipt_ends_the_answer(trip):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    turn, draft, version = host_change(server, trip, "a")
    assert turn["save_mode"]["mode"] == "proactive" and turn["save_mode"]["by"] == "default"
    assert version["number"] == 2 and "tell_user" in draft
    assert version["tell_user"] == dict(say=SAVED, how="回答末尾原样带上 say。", restore_version=1)


def test_the_saved_sentence_follows_an_english_conversation(trip):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    _, _, version = host_change(server, trip, "en", text="Move dinner on June 10 from 17:00 to 19:00.")
    assert version["tell_user"]["say"] == PROACTIVE_SAY["en"]["plain"] and "“undo”" in version["tell_user"]["say"]


def test_a_ledger_only_commit_and_a_cautious_commit_say_nothing(trip, monkeypatch):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    turn = call(server, "turn", text="我对花生过敏。", scenario="replan", speaker="p1")
    draft = call(server, "propose", ctx_id=turn["ctx_id"], base_version=turn["base_version"], changes=dict(items=[dict(
        kind="constraint", key="diet:peanut", text="对花生过敏", quote="我对花生过敏。", subject=["p1"], strength="forbid",
        source={"event": "this", "basis": "user_explicit"})]))
    assert draft["status"] == "proposed", draft
    assert "tell_user" not in call(server, "commit", draft_id=draft["draft_id"])
    monkeypatch.setenv("TRIPWRIGHT_SAVE_UNVERIFIED", "ask")
    _, draft = moved(trip, key="cautious")
    trip.turn("alice", "可以，照这样存", "yes", scenario="replan")
    assert "tell_user" not in call(server, "commit", draft_id=draft["draft_id"], accept_unverified=True)


def test_undo_names_the_version_restores_it_and_offers_the_cautious_mode_once(trip):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    host_change(server, trip, "a")
    turn, back, version = host_undo(server)
    assert turn["undo"]["restore_version"] == 1 and turn["next"].startswith("用户要撤回上一次改动：propose(ctx_id, restore_version=1)")
    assert turn["flow"]["dispatch"] == []  # an undo is no new requirement for the requirements role
    assert version["number"] == 3 and version["restored_from"] == 1
    assert version["tell_user"]["say"] == "已撤回：计划回到第 1 版。" + OFFER and version["tell_user"]["offer"] == "cautious_mode"
    # a second change and a second undo: the plan comes back, the offer does not
    host_change(server, trip, "b", dinner=("18:30", "19:30"))
    _, _, again = host_undo(server, "撤销")
    assert again["restored_from"] == 3 and again["tell_user"]["say"] == "已撤回：计划回到第 3 版。" and "offer" not in again["tell_user"]


def test_a_user_who_chose_the_proactive_mode_is_not_offered_the_cautious_one(trip):
    from test_ask_before_saving import mode_said

    mode_said(trip, "proactive", "以后直接改，不用问我。", "mode")
    server = create_server(trip, local_user="alice", manage_jobs=False)
    host_change(server, trip, "a")
    _, _, version = host_undo(server)
    assert version["tell_user"]["say"] == "已撤回：计划回到第 1 版。" and "offer" not in version["tell_user"]


def test_undo_keeps_what_the_undone_turn_recorded_in_the_ledger(trip):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    record = dict(items=[dict(kind="constraint", key="diet:peanut", text="对花生过敏", quote="我对花生过敏", subject=["p1"],
                              strength="forbid", source={"event": "this", "basis": "user_explicit"})])
    call(server, "turn", text=MOVE + "我对花生过敏。", scenario="quick", speaker="p1", record=record)
    host_change(server, trip, "a")
    host_undo(server)
    assert [item["key"] for item in trip.ledger.current("alice")["items"] if item["key"] == "diet:peanut"] == ["diet:peanut"]


@pytest.mark.parametrize("words", ["撤回", "撤回吧。", "  改回去！", "undo", "Undo that.", "revert", "change it back"])
def test_undo_words(words):
    assert UNDO_WORDS.match(words)


@pytest.mark.parametrize("words", ["撤回昨天说的花生过敏", "不要撤回", "undo the hotel booking and book another", "晚饭挪到 19:00"])
def test_a_message_that_says_more_is_no_undo(words):
    assert not UNDO_WORDS.match(words)


def test_nothing_to_go_back_to_gives_no_undo(trip):
    turn = trip.turn("alice", "撤回", "nothing", scenario="replan")
    assert "undo" not in turn


def test_with_a_public_page_the_sentence_says_the_page_is_being_updated(trip, monkeypatch):
    monkeypatch.setattr(type(trip), "_page_connected", lambda self: True)
    server = create_server(trip, local_user="alice", manage_jobs=False)
    _, _, version = host_change(server, trip, "page")
    assert version["tell_user"]["say"] == "当前是主动模式：改动已存，现在开始更新页面；想撤回，直接回复“撤回”。"
    _, _, back = host_undo(server)
    assert back["tell_user"]["say"].startswith("已撤回：计划回到第 1 版，页面也在更新。")


def test_on_the_road_the_optional_researcher_says_looking_it_up_yourself_is_the_design(trip):
    turn = trip.turn("alice", "今晚最晚几点出发回住处？", "road", scenario="quick")
    researcher = [entry for entry in turn["flow"]["dispatch"] if entry["role"] == "tripwright-researcher"]
    assert researcher and all(entry.get("optional") and "设计如此" in entry["why"] for entry in researcher)
