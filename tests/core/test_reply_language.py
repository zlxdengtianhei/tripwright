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
