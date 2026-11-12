"""`query(kind="saved")` finds what the user and the researcher stored, not only places (2026-11-12).

The user: guides sent as screenshots, and the agent's own research, become the user's database that later answers use;
what is missing is looked up and recorded. Before, the saved lookup read only the plan's places, so a preference
transcribed from a screenshot or a web check the researcher recorded was stored but never found again. Now it also
returns the facts and items in force and the recorded web checks, each with where it came from."""
import asyncio
import time

SHOTS = "【截图 1/2】虚构攻略：港口边的鱼汤店 Fiskebar Nord，周一闭店。\n【截图 2/2】虚构攻略：Kaffe Lille 的肉桂卷，早上 8 点开门。"


def seeded(service, plan, monkeypatch):
    monkeypatch.setenv("TRIPWRIGHT_NOW", "2030-06-10T09:00:00+02:00")
    monkeypatch.setenv("TRIPWRIGHT_NOW_SET_AT", str(time.time()))
    turn = service.turn("alice", "创建示例行程。", "seed")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=plan, idempotency_key="seed")
    service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)
    return service


def saved(service, text):
    return asyncio.run(service.query("alice", "saved", dict(text=text)))["evidence"]["value"]


def test_a_preference_from_a_screenshot_and_a_recorded_web_check_are_found_again(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    item = dict(kind="preference", key="food:fish_soup", quote="港口边的鱼汤店", text="想去港口边的鱼汤店 Fiskebar Nord",
                subject=["p1"], strength="prefer", source=dict(event="this"))
    service.turn("alice", SHOTS, "shots", nature="screenshot_extraction", record=dict(items=[item]))
    asyncio.run(service.query("alice", "web_evidence", dict(url="https://example.org/kaffe-lille", quote="Open from 08:00",
                                                             claim="Kaffe Lille opens at 08:00", type="opening_hours")))
    [found] = saved(service, "鱼汤")
    assert found["found"] == "item" and found["key"] == "food:fish_soup" and found["nature"] == "screenshot_extraction"
    [web] = saved(service, "kaffe")
    assert web["found"] == "web" and web["claim"] == "Kaffe Lille opens at 08:00" and isinstance(web["counts_as_checked"], bool)
    assert saved(service, "nothing-stored-like-this") == []


def test_places_are_found_as_before(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    values = saved(service, "Example Oslo Gallery")
    assert values and all("found" not in value for value in values) and values[0]["id"] == "oslo-art"


def test_an_earlier_transit_or_weather_lookup_is_found_again_and_says_when_it_expired(service, plan, monkeypatch):
    """The user: what was looked up once is recorded and reused. Before, only web checks came back; a transit or
    weather lookup was stored as evidence but `saved` could not find it."""
    seeded(service, plan, monkeypatch)
    asyncio.run(service.query("alice", "weather", dict(place=dict(name="Skerry Point", lat=60.39, lon=5.32), date="2030-06-10")))
    asyncio.run(service.query("alice", "transit", dict(origin=dict(name="Harbour", lat=59.91, lon=10.75),
                                                       destination="oslo-art", when="2030-06-10T20:30:00+02:00")))
    [weather] = saved(service, "skerry")
    assert weather["found"] == "lookup" and weather["kind"] == "weather" and weather["date"] == "2030-06-10"
    assert weather["synthetic"] is True and weather["value"] is not None and weather["fetched_at"]
    [transit] = [value for value in saved(service, "oslo-art") if value.get("found") == "lookup"]
    assert transit["found"] == "lookup" and transit["kind"] == "transit" and transit["told"] and transit["value"] is None
    assert transit["expired"] is False
    assert transit["valid_until"]
    monkeypatch.setenv("TRIPWRIGHT_NOW", "2031-01-01T09:00:00+01:00")
    monkeypatch.setenv("TRIPWRIGHT_NOW_SET_AT", str(time.time()))
    [later] = [value for value in saved(service, "oslo-art") if value.get("found") == "lookup"]
    assert later["expired"] is True
