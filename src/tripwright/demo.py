from __future__ import annotations

from copy import deepcopy
from importlib.resources import files
from pathlib import Path

import yaml

from tripwright.service import Tripwright
from tripwright.storage import now
from tripwright.usage import place_kind


def example() -> dict:
    bundled = files("tripwright").joinpath("examples/fjord_demo.yaml")
    path = bundled if bundled.is_file() else Path(__file__).resolve().parents[2] / "examples/fjord_demo.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def run_demo(service: Tripwright, owner: str = "demo", output=print) -> dict:
    data = example()
    trip = data["trip"]
    output("1. Fictional example: 4 days, 3 people, Oslo and Bergen. No real bookings or timetables.")
    received = service.turn(owner, "Create this fictional example trip.", "demo-seed")
    proposed = service.propose(owner, ctx_id=received["ctx_id"], base_version=0, plan=trip, idempotency_key="demo-seed")
    service.commit(owner, proposed["draft_id"], "demo-seed", accept_unverified=True)
    output("2. Initial version committed; hard-node evidence is explicitly UNVERIFIED.")
    items = []
    source_event = None
    for index, requirement in enumerate(data["requirements"]):
        received = service.turn(owner, requirement["text"], f"demo-preference-{index}", nature="user_verbatim", native_ref=f"fictional:message:{index}")
        source_event = received["receipt"]["event_id"]
        items.append(dict(kind="preference", key=requirement["key"], quote=requirement["text"], text=requirement["text"],
                          strength=requirement["strength"], subject=requirement["subject"], reason=requirement["reason"],
                          source={"event": source_event, "basis": "user_explicit"}))
    received = service.turn(owner, "On 2030-06-11 Mira will act alone in the morning. We have decided on the café after dinner on day one.", "demo-status", nature="user_verbatim", native_ref="fictional:status",
                            speaker="p2")
    source_event = received["receipt"]["event_id"]
    source = {"event": source_event, "basis": "user_explicit"}
    coffee = trip["days"][0]["plans"][0]["tracks"][0]["blocks"][2]
    normalized = service.versions.current(owner)["plan"]
    normalized_coffee = normalized["days"][0]["plans"][0]["tracks"][0]["blocks"][2]
    changes = dict(items=items, facts=[dict(key="companions", value=[], text="Mira acts alone in the morning.", subject=["p1"],
                   scope={"start": "2030-06-11", "end": "2030-06-11", "time_from": "08:00", "time_to": "12:00"},
                   true_from=now(), invalidated_by="Morning ends or user corrects the arrangement", source=source)],
                   decisions=[dict(question="What happens after dinner on the first day?", options=["Café", "Park"], choice=coffee["text"],
                                   decided_by="user", subject=["p1", "p2", "p3"], source=source,
                                   locked_paths={"/days/0/plans/0/tracks/0/blocks/2": normalized_coffee}, reason="The group chose the café.",
                                   revisit_when="Only an explicit new user decision can reopen this choice.")])
    proposed = service.propose(owner, ctx_id=received["ctx_id"], base_version=1, changes=changes, idempotency_key="demo-ledger")
    service.commit(owner, proposed["draft_id"], "demo-ledger", accept_unverified=True)
    output("3. Stored each person's preferences, optional strength, soft reason, scoped solo fact and user decision.")
    received = service.turn(owner, "Plan the first day for Mira with a relaxed pace.", "demo-plan", subject=["p1"], scope={"start": "2030-06-10", "end": "2030-06-10"}, scenario="replan")
    bundle = service.store.get("contexts", owner, received["ctx_id"])["bundle"]
    output("4. turn compiled Mira's applicable conditions: " + "; ".join(item["quote"] for item in bundle["items"]))
    plan = deepcopy(service.versions.current(owner)["plan"])
    usages = []
    places = {place["id"]: place for place in plan["places"]}
    for item in service.ledger.current(owner)["items"]:
        kind = place_kind(item)
        accepted = {"gallery": {"gallery", "museum"}, "view": {"view", "viewpoint"}}.get(kind, {kind})
        block_ids = [block["id"] for track in plan["days"][0]["plans"][0]["tracks"]
                     if set(track["subject"]) & set(item["subject"])
                     for block in track["blocks"] if kind and places.get(block.get("place"), {}).get("kind") in accepted]
        if kind and not block_ids:
            usages.append(dict(item_id=item["id"], status="traded_off", reason="The first day keeps a relaxed pace; this optional activity is not scheduled that day."))
        else:
            usages.append(dict(item_id=item["id"], status="used", block_ids=block_ids,
                               reason="Host interpretation of the soft condition; only matching activities are server-checked."))
    proposed = service.propose(owner, ctx_id=received["ctx_id"], base_version=2, plan=plan, usages=usages, idempotency_key="demo-plan")
    output("5. propose returned a draft and deep-check job; each applicable condition is used or traded off:")
    recorded = {item["id"]: item for item in service.ledger.current(owner)["items"]}
    for usage in proposed["usages"]:
        item = recorded.get(usage["item_id"], {})
        output(f"   {item.get('subject')}: {item.get('quote')} [{usage['status']}; {usage.get('verification', 'reason supplied')}]")
    service.jobs.run_once(owner)
    second = service.result(owner, proposed["job_id"])
    output(f"6. result retrieved: {second['status']}; unresolved hard-node evidence stays UNVERIFIED.")
    bad = deepcopy(plan)
    bad["days"][0]["plans"][0]["tracks"][0]["blocks"][2]["place"] = "oslo-park"
    rejected = service.propose(owner, ctx_id=received["ctx_id"], base_version=2, plan=bad, usages=usages, idempotency_key="demo-bad-lock")
    try:
        service.commit(owner, rejected["draft_id"], "demo-bad-lock", accept_unverified=True)
    except ValueError:
        output("7. Rejected a proposal that changed the user-decided after-dinner café.")
    else:
        raise AssertionError("A user-decided field was overwritten")
    version = service.commit(owner, proposed["draft_id"], "demo-plan", accept_unverified=True)
    output(f"8. commit created version {version['number']}; verified={version['checks']['verified']} (uncertainty disclosed).")
    output(service.state(owner, "day", "2030-06-10")["text"])
    return version
