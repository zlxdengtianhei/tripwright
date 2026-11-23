"""Retrieval fixes (2026-11-23, after the first review of PR #5): the words of a Chinese question, the general place
names, one question at a time, and no name of the example trip in the word table.

Before: a Chinese message was cut into character pairs, so a long message made hundreds of pairs — most meaningless —
and a precise short question ("圣沃尔夫冈 民宿 车票") was dropped by the half-coverage rule because the place is spelled
differently in the repository; a foreign city in Chinese never met its own spelling in an entry (慕尼黑 vs Munich); two
conflict questions could be open at once, and the next message marked both answered, so the agent applied a default to
a question the user never answered; `recall.yaml` still named the example trip's city and operator (wien, vienna,
boulderbar) and its Halloween activity.
"""
import asyncio
import time
from tripwright import recall
from tripwright.example_trip import reserved_names


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


def test_chinese_is_cut_into_words_not_only_character_pairs():
    words = recall.tokens("明天要是下雨就去博物馆")
    assert {"明天", "下雨", "博物馆"} <= words
    assert "天下" not in words and "物馆" not in words  # the pairs the old cut made, which matched by accident


def test_a_short_question_about_a_topic_finds_the_entry_even_when_the_place_is_spelled_differently(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    web(service, url="https://example.org/region-ticket", quote="One ticket covers buses and trains in the lake region",
        claim="湖区的一张区域车票可以坐公交和火车，26.50 欧", type="price")
    # the repository spells the place St. Wolfgang, the question says 圣沃尔夫冈; 民宿 and 车票 are the topics it shares
    found = saved(service, "圣沃尔夫冈 民宿 车票")
    assert any(value.get("found") == "web" and "区域车票" in value.get("claim", "") for value in found)


def test_a_general_place_name_meets_its_local_spelling(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    web(service, url="https://example.org/english-garden", quote="Der Englische Garten ist frei",
        claim="慕尼黑英国花园免费进入", type="price")
    [hit] = [value for value in saved(service, "慕尼黑 花园 门票") if value.get("found") == "web"]
    assert "name:munich" in hit["why"]
    # the other way round too: a Chinese entry answers a question that spells the city in Latin letters
    [other] = [value for value in saved(service, "Munich English Garden ticket price") if value.get("found") == "web"]
    assert other["id"] == hit["id"] and "name:munich" in other["why"]


def test_a_date_narrows_but_does_not_rank(service, plan, monkeypatch):
    assert recall.scale("§day:06-10") == 0.0  # a date narrows the search; it never lifts one entry over another
    seeded(service, plan, monkeypatch)
    web(service, url="https://example.org/art", quote="Open daily 10-18", claim="奥斯陆虚构画廊每天 10:00–18:00 开", type="opening_hours")
    dated = recall.search(service.store, "alice", service.versions.current("alice")["plan"], "06-10 画廊开门",
                          at=recall.aware_utc("2030-06-10T07:00:00+00:00"), zone="Europe/Oslo", language="zh", limit=8)
    assert "day:06-10" in dated["terms"] and any(hit["id"].startswith("EV-") for hit in dated["found"])


def test_two_open_questions_answer_only_the_one_answered_and_ask_the_other_after(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    check = web(service, url="https://example.org/gallery", quote="Closed on 10 June", claim="画廊 6 月 10 日闭馆", type="opening_hours")
    first = asyncio.run(service.query("alice", "conflict", dict(
        against=dict(date="2030-06-10", block_id="d1-art"), found="画廊 6 月 10 日闭馆", evidence_id=check["evidence"]["id"],
        options=["照原计划去，到门口再看", "改去奥斯陆虚构公园"], recommended=1, why="公园在同一区")))
    second = asyncio.run(service.query("alice", "conflict", dict(
        against=dict(date="2030-06-10", block_id="d1-coffee"), found="咖啡馆当天满座，订不到位",
        options=["改去车站那家", "不去了"], recommended=0, why="车站那家走路十分钟")))
    # only the first is open; the second waits its turn and is not asked yet
    assert first["ask_now"] is True and first["queued"] is False
    assert second["ask_now"] is False and second["queued"] is True and second["queued_after"] == first["id"]
    assert "先别问" in second["next"]
    monkeypatch.setenv("TRIPWRIGHT_NOW", "2030-06-10T09:10:00+02:00")
    monkeypatch.setenv("TRIPWRIGHT_NOW_SET_AT", str(time.time()))
    answer = service.turn("alice", "那就去公园吧，别去画廊了", "a1", scenario="replan")
    # the message answered the gallery question only; the coffee question is now asked, not settled
    assert [question["id"] for question in answer["decided"]] == [first["id"]]
    assert [question["id"] for question in answer["ask"] if question["id"] == second["id"]] == [second["id"]]
    assert "这一问现在问" in answer["next"]
    statuses = {question["id"]: question["status"] for question in service.store.all("undecided", "alice")}
    assert statuses[first["id"]] == "answered" and statuses[second["id"]] == "open"
    # the next message answers the coffee question and nothing else
    later = service.turn("alice", "改去车站那家", "a2", scenario="replan")
    assert [question["id"] for question in later["decided"]] == [second["id"]]
    assert not later.get("ask")


def test_the_word_table_holds_no_place_or_operator_of_the_example_trip():
    # one list for the generator and both guards: the plan's places and areas plus the destinations the trip's own
    # words and entries name (Hallstatt, Sankt Wolfgang, Bad Ischl, Gosau, its two cities)
    names = set(reserved_names())
    # generic words an address happens to carry name nothing of the trip (a main station, a street)
    generic = {"hauptbahnhof", "bahnhof", "station", "flughafen", "airport", "gasse", "strasse", "platz", "kirche", "hbf"}
    table = recall.table()
    forms = {recall.fold(form) for group in [*table["groups"].values(), *((table.get("names") or {}).values()),
                                             table["today"]["zh"], table["today"]["en"], table["tomorrow"]["zh"],
                                             table["tomorrow"]["en"]] for form in group}
    forms |= {recall.fold(name) for name in table["generic_names"]} | {recall.fold(word) for word in table["stop"]}
    for name in names:
        folded = recall.fold(name)
        if not folded or folded in generic or len(folded) < 3:
            continue
        assert not any(folded == form or folded in form for form in forms), \
            f"the word table holds {name!r}, a place or operator of the example trip"
