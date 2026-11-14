"""R-07: when a plan reaches a region without a real-time transport source, the turn says so and asks once whether to
set one up, with the setup guide (docs/transit-sources.md); a deployment source with real time for the trip's areas
needs no question. Each promise has a bad sample and a legal one."""
from copy import deepcopy
from itertools import count

import pytest

from tripwright.providers import sources

KEYS = count()


def trip(service, plan, *areas):
    """A committed trip that also takes in `areas` (the example's places stay in Oslo and Bergen)."""
    plan = deepcopy(plan)
    plan["areas"] = [*plan["areas"], *areas]
    turn = service.turn("alice", "Create the trip.", "seed", scenario="quick")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=plan, idempotency_key="seed")
    service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)
    for context in service.store.all("contexts", "alice"):
        service.store.put("contexts", "alice", dict(context, asked=[]))
    return service


def said(service, text, *, record=None, scenario="plan"):
    key = f"r3-{next(KEYS)}"
    return service.turn("alice", text, key, nature="user_verbatim", native_ref=f"fixture:{key}", speaker="p1", record=record,
                        scenario=scenario)


def question(turn):
    return next((item for item in turn["ask"] if item["id"] == "realtime"), None)


@pytest.fixture
def deployment(monkeypatch, tmp_path):
    def configure(realtime, match=("wien", "vienna", "oslo", "bergen")):
        path = tmp_path / f"sources-{realtime}.yaml"
        path.write_text("sources:\n  - id: wl\n    name: {en: Vienna (own MOTIS), zh: 维也纳（自托管 MOTIS）}\n"
                        "    api: http://motis:8080/api\n    bbox: [16.1, 48.1, 16.6, 48.35]\n    attribution: Wiener Linien, CC BY 4.0\n"
                        f"    match: [{', '.join(match)}]\n    realtime: {str(realtime).lower()}\n", encoding="utf-8")
        monkeypatch.setenv(sources.ENV, str(path))
    return configure


def test_a_region_without_a_source_is_named_and_the_guide_is_linked(service, plan):
    trip(service, plan, "Wien")
    asked = question(said(service, "帮我排一下。"))
    assert asked["say"].startswith("维也纳市内没接实时交通源")
    # The region's known sources cannot be connected yet: named as such, never offered as something to set up.
    assert "现在没有能直接接上的实时源（Wiener Linien、VAO 是自有接口，还没有适配器）" in asked["say"]
    assert "要不要我先找找这一带有没有" in asked["say"] and "要不要给你的部署配一个" not in asked["say"]  # bad sample: the old offer
    assert "GTFS-RT" in asked["then"] and "source:realtime" in asked["then"]  # a yes has a next step the agent can take
    assert sources.DOC["zh"] in asked["say"] and asked["doc"] == sources.DOC["zh"]
    assert "本人申请 key" in asked["say"] and "Transitous" in asked["say"]


def test_a_region_with_no_listed_option_gets_the_generic_one_not_viennas(service, plan):
    trip(service, plan)  # Oslo and Bergen: only the default row of the region table
    asked = question(said(service, "帮我排一下。"))
    assert asked["say"].startswith("这趟去的地方没接实时交通源") and "GTFS-RT" in asked["say"]
    assert "Wiener Linien" not in asked["say"] and "VAO" not in asked["say"]  # bad sample: the old fallback named Vienna's sources


def test_the_question_is_in_english_for_an_english_message(service, plan):
    trip(service, plan, "Vienna")
    asked = question(said(service, "Please plan the trip."))
    assert asked["say"].startswith("Vienna: no real-time transport source is connected") and sources.DOC["en"] in asked["say"]
    assert "(Wiener Linien, VAO: their own APIs, no adapter yet)" in asked["say"] and "Shall I look for one?" in asked["say"]


def test_it_is_asked_once_a_no_is_kept_and_silence_counts_as_no(service, plan):
    trip(service, plan, "Wien")
    assert question(said(service, "帮我排一下。"))
    silent = said(service, "排得轻松点。")
    assert any(entry["id"] == "realtime" for entry in silent["recorded"]["defaulted"]) and question(silent) is None
    assert question(said(service, "再排一下。")) is None  # bad sample: asking again on every plan


def test_an_answer_is_recorded_and_not_asked_again(service, plan):
    trip(service, plan, "Wien")
    assert question(said(service, "帮我排一下。"))
    record = dict(items=[dict(kind="setting", key="source:realtime", quote="不用配。", text="这趟不配实时", subject=["*"],
                              strength="prefer", value={"on": False}, source=dict(event="this", basis="user_explicit"))])
    assert question(said(service, "不用配。", record=record)) is None
    assert question(said(service, "再排。")) is None


def test_an_area_beside_the_covered_one_is_still_asked_about(service, plan, deployment):
    deployment(True, match=("wien",))
    trip(service, plan, "Wien")  # Oslo and Bergen stay in the trip, and nothing covers them
    assert question(said(service, "帮我排一下。"))["say"].startswith("这趟去的地方没接实时交通源")


def test_a_deployment_source_with_real_time_for_the_area_needs_no_question(service, plan, deployment):
    deployment(True)
    trip(service, plan, "Wien")
    turn = said(service, "帮我排一下。")
    assert question(turn) is None
    assert any(entry["id"] == "source:wl" and entry["text"] == "维也纳（自托管 MOTIS）：开" for entry in turn["consider"]["entries"])


def test_a_deployment_source_without_real_time_still_asks(service, plan, deployment):
    deployment(False)
    trip(service, plan, "Wien")
    assert question(said(service, "帮我排一下。"))  # bad sample: a timetable-only source taken as real time


def test_only_the_areas_the_source_does_not_cover_are_named(service, plan, deployment):
    deployment(True)
    trip(service, plan, "Wien", "Salzburg")
    asked = question(said(service, "帮我排一下。"))
    assert asked and asked["say"].startswith("奥地利其他乡村与区域巴士没接实时交通源")
    assert "维也纳市内" not in asked["say"]  # bad sample: naming the region the deployment already covers
    assert "（VAO 是自有接口" in asked["say"] and "Wiener Linien" not in asked["say"]
