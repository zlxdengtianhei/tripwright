"""Q19 (2026-11-23): the user's Git repository is the database; the core is finding the right entries in it, keeping
what is looked up (with its source, once), and handing a conflict found after the plan was decided to the user as one
question.

Before: `query(kind="saved")` needed every space-separated word verbatim, so a Chinese phrase or a few words found
nothing (11 of 17 host calls in the 11-22 replays), the turn never brought up an earlier lookup, the same page and
quote could be recorded again, and nothing stored a conflict as a question for the user."""
import asyncio
import time

import pytest

from tripwright import recall


def seeded(service, plan, monkeypatch, when="2030-06-10T09:00:00+02:00"):
    monkeypatch.setenv("TRIPWRIGHT_NOW", when)
    monkeypatch.setenv("TRIPWRIGHT_NOW_SET_AT", str(time.time()))
    turn = service.turn("alice", "创建示例行程。", "seed")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=plan, idempotency_key="seed")
    service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)
    return service


def saved(service, text):
    return asyncio.run(service.query("alice", "saved", dict(text=text)))["evidence"]["value"]


def web(service, **arguments):
    return asyncio.run(service.query("alice", "web_evidence", arguments))


def test_a_chinese_sentence_finds_the_place_and_the_web_check_it_names(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    web(service, url="https://example.org/gallery", quote="Open daily 10-18", claim="奥斯陆虚构画廊每天 10:00–18:00 开", type="opening_hours")
    found = saved(service, "画廊今天几点关门")
    ids = [value.get("id") for value in found]
    assert "oslo-art" in ids and any(value.get("found") == "web" for value in found)
    assert all(value["source"] for value in found)  # every hit says where it comes from
    # the old all-words match found nothing for a few words that are not all in one record
    assert saved(service, "gallery opening hours") and saved(service, "nothing-stored-like-this") == []


def test_words_in_two_languages_meet_through_the_travel_table(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    web(service, url="https://example.org/station", quote="Lockers at Oslo S", claim="Oslo S has luggage lockers", type="place")
    [hit] = [value for value in saved(service, "车站 行李寄存") if value.get("found") == "web"]
    assert "luggage" in hit["why"]


def test_the_turn_brings_up_what_the_repository_holds_without_this_messages_own_records(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    web(service, url="https://example.org/cafe", quote="Kitchen closes 21:00", claim="港边咖啡馆厨房 21:00 停止点餐", type="opening_hours")
    turn = service.turn("alice", "港边咖啡馆今晚几点停止点餐？", "q1", scenario="quick")
    found = turn["recall"]["found"]
    assert any(entry["found"] == "web" and "21:00" in entry["text"] for entry in found)
    assert all(entry["source"] for entry in found)
    assert any(line.get("kind") == "recall" for line in turn["opening_material"])
    assert "recall" in turn["after_first_part"] and "query(kind='conflict'" in turn["after_first_part"]


def test_superseded_entries_come_after_those_in_force_and_say_so(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    entries = recall.corpus(service.store, "alice", service.versions.current("alice")["plan"],
                            at=recall.aware_utc("2030-06-10T07:00:00+00:00"), zone="Europe/Oslo", language="zh")
    assert all("haystack" in entry and entry["source"] for entry in entries)


def test_the_same_page_and_quote_are_recorded_once(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    first = web(service, url="https://example.org/ferry", quote="Single ticket 4 EUR, cash only", claim="渡轮单程 4 欧，只收现金", type="price")
    again = web(service, url="https://www.example.org/ferry/", quote="Single ticket 4 EUR", claim="渡轮单程 4 欧", type="price")
    assert again["reused"] is True and again["evidence"]["id"] == first["evidence"]["id"]
    other = web(service, url="https://example.org/ferry", quote="Return ticket 8 EUR", claim="往返 8 欧", type="price")
    assert "reused" not in other
    assert len([record for record in service.store.all("evidence", "alice") if record.get("kind") == "web"]) == 2


def test_a_conflict_after_the_plan_was_decided_is_one_stored_question_and_the_next_message_answers_it(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    check = web(service, url="https://example.org/gallery", quote="Closed on 10 June", claim="画廊 6 月 10 日闭馆", type="opening_hours")
    result = asyncio.run(service.query("alice", "conflict", dict(
        against=dict(date="2030-06-10", block_id="d1-art"), found="画廊 6 月 10 日闭馆", evidence_id=check["evidence"]["id"],
        options=["照原计划去，到门口再看", "改去奥斯陆虚构公园", "下午再说"], recommended=1, why="公园在同一区，不用改别的")))
    assert result["status"] == "recorded" and "②" in result["say"] and "推荐②" in result["say"] and "06-10 10:00" in result["say"]
    assert "不回话就先照原计划" in result["say"]
    [question] = [q for q in service.store.all("undecided", "alice") if q.get("kind") == "after_decision"]
    assert question["status"] == "open" and question["conflict"]["evidence_ids"] == [check["evidence"]["id"]]
    # asked once: the same finding again is the same question
    again = asyncio.run(service.query("alice", "conflict", dict(
        against=dict(date="2030-06-10", block_id="d1-art"), found="画廊 6 月 10 日闭馆", options=["a", "b"], recommended=0, why="x")))
    assert again["reused"] is True and again["id"] == question["id"]
    monkeypatch.setenv("TRIPWRIGHT_NOW", "2030-06-10T09:10:00+02:00")
    monkeypatch.setenv("TRIPWRIGHT_NOW_SET_AT", str(time.time()))
    answer = service.turn("alice", "那就去公园吧", "a1", scenario="replan")
    assert answer["decided"][0]["id"] == question["id"] and answer["next"].startswith("这句话是在回之前那一问")
    assert service.store.get("undecided", "alice", question["id"])["status"] == "answered"
    later = service.turn("alice", "公园附近有什么吃的？", "a2", scenario="quick")
    assert "decided" not in later


def test_a_conflict_must_name_a_decided_thing_and_two_or_three_options(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    with pytest.raises(ValueError, match="not in the current plan"):
        asyncio.run(service.query("alice", "conflict", dict(against=dict(block_id="nope"), found="x", options=["a", "b"], recommended=0, why="y")))
    with pytest.raises(ValueError, match="two or three"):
        asyncio.run(service.query("alice", "conflict", dict(against=dict(block_id="d1-art"), found="x", options=["a"], recommended=0, why="y")))
    with pytest.raises(ValueError, match="exactly one"):
        asyncio.run(service.query("alice", "conflict", dict(against=dict(block_id="d1-art", anchor_id="z"), found="x",
                                                            options=["a", "b"], recommended=0, why="y")))
