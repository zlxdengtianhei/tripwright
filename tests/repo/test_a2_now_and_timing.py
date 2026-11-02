"""A2: a live read says what time it is now; a hard node says whether its time is an external deadline or the plan's
target, which plan version has stored it since when, which source last checked it, and whether that is too old; a
detached checkout and an unopenable repository still let the host connect."""
from __future__ import annotations

import asyncio
import sys
from datetime import date

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from packs_sample import FOLDER, OWNER, S1_AT, load_store, snapshot
from repo_fixtures import Writer, bare, clone, git, new_repository, seeded

from tripwright import packs
from tripwright.render.labels import words
from tripwright.render.pack import render_pack, timing_parts
from tripwright.repo.git import Git
from tripwright.storage import Store, now

V3_AT = "2030-07-14T20:20:00+02:00"  # V3 of the contract sample enters the index then (ledger.json versions)


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "a2.sqlite3")
    load_store(store, S1_AT)
    yield store
    store.close()


def narrow(store, *, at=S1_AT, live=False, language="zh"):
    return packs.build(store, OWNER, kind="narrow", scenario="replan", pack_id="CTX-300715-n4k", snapshot=snapshot("S1"), at=at,
                       subject=["u1"], scope={"start": "2030-07-15", "time_from": "13:00", "time_to": "21:00", "place": "seenland"},
                       trip_folder=FOLDER, language=language, live=live)


def set_anchor(store, **fields):
    """Change hn-dinner in the stored V3 plan, as a planner's commit would."""
    with store.transaction():
        version = store.all("versions", OWNER)[0]
        for day in version["plan"]["days"]:
            for anchor in day.get("anchors", []):
                if anchor["id"] == "hn-dinner":
                    anchor.update(fields)
        store.put("versions", OWNER, version)


def evidence(store, evidence_id, fetched_at, **fields):
    record = dict(dict(id=evidence_id, source="Example Rail timetable", fetched_at=fetched_at, valid_until="2030-07-16T00:00:00+02:00",
                       stale=False, synthetic=False, date="2030-07-15", place_id="lindengrund", facts=["hard_node"]), **fields)
    with store.transaction():
        store.put("evidence", OWNER, record, insert=True)


def hard_line(markdown: str) -> str:
    return next(line for line in markdown.splitlines() if line.startswith("硬节点：[hn-dinner]"))


# --- now -------------------------------------------------------------------------

def test_a_live_pack_starts_with_now_in_the_trip_zone(store):
    pack = narrow(store, live=True)
    assert date(2030, 7, 15).isoweekday() == 1
    assert pack["header"]["now"] == dict(at="2030-07-15T13:00:13+02:00", date="2030-07-15", time="13:00", weekday=1,
                                         timezone="Europe/Vienna")
    title = render_pack(pack)["markdown"].splitlines()[0]
    assert title.startswith("# 现在 13:00（Europe/Vienna，07-15 周一）｜约束包 CTX-300715-n4k｜重排第一段｜u1｜")
    english = render_pack(narrow(store, live=True, language="en"))["markdown"].splitlines()[0]
    assert english.startswith("# Now 13:00 (Europe/Vienna, Mon 07-15) | Pack CTX-300715-n4k | ")


def test_the_moment_is_converted_to_the_trip_zone_across_midnight(store):
    pack = narrow(store, at="2030-07-14T22:25:00+00:00", live=True)
    assert {key: pack["header"]["now"][key] for key in ("date", "time", "weekday")} == dict(date="2030-07-15", time="00:25", weekday=1)


def test_a_pack_written_into_the_repository_has_no_now(store):
    """A day pack is read later; its moment is the commit's, shown as "编于", never as now."""
    day = packs.day_packs(store, OWNER, snapshot=snapshot("S1"), at=S1_AT, trip_folder=FOLDER)["2030-07-15"]
    assert "now" not in day["header"]
    assert render_pack(day)["markdown"].startswith("# 约束包 ")
    assert "now" not in narrow(store)["header"]


# --- where a hard node's time comes from -----------------------------------------

def test_a_hard_node_says_since_when_the_plan_stores_its_time_and_that_nobody_checked_it(store):
    record = narrow(store)["records"]["hn-dinner"]
    assert {key: record[key] for key in ("deadline", "stored", "checked", "refresh_hours", "may_have_changed")} == dict(
        deadline="unmarked", stored={"version": 3, "at": V3_AT}, checked=None, refresh_hours=6, may_have_changed=True)
    assert hard_line(render_pack(narrow(store))["markdown"]).endswith(
        "｜没标是外部截止还是计划目标｜计划留 15 分钟余量｜没核过：计划 V3 存的（07-14 20:20 起），超过 6 小时，可能已变")


def test_a_stored_time_younger_than_six_hours_is_not_flagged(store):
    record = narrow(store, at="2030-07-15T02:19:00+02:00")["records"]["hn-dinner"]
    assert record["checked"] is None and record["may_have_changed"] is False


def test_a_checked_time_names_its_source_and_may_have_changed_after_six_hours(store):
    evidence(store, "EV-300715-aaa", "2030-07-15T08:00:00+02:00")
    evidence(store, "EV-300715-old", "2030-07-15T07:00:00+02:00")
    evidence(store, "EV-300715-syn", "2030-07-15T12:59:00+02:00", synthetic=True)  # demo data never counts as a check
    set_anchor(store, evidence_ids=["EV-300715-old", "EV-300715-aaa", "EV-300715-syn"])
    record = narrow(store)["records"]["hn-dinner"]
    assert record["deadline"] == "external"  # a source checked its time
    assert record["checked"] == dict(source="Example Rail timetable", at="2030-07-15T08:00:00+02:00", evidence_id="EV-300715-aaa",
                                     valid_until="2030-07-16T00:00:00+02:00", stale=False)
    assert record["may_have_changed"] is False
    assert hard_line(render_pack(narrow(store))["markdown"]).endswith("｜外部截止｜计划留 15 分钟余量｜核过：Example Rail timetable，07-15 08:00")
    later = narrow(store, at="2030-07-15T14:01:00+02:00")["records"]["hn-dinner"]
    assert later["may_have_changed"] is True
    assert "核过：Example Rail timetable，07-15 08:00，超过 6 小时，可能已变" in hard_line(render_pack(narrow(store, at="2030-07-15T14:01:00+02:00"))["markdown"])


def test_a_check_the_source_calls_stale_or_expired_may_have_changed_at_once(store):
    evidence(store, "EV-300715-exp", "2030-07-15T12:00:00+02:00", valid_until="2030-07-15T12:30:00+02:00")
    set_anchor(store, evidence_ids=["EV-300715-exp"])
    assert narrow(store)["records"]["hn-dinner"]["may_have_changed"] is True


def test_a_plan_target_is_kept_apart_from_an_external_deadline(store):
    set_anchor(store, deadline="plan")
    record = narrow(store)["records"]["hn-dinner"]
    assert record["deadline"] == "plan"
    assert "｜计划目标（不是外部截止）｜" in hard_line(render_pack(narrow(store))["markdown"])
    set_anchor(store, deadline="external")
    assert "｜外部截止｜" in hard_line(render_pack(narrow(store))["markdown"])


def test_stored_since_is_the_oldest_version_that_kept_the_same_time():
    def version(number, clock, at):
        return dict(number=number, committed_at=at, plan=dict(days=[dict(date="2030-07-15", anchors=[dict(id="hn", time=clock)])]))

    anchor = dict(id="hn", time="19:30")
    history = packs.committed([version(1, "19:00", "t1"), version(2, "19:30", "t2"), version(3, "19:30", "t3")])
    assert packs.stored_since(history, "2030-07-15", anchor) == {"version": 2, "at": "t2"}
    assert packs.stored_since(packs.committed([version(1, "19:30", "t1"), version(2, "19:30", "t2")]), "2030-07-15", anchor) == \
        {"version": 1, "at": "t1"}
    assert packs.stored_since(packs.committed([version(1, "19:00", "t1")]), "2030-07-15", anchor) is None


def test_a_hard_node_without_a_known_storing_says_so():
    record = packs.hard_node("2030-07-15", dict(id="hn", time="19:30", text="x", subject=["u1"]), "Europe/Vienna",
                             dict(deadline="unmarked", stored=None, checked=None, refresh_hours=6, may_have_changed=True))
    assert timing_parts(record, "Europe/Vienna", words("zh"))[-1] == "没核过：存进计划的时刻不明，超过 6 小时，可能已变"


# --- connecting --------------------------------------------------------------------

def test_a_detached_checkout_follows_the_branch_at_its_commit(tmp_path):
    remote = bare(tmp_path / "remote.git")
    writer = Writer(new_repository(tmp_path / "repo", remote=remote))
    seeded(writer)
    writer.close()
    work = clone(remote, tmp_path / "work")
    git(work, "checkout", "-q", "--detach", "HEAD")
    assert Git(work).branch() == "main"
    git(work, "checkout", "-q", "--detach", "HEAD~1")  # no branch here: the remote's default branch
    assert Git(work).branch() == "main"
    git(work, "checkout", "-q", "-b", "trip-notes")
    assert Git(work).branch() == "trip-notes"


async def connect(folder) -> dict:
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "tripwright.cli", "--repo", str(folder), "serve", "--stdio"])
    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            answer = await session.call_tool("state", dict(view="sync"))
            return dict(error=answer.is_error, text=answer.content[0].text if answer.content else "",
                        data=answer.structured_content)


def test_the_server_connects_and_answers_on_a_detached_checkout(tmp_path):
    """Replays r9—r11 checked out a commit: the server crashed before the handshake and the host fell back to files."""
    remote = bare(tmp_path / "remote.git")
    writer = Writer(new_repository(tmp_path / "repo", remote=remote))
    seeded(writer)
    writer.close()
    work = clone(remote, tmp_path / "work")
    git(work, "checkout", "-q", "--detach", "HEAD")
    answer = asyncio.run(connect(work))
    assert not answer["error"] and answer["data"]["canonical"] == "origin/main" and answer["data"]["now"]["text"].startswith("现在 ")


def test_a_repository_that_cannot_be_opened_still_connects_and_every_tool_says_why(tmp_path):
    (tmp_path / "plain").mkdir()
    answer = asyncio.run(connect(tmp_path / "plain"))
    assert answer["error"] and "could not open the trip repository" in answer["text"] and "not a Git repository" in answer["text"]
    assert answer["data"] is None


def test_the_committed_views_show_the_replan_state_of_the_version(tmp_path):
    """`generated()` gives B's views the version's needs_replan and warnings, so the day file says to replan."""
    writer = Writer(new_repository(tmp_path / "repo"))
    try:
        seeded(writer)
        turn = writer.turn("Today I am alone.", "alone", nature="user_verbatim", native_ref="fixture:alone")
        fact = dict(key="companions", value=[], text="Today p1 is alone", subject=["p1"], scope={"start": "2030-06-10", "end": "2030-06-10"},
                    true_from=now(), invalidated_by="Day ends", source=dict(event=turn["receipt"]["event_id"], basis="user_explicit"))
        version = writer.commit(writer.propose(turn, "alone", changes={"facts": [fact]}), "alone")
        writer.service.committer.flush()
        assert version["needs_replan"]
        day = (writer.root / "trips/2030-06-fjord/views/days/2030-06-10.md").read_text(encoding="utf-8")
        assert day.count("需重排：这一版计划和后来记下的情况冲突，先重排再照它走。") == 1
    finally:
        writer.close()
