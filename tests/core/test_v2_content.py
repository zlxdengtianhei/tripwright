"""11-04 (quality card v2, judge a): two content errors came from sentences the service writes for the host to copy.

1. “计划里的 22:42 RJX 19959 赶得上：…走到 Wien Brigittaplatz 坐 22:16 的 5B” named the first ride and nothing after
   it: no direction, no stop to get off, no change. Answers wrote “坐 5B 到 Wien Hauptbahnhof”; 5B ends at Praterstern,
   where the U1 goes on. The sentence now gives the whole way from where the user sets off, ride by ride (line,
   direction, where to get off and change, arrival); where the legs do not join up it says the rest was not found and
   what to search, never reading as direct.
2. “今晚不用赶：夜里一直有车到机场” while nothing ran between the 00:53 train and the 02:52 U1 to the airport bus. “All
   night” is said only when no two departures in a row are more than about an hour apart; else the last one before the
   gap and the first after it, in the second part's first line and in the first part's go line.

Offline: a fictional MOTIS (public Vienna stop and line names, made-up times), fictional places."""
from __future__ import annotations

import re
from datetime import timedelta

from test_a8_transit_facts import DAY, HBF, NIGHT, STREET, leg, local, stop
from test_a10_frame import Evening, fixed, query, second_part, problems
from test_a12_first_part import ASKED, BUSES, FOOD, SECOND, SOUTH
from test_a16_fixes import make  # noqa: F401 (the fixture)

BRIGITTAPLATZ, PRATERSTERN = "at-PTA-Eastern-Region-Flex-2026_at:49:1201", "at-Railway-Current-Reference-Data-2026_Pat:49:1500"


class Brigitta(Evening):
    """Evening where the way from the street to the station is the replay's: walk to Wien Brigittaplatz, bus 5B to
    Praterstern (both ways stop there), U1 to Wien Hauptbahnhof, then the train. `broken` leaves the U1 out, so the
    legs stop joining up at Praterstern."""

    def __init__(self, *args, broken=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.broken = broken

    def to_airport(self, start, from_street, coach, bus="FlixBus N877"):
        legs = super().to_airport(start, False, coach, bus)
        if not from_street:
            return legs
        u1 = start - timedelta(minutes=20)
        bus5 = u1 - timedelta(minutes=16)
        way = [leg("WALK", {"name": "START", "lat": 48.231, "lon": 16.368}, stop("Wien Brigittaplatz", BRIGITTAPLATZ), bus5 - timedelta(minutes=6), bus5),
               leg("BUS", stop("Wien Brigittaplatz", BRIGITTAPLATZ), stop("Wien Praterstern", PRATERSTERN), bus5, bus5 + timedelta(minutes=11),
                   displayName="5B", headsign="Praterstern", realTime=False)]
        if not self.broken:
            way += [leg("WALK", stop("Wien Praterstern", PRATERSTERN), stop("Wien Praterstern", PRATERSTERN), bus5 + timedelta(minutes=11), u1),
                    leg("SUBWAY", stop("Wien Praterstern", PRATERSTERN), stop("Wien Hauptbahnhof", HBF), u1, u1 + timedelta(minutes=8),
                        displayName="U1", headsign="Oberlaa", realTime=False)]
        return way + legs


def plan_sentence(found: dict) -> str:
    return next(line for line in found["sentences"] if line.startswith("计划里的 22:53 REX 7"))


# --- 1. the plan's train: the whole way ---------------------------------------------------------------------------------------

def test_the_plan_train_sentence_gives_every_ride_with_direction_stop_and_change(make):
    service = make(Brigitta(trains=NIGHT[:5], buses=BUSES))
    found = query(service, "latest_departure", origin=STREET, destination="airport")
    said = plan_sentence(found)
    # bad sample (the replay's shape): only where to board the first ride, nothing after it
    assert not re.search(r"坐 22:1\d 的 5B$", said)
    assert said == ("计划里的 22:53 REX 7 赶得上：22:11 出门，换 2 次车：22:17 5B（往 Praterstern）Wien Brigittaplatz → Wien Praterstern "
                    "22:28，换 22:33 U1（往 Oberlaa）Wien Praterstern → Wien Hauptbahnhof 22:41，换 22:53 REX 7（往 Flughafen Wien）"
                    "Wien Hauptbahnhof → Flughafen Wien Bahnhof 23:15；23:15 到")
    # the server's sentences still pass the check as they stand
    assert problems(service, second_part(found["first_line"], *found["sentences"][1:])) == {}


def test_legs_that_do_not_join_up_say_the_rest_was_not_found(make):
    service = make(Brigitta(trains=NIGHT[:5], buses=BUSES, broken=True))
    said = plan_sentence(query(service, "latest_departure", origin=STREET, destination="airport"))
    assert re.fullmatch(r"计划里的 22:53 REX 7 赶得上：22:11 出门，22:17 5B（往 Praterstern）Wien Brigittaplatz → Wien Praterstern 22:28；"
                        r"之后怎么换车没查到，在 \S+ ?App ?搜 Wien Praterstern → Wien Hauptbahnhof", said), said
    assert "不用换车" not in said and "23:15 到" not in said


# --- 2. “all night” only without a gap ---------------------------------------------------------------------------------------

GAP_BUSES = ("02:52", "03:15", "03:45")  # nothing between the 00:53 train and the 02:52 airport bus


def test_a_gap_in_the_night_is_said_with_the_last_one_before_it_and_the_first_after_it(make, monkeypatch):
    service = make(Evening(trains=NIGHT[:5], buses=GAP_BUSES))
    found = query(service, "latest_departure", origin=STREET, destination="airport")
    # bad sample (the replay's first line): all night, while nothing leaves between 00:30 and 02:29
    assert "一直有车" not in found["first_line"]
    assert found["first_line"] == ("今晚到 Flughafen Wien 的车夜里有空档：火车最后一班 00:53 REX 7（最晚 00:30 出门）；"
                                   "之后要等到 凌晨 02:29 出门（D 接 VAB 1，03:22 到）")
    # the last train is named once: the next line says how to take it
    assert found["sentences"][1].startswith("这班火车怎么坐：")
    assert problems(service, second_part(found["first_line"], *found["sentences"][1:])) == {}
    # the first part of a later question quotes the lookup with the gap, never “夜里一直有车”
    fixed(monkeypatch, local("21:52"))
    turn = service.turn("local", SECOND, "q2", scenario="quick", points=ASKED)
    go = turn["first_part"]["lines"][0]
    assert "一直有车" not in go
    assert go == "去维也纳机场：夜里有空档，火车最后一班 00:53 REX 7，Wien Hauptbahnhof 开；之后要等到 02:52 的 VAB 1 才再有车（21:01 查过）"


def test_all_night_is_still_said_when_departures_never_stop_for_an_hour(make):
    service = make(Evening(trains=NIGHT[:5], buses=BUSES))  # airport buses every 30 minutes all night
    found = query(service, "latest_departure", origin=STREET, destination="airport")
    assert found["first_line"] == "今晚不用赶：夜里一直有车到 Flughafen Wien；火车最后一班 00:53 REX 7（最晚 00:30 出门）"
    # and a night that ends (two hours or more without a departure) is still tonight's last one
    ends = make(Evening(trains=NIGHT))  # the last REX at 00:53, the next at 04:22
    assert query(ends, "latest_departure", origin=STREET, destination="airport")["first_line"].startswith("今晚最后一班：")


# --- 3. a stop to board at names the ride boarded there --------------------------------------------------------------------
# 11-04 quality judge (3 of 3 runs): “空档前最后一班 01:25 FlixBus N877（最晚 00:56 在 Wien Friedensbrücke 上车）” was
# answered “在 Wien Friedensbrücke 上 FlixBus”; the U4 boards there, the coach leaves another station. Where the words
# board a first ride that is not the one the sentence names, they give the rides and where the named one leaves.

FRIEDENSBRUECKE, KARLSPLATZ = "at-PTA-Eastern-Region-Flex-2026_at:49:1150", "at-PTA-Eastern-Region-Flex-2026_at:49:1072"
CLOSING = dict(id="gym-north", name="北边的岩馆", name_local="Kletterhalle Nordbahn, Beispielgasse 1, 1200 Wien", kind="climbing",
               coords=[48.2315, 16.3679], opening_hours=["Mo-Su 09:00-22:10"])  # closed long before the night's last rides
KIOSK = dict(id="hbf-kiosk", name="总站小卖部", name_local="Kiosk Beispiel, Wien Hauptbahnhof", kind="food", coords=[48.1851, 16.3762],
             transit_id=HBF, opening_hours=["Mo-Su 06:00-22:10"])  # at the station: the named ride is boarded there


class Friedensbruecke(Evening):
    """Evening where the way from the street to the station starts with the U4 at Wien Friedensbrücke to Wien
    Karlsplatz, then the U1 to Wien Hauptbahnhof (20 minutes before the ride from there)."""

    @staticmethod
    def tram(start):
        return [leg("WALK", {"name": "START", "lat": 48.231, "lon": 16.368}, stop("Wien Friedensbrücke", FRIEDENSBRUECKE), start - timedelta(minutes=4), start),
                leg("SUBWAY", stop("Wien Friedensbrücke", FRIEDENSBRUECKE), stop("Wien Karlsplatz", KARLSPLATZ), start, start + timedelta(minutes=9),
                    displayName="U4", headsign="Hütteldorf", realTime=False),
                leg("SUBWAY", stop("Wien Karlsplatz", KARLSPLATZ), stop("Wien Hauptbahnhof", HBF), start + timedelta(minutes=11),
                    start + timedelta(minutes=15), displayName="U1", headsign="Oberlaa", realTime=False)]


def test_a_time_to_board_a_first_ride_names_it_and_where_the_named_one_leaves(make, monkeypatch):
    places = (CLOSING, SOUTH, *FOOD, KIOSK)
    # the gap's last one is a coach: bad sample (the replay's shape) boards it at the U4's stop
    coach = make(Friedensbruecke(trains=NIGHT[:5], coaches=("01:25",), buses=GAP_BUSES), places=places)
    found = query(coach, "latest_departure", origin="gym-north", destination="airport")
    assert "空档前最后一班 01:25 FlixBus N877（最晚 01:05 在 Wien Friedensbrücke 上车）" not in found["first_line"]
    assert found["first_line"] == ("今晚到 Flughafen Wien 的车夜里有空档：空档前最后一班 01:25 FlixBus N877（最晚 01:05 在 Wien "
                                   "Friedensbrücke 上车：U4 接 U1 接 FlixBus N877，Wien Hauptbahnhof 开）；之后要等到 凌晨 02:32 在 "
                                   "Wien Friedensbrücke 上车（U4 接 U1 接 VAB 1，03:22 到）")
    # the server's sentences pass the check as they stand (fix2b: frame_matches_server said the server's own line, copied,
    # did not match its frame, which left out the gap's last one when it is no train)
    assert problems(coach, second_part(found["first_line"], *found["sentences"][1:])) == {}
    # the first part of a later question names the coach's own station, never the U4's
    fixed(monkeypatch, local("21:52"))
    go = coach.turn("local", SECOND, "q2", scenario="quick", points=ASKED)["first_part"]["lines"][0]
    assert go == "去维也纳机场：夜里有空档，空档前最后一班 01:25 FlixBus N877，Wien Hauptbahnhof 开；之后要等到 02:52 的 VAB 1 才再有车（21:01 查过）"
    # the gap's last one is the train
    train = make(Friedensbruecke(trains=NIGHT[:5], buses=GAP_BUSES), places=places)
    rail = query(train, "latest_departure", origin="gym-north", destination="airport")
    first = rail["first_line"]
    assert problems(train, second_part(first, *rail["sentences"][1:])) == {}
    assert first.startswith("今晚到 Flughafen Wien 的车夜里有空档：火车最后一班 00:53 REX 7（最晚 00:33 在 Wien Friedensbrücke 上车："
                            "U4 接 U1 接 REX 7，Wien Hauptbahnhof 开）；")
    # all night, the last train: the tram to the station, then the train (one change)
    night = make(Evening(trains=NIGHT[:5], buses=BUSES), places=places)
    assert query(night, "latest_departure", origin="gym-north", destination="airport")["first_line"] == \
        "今晚不用赶：夜里一直有车到 Flughafen Wien；火车最后一班 00:53 REX 7（最晚 00:33 在 Wien Matzleinsdorfer Platz 上车：D 接 REX 7，Wien Hauptbahnhof 开）"
    # controls: boarded where the named ride leaves (from the station) says no rides
    station = make(Evening(trains=NIGHT[:5], coaches=("01:25",), buses=GAP_BUSES), places=places)
    assert "空档前最后一班 01:25 FlixBus N877（最晚 01:25 在 Wien Hauptbahnhof 上车）；" in \
        query(station, "latest_departure", origin="hbf-kiosk", destination="airport")["first_line"]


def test_the_gap_last_one_that_is_no_train_says_its_rides_from_a_door_still_open(make):
    # fix2b: no other line says how to take the gap's last one when it is no train (the train has “这班火车怎么坐”), so a
    # time to leave the door goes on with its rides; bad sample: “（最晚 01:01 出门）” alone
    open_late = make(Friedensbruecke(trains=NIGHT[:5], coaches=("01:25",), buses=GAP_BUSES))
    found = query(open_late, "latest_departure", origin=STREET, destination="airport")
    assert "空档前最后一班 01:25 FlixBus N877（最晚 01:01 出门）；" not in found["first_line"]
    assert found["first_line"] == ("今晚到 Flughafen Wien 的车夜里有空档：空档前最后一班 01:25 FlixBus N877（最晚 01:01 出门：U4 接 U1 接 "
                                   "FlixBus N877，Wien Hauptbahnhof 开）；之后要等到 凌晨 02:28 出门（U4 接 U1 接 VAB 1，03:22 到）")
    assert problems(open_late, second_part(found["first_line"], *found["sentences"][1:])) == {}
    # controls: the gap's last one is the train (its own line says how), or no change from the station
    rail = make(Friedensbruecke(trains=NIGHT[:5], buses=GAP_BUSES))
    assert "火车最后一班 00:53 REX 7（最晚 00:29 出门）；" in query(rail, "latest_departure", origin=STREET, destination="airport")["first_line"]
    station = make(Evening(trains=NIGHT[:5], coaches=("01:25",), buses=GAP_BUSES))
    assert "空档前最后一班 01:25 FlixBus N877（最晚 01:25 出门）；" in query(station, "latest_departure", origin="hbf", destination="airport")["first_line"]


def test_the_words_to_board_say_the_rides_in_english_too():
    from tripwright.query import leave_words
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Europe/Vienna")

    def ride(line, origin, to, depart, arrive):
        return dict(walk=False, mode="bus", line=line, **{"from": origin}, to=to, depart=depart, arrive=arrive,
                    depart_at=local(depart).isoformat(), arrive_at=local(arrive).isoformat())

    u4 = ride("U4", "Wien Friedensbrücke", "Wien Karlsplatz", "00:56", "01:05")
    coach = ride("FlixBus N877", "Vienna Central Station", "Flughafen Wien", "01:25", "01:55")
    closes = local("22:10")
    changing = dict(leave="00:52", leave_at=local("00:52").isoformat(), legs=[u4, coach])
    direct = dict(leave="01:25", leave_at=local("01:25").isoformat(), legs=[coach])
    assert leave_words(changing, closes, tz, "en", latest=True, named=True) == \
        "board at Wien Friedensbrücke by 00:56: U4 then FlixBus N877, which leaves Vienna Central Station"
    assert leave_words(changing, closes, tz, "zh", latest=True, named=True) == \
        "最晚 00:56 在 Wien Friedensbrücke 上车：U4 接 FlixBus N877，Vienna Central Station 开"
    # controls: a direct ride, a sentence that names no ride, a door still open
    assert leave_words(direct, closes, tz, "en", latest=True, named=True) == "board at Vienna Central Station by 01:25"
    assert leave_words(changing, closes, tz, "zh", latest=True) == "最晚 00:56 在 Wien Friedensbrücke 上车"
    assert leave_words(changing, None, tz, "zh", latest=True, named=True) == "最晚 00:52 出门"
    # fix2b: a sentence that is the only one about a connection says its rides from a door still open too
    assert leave_words(changing, None, tz, "zh", latest=True, way=True) == "最晚 00:52 出门：U4 接 FlixBus N877，Vienna Central Station 开"
    assert leave_words(changing, None, tz, "en", latest=True, way=True) == \
        "leave by 00:52: U4 then FlixBus N877, which leaves Vienna Central Station"
    assert leave_words(direct, None, tz, "zh", latest=True, way=True) == "最晚 01:25 出门"


# --- 4. a connection with changes is never named by its first ride --------------------------------------------------------
# fix2b (after the 11-04 quality judge): “赶得上 23:50 的最后一班是 23:03 D” and the first part's “今晚最后一班 00:33 D，Wien
# Matzleinsdorfer Platz 开” named the tram to the station as the last one; the train leaves Wien Hauptbahnhof. With a
# change, the last one is said in fix2's shape: when it sets off, every ride, where the last ride leaves.

def test_the_last_one_with_a_change_is_said_with_its_rides(make, monkeypatch):
    service = make()
    found = query(service, "latest_departure", origin=STREET, destination="airport", by="23:50", date=DAY)
    last = next(line for line in found["sentences"] if line.startswith("赶得上 23:50 的最后一班"))
    assert not last.startswith("赶得上 23:50 的最后一班是 23:03 D")  # bad sample: the tram named as the last one
    assert last == "赶得上 23:50 的最后一班是 23:03 出发：D 接 REX 7，Wien Hauptbahnhof 开（23:00 出门）；再下一班 23:33 开，00:15 到，过了 23:50"
    assert problems(service, second_part(*found["sentences"])) == {}
    # the first part of a later question, quoting a night that ends after the 00:53 train
    ends = make(Evening(trains=NIGHT))
    query(ends, "latest_departure", origin=STREET, destination="airport")
    fixed(monkeypatch, local("21:52"))
    go = ends.turn("local", SECOND, "q2", scenario="quick", points=ASKED)["first_part"]["lines"][0]
    assert go == "去维也纳机场：今晚最后一班 00:33 在 Wien Matzleinsdorfer Platz 上车：D 接 REX 7，Wien Hauptbahnhof 开（21:01 查过）"
    # English
    english = make(Evening(trains=NIGHT))
    english.language = "en"
    said = query(english, "latest_departure", origin=STREET, destination="airport", by="23:50", date=DAY)["sentences"]
    assert "The last one that arrives by 23:50 starts at 23:03: D then REX 7, which leaves Wien Hauptbahnhof (leave 23:00)" in \
        " ".join(said)


def test_the_last_one_without_a_change_is_said_as_before(make, monkeypatch):
    station = make(Evening(trains=NIGHT))
    found = query(station, "latest_departure", origin="hbf", destination="airport", by="23:50", date=DAY)
    assert any(line.startswith("赶得上 23:50 的最后一班是 23:23 REX 7（23:23 出门）") for line in found["sentences"])
    query(station, "latest_departure", origin="hbf", destination="airport")
    fixed(monkeypatch, local("21:52"))
    go = station.turn("local", SECOND, "q2", scenario="quick", points=ASKED)["first_part"]["lines"][0]
    assert go == "去维也纳机场：今晚最后一班 00:53 REX 7，Wien Hauptbahnhof 开（21:01 查过）"
