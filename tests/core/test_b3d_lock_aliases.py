"""Metamorphic tests for user locks (B3d, S6a–S6c).

A user locks one block. A generator applies random compositions of transformations to the committed plan: legal ones
(unrelated edits, pure reorderings, description changes, the user's own new decision) and alias attacks (same-name plans
or people, swapped or stripped plan IDs, plan reordering, split and merged blocks, moved days, copies, redefined places,
bookings and people). Property: a candidate keeps the lock exactly when no attack was applied or the user decided again;
every attack is refused naming the lock at propose and at commit.

B3f (D-36) has no exemptions: four "legal" changes here assumed them (a place's hours, price, rain flag and priority; a
few metres of coordinates; a booking's channel, deadline and refund terms; buying the booking). They are attacks now,
and the user's new decision still releases each; the description edits keep only pure description fields."""
from __future__ import annotations

import random
from copy import deepcopy
from itertools import count

import pytest

D1, D2, D3 = "2031-05-01", "2031-05-02", "2031-05-03"
LOCKED = "d1-after"
GROUP = ["u1", "u2", "u3"]
EVERYONE = ["u1", "u2", "u3", "u4"]


def trip() -> dict:
    """Fictional trip: Bea (u2) and another Bea (u4); day one has two alternatives that share the display name."""
    place = lambda pid, name, local, kind, coords, area="Lakeside": dict(id=pid, name=name, name_local=local, area=area, kind=kind, coords=coords)
    return dict(
        id="alias-trip", title="Fictional lakes", start=D1, end=D3, timezone="Europe/Vienna", areas=["Lakeside", "Hilltown"],
        people=[dict(id="u1", name="Ash"), dict(id="u2", name="Bea"), dict(id="u3", name="Cy"), dict(id="u4", name="Bea")],
        places=[place("gym-a", "虚构岩馆 A", "Kletterhalle A (fictional)", "climbing", [47.80, 13.04]),
                place("museum", "虚构博物馆", "Museum M (fictional)", "museum", [47.81, 13.05]),
                place("cafe", "虚构咖啡馆", "Cafe Ruhe (fictional)", "food", [47.82, 13.06]),
                place("park", "虚构公园", "Seepark (fictional)", "park", [47.83, 13.07]),
                place("hill", "虚构山顶", "Huegel (fictional)", "view", [47.90, 13.20], area="Hilltown")],
        bookings=[dict(id="bk-gym", date=D1, kind="ticket", what="Climbing evening pass (fictional)", subject=GROUP,
                       status="to_buy", place="gym-a", why_now="Evening slots sell out")],
        days=[
            dict(date=D1, title="Day one", area="Lakeside", plans=[
                dict(id="rec", name="Recommended", tracks=[
                    dict(subject=GROUP, blocks=[
                        dict(id="d1-museum", start="10:00", end="11:00", kind="visit", place="museum"),
                        dict(id="d1-dinner", start="18:00", end="19:00", kind="meal", place="cafe", transfer_minutes=10),
                        dict(id=LOCKED, start="19:30", end="21:00", kind="visit", place="gym-a", transfer_minutes=15,
                             booking="bk-gym", text="饭后抱石（用户已定）")]),
                    dict(subject=["u4"], blocks=[dict(id="d1-u4-park", start="10:00", end="11:00", kind="flex", place="park")])]),
                dict(id="alt", name="Recommended", tracks=[
                    dict(subject=EVERYONE, blocks=[
                        dict(id="d1-park", start="10:00", end="12:00", kind="flex", place="park"),
                        dict(id="d1-alt-dinner", start="18:00", end="19:00", kind="meal", place="cafe", transfer_minutes=10)])])]),
            dict(date=D2, title="Day two", area="Lakeside", plans=[dict(name="Recommended", tracks=[dict(subject=EVERYONE, blocks=[
                dict(id="d2-cafe", start="10:00", end="11:00", kind="meal", place="cafe"),
                dict(id="d2-park", start="12:00", end="13:00", kind="flex", place="park", transfer_minutes=15)])])]),
            dict(date=D3, title="Day three", area="Hilltown", plans=[dict(name="Recommended", tracks=[dict(subject=EVERYONE, blocks=[
                dict(id="d3-hill", start="10:00", end="12:00", kind="visit", place="hill")])])])])


# --- where things are, found by identity rather than by position ------------------------------------------------------

def day(plan, date):
    return next(entry for entry in plan["days"] if entry["date"] == date)


def find(plan, block_id):
    for entry in plan["days"]:
        for rank, option in enumerate(entry["plans"]):
            for track in option["tracks"]:
                for block in track["blocks"]:
                    if block["id"] == block_id:
                        return entry, rank, option, track, block
    return None


def take(plan, block_id):
    entry, rank, option, track, block = find(plan, block_id)
    track["blocks"].remove(block)
    return block


def locked_plan(plan):
    return find(plan, LOCKED)[2]


def other_plan(plan):
    entry, rank, option, track, block = find(plan, LOCKED)
    return next(candidate for candidate in entry["plans"] if candidate is not option)


def person(plan, person_id):
    return next(entry for entry in plan["people"] if entry["id"] == person_id)


def gym(plan):
    return next(place for place in plan["places"] if place["id"] == "gym-a")


def booking(plan):
    return next(entry for entry in plan["bookings"] if entry["id"] == "bk-gym")


def has(plan, block_id):
    return find(plan, block_id) is not None


# --- transformations: (name, precondition, change, lock kinds it breaks) ---------------------------------------------

LEGAL = {}
ATTACKS = {}


NEEDS = {}


def legal(name, applies=lambda plan: True, needs=()):
    def register(change):
        LEGAL[name] = (applies, change)
        NEEDS[name] = list(needs)
        return change
    return register


def attack(name, applies=lambda plan: True, breaks=("block", "field"), refused_by="lock", redecidable=True):
    def register(change):
        ATTACKS[name] = dict(applies=applies, change=change, breaks=set(breaks), refused_by=refused_by, redecidable=redecidable)
        return change
    return register


@legal("insert unrelated block on day two", lambda plan: not has(plan, "d2-breakfast"))
def _(plan, rng):
    day(plan, D2)["plans"][0]["tracks"][0]["blocks"].insert(0, dict(id="d2-breakfast", start="07:00", end="07:30", kind="flex", text="Breakfast"))


@legal("insert unrelated block before the locked one", lambda plan: not has(plan, "d1-breakfast"))
def _(plan, rng):
    find(plan, LOCKED)[3]["blocks"].insert(0, dict(id="d1-breakfast", start="08:00", end="08:30", kind="flex", text="Breakfast"))


@legal("reorder the locked track's people")
def _(plan, rng):
    rng.shuffle(find(plan, LOCKED)[3]["subject"])


@legal("reorder the locked track's block list")
def _(plan, rng):
    rng.shuffle(find(plan, LOCKED)[3]["blocks"])


@legal("reorder places")
def _(plan, rng):
    rng.shuffle(plan["places"])


@legal("reorder people")
def _(plan, rng):
    rng.shuffle(plan["people"])


@legal("reorder tracks of the locked plan")
def _(plan, rng):
    locked_plan(plan)["tracks"].reverse()


@legal("reorder days")
def _(plan, rng):
    plan["days"].reverse()


@legal("rename the locked plan's display name")
def _(plan, rng):
    locked_plan(plan)["name"] = rng.choice(["Relaxed day", "Option B", "Recommended"])


@legal("rename the other plan's display name")
def _(plan, rng):
    other_plan(plan)["name"] = rng.choice(["Option B", "Recommended", "Rain"])


@legal("edit description fields of the locked place")
def _(plan, rng):
    gym(plan).update(notes="Bring chalk", links=["https://example.invalid/gym-a"], reason="Indoor if it rains", checked="2031-04-20")


@legal("edit description fields of the locked booking")
def _(plan, rng):
    booking(plan).update(why_now="Only 12 evening slots")


@attack("change the locked place's hours, price, rain flag or priority")
def _(plan, rng):
    field, value = rng.choice([("hours", "10:00–22:00"), ("price", {"EUR": 14}), ("rain_ok", True), ("priority", "must")])
    gym(plan)[field] = value


@attack("correct the locked place's coordinates by a few metres")
def _(plan, rng):
    latitude, longitude = gym(plan)["coords"]
    gym(plan)["coords"] = [round(latitude + rng.choice([-1, 1]) * 0.0003, 6), longitude]


@attack("change the locked booking's channel, deadline or refund terms", breaks=("block",))
def _(plan, rng):
    field, value = rng.choice([("where", ["Front desk"]), ("if_missed", "Climb at gym B"), ("due", D1)])
    booking(plan)[field] = value


@attack("buy the locked booking without the user's word", breaks=("block",))
def _(plan, rng):
    booking(plan)["status"] = "bought"


@legal("add an unrelated person with an existing name", lambda plan: all(entry["id"] != "u5" for entry in plan["people"]))
def _(plan, rng):
    plan["people"].append(dict(id="u5", name="Ash"))


@legal("rename a person outside the locked track")
def _(plan, rng):
    person(plan, "u4")["name"] = "Bee"


@legal("edit a block of the other plan", lambda plan: has(plan, "d1-park"))
def _(plan, rng):
    find(plan, "d1-park")[4]["end"] = "11:30"


@legal("add an alternative plan after the others", lambda plan: len(day(plan, D1)["plans"]) < 3 and not has(plan, "d1-extra"))
def _(plan, rng):
    day(plan, D1)["plans"].append(dict(name=rng.choice(["Recommended", "Rain"]), tracks=[dict(subject=EVERYONE, blocks=[
        dict(id="d1-extra", start="10:00", end="11:00", kind="visit", place="museum")])]))


@legal("swap the two alternatives after the locked plan",
       lambda plan: len(day(plan, D1)["plans"]) == 3 and find(plan, LOCKED)[1] == 0, needs=["add an alternative plan after the others"])
def _(plan, rng):
    plans = day(plan, D1)["plans"]
    plans[1], plans[2] = plans[2], plans[1]


@legal("swap the IDs of the two alternatives after the locked plan",
       lambda plan: len(day(plan, D1)["plans"]) == 3 and find(plan, LOCKED)[1] == 0 and all(option.get("id") for option in day(plan, D1)["plans"]),
       needs=["add an alternative plan after the others", "give the new alternative an ID"])
def _(plan, rng):
    plans = day(plan, D1)["plans"]
    plans[1]["id"], plans[2]["id"] = plans[2]["id"], plans[1]["id"]


@legal("give the new alternative an ID", lambda plan: any(option.get("id") is None for option in day(plan, D1)["plans"]),
       needs=["add an alternative plan after the others"])
def _(plan, rng):
    for option in day(plan, D1)["plans"]:
        option.setdefault("id", None)
        option["id"] = option["id"] or "rain"


@legal("omit every plan ID and keep the order")
def _(plan, rng):
    for entry in plan["days"]:
        for option in entry["plans"]:
            option.pop("id", None)


@legal("move an unrelated block to another day", lambda plan: has(plan, "d2-park") and find(plan, "d2-park")[0]["date"] == D2)
def _(plan, rng):
    moved = take(plan, "d2-park")
    moved.update(start="14:00", end="15:00")
    day(plan, D3)["plans"][0]["tracks"][0]["blocks"].append(moved)


@legal("split an unrelated block", lambda plan: has(plan, "d1-museum") and not has(plan, "d1-museum-2"))
def _(plan, rng):
    entry, rank, option, track, block = find(plan, "d1-museum")
    block["end"] = "10:30"
    track["blocks"].insert(track["blocks"].index(block) + 1, dict(block, id="d1-museum-2", start="10:30", end="11:00", transfer_minutes=None))


@legal("merge two unrelated blocks", lambda plan: has(plan, "d2-cafe") and has(plan, "d2-park") and find(plan, "d2-park")[0]["date"] == D2)
def _(plan, rng):
    take(plan, "d2-park")
    find(plan, "d2-cafe")[4]["end"] = "13:00"


@legal("copy the locked activity to another day under a new ID", lambda plan: not has(plan, "d3-climb"))
def _(plan, rng):
    copy = dict(deepcopy(find(plan, LOCKED)[4]), id="d3-climb", booking=None, transfer_minutes=None)
    day(plan, D3)["plans"][0]["tracks"][0]["blocks"].append(copy)


@attack("move the block into the other plan with the same display name")
def _(plan, rng):
    source, target = locked_plan(plan), other_plan(plan)
    target["name"] = source["name"]
    target["tracks"].append(dict(subject=list(GROUP), blocks=[take(plan, LOCKED)]))


@attack("move the block into the other plan under another display name")
def _(plan, rng):
    source, target = locked_plan(plan), other_plan(plan)
    target["name"] = source["name"] + " (B)"
    target["tracks"].append(dict(subject=list(GROUP), blocks=[take(plan, LOCKED)]))


@attack("move the block into a new plan named like its own", lambda plan: len(find(plan, LOCKED)[0]["plans"]) < 3)
def _(plan, rng):
    entry, rank, option, track, block = find(plan, LOCKED)
    track["blocks"].remove(block)
    entry["plans"].append(dict(name=option["name"], tracks=[dict(subject=list(GROUP), blocks=[block])]))


@attack("swap plan IDs and move the block with its old ID", lambda plan: all(option.get("id") for option in find(plan, LOCKED)[0]["plans"]))
def _(plan, rng):
    source, target = locked_plan(plan), other_plan(plan)
    source["id"], target["id"] = target["id"], source["id"]
    target["tracks"].append(dict(subject=list(GROUP), blocks=[take(plan, LOCKED)]))


@attack("swap plan IDs only", lambda plan: all(option.get("id") for option in find(plan, LOCKED)[0]["plans"]))
def _(plan, rng):
    source, target = locked_plan(plan), other_plan(plan)
    source["id"], target["id"] = target["id"], source["id"]


@attack("move the locked plan behind another alternative")
def _(plan, rng):
    entry, rank, option, track, block = find(plan, LOCKED)
    entry["plans"].remove(option)
    entry["plans"].insert(rank + 1 if rank + 1 <= len(entry["plans"]) else 0, option)


@attack("insert a new alternative before the locked plan", lambda plan: len(find(plan, LOCKED)[0]["plans"]) < 3)
def _(plan, rng):
    find(plan, LOCKED)[0]["plans"].insert(0, dict(name="Recommended", tracks=[dict(subject=EVERYONE, blocks=[
        dict(id="d1-new-first", start="10:00", end="11:00", kind="visit", place="museum")])]))


@attack("omit plan IDs and swap the alternatives")
def _(plan, rng):
    entry = find(plan, LOCKED)[0]
    for option in entry["plans"]:
        option.pop("id", None)
    entry["plans"].reverse()


@attack("split the locked block", breaks=("block",))
def _(plan, rng):
    entry, rank, option, track, block = find(plan, LOCKED)
    block["end"] = "20:15"
    track["blocks"].append(dict(block, id="d1-after-2", start="20:15", end="21:00", transfer_minutes=None, booking=None))


@attack("merge the locked block into dinner", redecidable=False)
def _(plan, rng):
    take(plan, LOCKED)
    find(plan, "d1-dinner")[4]["end"] = "21:00"


@attack("merge dinner into the locked block", breaks=("block",))
def _(plan, rng):
    take(plan, "d1-dinner")
    find(plan, LOCKED)[4].update(start="18:00", transfer_minutes=None)


@attack("move the block to another day")
def _(plan, rng):
    moved = take(plan, LOCKED)
    day(plan, D2)["plans"][0]["tracks"][0]["blocks"].append(dict(moved, transfer_minutes=None))


@attack("swap the dates of day one and day two")
def _(plan, rng):
    first, second = day(plan, D1), day(plan, D2)
    first["date"], second["date"] = D2, D1


@attack("replace Bea with the other Bea in the locked track")
def _(plan, rng):
    subject = find(plan, LOCKED)[3]["subject"]
    subject[subject.index("u2")] = "u4"


@attack("rename a person of the locked track")
def _(plan, rng):
    person(plan, "u2")["name"] = "Dana"


@attack("swap the names of two people of the locked track")
def _(plan, rng):
    bea, cy = person(plan, "u2"), person(plan, "u3")
    bea["name"], cy["name"] = cy["name"], bea["name"]


@attack("move the block into a track with fewer people")
def _(plan, rng):
    entry, rank, option, track, block = find(plan, LOCKED)
    track["blocks"].remove(block)
    option["tracks"].append(dict(subject=["u1"], blocks=[block]))


@attack("rename the locked place")
def _(plan, rng):
    gym(plan)["name"] = "虚构博物馆 B"


@attack("rename the locked place's local name")
def _(plan, rng):
    gym(plan)["name_local"] = "Museum B (fictional)"


@attack("change the locked place's kind")
def _(plan, rng):
    gym(plan)["kind"] = "museum"


@attack("move the locked place to another area")
def _(plan, rng):
    gym(plan)["area"] = "Hilltown"


@attack("move the locked place's coordinates to another branch")
def _(plan, rng):
    latitude, longitude = gym(plan)["coords"]
    gym(plan)["coords"] = [latitude + rng.choice([-1, 1]) * 0.05, longitude]


@attack("remove the locked place's coordinates")
def _(plan, rng):
    gym(plan)["coords"] = None


@attack("add a transit stop to the locked place", lambda plan: gym(plan).get("transit_id") is None)
def _(plan, rng):
    gym(plan)["transit_id"] = "stop:other-branch"


@attack("redefine what the locked booking is", breaks=("block",))
def _(plan, rng):
    booking(plan)["what"] = "Museum ticket (fictional)"


@attack("point the locked booking at another place", breaks=("block",))
def _(plan, rng):
    booking(plan)["place"] = "museum"


@attack("change the trip timezone")
def _(plan, rng):
    plan["timezone"] = "Europe/Berlin"


@attack("copy the block elsewhere under the same ID", refused_by="validation", redecidable=False)
def _(plan, rng):
    day(plan, D3)["plans"][0]["tracks"][0]["blocks"].append(deepcopy(find(plan, LOCKED)[4]))


@attack("copy the block elsewhere and drop the original", redecidable=False)
def _(plan, rng):
    moved = take(plan, LOCKED)
    day(plan, D3)["plans"][0]["tracks"][0]["blocks"].append(dict(moved, id="d3-climb-copy", booking=None, transfer_minutes=None))


@attack("rename the locked block's ID", redecidable=False)
def _(plan, rng):
    find(plan, LOCKED)[4]["id"] = "d1-after-renamed"


@attack("edit the locked block's text", breaks=("block",))
def _(plan, rng):
    find(plan, LOCKED)[4]["text"] = "饭后去博物馆"


@attack("edit the locked block's time", breaks=("block",))
def _(plan, rng):
    find(plan, LOCKED)[4].update(start="19:45", end="21:15")


# --- running a case --------------------------------------------------------------------------------------------------

LOCK_PATHS = {"block": f"/blocks/{LOCKED}", "field": f"/blocks/{LOCKED}/place"}
KEYS = count()


@pytest.fixture(params=["block", "field"])
def locked(request, service):
    """A committed trip where the group decided the after-dinner climbing block (whole block, or only its place)."""
    turn = service.turn("alice", "Create the fictional trip.", "seed")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=trip(), idempotency_key="seed")
    service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)
    # Adding a person is one of the legal walks here; since M1 people are set once unless the user says they change.
    service.turn("alice", "这趟同行的人还会变。", "people-change", nature="user_verbatim", native_ref="fixture:people", speaker="u1",
                 record=dict(items=[dict(kind="setting", key="stability:person", value="changing", quote="这趟同行的人还会变。",
                                         text="这趟人员可能会变", subject=["*"], strength="prefer", source=dict(event="this", basis="user_explicit"))]))
    block = find(service.state("alice")["plan"], LOCKED)[4]
    turn = service.turn("alice", "第一天饭后我们三个去岩馆 A，定了。", "lock", nature="user_verbatim", native_ref="fixture:lock", speaker="u1")
    path = LOCK_PATHS[request.param]
    decision = dict(question="第一天饭后去哪", options=["岩馆 A", "公园"], choice="岩馆 A", decided_by="user", must=True, subject=GROUP,
                    source=dict(event=turn["receipt"]["event_id"], basis="user_explicit"),
                    locked_paths={path: block if request.param == "block" else block["place"]})
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], changes=dict(decisions=[decision]), idempotency_key="lock")
    service.commit("alice", draft["draft_id"], "lock")
    return service, request.param


def propose(service, plan, *, redecide=False, lock_kind="block"):
    key = f"case-{next(KEYS)}"
    if not redecide:
        turn = service.turn("alice", "agent replans", key, nature="agent_inference")
        return service.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=plan, idempotency_key=key)
    turn = service.turn("alice", "改成这样，我们定了。", key, nature="user_verbatim", native_ref=f"fixture:{key}", speaker="u1")
    block = find(plan, LOCKED)[4]
    decision = dict(question="第一天饭后怎么安排", options=["照新的"], choice="照新的", decided_by="user", must=True, subject=GROUP,
                    source=dict(event=turn["receipt"]["event_id"], basis="user_explicit"),
                    locked_paths={LOCK_PATHS[lock_kind]: deepcopy(block) if lock_kind == "block" else block["place"]})
    return service.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=plan,
                           changes=dict(decisions=[decision]), idempotency_key=key)


def lock_checks(draft):
    return [check for check in draft["checks"]["checks"] if check["id"].startswith("locked:")]


def generate(plan, rng, *, legal_names, attack_name=None):
    applied = []
    for name in legal_names:
        applies, change = LEGAL[name]
        if applies(plan):
            change(plan, rng)
            applied.append(name)
    if attack_name is not None:
        ATTACKS[attack_name]["change"](plan, rng)
        applied.append(attack_name)
    return applied


def assert_case(service, lock_kind, plan, *, attack_name, redecide, label, commit_legal=False):
    breaks = attack_name is not None and lock_kind in ATTACKS[attack_name]["breaks"]
    if attack_name is not None and ATTACKS[attack_name]["refused_by"] == "validation":
        with pytest.raises(ValueError, match="unique"):
            propose(service, plan, redecide=redecide, lock_kind=lock_kind)
        return
    draft = propose(service, plan, redecide=redecide, lock_kind=lock_kind)
    checks = lock_checks(draft)
    assert checks, label
    conflicts = [check for check in checks if check["status"] == "conflict"]
    if breaks and not redecide:
        assert draft["status"] == "rejected", label
        assert conflicts and all(check["details"]["changed"] for check in conflicts), label
        assert any(check["id"].startswith("locked:") for check in draft["checks"]["conflicts"]), label
        with pytest.raises(ValueError, match=r"Commit refused: .*locked:"):
            service.commit("alice", draft["draft_id"], draft["draft_id"], accept_unverified=True)
    else:
        assert not conflicts, (label, [check["details"] for check in conflicts])
        if attack_name is None:
            assert draft["status"] == "proposed", (label, [check["id"] for check in draft["checks"]["conflicts"]])
            if commit_legal:
                service.commit("alice", draft["draft_id"], draft["draft_id"], accept_unverified=True)


def test_each_attack_alone_is_refused_naming_the_lock(locked):
    service, lock_kind = locked
    rng = random.Random(0)
    for name, spec in ATTACKS.items():
        plan = service.state("alice")["plan"]
        assert spec["applies"](plan), name
        generate(plan, rng, legal_names=[], attack_name=name)
        assert_case(service, lock_kind, plan, attack_name=name, redecide=False, label=name)


def test_each_legal_change_alone_keeps_the_lock(locked):
    service, lock_kind = locked
    rng = random.Random(1)
    for name in LEGAL:
        plan = service.state("alice")["plan"]
        assert generate(plan, rng, legal_names=[*NEEDS[name], name])[-1] == name, name
        assert_case(service, lock_kind, plan, attack_name=None, redecide=False, label=name)


def test_users_new_decision_releases_every_redecidable_attack(locked):
    service, lock_kind = locked
    rng = random.Random(2)
    for name, spec in ATTACKS.items():
        if not spec["redecidable"] or spec["refused_by"] != "lock":
            continue
        plan = service.state("alice")["plan"]
        generate(plan, rng, legal_names=[], attack_name=name)
        assert_case(service, lock_kind, plan, attack_name=name, redecide=True, label="redecided: " + name)


def test_random_compositions_keep_the_property(locked):
    """Random legal prefixes, then maybe an attack, then maybe the user's own decision; 48 cases per lock kind."""
    service, lock_kind = locked
    seen = dict(legal_only=0, refused=0, kept_despite_attack=0, redecided=0)
    for seed in range(48):
        rng = random.Random(1000 + seed)
        plan = service.state("alice")["plan"]
        legal_names = rng.sample(sorted(LEGAL), rng.randint(0, 5))
        prefix = generate(plan, rng, legal_names=legal_names)
        candidates = [name for name, spec in ATTACKS.items() if spec["applies"](plan)]
        attack_name = rng.choice(candidates) if candidates and rng.random() < 0.7 else None
        if attack_name:
            generate(plan, rng, legal_names=[], attack_name=attack_name)
        spec = ATTACKS.get(attack_name, {})
        redecide = bool(attack_name) and spec["redecidable"] and spec["refused_by"] == "lock" and rng.random() < 0.3
        assert_case(service, lock_kind, plan, attack_name=attack_name, redecide=redecide,
                    label=f"seed {seed}: {prefix} + {attack_name} redecide={redecide}")
        key = ("legal_only" if attack_name is None else "redecided" if redecide
               else "refused" if lock_kind in spec["breaks"] else "kept_despite_attack")
        seen[key] += 1
    assert seen["legal_only"] >= 5 and seen["refused"] >= 15 and seen["redecided"] >= 3, seen


@pytest.mark.parametrize("seed", range(3))
def test_lock_survives_a_walk_of_committed_legal_versions(locked, seed):
    """Commit legal candidates one after another (plan IDs omitted, reordered, renamed …); after each version every
    attack applicable to that version is still refused."""
    service, lock_kind = locked
    rng = random.Random(2000 + seed)
    walk = [name for name in LEGAL if name not in ("buy the locked booking", "edit description fields of the locked booking")]
    rng.shuffle(walk)
    for step, name in enumerate(walk[:6]):
        plan = service.state("alice")["plan"]
        if not LEGAL[name][0](plan):
            continue
        LEGAL[name][1](plan, rng)
        assert_case(service, lock_kind, plan, attack_name=None, redecide=False, label=f"walk {seed}:{step} {name}", commit_legal=True)
        current = service.state("alice")["plan"]
        assert all(option.get("id") for entry in current["days"] for option in entry["plans"])
        for attack_name in rng.sample(sorted(ATTACKS), 3):
            candidate = deepcopy(current)
            if ATTACKS[attack_name]["applies"](candidate):
                generate(candidate, rng, legal_names=[], attack_name=attack_name)
                assert_case(service, lock_kind, candidate, attack_name=attack_name, redecide=False, label=f"walk {seed}:{step} then {attack_name}")
    decision = next(entry for entry in service.ledger.current("alice")["decisions"] if entry["status"] == "active")
    recorded = decision["placement"][LOCK_PATHS[lock_kind]]
    assert (recorded["date"], recorded["plan"], recorded["rank"], recorded["subject"]) == (D1, "rec", 0, GROUP)
