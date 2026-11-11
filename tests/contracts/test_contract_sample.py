"""The product-behavior sample agrees with itself.

Every promise has a passing check on the sample and a failing check on a broken copy.
"""
import pytest
from pydantic import ValidationError

import contract_checks as cc
from tripwright.trip import Trip


@pytest.fixture(scope="module")
def sample():
    names = ("ledger", "trip", "candidate", "snapshots", "pack_narrow", "pack_wide", "write_states", "sequence",
             "answer_quick", "answer_first", "answer_second", "pack_partial", "pack_unknown", "pack_over_budget", "answer_en",
             "pack_partial.required", "pack_over_budget.required", "pack_unknown.required", "pack_omitted", "pack_omitted.required")
    data = {name: cc.load(f"{name}.json") for name in names}
    data["plan"] = cc.candidate_trip(data["trip"], data["candidate"])
    data["snaps"] = data["snapshots"]["snapshots"]
    return data


PACKS = ["pack_narrow", "pack_wide", "pack_partial", "pack_unknown", "pack_over_budget", "pack_omitted"]
ANSWERS = ["answer_quick", "answer_first", "answer_second", "answer_en"]
SHAPES = [(name, "pack") for name in PACKS] + [(name, "answer") for name in ANSWERS] + [("write_states", "write_states")]


@pytest.mark.parametrize("name,definition", SHAPES)
def test_sample_files_match_the_schema(sample, name, definition):
    assert cc.schema_errors(sample[name], definition) == []


def test_snapshots_and_result_replies_match_the_schema(sample):
    for snapshot in sample["snaps"].values():
        assert cc.schema_errors(snapshot, "snapshot") == []
    for step in sample["sequence"]["steps"]:
        if step["step"] == "result":
            assert cc.schema_errors(step["response"], "result_response") == []
        if "write" in step:
            assert cc.schema_errors(step["write"], "write_ref") == []


@pytest.mark.parametrize("definition,instance_path,value", [
    ("pack", ["required", "facts", "status"], KeyError),
    ("pack", ["required", "facts", "status"], "dropped"),
    ("pack", ["required", "facts"], KeyError),
    ("pack", ["dispositions"], [{"item_id": "I-300713-s4v", "status": "unaddressed"}]),
    ("pack", ["omitted", 0, "reason"], "too_long"),
])
def test_schema_rejects_a_pack_without_complete_required_groups(sample, definition, instance_path, value):
    assert cc.schema_errors(cc.mutated(sample["pack_narrow"], instance_path, value), definition)


@pytest.mark.parametrize("instance_path,value", [
    (["ops", 0, "transitions", 2, "state"], "saved"),
    (["ops", 3, "transitions", 2, "notice_id"], KeyError),
    (["ops", 0, "transitions", 1, "commit"], KeyError),
])
def test_schema_rejects_unknown_write_states_and_missing_proof(sample, instance_path, value):
    assert cc.schema_errors(cc.mutated(sample["write_states"], instance_path, value), "write_states")


@pytest.mark.parametrize("name,instance_path,value", [
    ("answer_first", ["options", "variants"], [{"label": "A", "diff": None, "how": "only one", "item_ids": [], "plan": None}]),
    ("answer_first", ["options", "recommend"], {"label": "B", "reason": ""}),
    ("answer_quick", ["answer"], ["one", "two", "three"]),
    ("answer_second", ["schedule", 0, "time"], "around noon"),
])
def test_schema_rejects_answer_data_outside_the_skeleton(sample, name, instance_path, value):
    assert cc.schema_errors(cc.mutated(sample[name], instance_path, value), "answer")


def test_sample_plans_are_valid_trips_with_the_core_latest_departure(sample):
    for plan in (sample["trip"], sample["plan"]):
        Trip.model_validate(plan)
    assert sample["pack_narrow"]["records"]["hn-dinner"]["latest_departure"] == "18:05"
    broken = cc.mutated(sample["trip"], ["days", 1, "anchors", 0, "place"], "nowhere")
    with pytest.raises(ValidationError):
        Trip.model_validate(broken)


def test_snapshots_follow_the_ledger_and_the_write_sequence(sample):
    for name, snapshot in sample["snaps"].items():
        assert cc.snapshot_problems(name, snapshot, sample["ledger"], sample["write_states"]) == []
    assert sample["pack_narrow"]["snapshot"] == sample["snaps"]["S1"]
    assert sample["pack_wide"]["snapshot"] == sample["snaps"]["S2"]


@pytest.mark.parametrize("key,value", [("ledger_revision", 11), ("ledger_digest", "sha256:0000000000000000"),
                                       ("plan_version_id", "V-300715-x4w")])
def test_snapshot_drift_is_detected(sample, key, value):
    broken = cc.mutated(sample["snaps"]["S2"], [key], value)
    assert cc.snapshot_problems("S2", broken, sample["ledger"], sample["write_states"])


def test_same_version_number_from_two_writers_has_different_content_keys(sample):
    ours = next(version for version in sample["ledger"]["versions"] if version["id"] == "V-300715-v4l")
    theirs = sample["snaps"]["S4"]
    assert ours["number"] == theirs["plan_version"] and ours["id"] != theirs["plan_version_id"]


@pytest.mark.parametrize("name", PACKS)
def test_packs_carry_every_applicable_record_once_and_name_the_rest(sample, name):
    assert cc.pack_problems(sample[name], sample["ledger"], sample["trip"]) == []


@pytest.mark.parametrize("change", [
    lambda pack: pack["required"]["facts"]["ids"].remove("F-300715-n2l") or pack["records"].pop("F-300715-n2l"),
    lambda pack: pack["omitted"].pop(0),
    lambda pack: pack["records"]["I-300713-s4v"].update(quote="想省钱"),
    lambda pack: pack["records"]["hn-dinner"].update(latest_departure="18:20"),
    lambda pack: pack["omitted"].append({"id": "I-300713-s4v", "reason": "over_budget"}),
])
def test_silently_dropped_or_altered_conditions_are_detected(sample, change):
    broken = cc.mutated(sample["pack_narrow"], ["kind"], "narrow")
    change(broken)
    assert cc.pack_problems(broken, sample["ledger"], sample["trip"])


def over_budget(pack: dict, group: str, record_id: str) -> dict:
    pack["required"][group]["ids"].remove(record_id)
    pack["records"].pop(record_id)
    pack["omitted"].append({"id": record_id, "reason": "over_budget"})
    return pack


def test_only_a_non_required_record_is_left_out_over_budget(sample):
    """CONTRACTS 3.1: the quick pack's window ends at 15:00, so the check due 15:30 is not required and may leave."""
    assert {"id": "P-300714-w6t", "reason": "over_budget"} in sample["pack_omitted"]["omitted"]
    assert "P-300714-w6t" not in sample["pack_omitted"]["records"]
    assert cc.non_required(sample["ledger"]["records"]["P-300714-w6t"], sample["pack_omitted"])
    assert not cc.non_required(sample["ledger"]["records"]["P-300714-w6t"], sample["pack_narrow"])


@pytest.mark.parametrize("change", [
    lambda pack: over_budget(pack, "facts", "F-300715-n2l"),
    lambda pack: over_budget(pack, "items", "I-300713-r7b"),
    lambda pack: over_budget(pack, "decisions", "D-300714-d3r"),
    lambda pack: pack["header"]["window"].update(to="16:00"),
    lambda pack: pack["header"]["window"].update(to=None),  # an open window ends with its day, after 15:30
])
def test_a_required_record_left_out_over_budget_is_detected(sample, change):
    broken = cc.mutated(sample["pack_omitted"], ["kind"], "narrow")
    change(broken)
    assert any("over budget" in problem for problem in cc.pack_problems(broken, sample["ledger"], sample["trip"]))


def test_wide_pack_needs_full_quotes_and_a_disposition_for_every_item(sample):
    broken = cc.mutated(sample["pack_wide"], ["dispositions"], sample["pack_wide"]["dispositions"][1:])
    assert cc.pack_problems(broken, sample["ledger"], sample["trip"])
    broken = cc.mutated(sample["pack_wide"], ["full_quotes", "E-300715-e05"], "膝盖")
    assert cc.pack_problems(broken, sample["ledger"], sample["trip"])


@pytest.mark.parametrize("name", PACKS)
def test_rendered_packs_mention_every_required_and_omitted_id(sample, name):
    assert cc.rendered_pack_problems(sample[name], cc.text(f"{name}.md"), sample["ledger"]) == []


@pytest.mark.parametrize("name", ["pack_partial", "pack_over_budget", "pack_unknown", "pack_omitted"])
def test_budget_downgrades_only_soft_records_and_marks_their_groups(sample, name):
    """CONTRACTS 3.3: ID-only lines only for records outside hard and principle; their group becomes partial; an
    unknown group keeps its reason; a pack still over budget says so in its header."""
    result = sample[f"{name}.required"]
    assert cc.downgrade_problems(sample[name], cc.text(f"{name}.md"), result["required"], result["tokens"]) == []


@pytest.mark.parametrize("name,edit", [
    ("pack_partial", lambda markdown, required: (markdown.replace("偏好·只列编号（原文按编号取）：[I-300713-s4v]、[I-300715-e5y]",
                                                                  "硬·只列编号（原文按编号取）：[I-300713-t2k]"), required)),
    ("pack_partial", lambda markdown, required: (markdown, {**required, "items": {**required["items"], "status": "complete"}})),
    ("pack_over_budget", lambda markdown, required: (markdown.replace("｜硬约束过多：约 1640 token，预算 1610", ""), required)),
    ("pack_unknown", lambda markdown, required: (markdown.replace("适用集没定：条目｜", "条目｜"), required)),
])
def test_a_downgrade_that_hides_a_hard_condition_or_its_status_is_detected(sample, name, edit):
    markdown, required = edit(cc.text(f"{name}.md"), sample[f"{name}.required"]["required"])
    assert cc.downgrade_problems(sample[name], markdown, required, sample[f"{name}.required"]["tokens"])


@pytest.mark.parametrize("edit", [
    lambda markdown: markdown.replace("实况：[F-300715-n2l] u1 没有箱子，箱子由 u2 带回 Grauwald｜自 07-15 13:00｜用户明说\n", ""),
    lambda markdown: markdown.replace("未入仓 1 条", "未入仓 0 条"),
    lambda markdown: markdown.replace("{{WRITING_RULES}}\n", ""),
    lambda markdown: markdown.replace("没放进来：3 条", "没放进来：2 条"),
    lambda markdown: markdown + "另见 I-300799-zzz\n",
])
def test_rendering_that_loses_a_condition_or_invents_one_is_detected(sample, edit):
    assert cc.rendered_pack_problems(sample["pack_narrow"], edit(cc.text("pack_narrow.md")), sample["ledger"])


@pytest.mark.parametrize("name", ANSWERS)
def test_answer_markdown_follows_its_data(sample, name):
    assert cc.answer_problems(sample[name], cc.text(f"{name}.md")) == []


def test_second_response_title_follows_verdict_commit_and_write_state(sample):
    markdown = cc.text("answer_second.md")
    assert cc.answer_problems(cc.mutated(sample["answer_second"], ["write", "state"], "effective"), markdown)
    assert cc.answer_problems(cc.mutated(sample["answer_second"], ["result", "verdict"], "incomplete"), markdown)
    effective = markdown.replace("# 第二段 · 核过的安排（草稿）", "# 第二段 · 核过的安排")
    assert cc.answer_problems(cc.mutated(sample["answer_second"], ["write", "state"], "effective"), effective) == []


@pytest.mark.parametrize("name,edit", [
    ("answer_quick", lambda markdown: markdown.replace("上次的安排已在别处改动，需要重排。\n", "")),
    ("answer_first", lambda markdown: markdown.replace("两湖之间坐 472 路 12 分钟，车上买单程。\n", "")),
    ("answer_first", lambda markdown: markdown.replace("没核：南湖岸步道今天开不开 → 封了就走不了 A → ", "没核：南湖岸步道今天开不开，")),
    ("answer_second", lambda markdown: markdown.replace("· 只坐 ÖBB 的车，WB 开头的不能坐 ", "")),
    ("answer_quick", lambda markdown: markdown.replace("Google 地图搜 Uferweg Nord, Seeufer · ", "")),
    ("answer_second", lambda markdown: markdown.replace("13:24 坐 471 路 · ÖBB App 搜 Altmoor Bahnhof → Seeufer Nord",
                                                        "13:24 坐 471 路 · Altmoor Bahnhof → Seeufer Nord")),
    ("answer_en", lambda markdown: markdown.replace("Not checked: ", "没核：")),
])
def test_answer_markdown_that_drops_a_line_or_reorders_a_notice_is_detected(sample, name, edit):
    assert cc.answer_problems(sample[name], edit(cc.text(f"{name}.md")))


def test_write_sequences_are_legal(sample):
    for op in sample["write_states"]["ops"]:
        assert cc.transition_problems(op, sample["write_states"]) == []
    states = {op["op_id"]: [transition["state"] for transition in op["transitions"]] for op in sample["write_states"]["ops"]}
    assert states["OP-300715-t4a"] == ["received", "pending_sync", "effective"]
    assert states["OP-300715-c9m"] == ["received", "pending_sync", "conflict"]


@pytest.mark.parametrize("transitions", [
    [{"state": "received", "at": "2030-07-15T13:00:13+02:00"},
     {"state": "effective", "at": "2030-07-15T13:00:24+02:00", "commit": "8b1d0e4"}],
    [{"state": "received", "at": "2030-07-15T13:00:13+02:00"},
     {"state": "pending_sync", "at": "2030-07-15T13:00:15+02:00", "commit": "8b1d0e4"},
     {"state": "effective", "at": "2030-07-15T13:00:24+02:00", "commit": "8b1d0e4"},
     {"state": "conflict", "at": "2030-07-15T13:00:30+02:00", "reason": "replay_refused", "detail": "x", "notice_id": "N-300715-n1c"}],
    [{"state": "pending_sync", "at": "2030-07-15T13:00:15+02:00", "commit": "8b1d0e4"}],
    [{"state": "received", "at": "2030-07-15T13:00:13+02:00"},
     {"state": "pending_sync", "at": "2030-07-15T13:00:10+02:00", "commit": "8b1d0e4"}],
    [{"state": "received", "at": "2030-07-15T13:00:13+02:00"},
     {"state": "pending_sync", "at": "2030-07-15T13:00:15+02:00", "commit": "c41a7f2"},
     {"state": "effective", "at": "2030-07-15T13:00:24+02:00", "commit": "c41a7f2"}],
    [{"state": "received", "at": "2030-07-15T13:00:13+02:00"},
     {"state": "conflict", "at": "2030-07-15T13:00:30+02:00", "reason": "plan_base_changed", "detail": "x", "notice_id": "N-300799-zzz"}],
])
def test_illegal_write_sequences_are_rejected(sample, transitions):
    op = dict(sample["write_states"]["ops"][0], transitions=transitions)
    assert cc.transition_problems(op, sample["write_states"])


def test_a_local_canonical_branch_may_skip_pending_sync(sample):
    local = dict(sample["write_states"], canonical="main")
    op = dict(sample["write_states"]["ops"][0], transitions=[
        {"state": "received", "at": "2030-07-15T13:00:13+02:00"},
        {"state": "effective", "at": "2030-07-15T13:00:24+02:00", "commit": "8b1d0e4"}])
    assert cc.transition_problems(op, local) == []


def test_freshness_sequence_follows_the_single_rule(sample):
    assert cc.sequence_problems(sample["sequence"], sample["snaps"], sample["ledger"], sample["write_states"]) == []
    steps = {step["n"]: step for step in sample["sequence"]["steps"]}
    assert steps[4]["response"]["status"] == "stale" and steps[4]["response"]["changed"] == ["ledger"]
    assert sample["snaps"]["S1"]["plan_version"] == sample["snaps"]["S2"]["plan_version"]
    assert steps[7]["response"]["status"] == "unsettled" and steps[8]["response"]["status"] == "completed"


@pytest.mark.parametrize("path,value", [
    (["steps", 3, "response"], {"job_id": "J-300715-j1a", "status": "completed", "changed": [], "unsettled_events": [],
                                "result": {"draft_id": "DR-300715-a7x", "verdict": "passed", "usages": []}, "notice": None}),
    (["steps", 6, "response", "status"], "completed"),
    (["steps", 7, "ack_events"], []),
    (["steps", 8, "acked_events"], []),
    (["steps", 11, "notices"], []),
    (["steps", 0, "write", "unsynced"], 0),
])
def test_stale_results_hidden_messages_and_missed_notices_are_detected(sample, path, value):
    broken = cc.mutated(sample["sequence"], path, value)
    assert cc.sequence_problems(broken, sample["snaps"], sample["ledger"], sample["write_states"])


def test_dispositions_cover_every_item_and_leave_the_choice_to_the_user(sample):
    usages = next(step for step in sample["sequence"]["steps"] if step["n"] == 8)["response"]["result"]["usages"]
    assert cc.disposition_problems(sample["pack_wide"], usages, sample["plan"]) == []
    traded = [dict(usage, status="traded_off", reason="省钱优先") if usage["item_id"] == "I-300715-e5y" else usage for usage in usages]
    assert cc.disposition_problems(sample["pack_wide"], traded, sample["plan"])
    same_plan = [dict(usage, block_ids=["d2a-walk"]) if usage["item_id"] == "I-300715-e5y" else usage for usage in usages]
    assert cc.disposition_problems(sample["pack_wide"], same_plan, sample["plan"])
    assert cc.disposition_problems(sample["pack_wide"], usages[1:], sample["plan"])


def test_every_reference_in_the_sample_resolves(sample):
    known = set(sample["ledger"]["records"]) | {event["id"] for event in sample["ledger"]["events"]}
    known |= {version["id"] for version in sample["ledger"]["versions"]} | {op["op_id"] for op in sample["write_states"]["ops"]}
    known |= {notice["notice_id"] for notice in sample["write_states"]["notices"]} | set(sample["sequence"]["contexts"])
    known |= {step[key] for step in sample["sequence"]["steps"] for key in ("draft_id", "job_id") if key in step}
    known |= {entry["id"] for entry in sample["ledger"]["journal"]}
    places = {place["id"] for place in sample["trip"]["places"]}
    plan_ids = set(cc.blocks(sample["trip"])) | set(cc.blocks(sample["plan"])) | set(cc.anchors(sample["trip"])) | places

    def unresolved(value):
        found = []
        if isinstance(value, dict):
            for item in value.values():
                found += unresolved(item)
        elif isinstance(value, list):
            for item in value:
                found += unresolved(item)
        elif isinstance(value, str):
            found += [token for token in cc.ID.findall(value) if token not in known]
        return found

    for name in ("pack_narrow", "pack_wide", "answer_quick", "answer_first", "answer_second", "answer_en", "write_states", "sequence",
                 "snapshots"):
        assert unresolved(sample[name]) == [], name
    for pack in (sample["pack_narrow"], sample["pack_wide"]):
        assert all(item in plan_ids for detail in pack["details"] for item in detail["ids"])
    for answer in (sample["answer_quick"], sample["answer_first"]):
        assert all(place["place_id"] in places for place in answer["places"])
    assert all(block in plan_ids for entry in sample["answer_second"]["schedule"] for block in entry["blocks"])
    # Answer variants still name their plan by its display label; blocks() keys plans by ID.
    plans = set(cc.blocks(sample["plan"]).values()) | {plan["name"] for day in sample["plan"]["days"] for plan in day.get("plans", [])}
    assert all(variant["plan"] in plans for variant in sample["answer_first"]["options"]["variants"])
    files = {step[key] for step in sample["sequence"]["steps"] for key in ("pack", "answer", "candidate") if key in step}
    assert all((cc.SAMPLE / name).is_file() for name in files)
    assert unresolved({"stray": "J-300799-zzz"}) == ["J-300799-zzz"]
