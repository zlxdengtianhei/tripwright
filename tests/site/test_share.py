"""The page friends read (tripwright.site.share) and its publication (tripwright.site.publish). All people, places,
stays, flights and numbers here are invented; the share repository is a local bare repository and `gh` is a fake."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tripwright.repo import layout
from tripwright.repo.notices import open_notices
from tripwright.repo.git import Git
from tripwright.site import Policy
from tripwright.site.privacy import flight_numbers, phone_numbers
from tripwright.site.publish import publish
from tripwright.site.share import Rules, ShareBlocked, load_trip, scan_tree, share_site
from tripwright.storage import Store

NOW = datetime(2030, 7, 1, 9, 0, tzinfo=timezone.utc)
PRIVATE = ("Fiona Example", "Fiona", "Kasimir Beispiel", "Odile Sample", "Hotel Fictiva Rosengarten", "Rosengarten",
           "Musterweg 17", "Musterweg", "48.201234", "16.351234", "QX 4821", "QX4821", "ZX93KD7", "5550123987")


def block(identifier, start, end, kind, **fields):
    return dict(dict(id=identifier, start=start, end=end, kind=kind, certainty="estimated", place=None, text=None, mode=None,
                     line=None, note=None, options=[], walk_minutes=None), **fields)


def sample_plan() -> dict:
    return dict(
        id="2030-07-fiktiv", title="维也纳两天（虚构）", start="2030-07-14", end="2030-07-15", timezone="Europe/Vienna",
        areas=["Wien"], default_pace="relaxed", budgets={},
        people=[dict(id="fiona", name="Fiona Example"), dict(id="kasimir", name="Kasimir Beispiel")],
        places=[
            dict(id="stay-1", kind="hotel", name="Hotel Fictiva Rosengarten", area="Wien", coords=[48.201234, 16.351234],
                 phone="+43 1 555 0123",
                 name_local="Hotel Fictiva Rosengarten, Musterweg 17, 1070 Wien"),
            dict(id="kunsthalle", kind="museum", name="虚构美术馆", name_local="Kunsthalle Beispiel, Museumsplatz 1, 1070 Wien", area="Wien"),
            dict(id="hbf", kind="transport", name="中央车站", name_local="Wien Hauptbahnhof", area="Wien"),
            dict(id="cafe", kind="food", name="示例咖啡馆", name_local="Café Muster, Beispielgasse 3, 1010 Wien", area="Wien",
                 phone="+43 1 555 0199"),
        ],
        bookings=[
            dict(id="bk-flight", date="2030-07-15", kind="flight", status="booked", subject=["fiona", "kasimir"], fares=[],
                 what="QX 4821 维也纳 → 奥斯陆，07:45 起飞", reference="ZX93KD7"),
            dict(id="bk-stay", date="2030-07-14", kind="lodging", status="booked", subject=["fiona"], place="stay-1", fares=[],
                 what="Hotel Fictiva Rosengarten 一晚", confirmation_number="5550123987"),
        ],
        days=[
            dict(date="2030-07-14", title="到维也纳：美术馆和咖啡", area="Wien", pace="relaxed", base="stay-1", sunset="20:55",
                 notes="Fiona Example 带相机；晚上回 Hotel Fictiva Rosengarten。", rain_plan="下雨就多待在美术馆",
                 anchors=[dict(id="a1", time="10:00", text="美术馆开门", subject=["fiona"], buffer_minutes=10)],
                 candidates={"下午·咖啡": ["cafe"]},
                 plans=[dict(id="P1", name="推荐：先美术馆再咖啡", tracks=[dict(subject=["fiona", "kasimir"], blocks=[
                     block("b1", "09:30", "10:00", "travel", text="住处 → 美术馆", mode="U6 → Westbahnhof，换 18 路电车，€2.40",
                           walk_minutes=4),
                     block("b2", "10:00", "12:30", "visit", place="kunsthalle", text="Fiona Example 和 Kasimir Beispiel 看展"),
                     block("b3", "13:00", "14:00", "meal", place="cafe", note="Musterweg 17 附近那家不去"),
                     block("b4", "21:00", "21:30", "travel", place="stay-1", text="回 Hotel Fictiva Rosengarten 放行李"),
                 ])])]),
            dict(date="2030-07-15", title="回程：07:45 QX 4821", area="Wien", pace="relaxed", base=None, sunset=None, notes=None,
                 anchors=[dict(id="a2", time="07:45", text="QX4821 起飞", subject=["fiona", "kasimir"], buffer_minutes=0)],
                 candidates={}, rain_plan=None,
                 plans=[dict(id="P1", name="去机场", tracks=[dict(subject=["fiona", "kasimir"], blocks=[
                     block("b5", "05:10", "05:35", "travel", place=None, text="中央车站 → 机场", line="RJX 19959",
                           mode="RJX 19959 05:12 → 05:27；或 REX 7 05:20"),
                 ])])]),
        ])


PEOPLE = [dict(id="fiona", name="Fiona Example", trips=["2030-07-fiktiv"]), dict(id="kasimir", name="Kasimir Beispiel"),
          dict(id="odile", name="Odile Sample", trips=["2029-01-other"])]


def build(tmp_path, plan=None, *, english=None, name="site"):
    return share_site(plan or sample_plan(), tmp_path / name, people=PEOPLE, english=english, now=NOW)


def site_text(folder: Path) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(folder.iterdir()) if path.is_file())


def with_text(text: str) -> dict:
    plan = sample_plan()
    plan["days"][0]["plans"][0]["tracks"][0]["blocks"][2]["text"] = text
    return plan


def quiet_plan() -> dict:
    """One stay, a segment 住处 → 某站 → 景点 and a segment 景点 → 住处 (S4h-b): the first leaves the stay in the
    morning, the last comes back to it at night."""
    plan = sample_plan()
    plan["places"].insert(1, dict(id="stop-1", kind="transport", name="某站", name_local="Beispielplatz, 1070 Wien", area="Wien"))
    plan["days"][0]["plans"][0]["tracks"][0]["blocks"] = [
        block("q1", "09:30", "09:45", "travel", text="住处 → 某站 → 美术馆：U6 09:31 → 09:38，步行 4 分钟",
              mode="U6 09:31 → Beispielplatz 09:38，步行 4 分钟", line="U6"),
        block("q2", "10:05", "12:30", "visit", place="kunsthalle"),
        block("q3", "21:00", "21:30", "travel", text="22:47 U1 → 我们那站 22:52，约 22:55 回到住处",
              mode="U1 22:47 → Beispielplatz 22:52", line="U1", walk_minutes=3, note="回到住处"),
    ]
    return plan


QUIET_ENGLISH = {"住处 → 某站 → 美术馆：U6 09:31 → 09:38，步行 4 分钟":
                     "From the lodging via Beispielplatz to the gallery: U6 09:31 → 09:38, a 4 minute walk",
                 "22:47 U1 → 我们那站 22:52，约 22:55 回到住处":
                     "U1 22:47 → our stop 22:52, back at the lodging at about 22:55",
                 "回到住处": "Back at the lodging by 22:55"}


# --- pages and rewrites ---------------------------------------------------------------------------------------------

def test_share_has_home_and_day_pages_only(tmp_path):
    result = build(tmp_path)
    assert result["pages"] == ["2030-07-14.html", "2030-07-15.html", "index.html"]
    names = sorted(path.name for path in result["out"].iterdir())
    assert names == sorted(["2030-07-14.html", "2030-07-15.html", "index.html", ".nojekyll", "robots.txt", ".tripwright-share"])
    text = site_text(result["out"])
    for absent in ("decisions.html", "memory.html", "Decisions", "Memory", "Transport costs", "Verification", "Booking"):
        assert absent not in text
    assert '<meta name="robots" content="noindex, nofollow">' in text


def test_names_stays_and_flights_are_rewritten_from_the_trip_records(tmp_path):
    text = site_text(build(tmp_path, english={"回程：07:45 航班": "Going home: 07:45 QX 4821"})["out"])
    for private in PRIVATE:
        assert private not in text, private
    assert "Traveler A" in text and "旅伴 A" in text and "Traveler B" in text
    assert "a hotel in 1070 Wien" in text and "1070 Wien 的酒店" in text
    assert "回程：07:45 航班" in text and "Going home: 07:45 flight" in text and "航班 起飞" in text
    assert "回 住处 放行李" in text and "晚上回 住处" in text


def test_places_keep_names_and_map_links_and_stays_have_none(tmp_path):
    html = (build(tmp_path)["out"] / "2030-07-14.html").read_text()
    assert "Kunsthalle Beispiel, Museumsplatz 1, 1070 Wien" in html
    assert 'href="https://www.google.com/maps/search/?api=1&amp;query=Kunsthalle%20Beispiel%2C%20Museumsplatz%201%2C%201070%20Wien"' in html
    assert "Café Muster" in html and html.count("google.com/maps/search") == 5  # museum and café in the list of places and in the timeline, the café's candidate
    assert "U6 → Westbahnhof，换 18 路电车，€2.40" in html


def test_default_english_switches_to_chinese_and_uses_given_english(tmp_path):
    english = {"到维也纳：美术馆和咖啡": "Arriving in Vienna: a gallery and coffee",
               "旅伴 A 和 旅伴 B 看展": "Traveler A and Kasimir Beispiel see the exhibition"}
    result = build(tmp_path, english=english)
    html = (result["out"] / "2030-07-14.html").read_text()
    assert '<html lang="en" data-language="en">' in html
    assert 'data-lang-button="en"' in html and 'data-lang-button="zh"' in html
    assert '<span class="en">Arriving in Vienna: a gallery and coffee</span>' in html
    assert "Traveler A and Traveler B see the exhibition" in html and "Kasimir" not in html
    assert '<span class="zh" lang="zh-CN">到维也纳：美术馆和咖啡</span>' in html
    assert '<span class="en" lang="zh-CN">下雨就多待在美术馆</span>' in html and "(中文)" not in html  # no tag after each Chinese text
    assert "下雨就多待在美术馆" in result["untranslated"] and "到维也纳：美术馆和咖啡" not in result["untranslated"]
    assert not any(private in text for text in result["untranslated"] for private in PRIVATE)
    assert 'aria-pressed' not in html.split("<script>")[0]  # the buttons' state is set by the script from ?lang= or the last choice
    assert "部分内容只有中文" in html and "Some texts are in Chinese only" in html  # said once in the footer


# --- the scan: legal controls pass, private data blocks -------------------------------------------------------------


def test_the_way_in_and_out_of_a_stay_keeps_line_stops_and_times_but_not_the_walk_from_the_door(tmp_path):
    """Rule: a segment that starts or ends at a stay is written as any other (line, stops, departure and arrival
    times); the minutes of walking from the door and door wording are cut, in both languages. Positive: they are gone.
    Counter-example: the line, the public station and its times, and the times between public places are all there."""
    result = build(tmp_path, quiet_plan(), english=QUIET_ENGLISH)
    html = (result["out"] / "2030-07-14.html").read_text()
    for gone in ("步行 4 分钟", "4 minute walk", "walk_minutes", "3 min walk"):
        assert gone not in html, gone
    for kept in ("09:31", "09:38", "Beispielplatz", "22:47", "22:52", "我们那站", "our stop"):
        assert kept in html, kept
    assert ">U6" in html and ">U1" in html
    assert "a hotel in 1070 Wien" in html and "1070 Wien 的酒店" in html
    assert "10:05" in html and "10:00" in html  # times between public places
    assert "05:12" in (build(tmp_path)["out"] / "2030-07-15.html").read_text()


def test_door_wording_and_bare_minutes_next_to_the_lodging_are_cut_from_a_stay_segment():
    from tripwright.site.privacy import stay_text

    assert stay_text("U6 09:31 → Beispielplatz 09:38，步行 4 分钟") == "U6 09:31 → Beispielplatz 09:38"
    assert stay_text("Tram 1 + bus 80 + walk 5′") == "Tram 1 + bus 80"
    assert stay_text("Walk 7′ + bus 85 + walk 19′") == "bus 85"
    assert stay_text("出门口走 3 分钟，然后坐 U4") == "然后坐 U4"  # the clause with the door word goes whole
    assert stay_text("楼下") is None
    for public in ("U1 22:47 → 22:52，回到住处", "22:47 U1 → our stop 22:52", "REX 7 05:20", "Wien Hbf → Flughafen 22:42–22:57"):
        assert stay_text(public) == public  # a station, a line and times are not what localizes the stay


def test_a_segment_from_a_stay_by_bus_names_line_stops_and_times(tmp_path):
    plan = sample_plan()
    plan["places"].append(dict(id="stop-2", kind="transport", name="某站", name_local="Beispielplatz, 1070 Wien", area="Wien"))
    plan["days"][0]["plans"][0]["tracks"][0]["blocks"] = [
        block("s1", "08:10", "08:40", "travel", text="住处 → Beispielplatz 站", mode="从门口走 3 分钟，坐 18 路电车 08:14 → Beispielplatz 08:31，换 U6 08:36 → Westbahnhof 08:40"),
        block("s2", "09:00", "10:00", "visit", place="kunsthalle")]
    plan["days"][0]["base"] = "stay-1"
    html = (build(tmp_path, plan, english={"住处 → Beispielplatz 站": "From the lodging to Beispielplatz"})["out"] / "2030-07-14.html").read_text()
    for line_stop_time in ("18 路电车 08:14", "Beispielplatz 08:31", "U6 08:36", "Westbahnhof 08:40"):
        assert line_stop_time in html, line_stop_time
    assert "门口" not in html and "3 分钟" not in html


@pytest.mark.parametrize("legal", ["订位打 +43 1 555 0199", "REX 7", "U6", "18 路", "RJX 19959", "RJX", "S45", "N25", "IC 540", "RJ 641", "U1 03:22",
                                   "22:42–23:15", "€13.40", "HUF 4500", "EUR 9.70", "Stephansdom", "Gleis 8A", "T3 2 楼",
                                   "U4 坐 10 站", "1100 Wien", "2030-07-14", "59A", "VAB 1"])
def test_transit_lines_times_prices_and_places_are_not_blocked(tmp_path, legal):
    result = build(tmp_path, with_text(f"控制样本 {legal} 照写"))
    assert legal in (result["out"] / "2030-07-14.html").read_text()


@pytest.mark.parametrize("private, category", [
    ("请写信到 fiction.person@example.test", "email"),
    ("Booking number: ZQ81AB772", "order-like identifier"),
    ("PNR: QWERTY1", "order-like identifier"),
    ("订单号 FX20300714A", "order-like identifier"),
    ("1234567890123", "order-like identifier"),
    ("参考 ZX93KD7 改签", "order-like identifier"),  # the flight booking's own reference field
    ("打 +43 660 1234567 联系", "phone number"),
    ("打 0664 1234567 联系", "phone number"),
    ("打 138 1234 5678 联系", "phone number"),
    ("前台 +43 1 555 0123", "phone number"),  # a stay's number is not a public business number here
])
def test_private_data_blocks_and_keeps_the_previous_output(tmp_path, private, category):
    first = build(tmp_path)
    before = {path.name: path.read_bytes() for path in first["out"].iterdir()}
    with pytest.raises(ShareBlocked) as caught:
        build(tmp_path, with_text(private))
    assert category in caught.value.findings
    assert caught.value.findings[category] == ["2030-07-14.html"]
    for word in private.split():
        if len(word) >= 4 and (any(character.isdigit() for character in word) or "@" in word):
            assert word not in str(caught.value)
    assert {path.name: path.read_bytes() for path in first["out"].iterdir()} == before
    assert [path.name for path in tmp_path.iterdir()] == ["site"]  # no staging folder left behind


def test_a_flight_number_in_the_plan_text_becomes_flight(tmp_path):
    html = (build(tmp_path, with_text("改坐 LH 1234 走，或 U2 1234"), english={"改坐 航班 走，或 航班": "Take LH 1234 instead"})["out"]
            / "2030-07-14.html").read_text()
    assert "LH 1234" not in html and "U2 1234" not in html and "改坐 航班 走，或 航班" in html and "Take flight instead" in html


def test_flight_rule_drops_flight_numbers_only(tmp_path):
    assert flight_numbers("ZQ731 07:45；U2 1234；BA1") == ["ZQ731", "U2 1234", "BA1"]
    assert flight_numbers("REX 7、U6、18 路、RJX 19959、N25、S45、IC 540、XL 71、Am Hauptbahnhof 1/EG 116、U4 10 站") == []
    assert phone_numbers("05:45 → 07:05，€13.40，1050 Wien，22779，2030-07-14") == []


def test_a_flight_booked_under_a_train_like_code_is_still_rewritten(tmp_path):
    plan = sample_plan()
    plan["bookings"][0]["what"] = "EN 8811 维也纳 → 威尼斯"
    plan["days"][1]["anchors"][0]["text"] = "EN 8811 起飞"
    html = (build(tmp_path, plan)["out"] / "2030-07-15.html").read_text()
    assert "EN 8811" not in html and "航班 起飞" in html


def test_scan_reports_names_stays_and_policy_words_by_category_without_values(tmp_path):
    rules = Rules.of(sample_plan(), PEOPLE, Policy(["Geheimwort"]))
    folder = tmp_path / "raw"
    folder.mkdir()
    (folder / "a.html").write_text('<a href="https://www.google.com/maps/search/?api=1&query=Odile%20Sample">x</a>')
    (folder / "b.html").write_text("Hotel Fictiva Rosengarten · 48.201234")
    (folder / "c.html").write_text("Geheimwort")
    (folder / "d.html").write_text("ZQ731 07:45")
    (folder / ".git").mkdir()
    (folder / ".git" / "config").write_text("Fiona Example")
    assert scan_tree(folder, rules.zh) == {"person name": ["a.html"], "lodging": ["b.html"], "private word": ["c.html"],
                                           "flight number": ["d.html"]}


def test_placeholder_names_are_coded_but_pronouns_are_left(tmp_path):
    plan = sample_plan()
    plan["people"] = [dict(id="me", name="我"), dict(id="mate-1", name="队友 1")]
    plan["days"][0]["notes"] = "我和队友 1 先走"
    result = share_site(plan, tmp_path / "site", people=[], now=NOW)
    html = (result["out"] / "2030-07-14.html").read_text()
    assert "我和旅伴 B 先走" in html and "队友 1" not in html


def test_a_stays_town_and_country_stay_public(tmp_path):
    plan = sample_plan()
    plan["places"][0]["name_local"] = "Hotel Fictiva Rosengarten, Musterweg 17, Neubau, 1070 Wien, Austria"
    plan["days"][0]["notes"] = "Neubau 和 Austria 照写；Rosengarten 不写"
    html = (build(tmp_path, plan)["out"] / "2030-07-14.html").read_text()
    assert "Neubau 和 Austria 照写；住处 不写" in html and "a hotel in 1070 Wien" in html


def test_an_unrelated_folder_is_never_replaced(tmp_path):
    other = tmp_path / "site"
    other.mkdir()
    (other / "notes.txt").write_text("keep")
    with pytest.raises(ValueError, match="not a share page folder"):
        build(tmp_path)
    assert (other / "notes.txt").read_text() == "keep"


# --- the trip repository and publication ------------------------------------------------------------------------------

GIT = ["git", "-c", "user.name=Example Writer", "-c", "user.email=writer@example.test", "-c", "commit.gpgsign=false"]


def trip_repo(root: Path, plan: dict) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".gitignore").write_text(".tripwright/\n")
    (root / "tripwright.yaml").write_text(layout.dump(dict(format=1, current_trip=plan["id"], timezone="Europe/Vienna")))
    (root / "profile").mkdir(exist_ok=True)
    (root / "profile/people.yaml").write_text(layout.dump(PEOPLE))
    folder = root / "trips" / plan["id"]
    for path, text in layout.plan_files(plan, plan["id"]).items():
        (folder / path).parent.mkdir(parents=True, exist_ok=True)
        (folder / path).write_text(text)
    if not (root / ".git").exists():
        subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run([*GIT, "-C", str(root), "add", "-A"], check=True)
    subprocess.run([*GIT, "-C", str(root), "commit", "-q", "-m", "plan"], check=True)
    return root


@pytest.fixture
def public(tmp_path, monkeypatch):
    from tripwright.site.publish import connect

    bare = tmp_path / "public.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    data = tmp_path / "data"
    monkeypatch.setenv("TRIPWRIGHT_DATA_DIR", str(data))
    connect(data, str(bare))
    return bare, data


def share_settings(repo: Path, trip: str, **settings):
    path = repo / "trips" / trip / "share.yaml"
    path.write_text(layout.dump(settings), encoding="utf-8")
    subprocess.run([*GIT, "-C", str(repo), "add", str(path)], check=True)
    subprocess.run([*GIT, "-C", str(repo), "commit", "-q", "-m", "share settings"], check=True)


def remote_files(bare: Path) -> dict[str, str]:
    names = subprocess.run(["git", "-C", str(bare), "ls-tree", "-r", "--name-only", "main"], capture_output=True, text=True, check=True)
    return {name: subprocess.run(["git", "-C", str(bare), "show", f"main:{name}"], capture_output=True, text=True, check=True).stdout
            for name in names.stdout.split()}


def head(bare: Path) -> str:
    return subprocess.run(["git", "-C", str(bare), "rev-parse", "main"], capture_output=True, text=True, check=True).stdout.strip()


def test_load_trip_reads_the_committed_plan_without_writing(tmp_path):
    repo = trip_repo(tmp_path / "trip", sample_plan())
    day = repo / "trips/2030-07-fiktiv/plan/days/2030-07-14.yaml"
    day.write_text(day.read_text().replace("到维也纳：美术馆和咖啡", "UNCOMMITTED"))
    plan, people = load_trip(repo)
    assert plan["days"][0]["title"] == "到维也纳：美术馆和咖啡" and [person["id"] for person in people] == ["fiona", "kasimir", "odile"]
    assert not (repo / ".tripwright").exists()


def test_publish_uses_a_random_committed_subpath_and_fixed_identity(tmp_path, public):
    bare, data = public
    repo = trip_repo(tmp_path / "trip", sample_plan())
    outcome = publish(repo, data_dir=data, now=NOW)
    path = outcome["path"]
    assert path.startswith("fiktiv-") and len(path.rsplit("-", 1)[1]) == 2
    assert layout.parse("share.yaml", (repo / "trips/2030-07-fiktiv/share.yaml").read_text())["path"] == path
    files = remote_files(bare)
    assert f"{path}/index.html" in files and files["robots.txt"].startswith("User-agent:")
    assert ".nojekyll" in files and "index.html" not in files
    assert not any(private in text for text in files.values() for private in PRIVATE)
    author = subprocess.run(["git", "-C", str(bare), "log", "-1", "--format=%an <%ae> %cn <%ce>", "main"],
                            capture_output=True, text=True, check=True).stdout.strip()
    assert author == "tripwright <share@tripwright.invalid> tripwright <share@tripwright.invalid>"
    assert publish(repo, data_dir=data, now=NOW)["changed"] is False
    assert not (repo / ".tripwright/share/config.json").exists()


def test_blocked_scan_records_categories_then_clean_publish_closes_notice(tmp_path, public):
    bare, data = public
    plan = sample_plan()
    repo = trip_repo(tmp_path / "trip", plan)
    publish(repo, data_dir=data, now=NOW)
    online = head(bare)
    leaking = deepcopy(plan)
    leaking["days"][1]["notes"] = "机场见，电话 +43 660 1234567"
    trip_repo(repo, leaking)
    with pytest.raises(ShareBlocked, match="phone number.*2030-07-15.html") as blocked:
        publish(repo, data_dir=data)
    assert "1234567" not in str(blocked.value)
    assert head(bare) == online
    store = Store(repo / ".tripwright/state.sqlite")
    try:
        shown = open_notices(store, "local")
        assert [item["kind"] for item in shown] == ["share_blocked"]
        record = store.get("share", "local", plan["id"])
        assert record["status"] == "blocked" and record["blocked"] == {"phone number": ["2030-07-15.html"]}
        assert "1234567" not in json.dumps(record, ensure_ascii=False)
    finally:
        store.close()
    trip_repo(repo, plan)
    assert publish(repo, data_dir=data)["ok"]
    store = Store(repo / ".tripwright/state.sqlite")
    try:
        assert open_notices(store, "local") == [] and store.get("share", "local", plan["id"])["ok"]
    finally:
        store.close()


def test_two_trips_have_isolated_directories_and_no_root_index(tmp_path, public):
    bare, data = public
    first, second = sample_plan(), sample_plan()
    second["id"] = "2030-08-second"
    repo = trip_repo(tmp_path / "trip", first)
    one = publish(repo, data_dir=data)["path"]
    trip_repo(repo, second)
    two = publish(repo, trip=second["id"], data_dir=data)["path"]
    before = {name: text for name, text in remote_files(bare).items() if name.startswith(one + "/")}
    changed = deepcopy(first)
    changed["days"][0]["title"] = "只改第一趟"
    trip_repo(repo, changed)
    publish(repo, trip=first["id"], data_dir=data)
    files = remote_files(bare)
    assert one != two and "只改第一趟" in files[f"{one}/2030-07-14.html"]
    assert {name: text for name, text in files.items() if name.startswith(one + "/")} != before
    assert f"{two}/index.html" in files and "index.html" not in files


def test_rename_and_off_remove_the_old_directory_without_later_commits(tmp_path, public):
    bare, data = public
    repo = trip_repo(tmp_path / "trip", sample_plan())
    old = publish(repo, data_dir=data)["path"]
    share_settings(repo, "2030-07-fiktiv", path="quiet-vienna")
    assert publish(repo, data_dir=data)["path"] == "quiet-vienna"
    assert old not in {name.split("/")[0] for name in remote_files(bare)} and "quiet-vienna/index.html" in remote_files(bare)
    share_settings(repo, "2030-07-fiktiv", path="quiet-vienna", off=True)
    (repo / "trips/2030-07-fiktiv/share.yaml").write_text("path: quiet-vienna\noff: true\n")  # unquoted YAML 1.1 key
    subprocess.run([*GIT, "-C", str(repo), "commit", "-qam", "unquoted off"], check=True)
    assert publish(repo, data_dir=data)["status"] == "off"
    assert "quiet-vienna/index.html" not in remote_files(bare)
    stopped = head(bare)
    assert publish(repo, data_dir=data)["changed"] is False and head(bare) == stopped


def test_missing_public_repository_does_not_lose_the_trip_and_next_commit_recovers(tmp_path, public):
    from tripwright.site.publish import PublishError

    bare, data = public
    repo = trip_repo(tmp_path / "trip", sample_plan())
    moved = tmp_path / "moved.git"
    bare.rename(moved)
    with pytest.raises(PublishError, match="check the public repository"):
        publish(repo, data_dir=data)
    assert Git(repo).rev("HEAD") is not None
    store = Store(repo / ".tripwright/state.sqlite")
    try:
        assert store.get("share", "local", "2030-07-fiktiv")["status"] == "error"
    finally:
        store.close()
    moved.rename(bare)
    changed = sample_plan()
    changed["days"][0]["title"] = "恢复后发布"
    trip_repo(repo, changed)
    assert publish(repo, data_dir=data)["ok"] and any("恢复后发布" in text for text in remote_files(bare).values())


def test_per_trip_repository_is_an_explicit_skip(tmp_path, public):
    bare, data = public
    repo = trip_repo(tmp_path / "trip", sample_plan())
    share_settings(repo, "2030-07-fiktiv", repository="git@example.test:other/pages.git")
    outcome = publish(repo, data_dir=data)
    assert outcome["status"] == "separate_repository" and not outcome["ok"]
    assert len(remote_files(bare)) == 2
