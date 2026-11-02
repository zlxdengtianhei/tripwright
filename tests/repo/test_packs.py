"""Constraint packs (CONTRACTS 3.1, 3.2): built from store records, they equal the contract sample, and each promise has a
check that turns red when it is broken."""
import copy
import json
import re

import pytest
from packs_sample import FOLDER, OWNER, S1_AT, WIDE_AT, contract_checks as cc, load, load_store, snapshot, text_quotes

from tripwright import packs
from tripwright.events import Events
from tripwright.ledger import Item, Ledger, PendingCheck
from tripwright.render.screens import repo_files
from tripwright.render.writing import writing_rules
from tripwright.storage import Store

NARROW_SCOPE = {"start": "2030-07-15", "time_from": "13:00", "time_to": "21:00", "place": "seenland"}
QUICK_SCOPE = {"start": "2030-07-15", "time_from": "13:00", "time_to": "15:00", "place": "seenland"}
IMPORTED = "I-300601-h0m"


@pytest.fixture
def packs_store(tmp_path):
    """A store loaded with the contract sample as of a snapshot's moment."""
    opened = []

    def make(name: str = "S1") -> Store:
        store = Store(tmp_path / f"packs-{name}-{len(opened)}.sqlite3")
        opened.append(store)
        load_store(store, snapshot(name)["taken_at"])
        return store

    yield make
    for store in opened:
        store.close()


def narrow(store, *, at=S1_AT, subject=("u1",), scope=None, scenario="replan", budget=None, pack_id="CTX-300715-n4k"):
    return packs.build(store, OWNER, kind="narrow", scenario=scenario, pack_id=pack_id, snapshot=snapshot("S1"), at=at,
                       subject=list(subject) if subject else None, scope=NARROW_SCOPE if scope is None else scope,
                       trip_folder=FOLDER, budget=budget)


def wide(store, *, budget=None):
    return packs.build(store, OWNER, kind="wide", scenario="replan", pack_id="CTX-300715-w2d", snapshot=snapshot("S2"), at=WIDE_AT,
                       candidate="DR-300715-b8d", trip_folder=FOLDER, budget=budget)


def problems(pack: dict) -> list[str]:
    return cc.pack_problems(text_quotes(pack), load("ledger.json"), load("trip.json"))


A2_TIMING = ("deadline", "stored", "checked", "refresh_hours", "may_have_changed")


def v12(pack: dict) -> dict:
    """The pack without what A2 added (`header.now` on live packs, where each hard node's time comes from), which the
    contract sample v1.2 predates. The additions have their own tests in test_a2_now_and_timing.py; once the sample
    carries them, compare whole packs again."""
    shaped = copy.deepcopy(pack)
    shaped["header"].pop("now", None)
    for record in shaped["records"].values():
        if record["type"] == "hard_node":
            for key in A2_TIMING:
                record.pop(key, None)
    return shaped


def expected_markdown(name: str) -> list[str]:
    return cc.text(f"{name}.md").replace("{{WRITING_RULES}}", writing_rules("zh")).splitlines()


def details_apart(lines: list[str]) -> list[str]:
    return [line for line in lines if not line.startswith("细节：")]


def imported(store, status: str = "pending"):
    """A long-term preference carried over from an earlier trip: its message is not in this store, only in its origin."""
    item = Item.model_validate(dict(id=IMPORTED, kind="preference", key="breakfast", quote="早饭要有咖啡。", text="早饭要有咖啡",
                                    subject=["u1"], strength="prefer", long_term=True,
                                    source=dict(event="E-300601-x01", nature="user_verbatim", basis="user_explicit"))).model_dump(mode="json")
    item["origin"] = dict(trip="2030-06-lakes", event="E-300601-x01", said_at="2030-06-01T08:00:00+02:00", speaker="u1",
                          nature="user_verbatim", path="profile/preferences.yaml", message="早饭要有咖啡。别的随意。",
                          import_event="E-300713-a01")
    with store.transaction():
        store.put("items", OWNER, item, insert=True)
        store.put("carry", OWNER, dict(id=IMPORTED, kind="preference", status=status, status_event=None, from_trip="2030-06-lakes",
                                       imported_at="2030-07-13T18:00:00+02:00", last_used=None), insert=True)


# --- the sample ---------------------------------------------------------------

def test_narrow_pack_at_s1_equals_the_sample(packs_store):
    pack = v12(narrow(packs_store("S1")))
    sample = load("pack_narrow.json")
    assert {key: value for key, value in pack.items() if key != "details"} == {key: value for key, value in sample.items() if key != "details"}
    # Details: the day file names the hard node and the locked block; places are those of the hard nodes and locked blocks.
    assert pack["details"] == [sample["details"][0], {"path": f"{FOLDER}/plan/places.yaml", "ids": ["lindengrund"]}]
    assert problems(pack) == [] and cc.schema_errors(pack, "pack") == []


def test_wide_pack_at_s2_equals_the_sample_with_full_quotes_as_objects(packs_store):
    pack = v12(wide(packs_store("S2")))
    sample = load("pack_wide.json")
    for key in ("schema", "pack_id", "kind", "scenario", "header", "snapshot", "required", "records", "orders", "omitted", "candidate",
                "dispositions", "digest"):
        assert pack[key] == sample[key], key
    assert {event_id: quote["text"] for event_id, quote in pack["full_quotes"].items()} == sample["full_quotes"]
    assert problems(pack) == [] and cc.schema_errors(text_quotes(pack), "pack") == []


def test_full_quotes_carry_when_who_and_how_each_message_was_said(packs_store):
    events = {event["id"]: event for event in load("ledger.json")["events"]}
    quotes = wide(packs_store("S2"))["full_quotes"]
    assert list(quotes) == ["E-300713-a01", "E-300714-b02", "E-300715-c03", "E-300715-d04", "E-300715-e05"]
    for event_id, quote in quotes.items():
        source = events[event_id]
        assert quote == {"event": event_id, "text": source["text"], "said_at": source["received_at"], "speaker": source["speaker"],
                         "nature": source["nature"]}


def test_presented_packs_render_like_the_sample_apart_from_details(packs_store):
    for name, pack in (("pack_narrow", v12(narrow(packs_store("S1")))), ("pack_wide", v12(wide(packs_store("S2"))))):
        out = packs.present(pack)
        assert details_apart(out["markdown"].splitlines()) == details_apart(expected_markdown(name)), name
        assert out["pack"] is pack and out["required"] == pack["required"]


def test_quick_pack_leaves_out_the_late_check_over_budget_like_the_sample(packs_store):
    """The window ends 15:00; the check due 15:30 is not required and leaves before any required text is shortened."""
    sample = load("pack_omitted.json")
    pack = v12(narrow(packs_store("S1"), scenario="quick", scope=QUICK_SCOPE, budget=sample["digest"]["budget_tokens"],
                      pack_id=sample["pack_id"]))
    assert packs.non_required(pack) == ["P-300714-w6t"] and "P-300714-w6t" in pack["records"]
    pack["details"] = sample["details"]  # the sample's hand-written places (see test_narrow_pack_at_s1_equals_the_sample)
    out = packs.present(pack)
    assert out["pack"] == sample
    assert out["markdown"].splitlines() == expected_markdown("pack_omitted")
    assert {"tokens": out["tokens"], "required": out["required"]} == {key: load("pack_omitted.required.json")[key] for key in ("tokens", "required")}
    assert problems(out["pack"]) == []


# --- promises -----------------------------------------------------------------

def test_a_quick_pack_never_drops_facts_or_preferences(packs_store):
    pack = narrow(packs_store("S1"), scenario="quick", scope={})
    assert pack["header"]["window"] == {"date": "2030-07-15", "from": "13:00", "to": "19:30", "timezone": "Europe/Vienna"}
    assert pack["header"]["place"] == "*" and pack["header"]["channel"] == "conversation"
    assert pack["required"]["facts"]["ids"] == ["F-300715-s1o", "F-300715-n2l"]
    assert {"I-300713-s4v", "I-300715-e5y"} <= set(pack["required"]["items"]["ids"])
    assert pack["digest"]["budget_tokens"] == 1800 and problems(pack) == []
    broken = copy.deepcopy(pack)
    broken["required"]["facts"]["ids"].remove("F-300715-n2l")
    broken["records"].pop("F-300715-n2l")
    assert "F-300715-n2l: applicable but missing from the required set" in problems(broken)


def test_a_principle_comes_along_whatever_its_people(packs_store):
    pack = narrow(packs_store("S1"), subject=["u2"])
    assert pack["records"]["I-300713-p1n"]["section"] == "principle" and pack["records"]["I-300713-p1n"]["subject"] == ["u1"]
    assert pack["required"]["items"]["ids"][0] == "I-300713-p1n"
    assert {"id": "I-300713-s4v", "reason": "out_of_scope:people"} in pack["omitted"]
    assert "I-300713-p1n" not in {entry["id"] for entry in pack["omitted"]}
    assert problems(pack) == []
    broken = copy.deepcopy(pack)
    broken["required"]["items"]["ids"].remove("I-300713-p1n")
    broken["records"].pop("I-300713-p1n")
    assert "I-300713-p1n: applicable but missing from the required set" in problems(broken)


@pytest.mark.parametrize("scope,record_id,reason", [
    (dict(NARROW_SCOPE, place="grauwald"), "P-300714-w6t", "out_of_scope:place"),
    (dict(NARROW_SCOPE, channel="site"), "I-300713-r7b", "out_of_scope:channel"),
    (dict(NARROW_SCOPE, start="2030-07-14", time_from=None, time_to=None), "F-300715-s1o", "out_of_scope:date"),
    (NARROW_SCOPE, "I-300714-w9a", "out_of_scope:time"),
    (NARROW_SCOPE, "I-300715-b8k", "out_of_scope:people"),
])
def test_out_of_scope_records_are_listed_with_their_reason(packs_store, scope, record_id, reason):
    pack = narrow(packs_store("S1"), scope=scope)
    assert {"id": record_id, "reason": reason} in pack["omitted"] and record_id not in pack["records"]
    assert problems(pack) == []
    broken = copy.deepcopy(pack)
    next(entry for entry in broken["omitted"] if entry["id"] == record_id)["reason"] = "out_of_scope:people" if reason != "out_of_scope:people" else "out_of_scope:date"
    assert any(problem.startswith(f"{record_id}: out of scope") for problem in problems(broken))


def test_a_pending_imported_entry_applies_is_marked_and_asked_about(packs_store):
    store = packs_store("S2")
    imported(store)
    assert IMPORTED not in {event["id"] for event in store.all("events", OWNER)}
    pack = narrow(store, at=WIDE_AT)
    record = pack["records"][IMPORTED]
    assert IMPORTED in pack["required"]["items"]["ids"] and record["memory"] == "pending"
    assert record["speaker"] == "u1" and record["source_event"] == "E-300601-x01"
    question = pack["records"]["MEM-2030-07-seenland"]
    assert pack["required"]["questions"]["ids"] == ["MEM-2030-07-seenland"]
    assert question == {"type": "question", "kind": "memory_pending", "question": f"长期条目待确认（没答前照用）：{IMPORTED}",
                        "default": "照用", "item_ids": [IMPORTED], "status": "open"}
    assert f"待定：[MEM-2030-07-seenland] 长期条目待确认（没答前照用）：{IMPORTED}｜默认：照用" in packs.present(pack)["markdown"]
    quotes = wide(store)["full_quotes"]
    assert quotes["E-300601-x01"] == {"event": "E-300601-x01", "text": "早饭要有咖啡。别的随意。", "said_at": "2030-06-01T08:00:00+02:00",
                                      "speaker": "u1", "nature": "user_verbatim"}
    entry = store.get("carry", OWNER, IMPORTED)
    store.put("carry", OWNER, dict(entry, status="confirmed"))
    confirmed = narrow(store, at=WIDE_AT)
    assert "memory" not in confirmed["records"][IMPORTED] and confirmed["required"]["questions"]["ids"] == []
    store.put("carry", OWNER, dict(entry, status="not_used"))
    unused = narrow(store, at=WIDE_AT)
    assert IMPORTED not in unused["records"] and IMPORTED not in {entry["id"] for entry in unused["omitted"]}


def test_a_possible_contradiction_is_a_question_until_declared_compatible(packs_store):
    store = packs_store("S1")
    message = Events(store).receive(OWNER, "晚上想吃中餐。晚上想早点睡。", "req-pc", nature="user_verbatim", native_ref="fixture:pc",
                                    speaker="u1")
    source = {"event": message["event_id"], "basis": "user_explicit"}
    day = {"start": "2030-07-15", "end": "2030-07-15"}
    ledger = Ledger(store)
    changed = ledger.apply(OWNER, {"items": [
        dict(kind="preference", key="dinner_food", quote="晚上想吃中餐。", text="晚上吃中餐", subject=["u1"], scope=day, strength="prefer", source=source),
        dict(kind="preference", key="sleep", quote="晚上想早点睡。", text="晚上早点睡", subject=["u1"], scope=day, strength="prefer", source=source)]})["changed"]
    pack = narrow(store)
    question_id = f"PC-{changed[0]}-{changed[1]}"
    assert pack["required"]["questions"]["ids"] == [question_id]
    question = pack["records"][question_id]
    assert question["kind"] == "possible_contradiction" and question["item_ids"] == changed and question["status"] == "open"
    assert "「晚上想吃中餐。」" in question["question"] and "「晚上想早点睡。」" in question["question"]
    assert question["default"] and len(question["readings"]) == 2
    assert f"待定：[{question_id}] " in packs.present(pack)["markdown"]
    ledger.apply(OWNER, {"relate": [{"items": changed, "relation": "compatible_with", "source": source}]})
    cleared = narrow(store)
    assert cleared["required"]["questions"]["ids"] == [] and set(changed) <= set(cleared["required"]["items"]["ids"])


def test_over_budget_only_ever_leaves_out_non_required_records(packs_store):
    store = packs_store("S1")
    goal = Item.model_validate(dict(id="I-300715-g0l", kind="goal", key="lakes", quote="下午想去两个湖边走走", text="下午去两个湖",
                                    subject=["u1"], strength="prefer", scope={"start": "2030-07-15", "end": "2030-07-15"},
                                    source=dict(event="E-300715-d04", nature="user_verbatim", basis="user_explicit"))).model_dump(mode="json")
    store.put("items", OWNER, goal, insert=True)
    pack = narrow(store, scenario="quick", scope=QUICK_SCOPE, budget=100)
    assert pack["records"]["I-300715-g0l"]["section"] == "goal"
    assert packs.non_required(pack) == ["I-300715-g0l", "P-300714-w6t"]
    out = packs.present(pack)
    left = [entry["id"] for entry in out["pack"]["omitted"] if entry["reason"] == "over_budget"]
    assert left == ["I-300715-g0l", "P-300714-w6t"]
    kept = {group: [record_id for record_id in ids["ids"] if record_id not in left] for group, ids in pack["required"].items()}
    assert {group: value["ids"] for group, value in out["required"].items()} == kept
    assert set(out["pack"]["records"]) == {record_id for ids in kept.values() for record_id in ids}
    # Still over: the renderer shortens what remains, never removes it.
    assert out["required"]["items"]["status"] == "partial" and out["tokens"] > 100
    # Goals are required outside quick and status; a wide pack has nothing non-required.
    assert packs.non_required(narrow(store, scope=QUICK_SCOPE, budget=100)) == ["P-300714-w6t"]
    wide_store = packs_store("S2")
    late = PendingCheck.model_validate(dict(id="P-300715-l8t", question="16 号早上的车开不开", due_at="2030-07-16T08:00:00+02:00",
                                            responsible="verifier", subject=["u1"], consequence="不开就赶不上", alternative="前一晚改订",
                                            source=dict(event="E-300715-e05", nature="user_verbatim", basis="agent_inference")))
    wide_store.put("pending_checks", OWNER, late.model_dump(mode="json"), insert=True)
    wide_store.put("items", OWNER, goal, insert=True)
    wide_pack = wide(wide_store, budget=100)
    assert "P-300715-l8t" in wide_pack["required"]["checks_due"]["ids"] and packs.non_required(wide_pack) == []
    quick_wide = packs.build(wide_store, OWNER, kind="wide", scenario="quick", pack_id="CTX-300715-w2d", snapshot=snapshot("S2"),
                             at=WIDE_AT, candidate="DR-300715-b8d", trip_folder=FOLDER)
    assert "I-300715-g0l" in quick_wide["required"]["items"]["ids"] and packs.non_required(quick_wide) == []
    wide_out = packs.present(wide_pack)
    assert not [entry for entry in wide_out["pack"]["omitted"] if entry["reason"] == "over_budget"]
    assert "P-300715-l8t" in wide_out["required"]["checks_due"]["ids"]


def test_a_pack_that_fits_keeps_its_non_required_records(packs_store):
    pack = narrow(packs_store("S1"), scenario="quick", scope=QUICK_SCOPE)
    out = packs.present(pack)
    assert out["pack"] == pack and "P-300714-w6t" in out["required"]["checks_due"]["ids"] and out["tokens"] <= 1800


def test_present_hands_the_host_meta_without_records_or_full_quotes(packs_store):
    pack = wide(packs_store("S2"))
    before = copy.deepcopy(pack)
    out = packs.present(pack)
    assert set(out) == {"pack", "markdown", "tokens", "required", "meta"}
    assert "records" not in out["meta"] and "full_quotes" not in out["meta"] and out["meta"]["required"] == out["required"]
    assert isinstance(out["pack"]["full_quotes"]["E-300715-e05"], dict) and pack == before
    assert "- [E-300715-e05] 07-15 13:08 CEST｜u1｜逐字｜「我膝盖有点疼。」" in out["markdown"].splitlines()


def test_the_window_follows_the_clock_only_on_the_day_itself(packs_store):
    store = packs_store("S1")
    evening = narrow(store, at="2030-07-15T20:00:00+02:00", scope={})
    assert evening["header"]["window"]["from"] == "20:00" and evening["header"]["window"]["to"] is None
    assert evening["records"]["P-300714-w6t"]["overdue"] is True and packs.non_required(evening) == []
    other_day = narrow(store, scope={"start": "2030-07-14"})
    assert (other_day["header"]["window"]["from"], other_day["header"]["window"]["to"]) == (None, None)
    assert other_day["required"]["hard_nodes"]["ids"] == [] and "I-300713-m0u" in other_day["required"]["items"]["ids"]


def test_outside_the_trip_days_a_narrow_pack_covers_the_whole_trip(packs_store):
    """A turn before the trip (here in 2026) must not narrow to that day, or every dated record would be out of scope."""
    store = packs_store("S1")
    question = dict(id="Q-300715-q9x", question="晚饭后去河边还是回住处", subject=["u1"],
                    scope={"start": "2030-07-15", "end": "2030-07-15", "time_from": None, "time_to": None, "place": "*", "channel": "*"},
                    item_ids=[], source_event="E-300715-d04", options=["河边", "回住处"], recommendation="问用户", default="先不排",
                    since=S1_AT, status="open")
    store.put("undecided", OWNER, question, insert=True)
    before = narrow(store, at="2026-10-31T12:00:00+01:00", scope={})
    assert before["header"]["window"] == {"date": "2030-07-14", "from": None, "to": None, "timezone": "Europe/Vienna"}
    assert {"I-300713-m0u", "I-300715-e5y", "I-300714-w9a"} <= set(before["required"]["items"]["ids"])
    assert before["required"]["questions"]["ids"] == ["Q-300715-q9x"] and before["required"]["hard_nodes"]["ids"] == ["hn-dinner"]
    assert before["omitted"] == [{"id": "I-300715-b8k", "reason": "out_of_scope:people"}]
    # The window ends with the trip: the check due on its second day is required, not left to the budget.
    assert packs.non_required(before) == [] and "non_required" not in before["records"]["P-300714-w6t"]
    assert cc.schema_errors(before, "pack") == []
    during = narrow(store, scope={})
    assert during["header"]["window"]["date"] == "2030-07-15" and during["header"]["window"]["from"] == "13:00"
    assert "I-300713-m0u" in {entry["id"] for entry in during["omitted"]}


def test_without_a_committed_plan_a_narrow_pack_has_no_date_filter(packs_store):
    unplanned = dict(snapshot("S1"), plan_version=0, plan_version_id=None)
    pack = packs.build(packs_store("S1"), OWNER, kind="narrow", scenario="replan", pack_id="CTX-261031-n0p", snapshot=unplanned,
                       at="2026-10-31T12:00:00+01:00", subject=["u1"])
    assert pack["header"]["window"] == {"date": "2026-10-31", "from": None, "to": None, "timezone": "UTC"}
    assert {"I-300713-m0u", "I-300715-e5y", "I-300714-w9a"} <= set(pack["required"]["items"]["ids"])
    assert pack["required"]["hard_nodes"]["ids"] == [] and pack["details"] == []
    assert pack["omitted"] == [{"id": "I-300715-b8k", "reason": "out_of_scope:people"}] and packs.non_required(pack) == []


def test_day_packs_are_whole_day_and_regenerate_to_the_same_bytes(packs_store):
    first = packs.day_packs(packs_store("S2"), OWNER, snapshot=snapshot("S2"), at=snapshot("S2")["taken_at"], trip_folder=FOLDER)
    again = packs.day_packs(packs_store("S2"), OWNER, snapshot=snapshot("S2"), at=snapshot("S2")["taken_at"], trip_folder=FOLDER)
    assert list(first) == ["2030-07-14", "2030-07-15"]
    assert json.dumps(first, ensure_ascii=False) == json.dumps(again, ensure_ascii=False)
    for day, pack in first.items():
        assert re.fullmatch(rf"CTX-{day[2:4]}{day[5:7]}{day[8:10]}-[0-9a-f]{{6}}", pack["pack_id"])
        assert pack["header"]["people"] == ["u1", "u2"] and pack["header"]["channel"] == "conversation"
        assert (pack["header"]["window"]["from"], pack["header"]["window"]["to"]) == (None, None)
        assert cc.schema_errors(pack, "pack") == [] and problems(pack) == []
    assert first["2030-07-15"]["required"]["hard_nodes"]["ids"] == ["hn-dinner"]
    moved = dict(snapshot("S2"), ledger_digest="sha256:0000000000000000")
    assert packs.day_pack_id("2030-07-15", moved) != first["2030-07-15"]["pack_id"]


def test_view_records_cover_every_record_and_feed_the_views(packs_store):
    store = packs_store("S2")
    ledger = load("ledger.json")
    records = packs.view_records(store, OWNER, at=snapshot("S2")["taken_at"])
    assert set(records) == set(ledger["records"])
    for record_id, record in records.items():
        source = ledger["records"][record_id]
        shared = {key: value for key, value in source.items() if key != "always"}  # `always` shows as section principle
        assert {key: record[key] for key in shared} == shared, record_id
    store.put("items", OWNER, dict(store.get("items", OWNER, "I-300713-m0u"), status="superseded"))
    assert packs.view_records(store, OWNER, at=snapshot("S2")["taken_at"])["I-300713-m0u"]["status"] == "superseded"
    events = {event["id"]: event for event in ledger["events"]}
    state = {"path": FOLDER, "trip": load("trip.json"), "records": records, "snapshot": snapshot("S2"), "events": events}
    day = packs.day_packs(store, OWNER, snapshot=snapshot("S2"), at=snapshot("S2")["taken_at"], trip_folder=FOLDER)
    files = repo_files([state], current="2030-07-seenland", snapshot=snapshot("S2"), packs={"2030-07-seenland": day}, sources=events)
    index = files[f"{FOLDER}/views/index.md"]
    assert all(f"[{record_id}]" in index for record_id in records)
    assert "[I-300713-p1n] 总带" in index and "[D-300714-d3r]" in files[f"{FOLDER}/views/decisions.md"]
    assert {f"{FOLDER}/views/packs/2030-07-14.md", f"{FOLDER}/views/packs/2030-07-15.md"} <= set(files)


@pytest.mark.parametrize("arguments,message", [
    (dict(kind="deep"), "Unknown pack kind"),
    (dict(scenario="chat"), "Unknown pack scenario"),
    (dict(kind="wide"), "candidate draft"),
])
def test_build_refuses_what_it_cannot_compile(packs_store, arguments, message):
    call = dict(kind="narrow", scenario="replan", pack_id="CTX-300715-n4k", snapshot=snapshot("S1"), at=S1_AT) | arguments
    with pytest.raises(ValueError, match=message):
        packs.build(packs_store("S1"), OWNER, **call)
