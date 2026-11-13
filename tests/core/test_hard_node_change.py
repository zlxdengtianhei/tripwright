"""Changing a booked train the user asked to change (tw-multi s3, 2026-11-13): "11 号那班去卑尔根的火车不坐下午 16:30 的了，
改坐早上 9 点左右的". Codex proposed the day with the node at 09:00, the service refused it (preserved-anchor), the user
answered "可以，就按这样存吧" and it was refused again, 6 runs of 6. The user's "yes" answers the unverified facts it was
asked about (D-11); a hard node changes only by the user's own words reported in changes.reports (D-36, D-53), and this
message carries them, so no further sentence is needed from the user. What was missing is the refusal saying so."""
from copy import deepcopy

import pytest
from test_ask_before_saving import host_commit, trip  # noqa: F401  (trip is a fixture)

ASKED = "改一下：11 号那班去卑尔根的火车不坐下午 16:30 的了，改坐早上 9 点左右的，下午就在卑尔根逛 Example Bergen Viewpoint。"
WORDS = "11 号那班去卑尔根的火车不坐下午 16:30 的了，改坐早上 9 点左右的"


def morning_train(service):
    day = deepcopy(service.state("alice", "day", "2030-06-11")["json"])
    day["anchors"][0]["time"] = "09:00"
    for option in day["plans"]:
        for track in option["tracks"]:
            track["blocks"] = [dict(block, start="08:10", end="08:30") for block in track["blocks"] if block["kind"] == "reserve"]
    return day


def asked(service, key):
    return service.turn("alice", ASKED, key, scenario="replan", speaker="p1")


def test_without_a_report_the_refusal_says_how_the_users_words_change_the_node(trip):
    turn = asked(trip, "s3")
    draft = trip.propose("alice", ctx_id=turn["ctx_id"], days=[morning_train(trip)], idempotency_key="s3")
    assert draft["status"] == "rejected"
    conflict = next(check for check in draft["checks"]["conflicts"] if check["id"] == "preserved-anchor:example-rail-departure")
    report = conflict["details"]["report"]
    assert report["target"] == "anchor:example-rail-departure" and report["field"] == "time"
    assert (report["old"], report["new"]) == ("16:30", "09:00")
    assert "changes.reports" in conflict["details"]["how"] and "not a correction" in conflict["details"]["how"]


def test_with_the_users_words_reported_the_change_waits_only_for_the_unverified_facts_then_saves(trip):
    turn = asked(trip, "s3")
    report = dict(target="anchor:example-rail-departure", field="time", old="16:30", new="09:00", quote=WORDS,
                  source=dict(event="this"))
    draft = trip.propose("alice", ctx_id=turn["ctx_id"], days=[morning_train(trip)], idempotency_key="s3r",
                         changes=dict(reports=[report]))
    assert draft["status"] == "proposed", draft.get("refused")
    assert not [check for check in draft["checks"]["conflicts"] if check["id"].startswith("preserved-anchor")]
    trip.turn("alice", "可以，就按这样存吧。", "yes", scenario="replan", speaker="p1")
    version = host_commit(trip, draft, accept_unverified=True)
    day = next(day for day in version["plan"]["days"] if day["date"] == "2030-06-11")
    assert day["anchors"][0]["time"] == "09:00"


def test_a_quote_that_is_not_in_this_message_cannot_be_reported(trip):
    """The quote must come from this turn's message; whether it states the new value is not checked (deploy/README)."""
    turn = trip.turn("alice", "下午想在卑尔根逛逛。", "s3x", scenario="replan", speaker="p1")
    report = dict(target="anchor:example-rail-departure", field="time", old="16:30", new="09:00", quote=WORDS,
                  source=dict(event="this"))
    with pytest.raises(ValueError):
        trip.propose("alice", ctx_id=turn["ctx_id"], days=[morning_train(trip)], idempotency_key="s3x",
                     changes=dict(reports=[report]))


def test_in_a_later_turn_the_earlier_words_are_not_admitted_and_the_refusal_says_to_ask_again(trip):
    """By design (D-49) a hard node changes only on the words of the turn whose proposal changes it. When the agent
    asked something first and proposes in the reply's turn, the user has to say the new value again."""
    first = asked(trip, "s3a")
    reply = trip.turn("alice", "可以，就按这样存吧。", "s3b", scenario="replan", speaker="p1")
    report = dict(target="anchor:example-rail-departure", field="time", old="16:30", new="09:00", quote=WORDS,
                  source=dict(event=first["receipt"]["event_id"]))
    with pytest.raises(ValueError, match="say the change again in one sentence"):
        trip.propose("alice", ctx_id=reply["ctx_id"], days=[morning_train(trip)], idempotency_key="s3b",
                     changes=dict(reports=[report]))
    again = trip.turn("alice", "11 号的火车改坐早上 9 点那班。", "s3c", scenario="replan", speaker="p1")
    draft = trip.propose("alice", ctx_id=again["ctx_id"], days=[morning_train(trip)], idempotency_key="s3c",
                         changes=dict(reports=[dict(report, quote="改坐早上 9 点那班", source=dict(event="this"))]))
    assert draft["status"] == "proposed", draft.get("refused")
