"""S4h2 (self replay of S4h on 92a094fa; two kinds of error the service and its check can stop):

1. The first question's two parts gave two ways (sub_on, ns_on, mcponly, codex): the first part the plan's “21:45 从 <岩馆>
   出门 … 坐 22:42 RJX”, the second part's main choice “22:00 关门出来 … 坐 22:16 的 5B”, and nothing said whether the plan's
   way still held. The service's sentence for the plan's train now says it; check_answer asks the second part to say again
   the time the first part's first line acts on, or what changed.
2. A host with the command line only wrote its own second part and never said the times were the timetable's (ns_off,
   both questions); check_answer asks for it whenever the second part gives rides this conversation looked up.

Offline: the fictional plan and MOTIS of test_a16_fixes (public Vienna stop names, made-up times), fictional places."""
from __future__ import annotations

from datetime import datetime

import httpx

from test_a8_transit_facts import HBF, NIGHT, STREET, local
from test_a10_frame import Evening, fixed, problems, query
from test_a12_first_part import BUSES, FIRST, FOOD, NORTH, SOUTH
from test_a14_fixes import GO
from test_a16_fixes import WAYS, make  # noqa: F401 (make is a fixture)
from tripwright import cli

GO_ON = "继续"
CLOSING = dict(NORTH, opening_hours=["Mo-Su 09:00-22:10"])  # the gym closes before the latest way to the plan's train


class TwoWays(Evening):
    """Evening where the plan's 22:53 REX is also reached by an earlier tram from the street (walk from 21:47, the 21:50
    D): the plan's own way, besides the latest one (walk from 22:30, the 22:33 D)."""

    async def handle(self, request: httpx.Request) -> httpx.Response:
        response = await super().handle(request)
        params = request.url.params
        if params["toPlace"] == HBF or "," not in params["fromPlace"]:
            return response
        body = response.json()
        legs = self.tram(local("21:50")) + self.to_airport(local("22:53"), False, False)
        if datetime.fromisoformat(legs[0]["startTime"].replace("Z", "+00:00")) >= datetime.fromisoformat(params["time"]):
            early = {"startTime": legs[0]["startTime"], "endTime": legs[-1]["endTime"], "duration": 0, "legs": legs}
            body["itineraries"] = sorted(body["itineraries"] + [early], key=lambda item: item["startTime"])[:int(params["numItineraries"])]
        return httpx.Response(200, json=body)


def draft(*lines: str, at: str = "21:05") -> str:
    """A second part in the replays' shape: title, the server's first line, the main choice, the full plan."""
    return "\n".join([f"# 第二段 · 去机场核完了（{at}）", *lines, "## 完整安排", "- 22:10 出岩馆 → 22:33 D → Wien Hbf → 22:53 REX 7 → 机场"])


def second_checks(service, text: str, request_id: str | None = "q1") -> dict[str, list[str]]:
    found = {}
    arguments = dict(text=text, part="second", **(dict(request_id=request_id) if request_id else {}))
    for entry in query(service, "check_answer", **arguments)["problems"]:
        found.setdefault(entry["check"], []).append(entry["why"] + " | " + entry["fix"])
    return found


# --- 1. the two parts' ways ----------------------------------------------------------------------------------------------------

def test_the_plans_train_sentence_says_whether_the_plans_own_way_still_holds(make, monkeypatch):
    service = make(TwoWays(trains=NIGHT[:5], buses=BUSES), tracks=WAYS, places=(CLOSING, SOUTH, *FOOD))
    first = service.turn("local", FIRST, "q1", scenario="quick", points=GO)["first_part"]["lines"][0]
    assert first.startswith("去维也纳机场：计划里是 21:45 从北边的岩馆出门，22:13 到 Wien Hauptbahnhof，坐今晚 22:53 REX 7")
    fixed(monkeypatch, local("21:05"))
    found = query(service, "latest_departure", origin="gym-north", destination="airport")
    planned = next(line for line in found["sentences"] if line.startswith("计划里的 22:53 REX 7 赶得上"))
    # bad sample (mcponly_r1 question 1 after S4h): the latest way alone, nothing on the plan's 21:45 way the first part gave
    assert planned == ("计划里的 22:53 REX 7 赶得上：22:10 关门出来（按存的营业时间），走到 Wien Matzleinsdorfer Platz，换 1 次车：22:33 D（往 Hauptbahnhof）Wien Matzleinsdorfer Platz → Wien Hauptbahnhof 22:45，换 22:53 REX 7（往 Flughafen Wien）Wien Hauptbahnhof → Flughafen Wien Bahnhof 23:15；23:15 到；"
                       "计划的 21:45 出门那条（存的走法）也赶得上，想早走就照它")
    # the sentence passes on a line the host marks 核过 (the 21:45 is the stored time, marked so), and so does the answer
    sent = draft(found["first_line"], f"主选（核过）：{planned}。", "以上交通时刻按时刻表，不是实时。")
    assert second_checks(service, sent) == {}
    # control: the plan's way not among the connections found is said so, not as one that works
    single = make(tracks=WAYS, places=(CLOSING, SOUTH, *FOOD))
    fixed(monkeypatch, local("21:05"))
    alone = next(line for line in query(single, "latest_departure", origin="gym-north", destination="airport")["sentences"]
                 if line.startswith("计划里的 22:53 REX 7 赶得上"))
    assert alone.endswith("；计划的 21:45 出门那条（存的走法）这次没在结果里，照这条走")
    # control: once the plan's way has begun, or from another place, nothing is added
    fixed(monkeypatch, local("21:50"))
    late = next(line for line in query(service, "latest_departure", origin="gym-north", destination="airport")["sentences"]
                if line.startswith("计划里的 22:53 REX 7 赶得上"))
    assert "计划的 21:45" not in late
    other = query(service, "latest_departure", origin=STREET, destination="airport")
    assert not any("计划的 21:45" in line for line in other["sentences"])


def test_a_second_part_that_drops_the_first_parts_time_without_saying_so_is_refused(make, monkeypatch):
    service = make(TwoWays(trains=NIGHT[:5], buses=BUSES), tracks=WAYS, places=(CLOSING, SOUTH, *FOOD))
    service.turn("local", FIRST, "q1", scenario="quick", points=GO)
    service.turn("local", GO_ON, "q2", scenario="quick", points=[])  # the reply ended with the first part; the user asks on
    fixed(monkeypatch, local("21:05"))
    found = query(service, "latest_departure", origin="gym-north", destination="airport")
    note = "以上交通时刻按时刻表，不是实时。"
    # bad sample (mcponly_r1 question 1 after S4h): the main choice changed to the latest way and the plan's 21:45 is gone
    changed = draft(found["first_line"], "主选：计划里的 22:53 REX 7 赶得上，22:10 关门出来（按存的营业时间），走到 Wien Matzleinsdorfer Platz "
                                         "坐 22:33 的 D 去 Hbf。", note)
    assert second_checks(service, changed)["first_times_followed"] == [
        "第一段首行说了 21:45，第二段没再提，也没说改了 | "
        "可以改得更好（不改也能发）：照 latest_departure 的句子写第一段那条还用不用（“计划的 21:45 出门那条也赶得上…”），或加一行“和第一段不同：21:45 那条……”"]
    # the “继续” turn's request_id names the same question and its first part
    assert "first_times_followed" in second_checks(service, changed, request_id="q2")
    # controls: the time said again, or the change said, passes; without the turn's first part nothing is checked
    assert "first_times_followed" not in second_checks(service, changed.replace("去 Hbf。", "去 Hbf；计划的 21:45 出门那条也行。"))
    assert "first_times_followed" not in second_checks(service, changed.replace(note, "和第一段不同：计划那条不用了，关门再走。\n" + note))
    assert "first_times_followed" not in second_checks(service, changed, request_id=None)


# --- 2. the timetable, said once -----------------------------------------------------------------------------------------------

def test_a_second_part_with_looked_up_rides_says_they_are_the_timetables(make, tmp_path, capsys):
    service = make(tracks=WAYS)
    found = query(service, "latest_departure", origin=STREET, destination="airport")
    # bad sample (ns_off_r1, both questions): the host's own second part, the rides of the lookup, no word on timetable or real time
    ride = "最晚火车怎么走（核过）：00:33 D Wien Matzleinsdorfer Platz → Wien Hauptbahnhof 00:45，00:53 REX 7 → Flughafen Wien Bahnhof 01:15。"
    bare = draft(found["first_line"], ride, at="21:01")
    assert problems(service, bare)["timetable_said"] == [
        "写了这一趟查到的车次时刻，没说是按时刻表还是实时 | 可以改得更好（不改也能发）：加一行照 latest_departure 的 note 写，例如“以上交通时刻按时刻表，不是实时。”"]
    # the command line runs the same check (ns_off checked with --question and was let through)
    question = tmp_path / "question.txt"
    question.write_text(FIRST, encoding="utf-8")
    code = cli.main(["--db", str(service.db_path), "query", "check_answer", "--part", "second", "--text", bare, "--question", str(question)])
    assert code == 0 and "没说是按时刻表还是实时" in capsys.readouterr().err
    # controls: the note (the service's, or in English) passes; a second part with no ride looked up is not asked for it
    assert found["note"] == "以上交通时刻按时刻表，不是实时。"
    assert "timetable_said" not in problems(service, draft(found["first_line"], ride, found["note"], at="21:01"))
    assert "timetable_said" not in problems(service, draft(found["first_line"], ride, "The transit times above are timetable, not real time.", at="21:01"))
    assert "timetable_said" not in problems(service, draft("吃：汉堡店开到 00:00（存的营业时间）。", at="21:01"))
