"""2026-11-28: the host decides which version an undo goes to; the service gives the material and does it.

`turn` no longer reads the user's words for an undo and never goes back by itself; every receipt (once there is a version
to go back to) carries `versions`, the newest five. `propose(ctx_id, restore_version=N)` saves version N's plan in that one
call. Going forward again to a version an undo had passed puts back the entries only that undo had taken back."""
import json

import pytest

from tripwright import version_brief
from tripwright.adapters.mcp import create_server
from tripwright.flow import FULL, SHORT, UNDO_HOST, UNDO_SHORT
from tripwright.service import RESTORED_SAY

from test_ask_before_saving import call, day_with, trip  # noqa: F401  (trip is a fixture)
from test_undo_judge_probes import chatgpt_move, dinner, must_decision, older_lock, withdraw_on


@pytest.fixture(autouse=True)
def default_mode(monkeypatch):
    monkeypatch.delenv("TRIPWRIGHT_SAVE_UNVERIFIED", raising=False)


def change(server, service, key, dinner_at, text):
    turn = call(server, "turn", text=text, scenario="replan", speaker="p1")
    start, end = dinner_at
    draft = call(server, "propose", ctx_id=turn["ctx_id"], idempotency_key=key,
                 days=[day_with(service, d1_dinner=(start, end), d1_coffee=("21:30", "22:30"))])
    assert draft["status"] == "proposed", draft
    call(server, "commit", draft_id=draft["draft_id"], accept_unverified=True)
    return turn


# --- turn gives material and does nothing about an undo ---------------------------------------------------------------

def test_turn_lists_the_newest_versions_with_what_each_changed_and_the_words_that_made_it(trip):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    change(server, trip, "a", ("19:00", "20:00"), "把第一天的晚饭改到 19:00。")
    change(server, trip, "b", ("18:00", "19:00"), "还是 18:00 吧。")
    turn = call(server, "turn", text="撤回", scenario="replan", speaker="p1")
    listed = turn["versions"]
    assert [entry["number"] for entry in listed] == [3, 2, 1]  # newest first
    assert listed[0]["said"] == "还是 18:00 吧。" and "d1-dinner 19:00→18:00" in listed[0]["change"]
    assert listed[1]["said"] == "把第一天的晚饭改到 19:00。" and "d1-dinner 17:00→19:00" in listed[1]["change"]
    assert listed[2]["change"] == "第一版"
    assert all(entry["at"][:4] == "2030" and len(entry["at"]) == 16 for entry in listed)
    assert trip.versions.current("alice")["number"] == 3  # the words changed nothing
    assert "undo" not in turn


def test_the_list_holds_five_versions_and_stays_small(trip):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    for index, start in enumerate(["18:00", "18:30", "19:00", "19:30", "20:00", "20:30"]):
        hour, minute = start.split(":")
        change(server, trip, f"k{index}", (start, f"{int(hour) + 1:02d}:{minute}"), f"晚饭改到 {start}，要在海边那家，他们说晚上人少而且有露台可以看日落。")
    turn = call(server, "turn", text="撤回", scenario="replan", speaker="p1")
    assert [entry["number"] for entry in turn["versions"]] == [7, 6, 5, 4, 3]
    assert all(len(entry["said"]) <= version_brief.SAID_LIMIT and len(entry["change"]) <= version_brief.CHANGE_LIMIT for entry in turn["versions"])
    assert len(json.dumps(turn["versions"], ensure_ascii=False)) <= 1100  # measured, see the handback


def test_a_restored_version_says_so_and_how_many_entries_went_with_it(trip):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    chatgpt_move(server, trip)
    turn = call(server, "turn", text="撤回", scenario="replan", speaker="p1")
    call(server, "propose", ctx_id=turn["ctx_id"], restore_version=1)
    later = call(server, "turn", text="撤回", scenario="replan", speaker="p1")
    newest = later["versions"][0]
    assert newest["number"] == 4 and newest["restored_from"] == 1 and newest["taken_back"] == 1
    assert newest["said"] == "撤回到第 1 版"


def test_the_reason_the_proposal_gave_is_the_change_line(trip):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    turn = call(server, "turn", text="晚饭晚一点。", scenario="replan", speaker="p1")
    draft = call(server, "propose", ctx_id=turn["ctx_id"], changes=dict(why="用户嫌 17:00 太早，晚饭挪到 19:00"),
                 days=[day_with(trip, d1_dinner=("19:00", "20:00"), d1_coffee=("20:30", "21:30"))])
    call(server, "commit", draft_id=draft["draft_id"], accept_unverified=True)
    assert call(server, "turn", text="好", scenario="replan", speaker="p1")["versions"][0]["change"] == "用户嫌 17:00 太早，晚饭挪到 19:00"


def test_a_plain_change_gets_the_same_next_as_before(trip):
    """Nothing about an undo comes before the first instruction of an ordinary change any more or less than before."""
    server = create_server(trip, local_user="alice", manage_jobs=False)
    turn = call(server, "turn", text="把第一天的晚饭改到 19:00。", scenario="replan", speaker="p1")
    assert "撤回" not in turn["next"] and "undo" not in turn["next"]


def test_the_flow_lines_carry_the_undo_sentence_in_both_languages():
    for language in ("zh", "en"):
        for mode in ("quick", "plan"):
            assert UNDO_SHORT[language] in SHORT[language][mode]
        for mode in ("quick", "quick_road", "plan"):
            assert UNDO_HOST[language] in FULL[language][mode]
    assert "restore_version=N" in UNDO_HOST["zh"] and "拿不准就问一句" in UNDO_HOST["zh"]
    assert "ask one question naming both versions" in UNDO_HOST["en"]


# --- propose(restore_version) does it in one call ----------------------------------------------------------------------

def test_propose_with_restore_version_saves_in_one_call_and_says_what_to_tell_the_user(trip):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    chatgpt_move(server, trip)
    turn = call(server, "turn", text="撤回", scenario="replan", speaker="p1")
    answer = call(server, "propose", ctx_id=turn["ctx_id"], restore_version=1)
    assert answer["status"] == "committed" and answer["version"] == 4 and dinner(trip) == "17:00"
    assert answer["undo"]["restore_version"] == 1 and answer["undo"]["say"].startswith("已撤回：计划回到第 1 版。")
    assert "不要再 commit" in answer["next"]
    assert trip.versions.current("alice")["number"] == 4 and trip.store.get("versions", "alice", trip.versions.current("alice")["id"])["restored_from"] == 1


def test_the_default_key_is_the_context_and_the_version_and_another_version_is_a_new_undo(trip):
    """The same ctx_id with another N is the host changing its mind: a new version, not a refusal."""
    server = create_server(trip, local_user="alice", manage_jobs=False)
    change(server, trip, "a", ("19:00", "20:00"), "把第一天的晚饭改到 19:00。")
    change(server, trip, "b", ("20:00", "21:00"), "再晚一点。")
    turn = call(server, "turn", text="撤回", scenario="replan", speaker="p1")
    first = call(server, "propose", ctx_id=turn["ctx_id"], restore_version=2)
    assert first["version"] == 4 and dinner(trip) == "19:00"
    changed = call(server, "propose", ctx_id=turn["ctx_id"], restore_version=1)
    assert changed["version"] == 5 and dinner(trip) == "17:00"
    # back to 2 once more after 1 was saved: the host changed its mind again, so the plan goes to 2 in a new version (the
    # 2026-11-28 review: answering with version 4 said "back to version 2" while the plan stood at 1); sent again it is
    # the same call and makes nothing new
    again = call(server, "propose", ctx_id=turn["ctx_id"], restore_version=2)
    assert again["version"] == 6 and dinner(trip) == "19:00"
    retried = call(server, "propose", ctx_id=turn["ctx_id"], restore_version=2)
    assert retried["version"] == 6 and trip.versions.current("alice")["number"] == 6
    drafts = [draft["idempotency_key"] for draft in trip.store.all("drafts", "alice") if draft.get("restored_from") is not None]
    assert f"restore:{turn['ctx_id']}:2" in drafts and f"restore:{turn['ctx_id']}:1" in drafts


def test_an_old_host_that_commits_the_saved_draft_gets_the_same_version(trip):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    chatgpt_move(server, trip)
    turn = call(server, "turn", text="撤回", scenario="replan", speaker="p1")
    answer = call(server, "propose", ctx_id=turn["ctx_id"], restore_version=1)
    version = call(server, "commit", draft_id=answer["draft_id"])
    assert version["number"] == answer["version"] and version["accepted"] and "已撤回" in version["tell_user"]["say"]
    assert [v["number"] for v in trip.store.all("versions", "alice")] == [1, 2, 3, 4]


def test_a_requirement_only_taken_back_is_an_ordinary_change_not_a_version_back(trip):
    """“撤回那条‘晚饭必须 19:00’”: one record goes, the plan and the other versions stay."""
    server = create_server(trip, local_user="alice", manage_jobs=False)
    lock = older_lock(server, trip)
    before = trip.versions.current("alice")["number"]
    withdraw_on(server, trip, "撤掉晚饭 19:00 那条。", lock)
    current = trip.versions.current("alice")
    assert trip.store.get("decisions", "alice", lock["id"])["status"] == "superseded"
    assert current["number"] == before + 1 and current.get("restored_from") is None


# --- 2.5: going forward again brings back what only an undo took back ---------------------------------------------------

def test_going_forward_to_a_version_an_undo_passed_brings_its_entries_back(trip):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    chatgpt_move(server, trip)  # version 2 holds the must, version 3 the plan with dinner at 19:00
    lock = next(d for d in trip.store.all("decisions", "alice") if d.get("question") == "晚饭时间")
    turn = call(server, "turn", text="撤回", scenario="replan", speaker="p1")
    call(server, "propose", ctx_id=turn["ctx_id"], restore_version=1)
    assert trip.store.get("decisions", "alice", lock["id"])["status"] == "superseded" and dinner(trip) == "17:00"
    # the user says they want what was taken back: the host goes to version 3
    turn = call(server, "turn", text="啊刚才那版其实是对的，撤回。", scenario="replan", speaker="p1")
    back = call(server, "propose", ctx_id=turn["ctx_id"], restore_version=3)
    restored = trip.store.get("decisions", "alice", lock["id"])
    assert restored["status"] == "active" and "withdrawn" not in restored and dinner(trip) == "19:00"
    assert [entry["id"] for entry in back["undo"]["put_back"]] == [lock["id"]]
    assert RESTORED_SAY["zh"]["back"].format(what="晚饭时间：19:00") in back["undo"]["say"]
    assert "随撤回一起撤掉" not in back["undo"]["say"]


def test_going_back_to_a_version_made_by_an_undo_keeps_out_what_that_undo_took_back(trip):
    """2026-11-28 review: v2, v3 record a must and move dinner; v4 is an undo to v1 that takes the must back; v5 moves dinner
    again. Going back to v4 restores a version at which the must was not in force: it stays taken back, and the answer does
    not say it came back."""
    server = create_server(trip, local_user="alice", manage_jobs=False)
    chatgpt_move(server, trip)
    lock = next(d for d in trip.store.all("decisions", "alice") if d.get("question") == "晚饭时间")
    turn = call(server, "turn", text="撤回", scenario="replan", speaker="p1")
    call(server, "propose", ctx_id=turn["ctx_id"], restore_version=1)
    assert trip.store.get("decisions", "alice", lock["id"])["status"] == "superseded"
    change(server, trip, "m2", ("18:00", "19:00"), "晚饭改 18:00。")
    turn = call(server, "turn", text="撤回", scenario="replan", speaker="p1")
    back = call(server, "propose", ctx_id=turn["ctx_id"], restore_version=4)
    assert back["status"] == "committed"
    assert trip.store.get("decisions", "alice", lock["id"])["status"] == "superseded"
    assert "put_back" not in back["undo"]


def test_one_message_that_undoes_and_then_changes_keeps_out_what_its_undo_took_back(trip):
    """2026-11-28 review, second pass: one message makes an undo version and then another change; going back to the undo's
    version later restores a version at which the must was taken back, so it stays out."""
    server = create_server(trip, local_user="alice", manage_jobs=False)
    chatgpt_move(server, trip)
    lock = next(d for d in trip.store.all("decisions", "alice") if d.get("question") == "晚饭时间")
    turn = call(server, "turn", text="撤回，然后晚饭改 18:00", scenario="replan", speaker="p1")
    undo = call(server, "propose", ctx_id=turn["ctx_id"], restore_version=1)
    draft = call(server, "propose", ctx_id=turn["ctx_id"], days=[day_with(trip, d1_dinner=("18:00", "19:00"), d1_coffee=("21:30", "22:30"))])
    assert draft["status"] == "proposed", draft
    call(server, "commit", draft_id=draft["draft_id"], accept_unverified=True)
    turn = call(server, "turn", text="撤回", scenario="replan", speaker="p1")
    back = call(server, "propose", ctx_id=turn["ctx_id"], restore_version=undo["version"])
    assert trip.store.get("decisions", "alice", lock["id"])["status"] == "superseded"
    assert "put_back" not in back["undo"]


def test_taken_back_put_back_and_taken_back_again_follows_the_version_gone_back_to(trip):
    """2026-11-28 review, second pass: back to 1 (the must goes), forward to 3 (it comes back), back to 1 again (it goes);
    going back to that last version keeps it out, going back to the middle one brings it in."""
    server = create_server(trip, local_user="alice", manage_jobs=False)
    chatgpt_move(server, trip)
    lock = next(d for d in trip.store.all("decisions", "alice") if d.get("question") == "晚饭时间")
    turn = call(server, "turn", text="撤回", scenario="replan", speaker="p1")
    call(server, "propose", ctx_id=turn["ctx_id"], restore_version=1)
    middle = call(server, "propose", ctx_id=turn["ctx_id"], restore_version=3)
    assert trip.store.get("decisions", "alice", lock["id"])["status"] == "active"
    last = call(server, "propose", ctx_id=turn["ctx_id"], restore_version=1)
    assert trip.store.get("decisions", "alice", lock["id"])["status"] == "superseded"
    turn = call(server, "turn", text="撤回", scenario="replan", speaker="p1")
    call(server, "propose", ctx_id=turn["ctx_id"], restore_version=last["version"])
    assert trip.store.get("decisions", "alice", lock["id"])["status"] == "superseded"
    turn = call(server, "turn", text="不对，回到刚才晚饭 19:00 必须的那版", scenario="replan", speaker="p1")
    call(server, "propose", ctx_id=turn["ctx_id"], restore_version=middle["version"])
    assert trip.store.get("decisions", "alice", lock["id"])["status"] == "active"


def test_what_the_user_took_back_with_their_own_words_stays_out(trip):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    lock = older_lock(server, trip)
    withdraw_on(server, trip, "撤掉晚饭 19:00 那条。", lock)  # the user's own words, a version of their own
    change(server, trip, "a", ("19:00", "20:00"), "把第一天的晚饭改到 19:00。")
    turn = call(server, "turn", text="撤回", scenario="replan", speaker="p1")
    back = call(server, "propose", ctx_id=turn["ctx_id"], restore_version=3)
    assert trip.store.get("decisions", "alice", lock["id"])["status"] == "superseded"
    assert "put_back" not in back["undo"]


def test_a_host_cannot_ask_for_entries_to_come_back_or_mark_its_withdrawals_as_the_service_s(trip):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    chatgpt_move(server, trip)
    lock = next(d for d in trip.store.all("decisions", "alice") if d.get("question") == "晚饭时间")
    turn = call(server, "turn", text="撤回", scenario="replan", speaker="p1")
    call(server, "propose", ctx_id=turn["ctx_id"], restore_version=1)
    turn = call(server, "turn", text="好", scenario="replan", speaker="p1")
    ledger = call(server, "propose", ctx_id=turn["ctx_id"], changes=dict(reinstate=[dict(id=lock["id"], reason="x")]))
    call(server, "commit", draft_id=ledger["draft_id"])
    assert trip.store.get("decisions", "alice", lock["id"])["status"] == "superseded"


# --- the list itself --------------------------------------------------------------------------------------------------

def test_the_sentence_names_a_block_by_its_words_and_counts_what_it_does_not_describe():
    before = dict(id="t", places=[], days=[dict(date="2030-06-10", plans=[dict(tracks=[dict(subject=["p1"], blocks=[
        dict(id="d1-dinner", text="晚饭", start="17:00", end="18:00", kind="meal")])])])])
    after = json.loads(json.dumps(before))
    block = after["days"][0]["plans"][0]["tracks"][0]["blocks"][0]
    block["start"] = "19:00"
    assert version_brief.change_sentence(before, after, "zh") == "晚饭 17:00→19:00"
    block["end"] = "20:00"  # moved as a whole: the start says it
    assert version_brief.change_sentence(before, after, "en") == "晚饭 17:00→19:00"
    block["end"] = "20:30"
    assert version_brief.change_sentence(before, after, "en") == "晚饭 17:00-18:00→19:00-20:30"
    block["kind"] = "visit"
    assert version_brief.change_sentence(before, after, "en").endswith("and 1 more")
    assert version_brief.change_sentence(before, before, "zh") == "计划没有变化"
    assert version_brief.cut("x" * 200, 80).endswith("…") and len(version_brief.cut("x" * 200, 80)) == 80
