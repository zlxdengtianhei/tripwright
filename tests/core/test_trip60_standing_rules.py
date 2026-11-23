"""trip60 (2026-11-22): a standing rule the user says in passing is recorded with the user's words, by the service.

In the replays of the user's 15 real questions, hosts recorded such a sentence (“以后注意终端输出不要用表格的”) in some
runs and not in others, and the user had to say it again or ask why it was not kept (T-09, T-11). The service finds the
clause by its words, with no model, and records it when the host's own record leaves it out; the first message says so.
"""
from __future__ import annotations

import pytest

from tripwright import standing


@pytest.fixture(autouse=True)
def default_mode(monkeypatch):
    monkeypatch.delenv("TRIPWRIGHT_SAVE_UNVERIFIED", raising=False)
    monkeypatch.delenv("TRIPWRIGHT_REPLY_FLOW", raising=False)


@pytest.mark.parametrize("text, clause", [
    ("以后注意终端输出不要用表格的 然后我们现在好像错过了一个车 现在的话怎么走", "以后注意终端输出不要用表格的"),
    ("别 以后的交互应该是你先给我终端输出我现在的走法 然后再去更新网站 现在直接告诉我我们怎么走",
     "以后的交互应该是你先给我终端输出我现在的走法 然后再去更新网站"),
    ("感觉比第一段时间短多了。之后这种可以考虑今晚的先输出出来", "之后这种可以考虑今晚的先输出出来"),
    ("From now on, never use tables. Also, how do I get to the airport?", "From now on, never use tables"),
])
def test_the_clause_runs_from_its_sentence_part_to_the_next_topic(text, clause):
    assert standing.clauses(text) == [clause]


def test_a_long_clause_is_cut_at_its_last_pause_not_mid_word():
    text = ("所以交互应该是你查好了就直接先输出结果在终端，我说不要表格是因为我只能复制完整的表格而不能复制其中的一部分，"
            "我要只复制一个地方就没办法")
    [clause] = standing.clauses(text)
    assert clause == "所以交互应该是你查好了就直接先输出结果在终端，我说不要表格是因为我只能复制完整的表格而不能复制其中的一部分"


@pytest.mark.parametrize("text", [
    "现在去机场怎么走？",
    "因为核验之后时间不一样了，你看看",          # “之后” after a verb is “after X”, not “from now on”
    "以后再订酒店吧",                              # about the trip itself: left to the host
])
def test_no_clause_without_a_lasting_rule(text):
    assert standing.clauses(text) == []


def test_turn_records_the_rule_the_host_left_out_and_the_first_message_says_so(seeded):
    turn = seeded.turn("alice", "以后注意终端输出不要用表格的 然后我们现在怎么去咖啡馆", "t60-standing-1", scenario="quick")
    said = turn["recorded"]["standing"]
    assert [entry["quote"] for entry in said] == ["以后注意终端输出不要用表格的"]
    item = seeded.store.get("items", "alice", said[0]["id"])
    assert item["kind"] == "rule" and item["strength"] == "forbid" and item["long_term"] is True
    assert item["source"]["basis"] == "user_explicit"
    assert "记下了，以后照做：「以后注意终端输出不要用表格的」" in turn["next"]


def test_the_host_s_own_record_of_the_same_words_is_not_doubled(seeded):
    record = dict(items=[dict(kind="preference", key="answer:no-tables", quote="以后注意终端输出不要用表格的",
                              text="回答不用表格", subject=["*"], strength="forbid", source=dict(event="this"))])
    turn = seeded.turn("alice", "以后注意终端输出不要用表格的", "t60-standing-2", scenario="plan", record=record)
    assert "standing" not in turn["recorded"]


def test_a_later_host_record_of_the_same_words_retires_the_service_copy(seeded):
    first = seeded.turn("alice", "以后注意终端输出不要用表格的", "t60-standing-3", scenario="plan")
    [auto] = first["recorded"]["standing"]
    record = dict(items=[dict(kind="preference", key="answer:no-tables", quote="以后注意终端输出不要用表格的",
                              text="回答不用表格", subject=["*"], strength="forbid", source=dict(event="this"))])
    seeded.turn("alice", "以后注意终端输出不要用表格的", "t60-standing-4", scenario="plan", record=record)
    assert seeded.store.get("items", "alice", auto["id"])["status"] == "superseded"


def test_a_host_record_through_propose_and_commit_also_retires_the_service_copy(seeded):
    """Kimi replay (2026-11-22): the host recorded the same rule with propose/commit, and both copies stayed."""
    first = seeded.turn("alice", "以后注意终端输出不要用表格的", "t60-standing-5", scenario="plan")
    [auto] = first["recorded"]["standing"]
    mine = dict(kind="preference", key="answer:no-tables", quote="以后注意终端输出不要用表格的", text="回答不用表格",
                subject=["*"], strength="forbid", source=dict(event=first["receipt"]["event_id"], basis="user_explicit"))
    draft = seeded.propose("alice", ctx_id=first["ctx_id"], base_version=first["base_version"], changes=dict(items=[mine]),
                           idempotency_key="t60-standing-5")
    seeded.commit("alice", draft["draft_id"], "t60-standing-5", accept_unverified=True)
    assert seeded.store.get("items", "alice", auto["id"])["status"] == "superseded"
