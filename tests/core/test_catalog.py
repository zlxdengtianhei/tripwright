"""The catalog in `turn`'s `recall` (route F of the 2026-11-24 comparison): one short row for every entry the repository
holds, so the host picks by meaning what the lexical top six missed, then fetches those by id.

Before: `recall` listed only the six entries whose words best matched the message. A rule the message shares no word
with (the Sparschiene ticket's "only the booked train" for "can I take the earlier train") was never shown, and the
host could not look it up because it did not know it existed (the 30-sentence development set: 56 of 62 gold entries
found with the catalog, against about half with the lexical six). After: `recall.catalog.lines` lists every entry as
`id|kind|text`, `query(kind="saved", arguments={ids})` returns the chosen ones whole with their source, and what the
budget cannot list is counted in `omitted` and opened with `arguments={list}`."""
import asyncio
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tripwright import catalog

AT = datetime(2030, 6, 10, 9, 0, tzinfo=ZoneInfo("Europe/Oslo"))
ZONE = "Europe/Oslo"
POOL_AT = datetime(2030, 6, 10, 7, 0, tzinfo=ZoneInfo("UTC"))  # the moment `recall_pool` and the probes below read the corpus at


def entry(kind, ident, text, day=None, in_force=True, valid_until=None):
    return dict(found=kind, id=ident, text=text, in_force=in_force, day=day, valid_until=valid_until)


def build(entries, scores=None, language="zh"):
    return catalog.build(entries, at=AT, zone=ZONE, language=language, scores=scores)


# ---- the row ------------------------------------------------------------------------------------------------------

def test_a_row_is_id_kind_text_with_a_short_text_whole_and_a_long_one_cut():
    short = "渡轮单程 4 欧，只收现金" + "。" * 60  # 71 characters: whole
    exact = "x" * 90
    long = "ÖBB Standard 票不绑车次：" + "在所选路线上的所有日间列车都有效，" * 6
    lines = build([entry("web", "EV-1", short), entry("web", "EV-2", exact), entry("web", "EV-3", long)])["lines"]
    assert lines[0] == f"EV-1|web|{short}"
    assert lines[1] == f"EV-2|web|{exact}"  # 90 characters is still whole
    cut = lines[2].split("|", 2)[2]
    assert lines[2].startswith("EV-3|web|") and cut == long[:56] + "…" and len(long) > 90


def test_whitespace_in_a_text_is_one_space_so_a_row_is_one_line():
    [line] = build([entry("fact", "F-1", "第一行\n\n第二行\t第三行")])["lines"]
    assert line == "F-1|fact|第一行 第二行 第三行"


def test_rows_of_one_kind_that_begin_alike_are_one_row_marked_with_how_many_more():
    same = [entry("web", f"EV-{n}", "ÖBB 多功能储物柜地点包括 Wien Hbf " + suffix) for n, suffix in enumerate(["一", "二", "三"])]
    other_kind = entry("fact", "F-1", "ÖBB 多功能储物柜地点包括 Wien Hbf 一")
    lines = build([*same, other_kind])["lines"]
    web = [line for line in lines if "|web|" in line]
    assert len(web) == 1 and web[0].startswith("EV-0|web|") and web[0].endswith(" +2")
    assert any(line.startswith("F-1|fact|") and "+" not in line.split("|")[2][-4:] for line in lines)  # not merged across kinds


def test_rows_are_listed_by_kind_in_a_fixed_order():
    lines = build([entry("said", "E-1", "a"), entry("web", "EV-1", "b"), entry("place", "p", "c"), entry("fact", "F-1", "d")])["lines"]
    assert [line.split("|")[1] for line in lines] == ["place", "fact", "web", "said"]


# ---- no longer in force ------------------------------------------------------------------------------------------

def test_an_entry_that_ended_in_the_last_48_hours_is_listed_marked_and_older_ones_are_only_counted():
    fresh = entry("web", "EV-new", "票价 €4", day="2030-06-08", in_force=False, valid_until="2030-06-08T12:00:00+00:00")  # 45 h
    old = entry("web", "EV-old", "旧的开放时间", day="2030-06-01", in_force=False, valid_until="2030-06-02T12:00:00+00:00")
    superseded = entry("fact", "F-old", "不再作数的偏好", day="2030-06-01", in_force=False)  # no end time to tell
    live = entry("web", "EV-live", "有效的", day="2030-06-09")
    result = build([fresh, old, superseded, live])
    rows = {line.split("|")[0]: line for line in result["lines"]}
    assert rows["EV-new"].split("|", 2)[2].startswith("过期，要重查 票价")
    assert "EV-old" not in rows and "F-old" not in rows and rows["EV-live"].endswith("有效的")
    assert {group["list"]: group["n"] for group in result["omitted"]} == {"fact 06-01": 1, "web 06-01": 1}


def test_the_mark_is_in_the_language_of_the_trip():
    fresh = entry("web", "EV-new", "fare", in_force=False, valid_until="2030-06-09T12:00:00+00:00")
    assert build([fresh], language="en")["lines"] == ["EV-new|web|expired, look again fare"]


def test_the_48_hour_window_lists_an_entry_that_ended_47_hours_ago_and_only_counts_one_that_ended_49_hours_ago():
    """Judge, 2026-11-24: setting EXPIRED_HOURS to 72 left all 37 catalog tests green, so the edge had no test."""
    def gone(ident, hours, text):
        until = AT.astimezone(ZoneInfo("UTC")) - timedelta(hours=hours)
        return entry("web", ident, text, day="2030-06-07", in_force=False, valid_until=until.isoformat())

    result = build([gone("EV-47", 47, "四十七小时前过期的票价"), gone("EV-49", 49, "四十九小时前过期的开放时间")])
    rows = {line.split("|")[0]: line for line in result["lines"]}
    assert set(rows) == {"EV-47"} and rows["EV-47"].split("|", 2)[2].startswith("过期，要重查 ")
    assert result["omitted"] == [dict(list="web 06-07", n=1)]
    assert catalog.EXPIRED_HOURS == 48


def test_an_expired_row_is_not_merged_with_a_live_one_that_begins_alike():
    live = entry("web", "EV-a", "ÖBB 选座可提前 180 天预订，是可选加项。")
    gone = entry("web", "EV-b", "ÖBB 选座可提前 180 天预订，是可选加项。", in_force=False, valid_until="2030-06-09T12:00:00+00:00")
    lines = build([live, gone])["lines"]
    assert len(lines) == 2 and lines[1].split("|", 2)[2].startswith("过期，要重查 ")


# ---- the budget --------------------------------------------------------------------------------------------------

def repository_of(n):
    """A trip's repository of about n entries: places, plan blocks and bookings over twenty days around the 10th, web
    checks, lookups, the user's facts and messages."""
    entries = [entry("place", f"place-{i}", f"地点{i}：开放时间 09–18") for i in range(12)]
    for i in range(n - 12):
        day = f"2030-06-{1 + i % 20:02d}"
        kind = ("plan", "booking", "web", "lookup", "fact", "item", "said", "check")[i % 8]
        entries.append(entry(kind, f"{kind}-{i:03d}", f"{day} 条目 {i}：{'互不相同的内容 ' * 3}{i * 7919 % 1000}", day=day))
    return entries


def test_a_600_entry_repository_fits_the_budget_and_counts_the_rest_per_kind_and_day():
    entries = repository_of(600)
    result = build(entries)
    assert len(result["lines"]) + len(result["omitted"]) <= catalog.MAX_LINES
    assert catalog.size(result["lines"], result["omitted"]) <= catalog.MAX_CHARS
    assert result["omitted"] and all(set(group) == {"list", "n"} and group["n"] > 0 for group in result["omitted"])
    listed = {line.split("|")[0] for line in result["lines"]}
    assert len(entries) - len(listed) == sum(group["n"] for group in result["omitted"])  # nothing is lost: listed or counted
    counts = classes_listed(result["lines"])  # D-07: no class is squeezed out by another
    assert all(counts.get(name, 0) >= 10 for name in ("place", "said", "check", "web", "lookup")), counts


def test_over_the_budget_the_places_and_the_plan_and_bookings_of_the_two_days_around_today_are_listed_first():
    entries = repository_of(600)
    result = build(entries)
    listed = {line.split("|")[0] for line in result["lines"]}
    assert {f"place-{i}" for i in range(12)} <= listed
    near = {e["id"] for e in entries if e["found"] in ("plan", "booking") and e["day"] in {f"2030-06-{d:02d}" for d in range(8, 13)}}
    assert near and near <= listed
    far = [e["id"] for e in entries if e["found"] in ("plan", "booking") and e["day"] not in {f"2030-06-{d:02d}" for d in range(8, 13)}]
    assert [i for i in far if i not in listed]  # the budget did leave out plan entries of other days


def classes_listed(lines):
    out = {}
    for line in lines:
        kind = line.split("|")[1]
        name = "said" if kind in catalog.SAID_KINDS else kind
        out[name] = out.get(name, 0) + 1
    return out


def test_the_room_left_after_the_near_plan_is_shared_equally_by_the_classes_best_match_first():
    near = [entry("plan", f"2030-06-10/b{i:02d}", f"2030-06-10 {i:02d}:00 计划块", day="2030-06-10") for i in range(20)]
    others = [entry(kind, f"{kind}-{i:03d}", f"{kind} 条目 {i:03d} 各不相同", day=f"2030-06-{1 + i % 9:02d}")
              for kind in ("place", "item", "check", "web", "lookup") for i in range(100)]
    scores = {("web", "web-050"): 5.0, ("web", "web-051"): 4.0}
    result = build([*near, *others], scores=scores)
    listed = {line.split("|")[0] for line in result["lines"]}
    assert {e["id"] for e in near} <= listed
    counts = classes_listed(result["lines"])
    floor = (catalog.MAX_LINES - 10 - 20) // 5 - 1
    assert counts.pop("plan") == 20 and set(counts) == {"place", "said", "check", "web", "lookup"}
    assert all(n >= floor for n in counts.values()), counts  # every class got its floor, none the whole room
    assert max(counts.values()) - min(counts.values()) <= 1
    assert {"web-050", "web-051"} <= listed  # inside a class the best match comes first
    web_days = [e["day"] for e in others if e["id"] in listed and e["found"] == "web" and e["id"] not in ("web-050", "web-051")]
    assert min(web_days) >= "2030-06-04"  # among equal scores the newest


def test_a_class_with_few_entries_gives_its_unused_share_to_the_rest():
    near = [entry("plan", f"2030-06-10/b{i:02d}", f"2030-06-10 {i:02d}:00 计划块", day="2030-06-10") for i in range(20)]
    few = [entry("place", f"place-{i}", f"地点 {i}") for i in range(3)]
    many = [entry("web", f"web-{i:03d}", f"检查 {i:03d} 各不相同", day="2030-06-01") for i in range(300)]
    counts = classes_listed(build([*near, *few, *many])["lines"])
    assert counts["place"] == 3 and counts["web"] > (catalog.MAX_LINES - 10 - 20) // 2  # more than an equal share


def test_text_is_cut_shorter_before_rows_are_dropped():
    entries = [entry("web", f"EV-{i:03d}", f"{i:03d} " + "很长的内容" * 18, day="2030-06-01") for i in range(140)]
    result = build(entries)
    assert len(result["lines"]) == 140 and result["omitted"] == []  # all rows fit once the text is cut to 40
    assert all(line.split("|", 2)[2].endswith("…") and len(line.split("|", 2)[2]) == 41 for line in result["lines"])
    assert catalog.size(result["lines"], []) <= catalog.MAX_CHARS


def test_shortening_stops_when_the_lines_not_the_characters_are_what_limits_the_listing():
    entries = [entry("web", f"EV-{i:03d}", f"{i:03d} " + "很长的内容" * 18, day="2030-06-01") for i in range(300)]
    result = build(entries)
    assert len(result["lines"]) + len(result["omitted"]) <= catalog.MAX_LINES and result["omitted"]
    texts = [line.split("|", 2)[2] for line in result["lines"]]
    assert all(len(text) == 41 for text in texts)  # 40 characters and the ellipsis, not 32 or 24: shorter would add no row


def test_over_the_character_budget_the_plan_near_today_is_still_listed_whole_before_the_classes():
    wide = "，内容很长的一句话" * 7
    near = [entry("plan", f"2030-06-10/b{i:03d}", f"2030-06-10 {i:03d} {wide}", day="2030-06-10") for i in range(120)]
    others = [entry("item", f"I-{i:03d}", f"偏好 {i}：{wide}", day="2030-06-05") for i in range(60)] + \
             [entry("web", f"EV-{i:03d}", f"检查 {i}：{wide}", day="2030-06-05") for i in range(60)]
    result = build([*others, *near])
    listed = {line.split("|")[0] for line in result["lines"]}
    assert {e["id"] for e in near} <= listed  # near plan: all of it, even when it nearly fills the room
    assert catalog.size(result["lines"], result["omitted"]) <= catalog.MAX_CHARS


def test_omitted_is_counted_per_kind_when_there_are_more_groups_than_lines():
    entries = [entry("web", f"EV-{i}", f"检查 {i} 不同", day=f"2030-{1 + i % 12:02d}-{1 + i % 28:02d}") for i in range(300)]
    result = build(entries)
    assert 0 < len(result["omitted"]) <= catalog.OMIT_GROUPS
    assert all(len(group["list"].split()) == 1 for group in result["omitted"])  # only the kind: `web`
    assert len(result["lines"]) + len(result["omitted"]) <= catalog.MAX_LINES


def test_the_same_repository_gives_the_same_catalog_twice():
    entries = repository_of(600)
    scores = {(e["found"], e["id"]): float(i % 7) for i, e in enumerate(entries)}
    assert build(entries, scores) == build(list(entries), dict(scores))
    assert build(list(reversed(entries)), scores)["omitted"] == build(entries, scores)["omitted"]


def test_a_small_repository_lists_everything_and_omits_nothing():
    result = build(repository_of(40))
    assert len(result["lines"]) == 40 and result["omitted"] == []


# ---- expanding a group and a merged row ------------------------------------------------------------------------

def test_expand_gives_the_entries_of_a_kind_and_day_newest_first_and_a_merged_rows_members_by_its_id():
    entries = [entry("web", "EV-1", "甲" * 30, day="2030-06-01"), entry("web", "EV-2", "乙", day="2030-06-02"),
               entry("web", "EV-3", "甲" * 30 + "后面不同", day="2030-06-03"), entry("fact", "F-1", "丙", day="2030-06-02")]
    assert [e["id"] for e in catalog.expand(entries, "web", AT)] == ["EV-3", "EV-2", "EV-1"]
    assert [e["id"] for e in catalog.expand(entries, "web 06-02", AT)] == ["EV-2"]
    assert [e["id"] for e in catalog.expand(entries, "EV-1", AT)] == ["EV-3", "EV-1"]  # the row `EV-1 … +1`
    assert catalog.expand(entries, "nothing 01-01", AT) == []


# ---- through the service ---------------------------------------------------------------------------------------

def seeded(service, plan, monkeypatch, when="2030-06-10T09:00:00+02:00"):
    monkeypatch.setenv("TRIPWRIGHT_NOW", when)
    monkeypatch.setenv("TRIPWRIGHT_NOW_SET_AT", str(time.time()))
    turn = service.turn("alice", "创建示例行程。", "seed")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=plan, idempotency_key="seed")
    service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)
    return service


def web(service, **arguments):
    return asyncio.run(service.query("alice", "web_evidence", arguments))


def saved(service, **arguments):
    return asyncio.run(service.query("alice", "saved", arguments))


def test_the_turn_lists_the_catalog_even_when_no_entry_matches_the_words(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    web(service, url="https://example.org/ticket", quote="valid only on the booked train", claim="Sparschiene 票只对订票选定的那一班车有效", type="rule")
    turn = service.turn("alice", "那我能不能坐早一点的车？", "q1", scenario="quick")
    recall = turn["recall"]
    assert recall["found"] == []  # nothing shares a word with the message
    rows = recall["catalog"]["lines"]
    assert any("|web|Sparschiene 票只对订票选定的那一班车有效" in row for row in rows)
    assert any(row.startswith("oslo-art|place|") for row in rows)
    assert "recall.catalog" in recall["use"] and "ids:[" in recall["use"]  # the instruction travels inside `recall`
    assert "recall.use" in turn["after_first_part"]  # and the answer's after_first_part points to it


def test_the_catalog_leaves_out_what_the_message_itself_recorded(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    service.turn("alice", "我下午想去画廊旁边的咖啡馆。", "earlier", nature="user_verbatim", native_ref="fixture:earlier")
    turn = service.turn("alice", "今晚想吃海鲜，别太贵。", "q2", nature="user_verbatim", native_ref="fixture:q2")
    rows = turn["recall"]["catalog"]["lines"]
    assert any("|said|我下午想去画廊旁边的咖啡馆" in row for row in rows)  # an earlier message is in the catalog
    assert not any("今晚想吃海鲜" in row for row in rows)  # this one is not (same rule as `found`)
    assert not any("今晚想吃海鲜" in entry["text"] for entry in turn["recall"]["found"])


def test_the_turns_catalog_is_the_same_for_the_same_repository_and_message(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    web(service, url="https://example.org/cafe", quote="Kitchen closes 21:00", claim="咖啡馆厨房 21:00 停止点餐", type="opening_hours")
    first = service.turn("alice", "咖啡馆几点关门？", "q3", scenario="quick")["recall"]["catalog"]
    again = service.turn("alice", "咖啡馆几点关门？", "q3b", scenario="quick")["recall"]["catalog"]
    own = [row for row in again["lines"] if "咖啡馆几点关门" in row]  # the first call's message is now an earlier message
    assert [row for row in again["lines"] if row not in own] == first["lines"]


def test_a_repository_with_nothing_in_it_has_no_catalog_key(service, plan, monkeypatch):
    monkeypatch.setenv("TRIPWRIGHT_NOW", "2030-06-10T09:00:00+02:00")
    monkeypatch.setenv("TRIPWRIGHT_NOW_SET_AT", str(time.time()))
    turn = service.turn("alice", "你好", "hello")
    assert "recall" not in turn or "catalog" not in turn["recall"]


def test_ids_return_the_entries_whole_with_their_source_in_the_shape_of_a_hit(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    long = "渡轮单程 4 欧，往返 8 欧，只收现金。" + "票上不写车次，上船时出示即可。" * 20
    first = web(service, url="https://example.org/ferry", quote="Single 4 EUR", claim=long, type="price")["evidence"]["id"]
    result = saved(service, ids=[first, "oslo-art", "no-such-id"])
    values = result["evidence"]["value"]
    assert [value["id"] for value in values] == [first, "oslo-art"]  # in the order asked
    ferry = values[0]
    assert ferry["found"] == "web" and ferry["claim"] == " ".join(long.split()) and len(ferry["claim"]) > 140
    assert ferry["source"] and ferry["url"] == "https://example.org/ferry" and ferry["in_force"] is True
    assert {"path", "line", "commit", "snippet", "snippet_line", "snippet_exact"} <= set(ferry)  # same shape as a hit of `text`
    assert values[1]["source"] and values[1]["name"] and values[1]["id"] == "oslo-art"  # a place is the stored place, as in a hit
    assert result["unknown"] == ["no-such-id"]


def test_ids_are_not_ranked_and_at_most_sixteen_are_taken(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    made = [web(service, url=f"https://example.org/p{n}", quote=f"q{n}", claim=f"第 {n} 条检查的内容", type="price")["evidence"]["id"]
            for n in range(18)]
    result = saved(service, ids=list(reversed(made)))
    assert [value["id"] for value in result["evidence"]["value"]] == list(reversed(made))[:16]
    assert result["skipped"] == list(reversed(made))[16:] and result["unknown"] == []


def test_ids_must_be_a_list_of_strings(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    with pytest.raises(ValueError, match="arguments.ids"):
        saved(service, ids="oslo-art")


def test_list_opens_a_group_and_the_text_use_is_unchanged(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    for n in range(5):
        web(service, url=f"https://example.org/p{n}", quote=f"q{n}", claim=f"第 {n} 条检查：一些很长的内容" + "。" * 100, type="price")
    opened = saved(service, list="web")
    rows = opened["evidence"]["value"]
    assert len(rows) == 5 and opened["list"] == "web" and opened["n"] == 5
    assert all(set(row) == {"id", "found", "day", "in_force", "text"} and row["found"] == "web" for row in rows)
    assert all(row["text"].endswith("…") and len(row["text"]) == 57 for row in rows)  # the catalog's own cut
    assert saved(service, list="web 01-01")["evidence"]["value"] == []
    assert saved(service, text="第 3 条检查")["evidence"]["value"][0]["found"] == "web"
    with pytest.raises(ValueError, match="arguments.list"):
        saved(service, list="")


def test_a_big_repository_counts_what_it_cannot_list_and_list_reaches_it(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    for n in range(260):
        service.store.put("evidence", "alice", dict(id=f"EV-big-{n:03d}", kind="web", source="example.org", url=f"https://example.org/{n}",
                                                    claim=f"第 {n:03d} 号检查的内容，各不相同 {n * 31 % 97}", quote="q", type="price",
                                                    fetched_at="2030-06-0%dT10:00:00+00:00" % (1 + n % 9),
                                                    valid_until="2031-01-01T00:00:00+00:00", counts_as_checked=True), insert=True)
    turn = service.turn("alice", "票的规则是什么？", "qbig", scenario="quick")
    listing = turn["recall"]["catalog"]
    assert len(listing["lines"]) + len(listing["omitted"]) <= catalog.MAX_LINES and listing["omitted"]
    group = listing["omitted"][0]
    opened = saved(service, list=group["list"])
    assert opened["n"] >= group["n"] > 0 and opened["evidence"]["value"]
    listed = {row.split("|")[0] for row in listing["lines"]}
    assert len(listed) + sum(g["n"] for g in listing["omitted"]) >= 260 + 1  # the places and the rest are counted too


# ---- judge's three repairs (2026-11-24) ---------------------------------------------------------------------------

def test_an_expired_ledger_record_of_the_last_48_hours_is_listed_marked_and_a_superseded_one_is_only_counted(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)  # now: 2030-06-10 09:00 +02:00
    base = dict(subject=["p1"], scope=dict(), source=dict(event="seed", basis="user_explicit"), kind="preference", strength="prefer",
                true_from="2030-06-01T00:00:00+00:00")
    service.store.put("facts", "alice", dict(id="F-EXP-recent", key="k1", value=1, text="两小时前过期的事实", status="expired",
                                             valid_until="2030-06-10T05:00:00+00:00", **base), insert=True)
    service.store.put("facts", "alice", dict(id="F-EXP-old", key="k2", value=1, text="一周前过期的事实", status="expired",
                                             valid_until="2030-06-03T05:00:00+00:00", **base), insert=True)
    service.store.put("facts", "alice", dict(id="F-SUP", key="k3", value=1, text="被取代的事实", status="superseded",
                                             valid_until="2031-01-01T00:00:00+00:00", **base), insert=True)
    rows = {row.split("|")[0]: row for row in service.turn("alice", "你好呀", "qx")["recall"]["catalog"]["lines"]}
    assert rows["F-EXP-recent"].split("|", 2)[2] == "过期，要重查 两小时前过期的事实"
    assert "F-EXP-old" not in rows and "F-SUP" not in rows  # an older one and a superseded one (no time of its own) are counted
    omitted = {group["list"].split()[0] for group in service.turn("alice", "你好呀", "qy")["recall"]["catalog"]["omitted"]}
    assert "fact" in omitted


def test_every_omitted_group_can_be_paged_out_whole_through_list(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    for n in range(420):
        service.store.put("evidence", "alice", dict(id=f"EV-pg-{n:03d}", kind="web", source="example.org", url=f"https://example.org/{n}",
                                                    claim=f"第 {n:03d} 号检查的内容，各不相同 {n * 31 % 97}", quote="q", type="price",
                                                    fetched_at="2030-06-0%dT10:00:00+00:00" % (1 + n % 9), valid_until="2031-01-01T00:00:00+00:00",
                                                    counts_as_checked=True), insert=True)
    for n in range(180):
        service.store.put("facts", "alice", dict(id=f"F-pg-{n:03d}", key=f"k{n}", value=n, text=f"第 {n:03d} 条事实 各不相同 {n * 17 % 89}", status="active",
                                                 subject=["p1"], scope=dict(), source=dict(event="seed", basis="user_explicit"), kind="preference",
                                                 true_from="2030-06-01T00:00:00+00:00"), insert=True)
    turn = service.turn("alice", "票的规则是什么？", "qpage", scenario="quick")
    listing = turn["recall"]["catalog"]
    assert listing["omitted"] and any(group["n"] > catalog.EXPAND_LIMIT for group in listing["omitted"])  # a group over one page
    pool = recall_pool(service)
    listed = {row.split("|")[0] for row in listing["lines"]}
    paged = set()
    for group in listing["omitted"]:
        got, offset = [], 0
        while offset is not None:
            page = saved(service, list=group["list"], offset=offset)
            got += [row["id"] for row in page["evidence"]["value"]]
            assert page["offset"] == offset
            offset = page.get("next_offset")
            assert (offset is None) == ("more" not in page)
        assert len(got) == len(set(got)) == page["n"]  # no row twice, none short
        assert {entry["id"] for entry in catalog.expand(pool, group["list"], POOL_AT)} == set(got)
        paged |= set(got)
    unlisted = {entry["id"] for entry in pool} - listed
    assert unlisted <= paged  # every entry the catalog did not list is reachable through some group's pages


def test_list_offset_must_be_a_whole_number(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    for bad in (-1, "3", 1.5, True):
        with pytest.raises(ValueError, match="arguments.offset"):
            saved(service, list="web", offset=bad)


def recall_pool(service):
    from tripwright import recall
    plan = service.versions.current("alice")["plan"]
    return recall.search(service.store, "alice", plan, "", at=recall.aware_utc("2030-06-10T07:00:00+00:00"), zone="Europe/Oslo",
                         language="zh", pool=True)["pool"]["entries"]


def test_the_recall_instruction_is_in_the_receipt_when_a_dispatch_rewrites_after_first_part(service, plan, monkeypatch):
    """Hold-out 2026-11-24: with a dispatch and an opening, `after_first_part` is written again and the recall text that
    was put into it was lost, so the host got a catalog and no word on how to use it."""
    seeded(service, plan, monkeypatch)
    web(service, url="https://example.org/ticket", quote="valid only on the booked train", claim="Sparschiene 票只对订票选定的那一班车有效", type="rule")
    turn = service.turn("alice", "今晚最晚几点出发回住处？", "qd", scenario="replan")
    assert turn["flow"]["dispatch"] and turn["after_first_part"].startswith("第一条发出去以后照这里做：")
    assert "第一条发出后先派" in turn["after_first_part"]  # the rewritten branch ran
    assert turn["recall"]["catalog"]["lines"] and "recall.catalog" in turn["recall"]["use"] and "ids:[" in turn["recall"]["use"]
    assert "recall.use" in turn["after_first_part"] and "query(kind='conflict')" in turn["after_first_part"]  # both uses survive
    from tripwright import service as module
    assert all("via_row" in module.RECALL_USE[language] and "recall.catalog" in module.RECALL_USE[language] for language in ("zh", "en"))
    assert "+n" in module.RECALL_USE["zh"] and "list" in module.RECALL_USE["en"] and "offset" in module.RECALL_USE["en"]


def test_the_id_of_a_plus_n_row_brings_the_merged_entries_marked_with_the_row(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    same = "ÖBB 多功能储物柜地点包括 Wien Hbf"
    ids = [web(service, url=f"https://example.org/l{n}", quote=f"q{n}", claim=f"{same} 第{n}页补充", type="place")["evidence"]["id"] for n in range(3)]
    other = web(service, url="https://example.org/o", quote="o", claim="完全不同开头的一条", type="place")["evidence"]["id"]
    rows = service.turn("alice", "你好呀", "qm")["recall"]["catalog"]["lines"]
    [row] = [line for line in rows if same in line]
    assert row.endswith(" +2")
    head = row.split("|")[0]
    result = saved(service, ids=[head, other])
    values = result["evidence"]["value"]
    assert [value["id"] for value in values][:1] == [head] and set(value["id"] for value in values) == set(ids) | {other}
    assert {value["id"]: value.get("via_row") for value in values} == {head: None, other: None, **{i: head for i in ids if i != head}}
    assert "skipped" not in result and result["unknown"] == []
    alone = saved(service, ids=[other])["evidence"]["value"]
    assert [value["id"] for value in alone] == [other]  # a row that is not merged brings nothing with it
    member = [i for i in ids if i != head][0]
    assert [value["id"] for value in saved(service, ids=[member])["evidence"]["value"]] == [member]  # a member asked for itself: itself


def put_web(service, ident, claim, fetched, until):
    service.store.put("evidence", "alice", dict(id=ident, kind="web", source="example.org", url=f"https://example.org/{ident}",
                                                claim=claim, quote="q", type="price", fetched_at=fetched, valid_until=until,
                                                counts_as_checked=True), insert=True)


SAME_START = "同一个开头的检查结果 一样的开头 一样的开头 一样的开头 一样的开头 一样的开头"


def test_the_id_of_an_expired_plus_n_row_brings_its_expired_members_and_not_an_older_one_counted_apart(service, plan, monkeypatch):
    """Judge, 2026-11-24, counterexample A: an entry that ended more than 48 hours ago is only counted by the catalog, but
    `ids` grouped it with the live-looking ones, so the row `EV-r1 … +2` came back without EV-r2 and EV-r3."""
    seeded(service, plan, monkeypatch)  # now 2030-06-10 07:00 UTC
    put_web(service, "EV-old", SAME_START + " 旧", "2030-05-01T10:00:00+00:00", "2030-05-02T10:00:00+00:00")  # ended long ago
    put_web(service, "EV-r1", SAME_START + " 一", "2030-06-09T10:00:00+00:00", "2030-06-10T01:00:00+00:00")
    put_web(service, "EV-r2", SAME_START + " 二", "2030-06-09T11:00:00+00:00", "2030-06-10T02:00:00+00:00")
    put_web(service, "EV-r3", SAME_START + " 三", "2030-06-09T12:00:00+00:00", "2030-06-10T03:00:00+00:00")
    [row] = [line for line in service.turn("alice", "你好呀", "qm")["recall"]["catalog"]["lines"] if SAME_START in line]
    assert row.split("|", 2)[2].startswith("过期，要重查 ") and row.endswith(" +2")
    got = [(value["id"], value.get("via_row")) for value in saved(service, ids=[row.split("|")[0]])["evidence"]["value"]]
    assert {i for i, _ in got} == {"EV-r1", "EV-r2", "EV-r3"} and all(via in (None, "EV-r1") for _, via in got)
    assert [value["id"] for value in saved(service, ids=["EV-old"])["evidence"]["value"]] == ["EV-old"]  # counted, heads no row


def test_the_id_of_an_expired_plus_n_row_does_not_bring_an_entry_the_row_did_not_merge(service, plan, monkeypatch):
    """Judge, 2026-11-24, counterexample B: with the old entry stored after r1 and r2, `ids` brought EV-old into the row."""
    seeded(service, plan, monkeypatch)
    put_web(service, "EV-r1", SAME_START + " 一", "2030-06-09T10:00:00+00:00", "2030-06-10T01:00:00+00:00")
    put_web(service, "EV-r2", SAME_START + " 二", "2030-06-09T11:00:00+00:00", "2030-06-10T02:00:00+00:00")
    put_web(service, "EV-old", SAME_START + " 旧", "2030-05-01T10:00:00+00:00", "2030-05-02T10:00:00+00:00")
    [row] = [line for line in service.turn("alice", "你好呀", "qm")["recall"]["catalog"]["lines"] if SAME_START in line]
    assert row.endswith(" +1")
    head = row.split("|")[0]
    assert {value["id"] for value in saved(service, ids=[head])["evidence"]["value"]} == {"EV-r1", "EV-r2"}
    assert {entry["id"] for entry in catalog.expand(recall_pool(service), head, POOL_AT)} == {"EV-r1", "EV-r2"}  # `list` agrees


def test_the_row_a_member_is_merged_into_is_one_function_for_the_catalog_and_for_ids():
    live = entry("web", "EV-a", "甲" * 30, in_force=True)
    fresh = entry("web", "EV-b", "甲" * 30, in_force=False, valid_until="2030-06-09T12:00:00+00:00")
    old = entry("web", "EV-c", "甲" * 30, in_force=False, valid_until="2030-06-01T12:00:00+00:00")
    assert catalog.row_key(live, AT) != catalog.row_key(fresh, AT) and catalog.row_key(old, AT) is None
    assert catalog.row_key(fresh, AT) == catalog.row_key(dict(fresh, id="EV-d", text="甲" * 24 + "不同"), AT)
    from tripwright import recall
    assert [e["id"] for e in recall.ordered([fresh, old, live], ["EV-b"], at=AT)] == ["EV-b"]  # old is no member of the fresh row
    assert [e["id"] for e in recall.ordered([old, live, dict(live, id="EV-e")], ["EV-a"], at=AT)] == ["EV-a", "EV-e"]


def test_the_words_to_the_host_name_ids_for_a_plus_n_row_and_list_only_for_the_groups_of_omitted(service, plan, monkeypatch):
    """Judge, 2026-11-24: the `list` error still called the id of a `+n` row a `list` value, and the zh text did not say `ids`."""
    seeded(service, plan, monkeypatch)
    with pytest.raises(ValueError, match="catalog.omitted") as error:
        saved(service, list="  ")
    assert "+n" not in str(error.value) and "id of a row" not in str(error.value)
    from tripwright import service as module
    zh = module.RECALL_USE["zh"]
    assert "点行末带 +n 的行（用 ids 取它的 id）" in zh and "list: 那一组的 list 值" in zh
    assert "+n" not in zh.split("list: 那一组的 list 值")[0].rsplit("catalog.omitted", 1)[1]


def test_the_merged_entries_count_toward_the_limit_and_the_overflow_is_skipped(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    same = "同一个开头的二十个条目 一样的开头 一样的开头 一样的开头"
    for n in range(20):
        web(service, url=f"https://example.org/m{n}", quote=f"q{n}", claim=f"{same} 第{n:02d}页", type="place")
    rows = service.turn("alice", "你好呀", "qn")["recall"]["catalog"]["lines"]
    [row] = [line for line in rows if same in line]
    assert row.endswith(" +19")
    result = saved(service, ids=[row.split("|")[0]])
    assert len(result["evidence"]["value"]) == catalog_limit() and len(result["skipped"]) == 20 - catalog_limit()


def catalog_limit():
    from tripwright import query
    return query.SAVED_IDS
