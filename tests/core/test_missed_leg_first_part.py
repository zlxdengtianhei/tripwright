"""A missed connection gets a first part only from what the plan stores for the leg just missed (2026-11-12).

D and D′ (measured, not adopted, D-21) gave a missed-bus question the plan's generic draft: the afternoon's and tomorrow's
nodes, which answered none of 18 questions. Now the first part needs three things: the words name a missed connection;
the followed plan has the user's travel leg (with origin and destination) that left in the last 30 minutes; and the plan
stores a fallback for it, either the alternative its author wrote on that departure's hard node (the default sentence
does not count) or the same leg leaving later in another of the day's plans. Without any of them, no first part, as before."""
import time
from copy import deepcopy

import pytest

MISSED = "我们好像刚错过了车，现在怎么走"
LEG = dict(id="leg-bus", kind="travel", certainty="fixed", start="12:00", end="12:20", origin="oslo-art", destination="oslo-cafe",
           line="Bus 31", text="Gallery → café")


def trip(service, plan, monkeypatch, *, later=True, structured=True, alternative=None, now="2030-06-10T12:08:00+02:00"):
    monkeypatch.setenv("TRIPWRIGHT_NOW", now)
    monkeypatch.setenv("TRIPWRIGHT_NOW_SET_AT", str(time.time()))
    day = plan["days"][0]
    leg = dict(LEG) if structured else {key: value for key, value in LEG.items() if key not in ("origin", "destination")}
    first = day["plans"][0]
    first["tracks"][0]["blocks"] = sorted([*first["tracks"][0]["blocks"], leg], key=lambda block: block["start"])
    if later:
        second = deepcopy(first)
        second["name"] = "Later bus"
        # block ids are unique across a day's plans; the later bus is the same leg 30 minutes on
        second["tracks"][0]["blocks"] = [dict(block, start="12:30", end="12:50", id="leg-bus-later") if block["id"] == "leg-bus"
                                          else dict(block, id=block["id"] + "-b") for block in second["tracks"][0]["blocks"]]
        day["plans"] = [first, second]
    if alternative is not None:
        day["anchors"] = [dict(id="bus-node", time="12:00", text="Bus 31 from the gallery (fictional)", subject=["p1", "p2", "p3"],
                               alternative=alternative)]
    turn = service.turn("alice", "创建示例行程。", "seed")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=plan, idempotency_key="seed")
    service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)


def ask(service, text=MISSED, points=None):
    return service.turn("alice", text, None, scenario="quick", points=points if points is not None else [dict(kind="other", said="错过了车")])


def test_the_missed_leg_and_its_stored_later_option(service, plan, monkeypatch):
    trip(service, plan, monkeypatch)
    lines = ask(service)["first_part"]["lines"]
    assert lines[0] == "按存的计划先说（时刻还没核）"
    assert lines[1].startswith("刚误掉的：12:00 Bus 31 ") and "（计划里的这一段）" in lines[1]
    assert lines[2].startswith("计划里存的晚一班：12:30 Bus 31 ") and lines[2].endswith("（存档，没核）")
    assert len(lines) == 3  # nothing about the afternoon or tomorrow


def test_no_stored_fallback_no_first_part(service, plan, monkeypatch):
    trip(service, plan, monkeypatch, later=False)
    assert ask(service)["first_part"] is None


def test_a_leg_without_origin_and_destination_is_not_found(service, plan, monkeypatch):
    trip(service, plan, monkeypatch, structured=False)
    assert ask(service)["first_part"] is None


def test_an_unrelated_question_is_not_drafted(service, plan, monkeypatch):
    trip(service, plan, monkeypatch)
    for text in ("今天下午会下雨吗？要不要带伞？", "这附近有没有厕所？"):
        assert ask(service, text, [dict(kind="other", said=text)])["first_part"] is None


@pytest.mark.parametrize("alternative, said", [(None, False), ("Confirm locally and retain an earlier departure option.", False),
                                               ("走 15 分钟到 Storgata 坐 Bus 54", True)])
def test_only_a_fallback_its_author_wrote_counts(service, plan, monkeypatch, alternative, said):
    # the default sentence (keep an earlier departure) is no answer once the bus is gone
    trip(service, plan, monkeypatch, later=False, alternative=alternative)
    first = ask(service)["first_part"]
    if said:
        assert first["lines"][2] == "计划里给这一段写的备选：走 15 分钟到 Storgata 坐 Bus 54（存档，没核）"
    else:
        assert first is None


def test_more_than_30_minutes_after_the_leg_is_not_just_missed(service, plan, monkeypatch):
    trip(service, plan, monkeypatch, now="2030-06-10T12:45:00+02:00")
    assert ask(service)["first_part"] is None


def test_a_usable_host_point_comes_first(service, plan, monkeypatch):
    # the host's points first: a stored place it named answers as before, not the missed-leg draft
    trip(service, plan, monkeypatch)
    first = ask(service, points=[dict(kind="do", said="美术馆", place="oslo-art")])["first_part"]
    assert first is not None and not any(line.startswith("刚误掉的") for line in first["lines"])
