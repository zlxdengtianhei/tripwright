"""B3d regressions: S6a–S6b (lock identity), plan IDs, S1a (per-person coverage), S4a–S4b (scoped declarations and the
possible-contradiction notice). Fictional people and places only."""
from __future__ import annotations

from copy import deepcopy

import pytest

from tripwright.locks import identify
from tripwright.trip import Trip

from test_b3d_lock_aliases import D1, D2, D3, GROUP, LOCKED, find, gym, trip
from core_helpers import package


def seed(service, plan=None):
    turn = service.turn("alice", "Create the fictional trip.", "seed")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=plan or trip(), idempotency_key="seed")
    service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)
    return service


def say(service, text, key, *, plan=None, changes=None, usages=None, nature="user_verbatim"):
    """The user's messages carry their writer, u1 (a user decision is admitted only from them, D-49); an agent's none."""
    turn = service.turn("alice", text, key, nature=nature, native_ref=f"fixture:{key}" if nature == "user_verbatim" else None,
                        speaker="u1" if nature in ("user_verbatim", "host_restatement") else None)
    if callable(changes):
        changes = changes(turn["receipt"]["event_id"])
    return turn, service.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=plan,
                                 changes=changes, usages=usages, idempotency_key=key)


def lock(service, path=f"/blocks/{LOCKED}"):
    block = find(service.state("alice")["plan"], LOCKED)[4]
    value = block if path == f"/blocks/{LOCKED}" else block[path.rsplit("/", 1)[1]]
    _, draft = say(service, "第一天饭后我们三个去岩馆 A，定了。", "lock", changes=lambda event: dict(decisions=[dict(
        question="第一天饭后去哪", options=["岩馆 A"], choice="岩馆 A", decided_by="user", must=True, subject=GROUP,
        source=dict(event=event, basis="user_explicit"), locked_paths={path: value})]))
    service.commit("alice", draft["draft_id"], "lock")
    return service


def agent(service, plan, key):
    return say(service, "agent replans", key, plan=plan, nature="agent_inference")[1]


def lock_conflicts(draft):
    return [check for check in draft["checks"]["conflicts"] if check["id"].startswith("locked:")]


# --- S6a: another plan of the same day, whatever its display name ----------------------------------------------------

@pytest.mark.parametrize("name", ["Recommended", "Option B"])
def test_s6a_moving_the_locked_block_into_another_plan_is_refused_whatever_its_name(service, name):
    lock(seed(service))
    plan = service.state("alice")["plan"]
    entry, rank, source, track, block = find(plan, LOCKED)
    track["blocks"].remove(block)
    target = entry["plans"][1]
    target["name"] = name
    target["tracks"].append(dict(subject=list(GROUP), blocks=[block]))
    draft = agent(service, plan, "move-" + name)
    assert draft["status"] == "rejected"
    assert lock_conflicts(draft)[0]["details"]["changed"] == ["plan", "rank"]
    with pytest.raises(ValueError, match=r"locked:.*/blocks/d1-after \(plan, rank changed\)"):
        service.commit("alice", draft["draft_id"], "move-" + name, accept_unverified=True)


def test_s6a_the_same_move_into_a_new_plan_without_an_id_is_refused(service):
    lock(seed(service))
    plan = service.state("alice")["plan"]
    entry, rank, source, track, block = find(plan, LOCKED)
    track["blocks"].remove(block)
    entry["plans"].pop(1)
    entry["plans"].append(dict(name=source["name"], tracks=[dict(subject=list(GROUP), blocks=[block])]))
    draft = agent(service, plan, "new-plan")
    assert lock_conflicts(draft)[0]["details"]["changed"] == ["plan", "rank"]
    # The ID-less plan takes the free ID of its position; the lock still sees another plan at another position.
    assert [option["id"] for option in find(draft_plan(service, draft), "d1-museum")[0]["plans"]] == ["rec", "alt"]


def draft_plan(service, draft):
    return service.store.get("drafts", "alice", draft["draft_id"])["plan"]


# --- S6b: the referenced place keeps its identity; description may change ------------------------------------------

@pytest.mark.parametrize("field,value", [("name", "虚构博物馆 B"), ("name_local", "Museum B (fictional)"), ("kind", "museum"),
                                         ("area", "Hilltown"), ("coords", [47.85, 13.04]), ("coords", None), ("transit_id", "stop:x")])
@pytest.mark.parametrize("path", [f"/blocks/{LOCKED}", f"/blocks/{LOCKED}/place"])
def test_s6b_redefining_the_locked_place_is_refused(service, field, value, path):
    lock(seed(service), path)
    plan = service.state("alice")["plan"]
    gym(plan)[field] = value
    draft = agent(service, plan, "retarget")
    assert draft["status"] == "rejected"
    # B3f: refusals name the field (the B3d assertion named only the object).
    assert lock_conflicts(draft)[0]["details"]["changed"] == [f"place:gym-a/{field}"]
    with pytest.raises(ValueError, match=rf"place:gym-a/{field} changed"):
        service.commit("alice", draft["draft_id"], "retarget", accept_unverified=True)


@pytest.mark.parametrize("path", [f"/blocks/{LOCKED}", f"/blocks/{LOCKED}/place"])
def test_s6b_description_of_the_locked_place_may_change(service, path):
    lock(seed(service), path)
    plan = service.state("alice")["plan"]
    # B3f (D-36): only pure description fields; hours, price, rain flag, priority, people and a few metres of
    # coordinates were exempt in B3d and are protected now, each refused naming its field.
    gym(plan).update(notes="Bring chalk", links=["https://example.invalid/a"], reason="Indoor", checked="2031-04-20")
    draft = agent(service, plan, "describe")
    assert draft["status"] == "proposed" and lock_conflicts(draft) == []
    service.commit("alice", draft["draft_id"], "describe", accept_unverified=True)
    latitude, longitude = gym(service.state("alice")["plan"])["coords"]
    for field, value in [("hours", "10:00–22:00"), ("price", {"EUR": 14}), ("rain_ok", True), ("priority", "must"),
                         ("subject", ["u1"]), ("coords", [latitude + 0.0003, longitude])]:
        plan = service.state("alice")["plan"]
        gym(plan)[field] = value
        assert lock_conflicts(agent(service, plan, f"describe-{field}"))[0]["details"]["changed"] == [f"place:gym-a/{field}"], field


def test_s6b_redefining_a_place_the_lock_does_not_use_is_allowed(service):
    lock(seed(service))
    plan = service.state("alice")["plan"]
    next(place for place in plan["places"] if place["id"] == "hill").update(name="Other hill", kind="park", coords=[47.0, 13.0])
    assert lock_conflicts(agent(service, plan, "unrelated")) == []


def test_s6b_redefining_the_locked_booking_or_a_track_person_is_refused(service):
    lock(seed(service))
    plan = service.state("alice")["plan"]
    plan["bookings"][0]["what"] = "Museum ticket (fictional)"
    assert lock_conflicts(agent(service, plan, "booking"))[0]["details"]["changed"] == ["booking:bk-gym/what"]
    plan = service.state("alice")["plan"]
    next(person for person in plan["people"] if person["id"] == "u2")["name"] = "Dana"
    assert lock_conflicts(agent(service, plan, "person"))[0]["details"]["changed"] == ["person:u2/name"]


def test_s6_users_new_decision_can_take_a_redefined_place(service):
    lock(seed(service))
    plan = service.state("alice")["plan"]
    gym(plan).update(name="虚构岩馆 A 新馆", coords=[47.86, 13.04])
    block = find(plan, LOCKED)[4]
    _, draft = say(service, "换到新馆，我们定了。", "new-branch", plan=plan, changes=lambda event: dict(decisions=[dict(
        question="哪个馆", options=["新馆"], choice="新馆", decided_by="user", must=True, subject=GROUP,
        source=dict(event=event, basis="user_explicit"), locked_paths={f"/blocks/{LOCKED}": block})]))
    assert draft["status"] == "proposed" and lock_conflicts(draft) == []
    service.commit("alice", draft["draft_id"], "new-branch", accept_unverified=True)
    assert [decision["status"] for decision in service.store.all("decisions", "alice")] == ["superseded", "active"]


# --- plan IDs ---------------------------------------------------------------------------------------------------------

def test_plan_ids_are_assigned_by_position_and_never_reuse_a_removed_plans_id():
    previous = identify(trip())
    assert [option["id"] for entry in previous["days"] for option in entry["plans"]] == ["rec", "alt", "P1", "P1"]
    candidate = trip()
    for entry in candidate["days"]:
        for option in entry["plans"]:
            option.pop("id", None)
    identify(candidate, previous)
    assert [option["id"] for option in candidate["days"][0]["plans"]] == ["rec", "alt"]
    candidate = trip()
    first = candidate["days"][0]["plans"]
    first[0]["id"] = "alt"
    first[1]["id"] = None
    identify(candidate, previous)
    assert [option["id"] for option in first] == ["alt", "P1"]


def test_duplicate_plan_ids_on_one_day_are_refused():
    plan = trip()
    plan["days"][0]["plans"][1]["id"] = "rec"
    with pytest.raises(ValueError, match="Duplicate plan id on 2031-05-01"):
        Trip.model_validate(plan)
    plan["days"][0]["plans"][1]["id"] = "alt"
    plan["days"][1]["plans"][0]["id"] = "rec"
    Trip.model_validate(plan)


def test_committed_plans_carry_ids_and_an_id_less_resubmission_inherits_them(service):
    seed(service, plan=deepcopy(trip()))
    committed = service.state("alice")["plan"]
    assert [option["id"] for entry in committed["days"] for option in entry["plans"]] == ["rec", "alt", "P1", "P1"]
    stripped = deepcopy(committed)
    for entry in stripped["days"]:
        for option in entry["plans"]:
            del option["id"]
    _, draft = say(service, "same plan", "stripped", plan=stripped, nature="host_restatement")
    assert draft_plan(service, draft) == committed


def test_legacy_lock_with_a_plan_name_is_rebound_to_the_committed_plan_id(service):
    lock(seed(service))
    stored = service.store.all("decisions", "alice")[0]
    stored["placement"] = {f"/blocks/{LOCKED}": dict(date=D1, plan="Recommended", subject=GROUP)}
    service.store.put("decisions", "alice", stored)
    plan = service.state("alice")["plan"]
    entry, rank, source, track, block = find(plan, LOCKED)
    track["blocks"].remove(block)
    entry["plans"][1]["tracks"].append(dict(subject=list(GROUP), blocks=[block]))
    assert lock_conflicts(agent(service, plan, "legacy-move"))[0]["details"]["changed"] == ["plan", "rank"]
    assert lock_conflicts(agent(service, service.state("alice")["plan"], "legacy-same")) == []


# --- S1a: a must for several people needs every one of them -----------------------------------------------------------

def park_must(service, subject=("u1", "u2"), strength="must", key="park-must"):
    quote = "第二天 Ash 和 Bea 两个人都必须去公园" if strength == "must" else "第二天 Ash 和 Bea 想去公园"
    _, draft = say(service, quote, key, changes=lambda event: dict(items=[dict(
        kind="constraint" if strength == "must" else "preference", key="park", quote=quote, text=quote, subject=list(subject),
        strength=strength, scope=dict(start=D2, end=D2), source=dict(event=event, basis="user_explicit"))]))
    service.commit("alice", draft["draft_id"], key, accept_unverified=True)
    return next(item for item in service.ledger.current("alice")["items"] if item["key"] == "park")


def day_two(plan, tracks):
    plan["days"][1]["plans"] = [dict(id="P1", name="Recommended", tracks=tracks)]
    return plan


def park_track(subject, block_id="park-visit"):
    return dict(subject=subject, blocks=[dict(id=block_id, start="10:00", end="11:00", kind="flex", place="park")])


def cafe_track(subject, block_id="cafe-visit"):
    return dict(subject=subject, blocks=[dict(id=block_id, start="10:00", end="11:00", kind="flex", place="cafe")])


def activity_check(draft, item):
    return next(check for check in draft["checks"]["checks"] if check["id"] == f"item:{item['id']}:activity")


def test_s1a_group_must_with_only_one_person_scheduled_is_refused(service):
    seed(service)
    item = park_must(service)
    plan = day_two(service.state("alice")["plan"], [park_track(["u1"]), cafe_track(["u2", "u3", "u4"])])
    usages = [dict(item_id=item["id"], status="used", block_ids=["park-visit"])]
    _, draft = say(service, "排第二天", "one", plan=plan, usages=usages, nature="host_restatement")
    check = activity_check(draft, item)
    assert check["status"] == "conflict"
    assert check["details"]["people"] == {"u1": dict(status="covered", blocks=["park-visit"]), "u2": dict(status="missing", blocks=[])}
    usage = draft["checks"]["usages"][0]
    assert usage["status"] == "invalid" and usage["verification"] == "conflict"
    assert draft["status"] == "rejected"
    with pytest.raises(ValueError, match=f"item:{item['id']}:activity"):
        service.commit("alice", draft["draft_id"], "one", accept_unverified=True)


def test_s1a_group_must_with_both_people_scheduled_is_server_checked(service):
    seed(service)
    item = park_must(service)
    plan = day_two(service.state("alice")["plan"], [park_track(["u1", "u2"]), cafe_track(["u3", "u4"])])
    _, draft = say(service, "排第二天", "both", plan=plan, usages=[dict(item_id=item["id"], status="used", block_ids=["park-visit"])],
                   nature="host_restatement")
    assert activity_check(draft, item)["status"] == "passed"
    assert draft["checks"]["usages"][0] | {} == dict(item_id=item["id"], status="used", block_ids=["park-visit"],
                                                     verification="server_checked", reported_status="used")
    assert draft["status"] == "proposed"


def test_s1a_each_person_in_a_different_alternative_does_not_cover_the_group(service):
    seed(service)
    item = park_must(service)
    plan = service.state("alice")["plan"]
    plan["days"][1]["plans"] = [dict(id="P1", name="A", tracks=[park_track(["u1"], "ash-park"), cafe_track(["u2", "u3", "u4"], "a-cafe")]),
                                dict(id="P2", name="B", tracks=[park_track(["u2"], "bea-park"), cafe_track(["u1", "u3", "u4"], "b-cafe")])]
    _, draft = say(service, "两套方案", "split", plan=plan, usages=[dict(item_id=item["id"], status="used", block_ids=["ash-park", "bea-park"])],
                   nature="host_restatement")
    assert activity_check(draft, item)["status"] == "conflict" and draft["status"] == "rejected"
    plan["days"][1]["plans"][1]["tracks"] = [park_track(["u1", "u2"], "bea-park"), cafe_track(["u3", "u4"], "b-cafe")]
    plan["days"][1]["plans"][0]["tracks"] = [park_track(["u1", "u2"], "ash-park"), cafe_track(["u3", "u4"], "a-cafe")]
    _, good = say(service, "两套方案都有公园", "split-good", plan=plan, usages=[dict(item_id=item["id"], status="used", block_ids=["ash-park"])],
                  nature="host_restatement")
    assert activity_check(good, item)["status"] == "passed" and good["status"] == "proposed"


def test_s1a_unclassified_text_for_one_person_stays_unverified(service):
    seed(service)
    item = park_must(service)
    vague = dict(subject=["u2"], blocks=[dict(id="maybe", start="10:00", end="11:00", kind="flex", text="Maybe a park walk")])
    plan = day_two(service.state("alice")["plan"], [park_track(["u1"]), vague, cafe_track(["u3", "u4"])])
    _, draft = say(service, "排第二天", "vague", plan=plan, usages=[dict(item_id=item["id"], status="not_applicable", reason="unclear")],
                   nature="host_restatement")
    check = activity_check(draft, item)
    assert check["status"] == "unverified" and check["details"]["people"]["u2"]["status"] == "unknown"


def test_s1a_everyone_subject_needs_every_person(service):
    seed(service)
    item = park_must(service, subject=("*",))
    plan = day_two(service.state("alice")["plan"], [park_track(["u1", "u2", "u3"]), cafe_track(["u4"])])
    _, draft = say(service, "排第二天", "everyone", plan=plan, usages=[dict(item_id=item["id"], status="used", block_ids=["park-visit"])],
                   nature="host_restatement")
    assert activity_check(draft, item)["details"]["people"]["u4"]["status"] == "missing" and draft["status"] == "rejected"


def test_s1a_soft_group_wish_for_one_person_is_not_server_checked(service):
    seed(service)
    item = park_must(service, strength="prefer", key="park-wish")
    plan = day_two(service.state("alice")["plan"], [park_track(["u1"]), cafe_track(["u2", "u3", "u4"])])
    _, draft = say(service, "排第二天", "soft-one", plan=plan, usages=[dict(item_id=item["id"], status="used", block_ids=["park-visit"])],
                   nature="host_restatement")
    usage = draft["checks"]["usages"][0]
    assert usage["status"] == "host_reported" and usage["verification"] == "unverified" and "u2" in usage["reason"]
    assert next(check for check in draft["checks"]["checks"] if check["id"] == f"usage-proof:{item['id']}")["details"]["uncovered"] == ["u2"]
    plan = day_two(service.state("alice")["plan"], [park_track(["u1", "u2"]), cafe_track(["u3", "u4"])])
    _, good = say(service, "排第二天", "soft-both", plan=plan, usages=[dict(item_id=item["id"], status="used", block_ids=["park-visit"])],
                  nature="host_restatement")
    assert good["checks"]["usages"][0]["verification"] == "server_checked"


# --- S4a: a declaration only holds where people, dates and time windows overlap -------------------------------------

def two_items(event, quotes, *, second_scope, first_scope=None, second_subject=("u1",), **declared):
    common = dict(source=dict(event=event, basis="user_explicit"), kind="preference", strength="prefer")
    first_scope = first_scope or dict(start=D1, end=D1)
    return dict(items=[dict(common, id="I-first", key="fort-evening", quote=quotes[0], text=quotes[0], subject=["u1"], scope=first_scope),
                       dict(common, key="viewpoint-evening", quote=quotes[1], text=quotes[1], subject=list(second_subject), scope=second_scope, **declared)])


@pytest.mark.parametrize("declaration", [dict(same_object_as=["fort-evening"]), dict(exclusive_with=["I-first"])])
@pytest.mark.parametrize("separation", ["day", "time", "people"])
def test_s4a_declared_items_in_separate_scopes_stay_active(service, declaration, separation):
    seed(service)
    quotes = {"day": ("第一天晚饭后去渔人堡", "第二天晚饭后不去渔人堡"), "time": ("第一天上午去渔人堡", "第一天晚上不去渔人堡"),
              "people": ("第一天 Ash 去渔人堡", "第一天 Bea 不去渔人堡")}[separation]
    first_scope = dict(start=D1, end=D1, time_from="08:00", time_to="12:00") if separation == "time" else None
    second_scope = dict(start=D2, end=D2) if separation == "day" else dict(start=D1, end=D1, time_from="18:00", time_to="22:00") \
        if separation == "time" else dict(start=D1, end=D1)
    subject = ("u2",) if separation == "people" else ("u1",)
    _, draft = say(service, "。".join(quotes), "separate", changes=lambda event: two_items(
        event, quotes, first_scope=first_scope, second_scope=second_scope, second_subject=subject, **declaration))
    service.commit("alice", draft["draft_id"], "separate")
    assert [item["status"] for item in service.store.all("items", "alice")] == ["active", "active"]
    assert service.store.all("undecided", "alice") == [] and draft["possible_contradictions"] == []
    bundle = package(service, service.turn("alice", "第一天怎么排", "package", scope=dict(start=D1, end=D1)))
    assert "fort-evening" in {item["key"] for item in bundle["items"]}


@pytest.mark.parametrize("declaration", [dict(same_object_as=["fort-evening"]), dict(exclusive_with=["I-first"])])
def test_s4a_declared_items_in_one_scope_become_pending(service, declaration):
    seed(service)
    quotes = ("第一天晚饭后去渔人堡", "第一天晚饭后不去渔人堡")
    _, draft = say(service, "。".join(quotes), "same", changes=lambda event: two_items(event, quotes, second_scope=dict(start=D1, end=D1), **declaration))
    service.commit("alice", draft["draft_id"], "same")
    assert [item["status"] for item in service.store.all("items", "alice")] == ["pending", "pending"]
    question = service.store.all("undecided", "alice")[0]
    assert question["options"] == list(quotes) and question["default"]


# --- S4b: undeclared overlapping items of one message get a non-blocking notice --------------------------------------

SCOPES = {"date": (dict(start=D1, end=D1), dict(start=D1, end=D1)), "whole trip": ({}, {}),
          "trip and date": ({}, dict(start=D1, end=D1)),
          "time window": (dict(start=D1, end=D1, time_from="19:00", time_to="22:00"),) * 2}


def undeclared(service, scopes, key="hint", **declared):
    quotes = ("晚饭后去渔人堡", "晚饭后去抱石")

    def changes(event):
        common = dict(source=dict(event=event, basis="user_explicit"), kind="preference", strength="prefer", subject=["u1", "u2"])
        return dict(items=[dict(common, key="fort", quote=quotes[0], text=quotes[0], scope=scopes[0]),
                           dict(common, key="climb", quote=quotes[1], text=quotes[1], scope=scopes[1], **declared)])
    turn, draft = say(service, "。".join(quotes), key, changes=changes)
    service.commit("alice", draft["draft_id"], key)
    return turn, draft, quotes


@pytest.mark.parametrize("shape", sorted(SCOPES))
def test_s4b_undeclared_overlap_returns_a_non_blocking_notice_and_keeps_both(service, shape):
    seed(service)
    turn, draft, quotes = undeclared(service, SCOPES[shape])
    [notice] = draft["possible_contradictions"]
    assert notice["quotes"] == list(quotes) and len(notice["readings"]) == 2 and notice["blocking"] is False
    assert "Keep both active" in notice["default"]
    assert {item["status"] for item in service.store.all("items", "alice")} == {"active"}
    assert service.store.all("undecided", "alice") == [] and draft["checks"]["undecided"] == []
    later = service.turn("alice", "第一天怎么排", "later", scope=dict(start=D1, end=D1))
    assert {item["key"] for item in package(service, later)["items"]} == {"fort", "climb"}
    assert [entry["item_ids"] for entry in later["possible_contradictions"]] == [notice["item_ids"]]


def relate(service, relation, key):
    first, second = service.store.all("items", "alice")
    _, draft = say(service, "宿主核对了两条的关系", key, nature="host_restatement", changes=lambda event: dict(relate=[dict(
        items=[first["id"], second["id"]], relation=relation, source=dict(event=event))]))
    service.commit("alice", draft["draft_id"], key)
    return draft


def test_s4b_declaring_exclusive_afterwards_makes_both_pending(service):
    seed(service)
    undeclared(service, SCOPES["date"])
    draft = relate(service, "exclusive_with", "exclusive")
    assert draft["possible_contradictions"] == []
    assert {item["status"] for item in service.store.all("items", "alice")} == {"pending"}
    [question] = service.store.all("undecided", "alice")
    assert question["options"] == ["晚饭后去渔人堡", "晚饭后去抱石"]
    assert service.turn("alice", "然后呢", "after")["possible_contradictions"] == []


def test_s4b_declaring_compatible_afterwards_clears_the_notice(service):
    seed(service)
    undeclared(service, SCOPES["whole trip"])
    draft = relate(service, "compatible_with", "compatible")
    assert draft["possible_contradictions"] == []
    assert {item["status"] for item in service.store.all("items", "alice")} == {"active"}
    assert service.store.all("undecided", "alice") == []
    assert service.turn("alice", "然后呢", "after")["possible_contradictions"] == []


def test_s4b_compatible_declared_up_front_gives_no_notice(service):
    seed(service)
    turn, draft, quotes = undeclared(service, SCOPES["date"], compatible_with=["fort"])
    assert draft["possible_contradictions"] == [] and {item["status"] for item in service.store.all("items", "alice")} == {"active"}


def test_s4b_items_in_separate_scopes_get_no_notice(service):
    seed(service)
    turn, draft, quotes = undeclared(service, (dict(start=D1, end=D1), dict(start=D2, end=D2)))
    assert draft["possible_contradictions"] == []


def test_s4b_one_message_recorded_over_two_proposals_gets_the_same_notice(service):
    seed(service)
    quotes = ("晚饭后去渔人堡", "晚饭后去抱石")
    turn = service.turn("alice", "。".join(quotes), "split-message", nature="user_verbatim", native_ref="fixture:split-message")
    event = turn["receipt"]["event_id"]
    common = dict(source=dict(event=event, basis="user_explicit"), kind="preference", strength="prefer", subject=["u1"], scope=dict(start=D1, end=D1))
    for index, (key, quote) in enumerate(zip(("fort", "climb"), quotes)):
        current = service.turn("alice", "continue", f"part-{index}")
        draft = service.propose("alice", ctx_id=current["ctx_id"], base_version=current["base_version"],
                                changes=dict(items=[dict(common, key=key, quote=quote, text=quote)]), idempotency_key=f"part-{index}")
        service.commit("alice", draft["draft_id"], f"part-{index}")
    assert len(draft["possible_contradictions"]) == 1


def test_s4b_relate_refuses_items_of_different_messages(service):
    seed(service)
    for index, quote in enumerate(("晚饭后去渔人堡", "晚饭后去抱石")):
        _, draft = say(service, quote, f"message-{index}", changes=lambda event, quote=quote, index=index: dict(items=[dict(
            source=dict(event=event, basis="user_explicit"), kind="preference", strength="prefer", subject=["u1"], key=f"k{index}",
            quote=quote, text=quote)]))
        service.commit("alice", draft["draft_id"], f"message-{index}")
    with pytest.raises(ValueError, match="relate declares items of one message"):
        relate(service, "exclusive_with", "cross")


# --- locks on other plan parts, option places, declarations across messages ------------------------------------------

@pytest.mark.parametrize("path,stable", [("/places/0", "/places/gym-a"), ("/days/0/title", "/days/2031-05-01/title"),
                                         ("/days/0/plans/1/name", "/days/2031-05-01/plans/alt/name")])
def test_s6c_positional_paths_outside_blocks_are_addressed_by_identity(service, path, stable):
    seed(service)
    plan = service.state("alice")["plan"]
    from tripwright.locks import pointer
    _, draft = say(service, "这个定了", "lock-other", changes=lambda event: dict(decisions=[dict(
        question="定了", options=["这个"], choice="这个", decided_by="user", must=True, subject=GROUP,
        source=dict(event=event, basis="user_explicit"), locked_paths={path: pointer(plan, path)})]))
    service.commit("alice", draft["draft_id"], "lock-other")
    assert list(service.ledger.current("alice")["decisions"][0]["locked_paths"]) == [stable]
    reordered = service.state("alice")["plan"]
    reordered["places"].reverse()
    reordered["days"].reverse()
    first_day = next(entry for entry in reordered["days"] if entry["date"] == D1)
    first_day["plans"].append(first_day["plans"].pop(0))
    assert lock_conflicts(agent(service, reordered, "reordered")) == []
    changed = service.state("alice")["plan"]
    target = pointer(changed, stable.rsplit("/", 1)[0]) if not stable.startswith("/places/") else gym(changed)
    target.update(title="Changed") if "title" in stable else target.update(name="Changed")
    assert lock_conflicts(agent(service, changed, "changed"))[0]["details"]["changed"] == ["value/name" if stable.startswith("/places/") else "value"]


def test_s6b_option_places_of_a_locked_block_keep_their_identity(service):
    plan = trip()
    find(plan, LOCKED)[4]["options"] = ["museum"]
    lock(seed(service, plan))
    candidate = service.state("alice")["plan"]
    next(place for place in candidate["places"] if place["id"] == "museum")["kind"] = "park"
    assert lock_conflicts(agent(service, candidate, "option"))[0]["details"]["changed"] == ["place:museum/kind"]


def test_s4a_a_later_message_declaring_the_same_object_for_another_day_keeps_both(service):
    seed(service)
    _, draft = say(service, "第一天去渔人堡", "first", changes=lambda event: dict(items=[dict(
        source=dict(event=event, basis="user_explicit"), kind="preference", strength="prefer", subject=["u1"], key="fort-d1",
        quote="第一天去渔人堡", text="第一天去渔人堡", scope=dict(start=D1, end=D1))]))
    service.commit("alice", draft["draft_id"], "first")
    _, draft = say(service, "第二天不去渔人堡", "second", changes=lambda event: dict(items=[dict(
        source=dict(event=event, basis="user_explicit"), kind="preference", strength="prefer", subject=["u1"], key="fort-d2",
        quote="第二天不去渔人堡", text="第二天不去渔人堡", scope=dict(start=D2, end=D2), same_object_as=["fort-d1"])]))
    service.commit("alice", draft["draft_id"], "second")
    assert [item["status"] for item in service.store.all("items", "alice")] == ["active", "active"]
    _, draft = say(service, "第一天也不去渔人堡了", "third", changes=lambda event: dict(items=[dict(
        source=dict(event=event, basis="user_explicit"), kind="preference", strength="prefer", subject=["u1"], key="fort-d1-again",
        quote="第一天也不去渔人堡了", text="第一天也不去渔人堡了", scope=dict(start=D1, end=D1), same_object_as=["fort-d1"])]))
    service.commit("alice", draft["draft_id"], "third")
    assert [item["status"] for item in service.store.all("items", "alice")] == ["superseded", "active", "active"]


def test_legacy_lock_that_cannot_be_rebound_fails_closed(service):
    lock(seed(service))
    plan = service.state("alice")["plan"]
    take_block = find(plan, LOCKED)
    take_block[3]["blocks"].remove(take_block[4])
    service.store.put("versions", "alice", dict(service.versions.current("alice"), plan=plan))
    stored = service.store.all("decisions", "alice")[0]
    stored["placement"] = {f"/blocks/{LOCKED}": dict(date=D1, plan="Recommended", subject=GROUP)}
    service.store.put("decisions", "alice", stored)
    candidate = trip()
    assert set(lock_conflicts(agent(service, candidate, "legacy-missing"))[0]["details"]["changed"]) >= {"plan", "rank"}
