"""Service-written reply text follows the latest message, not the language of stored trip data."""
from __future__ import annotations

import asyncio
import re

from tripwright.query import QueryRouter

HAN = re.compile(r"[\u4e00-\u9fff]")
ORIGIN = "59.9139,10.7522"
DESTINATION = "59.9100,10.7500"


def synthetic(service):
    service.query_router = QueryRouter(service.store, mode="synthetic")
    return service


def query(service, kind="transit"):
    arguments = dict(origin=ORIGIN, destination=DESTINATION)
    if kind == "transit":
        arguments["when"] = "2030-06-10T17:00:00+02:00"
    return asyncio.run(service.query("alice", kind, arguments))


def service_sentences(result):
    found = [*result.get("sentences", []), result.get("summary"), (result.get("how_to_check") or {}).get("steps")]
    return [text for text in found if text]


def test_english_turn_writes_now_pack_next_ask_and_offer_in_english(seeded):
    result = seeded.turn("alice", "What should we do today?", "english-turn")

    assert not HAN.search(result["now"]["text"])
    assert not HAN.search(result["pack"]["markdown"].splitlines()[0])
    assert not HAN.search(result["next"])
    assert result["ask"] and all(not HAN.search(question["say"]) for question in result["ask"])
    assert not HAN.search(result["offer"]["say"])
    assert seeded.store.get("contexts", "alice", result["ctx_id"])["language"] == "en"


def test_transit_and_latest_departure_after_an_english_turn_are_english(seeded):
    synthetic(seeded).turn("alice", "Check the transport.", "english-query")

    for kind in ("transit", "latest_departure"):
        result = query(seeded, kind)
        assert service_sentences(result), kind
        assert all(not HAN.search(sentence) for sentence in service_sentences(result)), kind


def test_transit_and_latest_departure_after_a_chinese_turn_remain_chinese(seeded):
    synthetic(seeded).turn("alice", "查一下交通。", "chinese-query")

    for kind in ("transit", "latest_departure"):
        result = query(seeded, kind)
        assert all(HAN.search(sentence) for sentence in service_sentences(result)), kind


def test_each_turn_changes_the_language_of_the_next_query(seeded):
    synthetic(seeded)
    seeded.turn("alice", "先按计划看。", "first-chinese")
    seeded.turn("alice", "Now answer in English.", "then-english")
    assert all(not HAN.search(sentence) for sentence in service_sentences(query(seeded)))

    seeded.turn("alice", "再换回中文。", "chinese-again")
    assert all(HAN.search(sentence) for sentence in service_sentences(query(seeded)))


def test_query_without_a_context_or_with_a_legacy_context_uses_the_default_language(service):
    synthetic(service)
    assert all(HAN.search(sentence) for sentence in service_sentences(query(service)))

    result = service.turn("alice", "An English message.", "legacy")
    context = service.store.get("contexts", "alice", result["ctx_id"])
    context.pop("language", None)
    service.store.put("contexts", "alice", context)
    assert all(HAN.search(sentence) for sentence in service_sentences(query(service)))


def test_the_latest_context_of_one_moment_is_the_last_written(seeded):
    newest = seeded.store.all("contexts", "alice")[-1]
    seeded.store.put("contexts", "alice", dict(newest, id="CTX-tie-zh", language="zh"), insert=True)
    seeded.store.put("contexts", "alice", dict(newest, id="CTX-tie-en", language="en"), insert=True)

    assert seeded._reply_language("alice") == "en"


def test_the_reply_language_read_decodes_one_record(seeded, monkeypatch):
    import json

    seeded.turn("alice", "再问一句。", "second")
    calls = []
    real_loads = json.loads
    monkeypatch.setattr(json, "loads", lambda payload: (calls.append(payload), real_loads(payload))[1])

    assert seeded._reply_language("alice") == "zh"
    assert len(calls) == 1  # the latest context only, not every record of the user


def test_the_first_text_read_decodes_one_record(seeded, monkeypatch):
    import json

    monkeypatch.setenv("TRIPWRIGHT_REPLY_FLOW", "one")
    place = seeded.state("alice", "plan")["plan"]["places"][0]
    points = [dict(kind="do", said=place["name_local"], place=place["id"])]
    seeded.turn("alice", "What about this place?", scenario="quick", points=points)
    seeded.turn("alice", "Anything else?", scenario="quick", points=[dict(kind="other", said="anything else")])
    calls = []
    real_loads = json.loads
    monkeypatch.setattr(json, "loads", lambda payload: (calls.append(payload), real_loads(payload))[1])

    assert seeded._answer_instruction("alice")["text"]
    assert len(calls) == 2  # the latest context holding first_text, then its source event


def test_memory_and_explain_labels_after_an_english_turn_are_english(seeded, item):
    record_id = seeded.ledger.apply("alice", {"items": [item]})["changed"][0]
    seeded.turn("alice", "Show my memory.", "english-memory")

    memory = seeded.state("alice", view="memory")
    assert all(not HAN.search(region["name"]) for region in memory["sources"]["regions"])
    assert all(not HAN.search(entry["text"]) for entry in memory["sources"]["entries"])
    assert seeded.explain("alice", record_id=record_id)["record"]["layer"] == "user said"


def test_draft_first_part_uses_its_message_language_without_a_turn(seeded):
    result = seeded.draft_first_part("alice", "Give me the first part.", points=[])

    assert not HAN.search(result["now"]["text"])
    assert not HAN.search(result["next"])


def test_next_opens_with_the_reply_language_for_an_english_message_and_never_for_a_chinese_one(seeded):
    english = seeded.turn("alice", "What should we do today?", "english-next")
    chinese = seeded.turn("alice", "今天做什么？", "chinese-next")

    assert english["next"].startswith("Reply in English: translate the stored text you use; keep place names as written.")
    assert "Reply in English" not in chinese["next"] and "Reply in English" not in str(chinese.get("after_first_part") or "")


# --- the first part of an English answer on a trip stored in Chinese ------------------------------------------------------
def chinese_trip(service, plan, monkeypatch, anchor_text="值机柜台关闭（虚构）"):
    """The demo day with one stored key time in Chinese, on a clock at noon of that day."""
    import time

    monkeypatch.setenv("TRIPWRIGHT_NOW", "2030-06-10T12:00:00+02:00")
    monkeypatch.setenv("TRIPWRIGHT_NOW_SET_AT", str(time.time()))
    plan["days"][0]["anchors"] = [dict(id="desk", time="21:30", text=anchor_text, subject=["p1", "p2", "p3"], place="oslo-cafe")]
    turn = service.turn("alice", "创建示例行程。", "seed")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=plan, idempotency_key="seed")
    service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)
    return service


def gallery(service, text, said):
    return service.turn("alice", text, None, scenario="quick", points=[dict(kind="do", said=said, place="oslo-art")])


def test_an_english_first_part_names_a_stored_place_by_its_local_name_and_a_chinese_one_by_its_chinese_name(service, plan, monkeypatch):
    chinese_trip(service, plan, monkeypatch)

    english = gallery(service, "Is the gallery open now?", "the gallery")["first_part"]["lines"][1]
    chinese = gallery(service, "画廊现在开着吗？", "画廊")["first_part"]["lines"][1]

    assert english == "the gallery: Example Oslo Gallery, hours not stored (stored)"
    assert chinese == "画廊：奥斯陆虚构画廊，营业时间没存（存的）"


def test_a_first_part_with_stored_chinese_is_sent_in_english_and_any_other_first_part_as_it_is(service, plan, monkeypatch):
    from tripwright.service import SEND_AND_END

    chinese_trip(service, plan, monkeypatch)
    english = gallery(service, "Is the gallery open now?", "the gallery")
    chinese = gallery(service, "画廊现在开着吗？", "画廊")

    assert HAN.search(english["first_part"]["text"])  # the stored key time keeps its Chinese words
    assert english["next"].startswith("Send first_part.text in English (translate its stored text; keep names, times and "
                                      "numbers as written) as this reply's first and only message")
    assert "as it is" not in english["next"]
    assert chinese["next"] == SEND_AND_END["zh"]


def test_an_english_first_part_without_stored_chinese_is_sent_as_it_is(service, plan, monkeypatch):
    from tripwright.service import SEND_AND_END

    chinese_trip(service, plan, monkeypatch, anchor_text="Check-in desk closes (fictional)")
    english = gallery(service, "Is the gallery open now?", "the gallery")

    assert not HAN.search(english["first_part"]["text"])
    assert english["next"] == SEND_AND_END["en"]
