"""render_pack: byte-for-byte against the contract samples, budget downgrades and honest headers (CONTRACTS 3.1—3.3)."""
import copy
import re

import pytest
from render_samples import load, text

from tripwright.render.pack import pack_meta, render_pack
from tripwright.render.writing import writing_rules

SAMPLES = ["pack_narrow", "pack_wide", "pack_partial", "pack_unknown", "pack_over_budget", "pack_omitted"]


def expected(name: str, pack: dict) -> str:
    return text(f"{name}.md").replace("{{WRITING_RULES}}", writing_rules(pack["digest"]["writing_rules"]))


@pytest.mark.parametrize("name", SAMPLES)
def test_rendered_pack_matches_the_sample_byte_for_byte(name, sources):
    pack = load(f"{name}.json")
    before = copy.deepcopy(pack)
    out = render_pack(pack, sources=sources)
    assert out["markdown"] == expected(name, pack)
    assert render_pack(pack, sources=sources) == out and pack == before  # pure: same bytes, input untouched
    if name in ("pack_partial", "pack_unknown", "pack_over_budget", "pack_omitted"):
        assert out["required"] == load(f"{name}.required.json")["required"]
        assert out["tokens"] == load(f"{name}.required.json")["tokens"]


@pytest.mark.parametrize("name", SAMPLES)
def test_every_required_id_appears_and_the_writing_rules_are_inlined_once(name, sources):
    pack = load(f"{name}.json")
    markdown = render_pack(pack, sources=sources)["markdown"]
    for group in pack["required"].values():
        for record_id in group["ids"]:
            assert f"[{record_id}]" in markdown
    assert markdown.count(writing_rules("zh")) == 1 and "{{WRITING_RULES}}" not in markdown


def test_a_pack_within_budget_is_never_downgraded(sources):
    out = render_pack(load("pack_narrow.json"), sources=sources)
    assert out["tokens"] <= 3000 and all(group["status"] == "complete" for group in out["required"].values())
    assert "只列编号" not in out["markdown"]


def test_a_tiny_budget_never_drops_hard_conditions_principles_or_hard_nodes(sources):
    pack = load("pack_narrow.json")
    pack["digest"]["budget_tokens"] = 100
    out = render_pack(pack, sources=sources)
    for record_id in ("I-300713-p1n", "I-300713-t2k"):
        assert f"「{pack['records'][record_id]['quote']}」" in out["markdown"]
    assert "最晚 18:05 出发" in out["markdown"] and out["required"]["hard_nodes"]["status"] == "complete"
    assert out["tokens"] > 100


def test_overrun_from_the_fixed_part_is_not_blamed_on_hard_conditions(sources):
    pack = load("pack_narrow.json")
    pack["digest"]["budget_tokens"] = 1000  # the recommended quick-question budget
    header = render_pack(pack, sources=sources)["markdown"].splitlines()[1]
    assert "固定部分超预算" in header and "硬约束过多" not in header


def test_overrun_after_every_drop_names_hard_conditions():
    header = text("pack_over_budget.md").splitlines()[1]
    assert re.search(r"硬约束过多：约 \d+ token，预算 1610", header)


def test_unknown_group_keeps_its_status_and_reason():
    pack = load("pack_unknown.json")
    out = render_pack(pack)
    assert out["required"]["items"]["status"] == "unknown"
    assert f"适用集没定：条目｜{pack['required']['items']['reason']}" in out["markdown"].splitlines()


def test_a_record_without_its_text_is_listed_by_id_and_its_group_is_partial():
    pack = load("pack_narrow.json")
    del pack["records"]["F-300715-n2l"]["text"]
    out = render_pack(pack)
    assert "实况·只列编号（原文按编号取）：[F-300715-n2l]" in out["markdown"]
    assert out["required"]["facts"]["status"] == "partial"


def test_full_messages_without_sources_do_not_claim_verbatim(sources):
    pack = load("pack_wide.json")
    with_sources = render_pack(pack, sources=sources)["markdown"]
    without = render_pack(pack)["markdown"]
    assert "｜逐字｜" in with_sources
    assert "逐字" not in without and "- [E-300715-e05] u1｜「我膝盖有点疼。」" in without.splitlines()


def test_a_restated_message_is_not_labelled_verbatim(sources):
    restated = dict(sources, **{"E-300715-e05": dict(sources["E-300715-e05"], nature="host_restatement")})
    lines = render_pack(load("pack_wide.json"), sources=restated)["markdown"].splitlines()
    assert next(line for line in lines if line.startswith("- [E-300715-e05]")) == \
        "- [E-300715-e05] 07-15 13:08 CEST｜u1｜宿主转述｜「我膝盖有点疼。」"


def v11_quotes(pack: dict, sources: dict) -> dict:
    """The v1.1 shape A compiles: one object per message, in the order the messages arrived."""
    found = {event_id: {"event": event_id, "text": text, "said_at": sources[event_id]["received_at"],
                        "speaker": sources[event_id]["speaker"], "nature": sources[event_id]["nature"]}
             for event_id, text in pack["full_quotes"].items()}
    return dict(sorted(found.items(), key=lambda entry: (entry[1]["said_at"], entry[0])))


def test_v11_full_message_objects_render_like_v1_text_with_sources(sources):
    pack = load("pack_wide.json")
    objects = dict(pack, full_quotes=v11_quotes(pack, sources))
    assert render_pack(objects)["markdown"] == expected("pack_wide", pack)
    # The objects carry the context, so sources passed alongside change nothing.
    assert render_pack(objects, sources={}) == render_pack(objects)


def test_a_message_without_time_speaker_or_nature_says_unknown_and_never_verbatim(sources):
    pack = load("pack_wide.json")
    objects = v11_quotes(pack, sources)
    objects["E-300715-e05"] = dict(objects["E-300715-e05"], said_at=None, speaker=None, nature=None)
    pack["records"] = {record_id: dict(record, speaker=None) if record.get("source_event") == "E-300715-e05" else record
                       for record_id, record in pack["records"].items()}
    lines = render_pack(dict(pack, full_quotes=objects))["markdown"].splitlines()
    assert "- [E-300715-e05] 时刻未知｜说话人未知｜来源性质未知｜「我膝盖有点疼。」" in lines
    # v1 text with sources whose fields are missing (what made the renderer fail before).
    missing = dict(sources, **{"E-300715-e05": {"received_at": None, "speaker": None, "nature": None}})
    lines = render_pack(load("pack_wide.json"), sources=missing)["markdown"].splitlines()
    line = next(line for line in lines if line.startswith("- [E-300715-e05]"))
    assert line == "- [E-300715-e05] 时刻未知｜u1｜来源性质未知｜「我膝盖有点疼。」" and "逐字" not in line


QUESTIONS = {
    "PC-I-300713-s4v-I-300715-e5y": {
        "type": "question", "kind": "possible_contradiction", "question": "「早点回住处」和「晚上去湖边」出自同一条消息：两条都算，还是只算一条？",
        "default": "两条都照用，直到用户说明", "item_ids": ["I-300713-s4v", "I-300715-e5y"], "status": "open",
        "readings": ["两条都成立：早点回住处；晚上去湖边", "只算一条：早点回住处，或 晚上去湖边"]},
    "MEM-2030-07-seenland": {
        "type": "question", "kind": "memory_pending", "question": "长期条目待确认（没答前照用）：I-300715-e5y", "default": "照用",
        "item_ids": ["I-300715-e5y"], "status": "open"},
    "Q-300715-q9x": {
        "type": "question", "kind": "undecided", "question": "晚饭后去河边还是回住处", "default": "先不排", "options": ["河边", "回住处"],
        "item_ids": [], "subject": ["u1"], "scope": {}, "source_event": "E-300715-d04", "speaker": "u1", "status": "open"},
    "Q-300715-r2m": {
        "type": "question", "kind": "undecided", "question": "明早坐哪班车", "default": None, "options": [{"text": "08:10"}, "09:10"],
        "item_ids": [], "subject": ["u1", "u2"], "scope": {}, "source_event": "E-300715-d04", "speaker": "u1", "status": "open"},
}


def with_questions(language: str = "zh") -> dict:
    pack = load("pack_narrow.json")
    pack["records"].update(copy.deepcopy(QUESTIONS))
    pack["records"]["I-300715-e5y"]["memory"] = "pending"
    pack["required"]["questions"]["ids"] = list(QUESTIONS)
    pack["digest"]["writing_rules"] = language
    return pack


def test_open_questions_say_what_the_user_or_the_host_must_do():
    lines = render_pack(with_questions())["markdown"].splitlines()
    assert ("待定：[PC-I-300713-s4v-I-300715-e5y] 「早点回住处」和「晚上去湖边」出自同一条消息：两条都算，还是只算一条？"
            "｜默认：两条都照用，直到用户说明｜读法：两条都成立：早点回住处；晚上去湖边；只算一条：早点回住处，或 晚上去湖边"
            "｜宿主：问用户两条都算还是只算一条；没答前照默认，不替用户挑读法") in lines
    assert ("待定：[MEM-2030-07-seenland] 长期条目待确认（没答前照用）：I-300715-e5y｜默认：照用"
            "｜宿主：在对话里问用户这几条这趟还用不用，按回答逐条记“沿用”或“这趟不用”") in lines
    assert "待定：[Q-300715-q9x] 晚饭后去河边还是回住处｜u1｜默认：先不排｜可选：河边、回住处｜要用户定；宿主：问一次，没答前照默认" in lines
    assert "待定：[Q-300715-r2m] 明早坐哪班车｜u1、u2｜可选：08:10、09:10｜要用户定；宿主：问一次，没答前不替用户选" in lines


def test_an_imported_entry_awaiting_confirmation_is_marked_and_others_are_not():
    lines = render_pack(with_questions())["markdown"].splitlines()
    assert next(line for line in lines if "[I-300715-e5y]" in line and line.startswith("偏好")).startswith("偏好：[I-300715-e5y] 待确认｜")
    assert "待确认" not in next(line for line in lines if line.startswith("偏好：[I-300713-s4v]"))


def test_a_question_without_a_kind_keeps_the_v1_line():
    pack = load("pack_narrow.json")
    pack["records"]["Q-300715-q9x"] = {"type": "question", "question": "晚饭后去河边还是回住处", "default": "先不排", "status": "open"}
    pack["required"]["questions"]["ids"] = ["Q-300715-q9x"]
    assert "待定：[Q-300715-q9x] 晚饭后去河边还是回住处｜默认：先不排" in render_pack(pack)["markdown"].splitlines()


def test_open_questions_in_english_name_the_host_step():
    markdown = render_pack(with_questions("en"))["markdown"]
    assert "Preference: [I-300715-e5y] awaiting confirmation | " in markdown
    assert "| host: ask the user in the conversation whether these still apply on this trip" in markdown
    assert "Open: [Q-300715-r2m] 明早坐哪班车 | u1, u2 | options: 08:10, 09:10 | the user decides; host: ask once, never choose for the user" in markdown


def test_over_budget_omissions_and_user_orders_render_with_their_own_labels():
    pack = load("pack_narrow.json")
    pack["omitted"].append({"id": "I-300799-x1y", "reason": "over_budget"})
    pack["orders"] = [{"between": ["I-300713-s4v", "I-300715-e5y"], "status": "user", "order": ["I-300715-e5y", "I-300713-s4v"],
                       "source_event": "E-300715-c03"}]
    markdown = render_pack(pack)["markdown"]
    assert "没放进来：4 条｜人不在范围：I-300715-b8k｜时段外：I-300714-w9a｜日期外：I-300713-m0u｜超预算：I-300799-x1y" in markdown
    assert "次序：I-300715-e5y 先于 I-300713-s4v｜用户定｜原话 E-300715-c03" in markdown
    assert "冲突时给方案" not in markdown


def test_english_packs_use_english_labels_and_the_english_writing_rules():
    pack = load("pack_narrow.json")
    pack["digest"]["writing_rules"] = "en"
    markdown = render_pack(pack)["markdown"]
    assert markdown.startswith("# Pack CTX-300715-n4k | replan, first reply | u1 | 07-15 13:00—21:00 CEST")
    assert writing_rules("en") in markdown and "Hard: [I-300713-t2k] must | u1, u2 |" in markdown


def test_pack_meta_leaves_out_records_and_full_messages_and_carries_the_downgraded_status():
    pack = load("pack_partial.json")
    out = render_pack(pack)
    meta = pack_meta(pack, out["required"])
    assert "records" not in meta and "full_quotes" not in meta
    assert meta["required"]["items"]["status"] == "partial"


def test_a2_now_and_hard_node_timing_render_in_english_and_only_when_present(sources):
    """A pack with `header.now` and timed hard nodes (A2) adds them; the v1.2 samples above render as before."""
    pack = copy.deepcopy(load("pack_narrow.json"))
    pack["digest"]["writing_rules"] = "en"
    pack["header"]["now"] = dict(at="2030-07-15T13:00:13+02:00", date="2030-07-15", time="13:00", weekday=1, timezone="Europe/Vienna")
    pack["records"]["hn-dinner"].update(deadline="external", stored={"version": 3, "at": "2030-07-14T20:20:00+02:00"},
                                        checked=dict(source="Example Rail timetable", at="2030-07-15T05:00:00+02:00", evidence_id="EV-1",
                                                     valid_until=None, stale=False), refresh_hours=6, may_have_changed=True)
    lines = render_pack(pack, sources=sources)["markdown"].splitlines()
    assert lines[0].startswith("# Now 13:00 (Europe/Vienna, Mon 07-15) | Pack CTX-300715-n4k | ")
    node = next(line for line in lines if "[hn-dinner]" in line)
    assert node.endswith(" | external deadline | plan keeps 15 min margin | checked: Example Rail timetable, 07-15 05:00, "
                         "older than 6 h, may have changed")
