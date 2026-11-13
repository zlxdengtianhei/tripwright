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


def test_an_at_that_names_no_place_says_where_the_plan_has_the_user(service, plan, monkeypatch):
    # DeepSeek, 2026-11-12 live replay: “说一个最最迟的时间，让我有个底” came as an `at` without a place, beside a point the
    # service could answer, and the first part said “按现在 13:48 算，你在‘说一个最最迟的时间，让我有个底’”. Words that name
    # no stored place, sent without a place, are no place the user named: the line says where the plan has the user.
    next(place for place in plan["places"] if place["id"] == "oslo-cafe")["coords"] = [59.9100, 10.7300]  # fictional
    trip_with_a_node(service, plan, monkeypatch, now="2030-06-10T17:30:00+02:00")  # the plan: dinner at the café, 17:00-18:00
    points = [dict(kind="at", said="说一个最最迟的时间，让我有个底"), dict(kind="do", said="画廊", place="oslo-art")]
    lines = service.turn("alice", ASKED, None, scenario="quick", points=points)["first_part"]["lines"]
    assert any(line.startswith("按现在 17:30 算，假设你在") and "计划里这时在这" in line for line in lines)
    assert "最最迟的时间" not in "".join(lines)


def test_an_at_the_user_named_still_says_it(service, plan, monkeypatch):
    # unchanged: words that name a stored place, and a place the host named that is not stored (S4h-a)
    trip_with_a_node(service, plan, monkeypatch, now="2030-06-10T17:30:00+02:00")
    stored = [dict(kind="at", said="刚到奥斯陆虚构画廊"), dict(kind="do", said="画廊", place="oslo-art")]
    lines = service.turn("alice", ASKED, None, scenario="quick", points=stored)["first_part"]["lines"]
    assert not any("假设你在" in line for line in lines) and any("奥斯陆虚构画廊" in line for line in lines)
    unknown = [dict(kind="at", said="在一家没存的咖啡馆", place="Café Nirgendwo"), dict(kind="do", said="画廊", place="oslo-art")]
    first = service.turn("alice", ASKED, None, scenario="quick", points=unknown)["first_part"]
    assert first is None or not any("假设你在" in line for line in first["lines"])


def test_a_stored_place_the_question_is_about_is_not_where_the_user_is(service, plan, monkeypatch):
    # G7 (analysis 2026-11-13; README's known case): “机场我进得去吗？” with the airport marked `at` said “你在机场”. A stored
    # place marked `at` that is not where the plan has the user, with no words that put the user there, counts from the
    # plan's place, as assumed (which asks the user to say so if not); the marked place stays in `mentioned`.
    next(place for place in plan["places"] if place["id"] == "oslo-cafe")["coords"] = [59.9100, 10.7300]  # fictional
    trip_with_a_node(service, plan, monkeypatch, now="2030-06-10T17:30:00+02:00")  # the plan: dinner at the café, 17:00-18:00
    for question, said, head in (("画廊我进得去吗？", "画廊", "按现在 17:30 算，你提到奥斯陆虚构画廊；按计划你这时在"),
                                 ("Can I get into the gallery?", "the gallery", "As of 17:30: you mentioned Example Oslo Gallery; the plan has you at")):
        points = [dict(kind="at", said=said, place="oslo-art"), dict(kind="do", said=said, place="oslo-art")]
        result = service.turn("alice", question, None, scenario="quick", points=points)
        lines = result["first_part"]["lines"]
        # both places are said, counted from the plan's (analysis 5.6): "你提到 X；按计划你这时在 Y……已经在 X 就回一句"
        assert any(line.startswith(head) and ("已经在奥斯陆虚构画廊就回一句" in line or "already at Example Oslo Gallery" in line)
                   for line in lines), lines
        assert result["points"]["at"]["place"] == "oslo-cafe" and result["points"]["at"]["mentioned"]["place"] == "oslo-art"
    # words that put the user there keep the place they said
    for question in ("我到画廊了，进得去吗？", "I'm at the gallery, can I get in?"):
        points = [dict(kind="at", said="画廊", place="oslo-art"), dict(kind="do", said="画廊", place="oslo-art")]
        lines = service.turn("alice", question, None, scenario="quick", points=points)["first_part"]["lines"]
        assert not any("假设你在" in line or "assuming you are" in line for line in lines), lines
