"""A latest-time question whose points the host marked all `other` gets the plan's hard nodes as its first part.

GPT marked “你去说一个最最迟的时间……机场能不能进去，我过夜怎么过” (the trip-log page's question, public) all `other` in 17
of 20 runs on 2026-11-12, so the service drafted no first part and the first reply came 8 to 27 s later, after the lookups
(H-02: no long silence). The service now reads the latest-time intent in the words when the host named no point it can use.
A point the host named and the service cannot place still gets no first part (A12, S4h): that is unchanged."""
import time

TITLE = "按存的计划先说（时刻还没核）"
# the trip-log question and the points GPT sent with it (run late2/luna__p8_zh__8, 2026-11-12), as they were
ASKED = "你去说一个最最迟的时间，让我有一个这个底。机场能不能进去，我过夜怎么过"
GPT_POINTS = [dict(kind="other", said="要一个最晚时间作为底"), dict(kind="other", said="机场能不能进去"),
              dict(kind="other", said="怎么在机场过夜")]


def trip_with_a_node(service, plan, monkeypatch, now="2030-06-10T12:00:00+02:00"):
    monkeypatch.setenv("TRIPWRIGHT_NOW", now)
    monkeypatch.setenv("TRIPWRIGHT_NOW_SET_AT", str(time.time()))
    plan["days"][0]["anchors"] = [dict(id="desk", time="21:30", text="值机柜台关闭（虚构）", subject=["p1", "p2", "p3"], place="oslo-cafe")]
    turn = service.turn("alice", "创建示例行程。", "seed")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=plan, idempotency_key="seed")
    service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)
    return service


def test_the_measured_question_with_the_points_gpt_sent_gets_a_first_part(service, plan, monkeypatch):
    trip_with_a_node(service, plan, monkeypatch)
    result = service.turn("alice", ASKED, None, scenario="quick", points=GPT_POINTS)
    lines = result["first_part"]["lines"]
    assert lines[0] == TITLE  # it says it is the stored plan, times not checked
    assert any("21:30" in line and "存档" in line for line in lines)
    assert "query(kind='latest_departure'" in result["after_first_part"]


def test_a_latest_question_without_an_answerable_point_gets_the_plans_latest_draft(service, plan, monkeypatch):
    trip_with_a_node(service, plan, monkeypatch)
    result = service.turn("alice", "你去说一个最最迟的时间，让我有一个这个底", None, scenario="quick",
                          points=[dict(kind="other", said="最最迟的时间")])
    assert result["first_part"]["lines"][0] == TITLE
    assert any("21:30" in line and "存档" in line for line in result["first_part"]["lines"])


def test_another_question_without_an_answerable_point_still_gets_no_first_part(service, plan, monkeypatch):
    trip_with_a_node(service, plan, monkeypatch)
    result = service.turn("alice", "今天的天气怎么样", None, scenario="quick", points=[dict(kind="other", said="天气")])
    assert result["first_part"] is None


def test_a_latest_question_with_no_points_or_a_place_the_host_named_keeps_the_old_rule(service, plan, monkeypatch):
    trip_with_a_node(service, plan, monkeypatch)
    # no points at all: nothing the host marked to back up
    assert service.turn("alice", ASKED, None, scenario="quick", points=[])["first_part"] is None
    # a place the host named and the service cannot place: no stored place stands in for it (S4h)
    named = [dict(kind="other", said="最晚几点"), dict(kind="do", said="去一家没存的咖啡馆", place="Café Nirgendwo")]
    assert service.turn("alice", ASKED, None, scenario="quick", points=named)["first_part"] is None


def test_the_hosts_at_does_not_say_where_the_user_is_on_this_path(service, plan, monkeypatch):
    # GLM, 2026-11-12: “机场能不能进去” sent as `at`, and the first part said “按现在 13:48 算，你在维也纳机场（你说的“机场”）”
    # while the plan had the user at lunch. A draft from the words alone says where the plan has the user, as assumed.
    next(place for place in plan["places"] if place["id"] == "oslo-cafe")["coords"] = [59.9100, 10.7300]  # fictional
    trip_with_a_node(service, plan, monkeypatch, now="2030-06-10T17:30:00+02:00")  # the plan: dinner at the café, 17:00-18:00
    points = [dict(kind="other", said="最晚时间"), dict(kind="at", said="机场能不能进去", place="机场")]
    lines = service.turn("alice", ASKED, None, scenario="quick", points=points)["first_part"]["lines"]
    assert lines[1].startswith("按现在 17:30 算，假设你在") and "计划里这时在这" in lines[1]
    assert "机场能不能进去" not in "".join(lines)
