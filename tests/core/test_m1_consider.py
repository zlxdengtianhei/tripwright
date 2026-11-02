"""M1: the memory a turn puts in front of the agent (consider), stable and changing entries, this trip's sources and
notes, what to ask and the offer to draft a question (R-58, R-67, R-76, R-79, R-80; D-61 four and six; D-63 four).
Each promise has a bad sample and a legal one."""
from copy import deepcopy
from itertools import count

import pytest

from tripwright import consider
from tripwright.query import how_to_check, trip_sources
from tripwright.render.pack import render_pack
from tripwright.render.regions import table as regions

KEYS = count()


@pytest.fixture
def vienna(service, plan):
    """A committed trip that also takes in Vienna (the example's places stay in Oslo and Bergen), everyone p1—p3."""
    plan = deepcopy(plan)
    plan["areas"] = [*plan["areas"], "Wien"]
    turn = service.turn("alice", "Create the trip.", "seed", scenario="quick")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=plan, idempotency_key="seed")
    service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)
    for context in service.store.all("contexts", "alice"):  # the seed turn's questions are not these tests' (the next turn would settle them)
        service.store.put("contexts", "alice", dict(context, asked=[]))
    return service


def said(service, text, *, record=None, scenario="quick", speaker="p1", scope=None):
    key = f"m1-{next(KEYS)}"
    return service.turn("alice", text, key, nature="user_verbatim", native_ref=f"fixture:{key}", speaker=speaker,
                        record=record, scenario=scenario, scope=scope)


def agent(service, text, *, record=None, scenario="quick"):
    return service.turn("alice", text, f"m1-{next(KEYS)}", nature="agent_inference", record=record, scenario=scenario)


def item(kind, key, text, *, quote, basis="user_explicit", strength="prefer", long_term=False, value=None, scope=None):
    return dict(kind=kind, key=key, quote=quote, text=text, subject=["*"], strength=strength, long_term=long_term, value=value,
                scope=scope or {}, source=dict(event="this", basis=basis))


def kinds(turn):
    return [entry["kind"] for entry in turn["consider"]["entries"]]


# --- 1. consider: what this question must weigh ---------------------------------------------------------------------

def test_a_quick_question_lists_only_what_bears_on_it_and_a_plan_lists_people_destination_and_sources(vienna):
    said(vienna, "我们喜欢安静的咖啡馆。", record=dict(items=[item("preference", "cafe", "喜欢安静的咖啡馆", quote="我们喜欢安静的咖啡馆。")]))
    quick = said(vienna, "附近有咖啡馆吗？")
    assert "preference" in kinds(quick)
    assert not {"person", "destination", "source"} & set(kinds(quick))  # bad sample: a quick answer is not handed the whole trip
    plan = said(vienna, "帮我排明天。", scenario="plan")
    assert {"preference", "person", "destination", "source", "app", "ask_languages"} <= set(kinds(plan))
    entry = next(entry for entry in plan["consider"]["entries"] if entry["kind"] == "preference")
    assert entry["text"] == "喜欢安静的咖啡馆" and entry["stability"] == "changing" and entry["from"]["record"] == entry["id"]
    assert next(entry for entry in plan["consider"]["entries"] if entry["kind"] == "destination")["stability"] == "stable"
    assert "逐条看 consider" in plan["next"]


def test_a_long_list_is_cut_at_the_scenario_limit_and_state_memory_lists_everything(vienna):
    quote = "我们喜欢公园、博物馆、咖啡、甜点、市场、河边、老街、书店。"
    words = ["公园", "博物馆", "咖啡", "甜点", "市场", "河边", "老街", "书店"]
    said(vienna, quote, record=dict(items=[item("preference", f"likes-{index}", f"喜欢{word}", quote=quote) for index, word in enumerate(words)]))
    quick = said(vienna, "现在去哪？")
    limit = consider.table()["scenarios"]["quick"]["limit"]
    assert len(quick["consider"]["entries"]) == limit and quick["consider"]["more"] > 0
    assert "state(view='memory')" in quick["consider"]["more_how"] and "state(view='memory')" in quick["next"]
    listed = {entry["id"] for entry in vienna.state("alice", view="memory")["consider"]}
    assert {entry["id"] for entry in quick["consider"]["entries"]} < listed
    assert sum(1 for entry in vienna.state("alice", view="memory")["consider"] if entry["kind"] == "preference") == len(words)


def test_decisions_carry_must_and_a_must_comes_first(vienna):
    turn = said(vienna, "晚饭一定要去 A 店，午饭在 B 也行。")
    decisions = [dict(question="晚饭", options=["A"], choice="A", decided_by="user", must=True, subject=["*"],
                      source=dict(event=turn["receipt"]["event_id"], basis="user_explicit")),
                 dict(question="午饭", options=["B"], choice="B", decided_by="user", subject=["*"],
                      source=dict(event=turn["receipt"]["event_id"], basis="user_explicit"))]
    draft = vienna.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], changes=dict(decisions=decisions), idempotency_key="dec")
    vienna.commit("alice", draft["draft_id"], "dec")
    entries = said(vienna, "接下来呢？", scenario="plan")["consider"]["entries"]
    first, second = (next(entry for entry in entries if entry["text"].startswith(name)) for name in ("晚饭", "午饭"))
    assert first["must"] is True and second["must"] is False and entries.index(first) < entries.index(second)


def test_settings_stay_out_of_the_constraint_pack(vienna):
    turn = said(vienna, "这趟查车用 WienMobil。", record=dict(items=[item("setting", "app:timetable", "这趟查车用 WienMobil", quote="这趟查车用 WienMobil。",
                                                                         value="wienmobil", scope={"place": "at-vienna"})]))
    pack = vienna.store.get("contexts", "alice", turn["ctx_id"])
    assert all(record.get("kind") != "setting" for record in pack["pack"]["records"].values())
    assert all(entry["kind"] != "setting" for entry in pack["bundle"]["items"])
    assert any(entry["kind"] == "app" and entry["text"] == "这趟查车用 WienMobil" for entry in turn["consider"]["entries"])


# --- 2. stable and changing -----------------------------------------------------------------------------------------

def test_changing_the_destination_needs_the_users_decision_this_turn(vienna):
    plan = deepcopy(vienna.state("alice")["plan"])
    plan["areas"] = [*plan["areas"][:-1], "Salzburg"]
    turn = agent(vienna, "agent replans")
    draft = vienna.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=plan, idempotency_key="move-1")
    assert draft["status"] == "rejected" and "not admitted (stable): the plan changes /areas (destination" in draft["notice"]
    turn = said(vienna, "我们改去萨尔茨堡。")
    decision = dict(question="去哪", options=["Salzburg"], choice="Salzburg", decided_by="user", subject=["*"], locked_paths={"/areas": plan["areas"]},
                    source=dict(event=turn["receipt"]["event_id"], basis="user_explicit"))
    draft = vienna.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=plan, changes=dict(decisions=[decision]),
                           idempotency_key="move-2")
    assert draft["status"] == "proposed", draft["notice"]
    assert vienna.commit("alice", draft["draft_id"], "move-2", accept_unverified=True)["plan"]["areas"][-1] == "Salzburg"


def test_a_decision_from_an_earlier_message_does_not_authorize_a_people_change(vienna):
    plan = deepcopy(vienna.state("alice")["plan"])
    plan["people"].append(dict(id="p4", name="Dana"))
    earlier = said(vienna, "Dana 也来。")
    turn = said(vienna, "排一下。")
    decision = dict(question="谁来", options=["加 Dana"], choice="加 Dana", decided_by="user", subject=["*"],
                    locked_paths={"/people": plan["people"]}, source=dict(event=earlier["receipt"]["event_id"], basis="user_explicit"))
    draft = vienna.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=plan, changes=dict(decisions=[decision]),
                           idempotency_key="people")
    assert draft["status"] == "rejected" and "/people (person" in draft["notice"] and "this turn" in draft["notice"]


def test_a_changing_wish_is_replaced_and_said_a_stable_one_is_not_replaced_by_an_inference(vienna):
    said(vienna, "想去美景宫。", record=dict(items=[item("goal", "visit", "想去美景宫", quote="想去美景宫。")]))
    replaced = agent(vienna, "改成想去美泉宫", record=dict(items=[item("goal", "visit", "想去美泉宫", quote="", basis="agent_inference")]))
    assert replaced["recorded"]["replaced"][0]["old"] == "想去美景宫" and replaced["recorded"]["replaced"][0]["stability"] == "changing"
    assert "想去美景宫 → 想去美泉宫" in replaced["next"]
    said(vienna, "我一直只喝无糖饮料。", record=dict(items=[item("preference", "drink", "只喝无糖饮料", quote="我一直只喝无糖饮料。", long_term=True)]))
    refused = agent(vienna, "改成可以喝甜的", record=dict(items=[item("preference", "drink", "甜的也行", quote="", basis="agent_inference")]))
    assert refused["recorded"]["not_recorded"][0]["part"] == "items" and "not admitted (stable)" in refused["recorded"]["not_recorded"][0]["reason"]
    assert "先问用户" in refused["recorded"]["not_recorded"][0]["ask"]
    assert [entry["text"] for entry in vienna.ledger.current("alice")["items"] if entry["key"] == "drink"] == ["只喝无糖饮料"]
    accepted = said(vienna, "这次甜的也行。", record=dict(items=[item("preference", "drink", "这次甜的也行", quote="这次甜的也行。")]))
    assert not accepted["recorded"]["not_recorded"] and accepted["recorded"]["replaced"][0]["stability"] == "stable"


def test_the_user_can_make_a_kind_stable(vienna):
    said(vienna, "想去的地方定了就别改。", record=dict(items=[item("setting", "stability:want_to_go", "想去的地方定了基本不变", quote="想去的地方定了就别改。",
                                                         value="stable")]))
    said(vienna, "想去美景宫。", record=dict(items=[item("goal", "visit", "想去美景宫", quote="想去美景宫。")]))
    refused = agent(vienna, "改成想去美泉宫", record=dict(items=[item("goal", "visit", "想去美泉宫", quote="", basis="agent_inference")]))
    assert "not admitted (stable)" in refused["recorded"]["not_recorded"][0]["reason"]


# --- 3. this trip's sources and notes -------------------------------------------------------------------------------

def test_sources_merge_this_trip_over_long_term_over_the_region_table(vienna):
    at = "2030-06-10T09:00:00+02:00"
    plan = vienna.state("alice")["plan"]
    table = consider.sources(vienna.store, "alice", plan, at=at)
    assert table["apps"] == {"at-vienna": "google-maps"} and table["languages"] == ["en", "de"]
    said(vienna, "以后都用 ÖBB 查车，英语法语问。", record=dict(items=[
        item("setting", "app:timetable", "长期：用 ÖBB 查车", quote="以后都用 ÖBB 查车", value="oebb-scotty", long_term=True),
        item("setting", "ask_languages", "长期：英语法语问", quote="英语法语问", value=["fr"], long_term=True)]))
    long_term = consider.sources(vienna.store, "alice", plan, at=at)
    assert long_term["apps"] == {"at-vienna": "oebb-scotty"} and long_term["languages"] == ["en", "fr"]
    said(vienna, "这趟查车用 WienMobil。", record=dict(items=[item("setting", "app:timetable", "这趟用 WienMobil", quote="这趟查车用 WienMobil。",
                                                                 value="wienmobil", scope={"place": "at-vienna"})]))
    trip = consider.sources(vienna.store, "alice", plan, at=at)
    assert trip["apps"] == {"at-vienna": "wienmobil"} and trip["reports"][0]["timetable"] == "wienmobil"
    app = next(entry for entry in trip["entries"] if entry["kind"] == "app")
    assert app["from"]["tier"] == "trip" and app["stability"] == "stable"


def test_every_region_row_gives_an_app_english_and_its_languages(service):
    for row in regions()["regions"]:
        found = consider.sources(service.store, "alice", None, at="2030-06-10T09:00:00+02:00", region=row["id"])
        assert found["apps"] == {row["id"]: row["timetable"]}
        assert found["languages"] == list(dict.fromkeys(["en", *row["languages"]]))
        assert [entry["from"] for entry in found["entries"] if entry["kind"] == "app"] == [{"table": "regions.yaml", "row": row["id"]}]
        if row["match"]:
            assert row["id"] in [match["id"] for match in consider.regions_of(dict(areas=[row["match"][0].title()], days=[]))]
    assert [row["id"] for row in consider.regions_of(dict(areas=["Oslo"], days=[]))] == ["default"]


def test_how_to_check_names_this_trips_app(vienna):
    view = dict(legs=[dict(walk=False, **{"from": "Wien Hbf", "to": "Wien Mitte"}, depart="09:00", depart_at="2030-06-10T09:00", real_time=False,
                           platform=None)])
    default = how_to_check(view, (None, None), "at-vienna", [], "zh")
    assert default["app"] == "Google 地图"
    said(vienna, "这趟查车用 ÖBB。", record=dict(items=[item("setting", "app:timetable", "这趟用 ÖBB", quote="这趟查车用 ÖBB。", value="oebb-scotty",
                                                             scope={"place": "at-vienna"})]))
    reports, offer = trip_sources(vienna.store, "alice", vienna.state("alice")["plan"], "at-vienna", "zh")
    assert how_to_check(view, (None, None), "at-vienna", [], "zh", reports)["app"] == "ÖBB App"
    assert offer == dict(say="我可以帮你起草询问内容（英语、德语）。", languages=["en", "de"])


# --- 4. what to ask, and silence ------------------------------------------------------------------------------------

def test_must_go_is_asked_when_planning_until_answered_or_silent(vienna):
    quick = said(vienna, "附近有吃的吗？")
    assert "must_go" not in [question["id"] for question in quick["ask"]]  # bad sample: a quick question is not a plan
    asked = said(vienna, "帮我排一下。", scenario="plan")
    question = next(question for question in asked["ask"] if question["id"] == "must_go")
    assert question["say"].startswith("有没有必须要去的地方") and "ask 里的问题照问" in asked["next"]
    silent = said(vienna, "排得轻松点。", scenario="plan")
    assert {"id": "must_go", "scope": {}, "text": "用户没提必须去的地方，按没有算"} in silent["recorded"]["defaulted"]
    assert "must_go" not in [question["id"] for question in silent["ask"]]
    assert not [entry for entry in vienna.ledger.current("alice")["items"] if entry["key"] == "must_go"]  # silence writes no record
    day = said(vienna, "明天呢？", scenario="plan", scope={"start": "2030-06-11"})
    assert "must_go" not in [question["id"] for question in day["ask"]]  # the trip-wide answer covers each day


def test_a_day_asked_alone_is_answered_alone(vienna):
    said(vienna, "11 号没有必须去的。", scenario="plan", scope={"start": "2030-06-11"},
         record=dict(items=[item("setting", "must_go", "11 号没有必须去的", quote="11 号没有必须去的。", value="none", scope={"start": "2030-06-11", "end": "2030-06-11"})]))
    day = said(vienna, "11 号排一下。", scenario="plan", scope={"start": "2030-06-11"})
    trip = said(vienna, "整趟排一下。", scenario="plan")
    assert "must_go" not in [question["id"] for question in day["ask"]]
    assert "must_go" in [question["id"] for question in trip["ask"]]


def test_real_time_is_asked_once_the_destination_is_set(service, vienna):
    asked = said(vienna, "帮我排一下。", scenario="plan")
    question = next(question for question in asked["ask"] if question["id"] == "realtime")
    assert "Wiener Linien" in question["say"] and "Transitous" in question["say"]
    answered = said(vienna, "不用实时。", scenario="plan", record=dict(items=[item("setting", "source:realtime", "这趟不配实时", quote="不用实时。", value={"on": False})]))
    assert "realtime" not in [question["id"] for question in answered["ask"]]
    assert any(entry["id"] == answered["recorded"]["changed"][0] and entry["kind"] == "source"
               for entry in said(vienna, "再排。", scenario="plan")["consider"]["entries"])


def test_real_time_is_not_asked_without_a_destination(service):
    scratch = service.turn("alice", "我想去旅行。", "scratch")
    ids = [question["id"] for question in scratch["ask"]]
    assert "must_go" in ids and "realtime" not in ids


# --- 5. the offer to draft a question -------------------------------------------------------------------------------

def test_the_offer_has_english_and_the_local_language(vienna, service):
    turn = said(vienna, "怎么问店员？")
    assert turn["offer"]["say"] == "我可以帮你起草询问内容（英语、德语）。" and turn["offer"]["spec"]["languages"] == ["en", "de"]
    assert consider.offer(["en"], "en")["say"] == "I can draft what to ask, in English."
    assert consider.offer(["en", "de"])["spec"]["sentences_per_language"] == 4


# --- 6. "locked" only for a must ------------------------------------------------------------------------------------

def test_only_a_must_is_written_as_locked(vienna):
    plan = vienna.state("alice")["plan"]
    block = plan["days"][0]["plans"][0]["tracks"][0]["blocks"][0]
    turn = said(vienna, "第一站定了。")
    decision = dict(question="第一站", options=[block["id"]], choice=block["id"], decided_by="user", subject=["*"],
                    locked_paths={f"/blocks/{block['id']}/start": block["start"]}, source=dict(event=turn["receipt"]["event_id"], basis="user_explicit"))
    draft = vienna.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], changes=dict(decisions=[decision]), idempotency_key="nm")
    vienna.commit("alice", draft["draft_id"], "nm")
    pack = vienna.store.get("contexts", "alice", said(vienna, "接着呢？", scenario="plan")["ctx_id"])["pack"]
    record = next(record for record in pack["records"].values() if record["type"] == "decision")
    assert record["must"] is False
    markdown = render_pack(pack)["markdown"]
    assert "（可调整）" in markdown and "锁定 /blocks" not in markdown
    assert "锁定 /blocks" in render_pack(dict(pack, records={key: dict(value, must=True) if value["type"] == "decision" else value
                                                             for key, value in pack["records"].items()}))["markdown"]
