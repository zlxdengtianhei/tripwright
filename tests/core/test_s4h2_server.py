"""S4h2 (self replay of S4h on 92a094fa; two kinds of error the service and its check can stop):

1. The first question's two parts gave two ways (sub_on, ns_on, mcponly, codex): the first part the plan's “21:45 从 <岩馆>
   出门 … 坐 22:42 RJX”, the second part's main choice “22:00 关门出来 … 坐 22:16 的 5B”, and nothing said whether the plan's
   way still held. The service's sentence for the plan's train now says it.

Offline: the fictional plan and MOTIS of test_a16_fixes (public Vienna stop names, made-up times), fictional places."""
from __future__ import annotations

from datetime import datetime

import httpx

from test_a8_transit_facts import HBF, NIGHT, STREET, local
from test_a10_frame import Evening, fixed, query
from test_a12_first_part import BUSES, FIRST, FOOD, NORTH, SOUTH
from test_a14_fixes import GO
from test_a16_fixes import WAYS, make  # noqa: F401 (make is a fixture)

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


# --- 1. the two parts' ways ----------------------------------------------------------------------------------------------------

def test_the_plans_train_sentence_says_whether_the_plans_own_way_still_holds(make, monkeypatch):
    service = make(TwoWays(trains=NIGHT[:5], buses=BUSES), tracks=WAYS, places=(CLOSING, SOUTH, *FOOD))
    first = service.turn("local", FIRST, "q1", scenario="quick", points=GO)["first_part"]["lines"][1]
    assert first.startswith("去维也纳机场：计划里是 21:45 从北边的岩馆出门，22:13 到 Wien Hauptbahnhof，坐今晚 22:53 REX 7")
    fixed(monkeypatch, local("21:05"))
    found = query(service, "latest_departure", origin="gym-north", destination="airport")
    planned = next(line for line in found["sentences"] if line.startswith("计划里的 22:53 REX 7 赶得上"))
    # bad sample (mcponly_r1 question 1 after S4h): the latest way alone, nothing on the plan's 21:45 way the first part gave
    assert planned == ("计划里的 22:53 REX 7 赶得上：22:10 关门出来（按存的营业时间），走到 Wien Matzleinsdorfer Platz，换 1 次车：22:33 D（往 Hauptbahnhof）Wien Matzleinsdorfer Platz → Wien Hauptbahnhof 22:45，换 22:53 REX 7（往 Flughafen Wien）Wien Hauptbahnhof → Flughafen Wien Bahnhof 23:15；23:15 到；"
                       "计划的 21:45 出门那条（存的走法）也赶得上，想早走就照它")
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
