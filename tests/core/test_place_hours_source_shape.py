"""A place whose hours_source is not a mapping is refused by name, not with an unexpected server error (2026-11-12).

Claude Code (Sonnet), storing places from guide screenshots, sent `hours_source` as a sentence ("小红书截图 IMG_4101（未核实）")
in 2 runs whose tool calls were recorded; 2 earlier runs failed the same way without a record. propose then raised
AttributeError while looking for a cited evidence ID, the host got only "Unexpected server error in propose
(AttributeError)", retried once and stored nothing."""
import time

import pytest


def test_hours_source_as_a_sentence_is_named_in_the_refusal(service, plan, monkeypatch):
    monkeypatch.setenv("TRIPWRIGHT_NOW", "2030-06-09T20:00:00+02:00")
    monkeypatch.setenv("TRIPWRIGHT_NOW_SET_AT", str(time.time()))
    turn = service.turn("alice", "创建示例行程。", "seed")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=plan, idempotency_key="seed")
    service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)
    turn = service.turn("alice", "【截图】Kaffe Lille 早上 8 点开门", "shot", nature="screenshot_extraction")
    place = dict(id="kaffe-lille", name="Kaffe Lille", name_local="Kaffe Lille", area="Oslo", hours_source="小红书截图 IMG_4102")
    with pytest.raises(ValueError, match=r"places\.\d+\.hours_source"):
        service.propose("alice", ctx_id=turn["ctx_id"], base_version=1, places=[place], idempotency_key="shot")
