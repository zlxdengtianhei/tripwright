"""A hard node or a booked item the user asked to change (D-19, 2026-11-13). The user: “假如用户已经说了，那肯定要认啊……
1. 先去分析用户说的内容到底是不是一个确定的时间或车次。2. 如果是不确定的，那肯定不能定，需要再去询问。3. 假如用户确实给出了明确的
修改信息，那就必须认。不只是车次，其他类似的问题也都要这样处理。” A report (changes.reports) counts when the quoted words give a
definite new value, in this turn's message or the user's message just before it; a value said roughly counts only from
the turn of the user's answer, after the agent asked; words with no value are refused with what to ask. tw-multi s3:
Codex 6 of 6 never saved the morning train after the user's “可以，就按这样存吧”."""
from copy import deepcopy

import pytest
from test_ask_before_saving import host_commit, trip  # noqa: F401  (trip is a fixture)

ASKED = "改一下：11 号那班去卑尔根的火车不坐下午 16:30 的了，改坐早上 9 点左右的，下午就在卑尔根逛 Example Bergen Viewpoint。"
WORDS = "11 号那班去卑尔根的火车不坐下午 16:30 的了，改坐早上 9 点左右的"
YES = "可以，就按这样存吧。"
NODE = "anchor:example-rail-departure"


def morning_train(service, time="09:00"):
    day = deepcopy(service.state("alice", "day", "2030-06-11")["json"])
    day["anchors"][0]["time"] = time
    for option in day["plans"]:
        for track in option["tracks"]:
            track["blocks"] = [dict(block, start="08:10", end="08:30") for block in track["blocks"] if block["kind"] == "reserve"]
    return day


def said(service, text, key):
    return service.turn("alice", text, key, scenario="replan", speaker="p1")


def report(quote, event="this", new="09:00", target=NODE, field="time", old="16:30"):
    return dict(target=target, field=field, old=old, new=new, quote=quote, source=dict(event=event))


def propose(service, turn, key, *reports, time="09:00"):
    return service.propose("alice", ctx_id=turn["ctx_id"], days=[morning_train(service, time)], idempotency_key=key,
                           **(dict(changes=dict(reports=list(reports))) if reports else {}))


def node_time(version):
    return next(day for day in version["plan"]["days"] if day["date"] == "2030-06-11")["anchors"][0]["time"]


def test_s3_the_rough_time_is_asked_first_and_after_the_users_yes_the_change_is_saved(trip):
    first = said(trip, ASKED, "s3")
    with pytest.raises(ValueError, match="only roughly.*confirm \"09:00\" first"):
        propose(trip, first, "s3-r", report(WORDS))
    shown = propose(trip, first, "s3")  # the draft the agent shows when it asks: refused, and it names what to send
    conflict = next(check for check in shown["checks"]["conflicts"] if check["id"] == f"preserved-{NODE}")
    assert conflict["details"]["report"]["new"] == "09:00" and "ask the user for the exact value first" in conflict["details"]["how"]
    answer = said(trip, YES, "yes")
    draft = propose(trip, answer, "s3-yes", report(WORDS, event=first["receipt"]["event_id"]))
    assert draft["status"] == "proposed", draft.get("refused")
    assert node_time(host_commit(trip, draft, accept_unverified=True)) == "09:00"


def test_a_definite_time_counts_in_its_own_turn(trip):
    turn = said(trip, "11 号去卑尔根的火车不坐 16:30 那班了，改坐早上 9 点的。", "def")
    draft = propose(trip, turn, "def", report("改坐早上 9 点的"))
    assert draft["status"] == "proposed", draft.get("refused")


@pytest.mark.parametrize("text, quote, message", [
    ("火车改坐早一点的车吧。", "改坐早一点的车", "name no time"),
    (YES, YES, "name no time"),
    ("火车改坐 10 点那班。", "改坐 10 点那班", "another time"),
])
def test_words_without_the_new_value_are_refused_with_what_to_ask(trip, text, quote, message):
    turn = said(trip, text, "x")
    with pytest.raises(ValueError, match=f"{message}.*ask the user which time exactly"):
        propose(trip, turn, "x", report(quote))


def test_a_yes_quoted_in_the_answer_turn_is_refused_and_the_earlier_words_are_the_ones_to_cite(trip):
    first = said(trip, ASKED, "s3")
    answer = said(trip, YES, "yes")
    with pytest.raises(ValueError, match="name no time"):
        propose(trip, answer, "yes-quote", report(YES))
    assert propose(trip, answer, "earlier", report(WORDS, event=first["receipt"]["event_id"]))["status"] == "proposed"


def test_words_from_further_back_do_not_count(trip):
    first = said(trip, ASKED, "s3")
    said(trip, "对了，晚饭想吃鱼。", "other")
    answer = said(trip, YES, "yes")
    with pytest.raises(ValueError, match="not admitted \\(this turn\\).*ask the user to say the change again"):
        propose(trip, answer, "far", report(WORDS, event=first["receipt"]["event_id"]))


def test_a_quote_that_is_not_in_its_message_is_refused(trip):
    turn = said(trip, "下午想在卑尔根逛逛。", "s3x")
    with pytest.raises(ValueError, match="Quote is absent"):
        propose(trip, turn, "s3x", report("改坐早上 9 点的"))


def test_the_same_in_english(trip):
    first = said(trip, "Change the train on the 11th: not the 16:30 one, take the one around 9 instead.", "en")
    with pytest.raises(ValueError, match="only roughly"):
        propose(trip, first, "en-r", report("take the one around 9 instead"))
    answer = said(trip, "Yes, save it like this.", "en-yes")
    with pytest.raises(ValueError, match="name no time"):
        propose(trip, answer, "en-yes-quote", report("Yes, save it like this."))
    draft = propose(trip, answer, "en-ok", report("take the one around 9 instead", event=first["receipt"]["event_id"]))
    assert draft["status"] == "proposed", draft.get("refused")
    later = said(trip, "Take an earlier train.", "en-vague")
    with pytest.raises(ValueError, match="name no time"):
        propose(trip, later, "en-vague", report("Take an earlier train."))


def test_a_booked_ticket_follows_the_same_rule(trip):
    """paid-booking: the booked train's date, moved by the user's words to the 12th, and refused for a vague day."""
    def moved_booking(day):
        plan = deepcopy(trip.state("alice")["plan"])
        booking = next(entry for entry in plan["bookings"] if entry["id"] == "bk-example-rail")
        booking["date"] = day
        return plan

    turn = said(trip, "火车票改到 12 号。", "bk")
    target = dict(target="booking:bk-example-rail", field="date", old="2030-06-11")
    draft = trip.propose("alice", ctx_id=turn["ctx_id"], plan=moved_booking("2030-06-12"), idempotency_key="bk",
                         changes=dict(reports=[dict(target, new="2030-06-12", quote="火车票改到 12 号", source=dict(event="this"))]))
    assert not [check for check in draft["checks"]["conflicts"] if check["id"].startswith("paid-booking")]
    unreported = trip.propose("alice", ctx_id=turn["ctx_id"], plan=moved_booking("2030-06-12"), idempotency_key="bk0")
    conflict = next(check for check in unreported["checks"]["conflicts"] if check["id"] == "paid-booking:bk-example-rail")
    assert conflict["details"]["report"] == dict(target="booking:bk-example-rail", field="date", old="2030-06-11", new="2030-06-12")
    vague = said(trip, "火车票改到后面几天吧。", "bk2")
    with pytest.raises(ValueError, match="name no date"):
        trip.propose("alice", ctx_id=vague["ctx_id"], plan=moved_booking("2030-06-12"), idempotency_key="bk2",
                     changes=dict(reports=[dict(target, new="2030-06-12", quote="火车票改到后面几天吧", source=dict(event="this"))]))
