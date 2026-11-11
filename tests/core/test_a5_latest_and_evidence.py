"""A5 (D-42): "checked" hangs only on evidence the tools made. Transit results come per leg in the trip's local time with
the window they cover; the latest departure for a hard node is computed by the tool from the node's deadline type
(external, plan, not marked), with the later departures only the external deadline allows and a fallback later than
the first choice; a web check is recorded with the page's address, the verbatim quote and the service's own time, and
a search snippet never counts as checked; turn names the field path of a wrong record. Offline: a fictional MOTIS that
answers in UTC with walking legs, and a fictional web. Fictional people, places and lines only."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone

import pytest

from a5_upstream import DAY, DESTINATION, ORIGIN, Timetable, page_reader
from test_a3_anchor_deadline import anchored_plan
from tripwright import locks, packs
from tripwright.evidence import WEB_TYPES
from tripwright.providers.pages import PageReader, PageUnavailable
from tripwright.query import Arguments, QueryRouter
from tripwright.service import Tripwright
from tripwright.storage import Store


@pytest.fixture
def make(tmp_path):
    made = []

    def build(plan=None, *, timetable=None, router=None):
        store = Store(tmp_path / f"a5-{len(made)}.sqlite3")
        service = Tripwright(store, query_router=router(store) if router else
                             QueryRouter(store, transit=(timetable or Timetable()).provider(), pages=page_reader()))
        made.append((service, store))
        if plan is not None:
            turn = service.turn("alice", "创建行程。", "seed")
            draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=plan, idempotency_key="seed")
            service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)
        return service

    yield build
    for service, store in made:
        service.jobs.stop()
        store.close()


def query(service, kind, **arguments):
    return asyncio.run(service.query("alice", kind, arguments))


def latest(service, **arguments):
    return query(service, "latest_departure", origin=ORIGIN, destination=DESTINATION, **arguments)


def boards(view):
    return None if view is None else view["boards"]["time"]


# --- transit: per leg, local, with its window ---------------------------------------------------------------------------

def test_transit_is_answered_per_leg_in_the_trips_local_time_with_its_window(make):
    service = make(anchored_plan())
    result = query(service, "transit", origin=ORIGIN, destination=DESTINATION, when="2030-06-10T19:00:00Z", limit=2)  # 21:00 in Oslo
    assert result["timezone"] == "Europe/Oslo"
    first = result["connections"][0]
    assert (first["leave"], first["boards"]["time"], first["alights"]["time"], first["reach"]) == ("21:00", "21:05", "21:25", "21:28")
    assert [(leg["walk"], leg["depart"], leg["arrive"]) for leg in first["legs"]] == [(True, "21:00", "21:05"), (False, "21:05", "21:25"),
                                                                                      (True, "21:25", "21:28")]
    assert result["window"] == {"from": "2030-06-10T21:00:00+02:00", "to": "2030-06-10T21:30:00+02:00", "by": "departure", "count": 2,
                                "text": "查到 21:00—21:30 之间出发的 2 条；21:30 之后出发的没查"}
    assert result["summary"].startswith("查到 21:00—21:30 之间出发的 2 条；21:30 之后出发的没查：21:05 REX 7 Nordkai → Fergeterminal 21:25（21:00 出门，21:28 到）")
    stored = service.store.get("evidence", "alice", result["evidence"]["id"])
    assert stored["window"] == result["window"] and stored["value"][0]["legs"][1]["depart"] == "2030-06-10T21:05:00+02:00"


# --- latest departure: three deadline types --------------------------------------------------------------------------------

def test_latest_departure_for_an_external_deadline(make):
    service = make(anchored_plan(**{"hn-counter": "external"}))
    before = datetime.now(timezone.utc)
    result = latest(service, anchor_id="hn-counter")
    assert result["status"] == "sourced"
    target = result["target"]
    assert (target["deadline"], target["time"], target["plan_by"], target["limit"], target["limit_from"]) == \
        ("external", "23:10", "23:10", "23:10", "target")
    assert (result["primary"]["leave"], boards(result["primary"])) == ("22:30", "22:35")
    assert result["later"] == [] and result["fallback"] is None
    assert result["fallback_note"] == "主选之后到 23:10 前没有别的班次赶得上"
    assert result["window"]["covers_target"] and result["window"]["target"] == "2030-06-10T23:10:00+02:00"
    assert result["summary"].startswith("Ferry counter closes (fictional) 23:10（外部截止）：最晚 22:30 出门，22:35 REX 7 Nordkai → Fergeterminal 22:55")
    # recorded as evidence with the server's time and validity, not as demo data
    evidence = service.store.get("evidence", "alice", result["evidence"]["id"])
    assert evidence["kind"] == "latest_departure" and evidence["source"] == "transitous" and evidence["synthetic"] is False
    assert before - timedelta(seconds=1) <= datetime.fromisoformat(evidence["fetched_at"]) <= datetime.now(timezone.utc)
    assert evidence["valid_until"] and evidence["value"]["primary"]["leave"] == "22:30"
    assert evidence["question"] == dict(origin=ORIGIN, destination=DESTINATION, anchor_id="hn-counter")


def test_latest_departure_for_a_plan_target_lists_later_ones_for_the_external_deadline_and_a_later_fallback(make):
    service = make(anchored_plan(**{"hn-counter": "external", "hn-arrive": "plan"}))
    result = latest(service, anchor_id="hn-arrive", detail="full")  # every flag of every connection (A6 made compact the default)
    target = result["target"]
    # 22:30 less the plan's 10 minutes; the external limit is the trip's next external node after it
    assert (target["deadline"], target["plan_by"], target["limit"], target["limit_from"]) == ("plan", "22:20", "23:10", "hn-counter")
    assert boards(result["primary"]) == "21:35" and result["primary"]["reaches_plan_target"]
    assert [(boards(view), view["past_plan_target"], view["within_limit"]) for view in result["later"]] == [("22:05", True, True), ("22:35", True, True)]
    assert boards(result["fallback"]) == "22:05" and result["fallback"]["leave"] > result["primary"]["leave"]
    assert "只按外部截止 23:10 还能坐 22:05 REX 7 Nordkai → Fergeterminal 22:25、22:35 REX 7 Nordkai → Fergeterminal 22:55（过了计划目标）" in result["summary"]
    assert "误了主选的后备：22:05 REX 7" in result["summary"]
    named = latest(service, anchor_id="hn-arrive", external_deadline="hn-last-bus")
    assert named["target"]["limit"] == "23:40" and [boards(view) for view in named["later"]] == ["22:05", "22:35", "23:05"]


def test_latest_departure_for_a_time_not_marked_takes_it_as_the_limit_and_says_so(make):
    service = make(anchored_plan())
    result = latest(service, anchor_id="hn-last-bus")
    assert (result["target"]["deadline"], result["target"]["limit"]) == ("unmarked", "23:40")
    assert boards(result["primary"]) == "23:05" and result["later"] == [] and result["fallback"] is None
    assert "没标是外部截止还是计划目标" in result["summary"]
    unlinked = make(anchored_plan(**{"hn-arrive": "plan"}))  # a plan target with no external node marked after it
    alone = latest(unlinked, anchor_id="hn-arrive")
    assert alone["target"]["limit"] is None and alone["later"] == [] and alone["fallback"] is None
    assert alone["fallback_note"].startswith("计划目标后面没标外部截止")


def test_a_window_that_stops_before_the_first_choice_says_how_far_it_reaches(make):
    timetable = Timetable(until="21:40")  # the source has nothing after 21:35 (a gap, not the end of service)
    service = make(anchored_plan(**{"hn-counter": "external"}), timetable=timetable)
    result = latest(service, anchor_id="hn-counter")
    assert boards(result["primary"]) == "21:35"  # the latest found, not known to be the latest that runs
    assert result["window"]["covers_target"] is False and result["window"]["to"] == "2030-06-10T21:30:00+02:00"
    assert result["window"]["text"] == "窗口只到 21:30：之后出发的班次没查到，不能说“最后一班”"
    assert result["window"]["text"] in result["summary"] and result["fallback_note"] == "主选之后还有没有班次不知道（见窗口）"
    # searched on from the last departure found and got nothing more (the provider also widened its walking radius)
    assert sorted({request.url.params["time"] for request in timetable.requests}) == ["2030-06-10T19:10:00+02:00", "2030-06-10T21:31:00+02:00"]


def test_latest_departure_by_a_time_and_its_wrong_inputs(make):
    service = make(anchored_plan())
    by = latest(service, by="2030-06-10T21:10:00Z", deadline="external")  # 23:10 in Oslo
    assert (by["target"]["time"], boards(by["primary"])) == ("23:10", "22:35")
    assert latest(service, by="23:10", date=DAY)["target"]["deadline"] == "unmarked"
    for arguments, reason in [
        # A10: the node sets the target alone; without any of the three, latest_departure answers tonight's last connection
        (dict(anchor_id="hn-counter", by="23:10"), "arguments.by (an arrival deadline) was sent with it. Send one of anchor_id, by or board_at"),
        (dict(by="23:10", board_at="23:00", date=DAY), "arguments takes one of by (an arrival deadline at the destination) or board_at"),
        (dict(anchor_id="hn-ghost"), "arguments.anchor_id: no hard node 'hn-ghost' in the current plan"),
        (dict(by="23:10"), "arguments.by must be an ISO time with offset, or HH:MM with arguments.date, got '23:10'"),
        (dict(anchor_id="hn-counter", target=dict(anchor_id="hn-counter", date=DAY, fact="hard_node")), "latest_departure does not take it"),
    ]:
        with pytest.raises(ValueError, match=reason.replace("(", r"\(").replace(")", r"\)")):
            latest(service, **arguments)
    with pytest.raises(ValueError, match="arguments.origin is required"):
        query(service, "latest_departure", destination=DESTINATION, anchor_id="hn-counter")
    after = latest(service, anchor_id="hn-counter", after="2030-06-10T23:06:00+02:00")  # 23:30 leaves after the limit: covered
    assert after["primary"] is None and after["window"]["covers_target"] is True
    quiet = make(anchored_plan(**{"hn-counter": "external"}), timetable=Timetable(until="22:00"))  # the source returns nothing
    empty = latest(quiet, anchor_id="hn-counter", after="2030-06-10T22:10:00+02:00")
    assert empty["primary"] is None and empty["window"]["covers_target"] is False
    assert empty["window"]["text"] == "22:10 起没查到班次：不能说没有车，也不能说“最后一班”"
    passed = latest(service, anchor_id="hn-counter", after="2030-06-10T23:30:00+02:00")
    assert passed["status"] == "unverified" and passed["reason"].startswith("目标时刻已过")


def test_demo_data_never_makes_a_checked_latest_departure(make):
    service = make(anchored_plan(**{"hn-counter": "external"}), router=lambda store: QueryRouter(store, mode="synthetic"))
    result = latest(service, anchor_id="hn-counter")
    assert result["status"] == "unverified" and result["evidence"]["synthetic"] is True
    assert packs.last_check(service.store, "alice", [result["evidence"]["id"]]) is None


# --- web evidence ------------------------------------------------------------------------------------------------------

RULE = dict(url="https://ferry.example.org/rules", quote="The ferry counter closes 40 minutes before departure.",
            claim="The counter closes 40 minutes before the ferry leaves", type="rule")


def test_a_quote_read_on_the_page_is_recorded_with_the_services_time_and_found_there(make):
    service = make(anchored_plan())
    before = datetime.now(timezone.utc).replace(microsecond=0)
    result = query(service, "web_evidence", **RULE)
    assert (result["status"], result["quote_found"], result["counts_as_checked"]) == ("sourced", True, True)
    evidence = service.store.get("evidence", "alice", result["evidence"]["id"])
    assert {key: evidence[key] for key in ("kind", "source", "url", "quote", "claim", "type", "read_from")} == dict(
        kind="web", source="ferry.example.org", **RULE, read_from="page")
    recorded = datetime.fromisoformat(evidence["fetched_at"])
    assert before <= recorded <= datetime.now(timezone.utc)
    assert datetime.fromisoformat(evidence["valid_until"]) - recorded == timedelta(days=30)
    assert "可以写核过，要带原句" in result["summary"]
    # typographic quotes, case and spacing do not matter; a redirect is followed
    loose = query(service, "web_evidence", **dict(RULE, url="https://ferry.example.org/moved", quote="the FERRY counter  closes 40 minutes"))
    assert loose["quote_found"] is True
    assert set(WEB_TYPES) == set(Arguments.model_fields["type"].annotation.__args__[0].__args__)


def test_the_host_cannot_write_the_time_of_a_web_check(make):
    service = make()
    for name in ("fetched_at", "checked_at", "at"):
        with pytest.raises(ValueError, match=f"arguments.{name}: the service records the time of a web check itself; do not send it"):
            query(service, "web_evidence", **RULE, **{name: "2030-06-10T09:00:00+02:00"})
    with pytest.raises(ValueError, match=r"arguments.url must be an http or https address with a host, got 'file:///etc/hosts'"):
        query(service, "web_evidence", **dict(RULE, url="file:///etc/hosts"))
    with pytest.raises(ValueError, match="arguments.claim is required"):
        query(service, "web_evidence", **{key: value for key, value in RULE.items() if key != "claim"})


def test_a_search_snippet_is_recorded_but_never_counts_as_checked(make):
    service = make()
    result = query(service, "web_evidence", **RULE, read_from="search_snippet")
    assert (result["status"], result["quote_found"], result["counts_as_checked"]) == ("unverified", "unchecked", False)
    assert result["reason"] == "it is a search snippet, not the page itself"
    assert "不算核过" in result["summary"]


def test_a_quote_not_on_the_page_does_not_count_and_an_unreadable_page_stays_unchecked(make):
    service = make()
    hidden = query(service, "web_evidence", **dict(RULE, url="https://ferry.example.org/app"))
    assert (hidden["quote_found"], hidden["counts_as_checked"]) == (False, False)
    assert hidden["reason"] == "its quote was not found on the page" and "scripts" in hidden["evidence"]["notes"][0]
    pdf = query(service, "web_evidence", **dict(RULE, url="https://ferry.example.org/timetable.pdf"))
    assert (pdf["quote_found"], pdf["counts_as_checked"]) == ("unchecked", True) and "not an HTML or text page" in pdf["evidence"]["notes"][0]
    missing = query(service, "web_evidence", **dict(RULE, url="https://ferry.example.org/gone"))
    assert missing["quote_found"] == "unchecked" and "HTTP 404" in missing["evidence"]["notes"][0]
    skipped = query(service, "web_evidence", **RULE, check_quote=False)
    assert skipped["quote_found"] == "unchecked" and skipped["counts_as_checked"]


def test_the_page_reader_refuses_private_addresses():
    async def read(url):
        reader = PageReader()
        try:
            return await reader.text(url)
        finally:
            await reader.aclose()

    for url in ("http://127.0.0.1/admin", "http://10.0.0.8/", "http://[::1]/", "http://169.254.169.254/latest"):
        with pytest.raises(PageUnavailable, match="is not a public address"):
            asyncio.run(read(url))


def test_web_evidence_records_only_and_a_snippet_cannot_back_a_correction(make):
    service = make(anchored_plan(**{"hn-counter": "external"}))
    before = service.state("alice")
    snippet = query(service, "web_evidence", **RULE, read_from="search_snippet")["evidence"]["id"]
    page = query(service, "web_evidence", **RULE)["evidence"]["id"]
    after = service.state("alice")
    assert after["number"] == before["number"] and after["plan"] == before["plan"]
    turn = service.turn("alice", "Lock this day.", "lock", nature="user_verbatim", native_ref="fixture:lock", speaker="p1")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=1, idempotency_key="lock", changes=dict(decisions=[dict(
        question="这天定了", options=["照现在"], choice="照现在", decided_by="user", subject=["p1"],
        source=dict(event=turn["receipt"]["event_id"], basis="user_explicit"),
        locked_paths={f"/days/{DAY}": locks.pointer(service.state("alice")["plan"], f"/days/{DAY}")})]))
    service.commit("alice", draft["draft_id"], "lock", accept_unverified=True)
    turn = service.turn("alice", "The counter closes earlier.", "fix")

    def correct(evidence_id):
        return service.propose("alice", ctx_id=turn["ctx_id"], base_version=service.state("alice")["number"], idempotency_key=f"fix-{evidence_id}",
                               changes=dict(corrections=[dict(target="anchor:hn-counter", field="time", old="23:10", new="23:05",
                                                              basis="The operator's page", ask="Is it 23:05?", evidence_ids=[evidence_id],
                                                              source=dict(event=turn["receipt"]["event_id"], basis="agent_inference"))]))

    with pytest.raises(ValueError, match=f"{snippet} does not count as checked: it is a search snippet"):
        correct(snippet)
    assert correct(page)["status"] == "proposed"


def test_a_snippet_on_a_hard_node_is_not_shown_as_its_check(make):
    service = make(anchored_plan(**{"hn-counter": "external"}))
    snippet = query(service, "web_evidence", **RULE, read_from="search_snippet")["evidence"]["id"]
    page = query(service, "web_evidence", **RULE)["evidence"]["id"]
    assert packs.last_check(service.store, "alice", [snippet]) is None
    assert packs.last_check(service.store, "alice", [snippet, page])["evidence_id"] == page


# --- turn: a wrong record names its field path ----------------------------------------------------------------------------

def fact(true_from):
    return dict(facts=[dict(key="companions", value=[], text="Today p1 is alone", subject=["p1"], scope={"start": DAY, "end": DAY},
                            true_from=true_from, invalidated_by="Day ends", source=dict(event="this", basis="user_explicit"))])


def test_turn_names_the_field_path_of_a_wrong_record(make):
    service = make(anchored_plan())
    # S5: a wrong record no longer refuses the turn; recorded.not_recorded names the field path in the same words
    def left(result):
        return result["recorded"]["not_recorded"][0]["reason"]

    refused = service.turn("alice", "Today I am alone.", "bad", nature="user_verbatim", native_ref="fixture:bad", record=fact("this"))
    assert left(refused) == ("Invalid record: record.facts.0.true_from: must be an ISO time with a timezone offset "
                             "(e.g. 2030-06-10T09:00:00+02:00), got 'this'")
    assert re.search(r"record.facts.0.subject must be an array, got string; record.facts.0.true_from must be a string, got integer",
                     left(service.turn("alice", "Today I am alone.", "bad2", record=dict(facts=[dict(fact("x")["facts"][0], subject="p1", true_from=5)]))))
    assert re.search(r"record.facts.0.true_from: must be an ISO time with a timezone offset .*got '2030-06-10T09:00'",
                     left(service.turn("alice", "Today I am alone.", "bad3", record=fact("2030-06-10T09:00"))))
    good = service.turn("alice", "Today I am alone.", "good", nature="user_verbatim", native_ref="fixture:good",
                        record=fact("2030-06-10T09:00:00+02:00"))
    assert good["recorded"]["changed"]
