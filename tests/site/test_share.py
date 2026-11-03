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

from tripwright.cli import main
from tripwright.repo import layout
from tripwright.repo.notices import open_notices
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
    assert "Staying in 1070 Wien (hotel)" in text and "住在 1070 Wien（酒店）" in text
    assert "回程：07:45 航班" in text and "Going home: 07:45 flight" in text and "航班 起飞" in text
    assert "回 住处 放行李" in text and "晚上回 住处" in text


def test_places_keep_names_and_map_links_and_stays_have_none(tmp_path):
    html = (build(tmp_path)["out"] / "2030-07-14.html").read_text()
    assert "Kunsthalle Beispiel, Museumsplatz 1, 1070 Wien" in html
    assert 'href="https://www.google.com/maps/search/?api=1&amp;query=Kunsthalle%20Beispiel%2C%20Museumsplatz%201%2C%201070%20Wien"' in html
    assert "Café Muster" in html and html.count("google.com/maps/search") == 3  # the museum, the café block and its candidate
    assert "U6 → Westbahnhof，换 18 路电车，€2.40" in html


def test_default_english_switches_to_chinese_and_uses_given_english(tmp_path):
    english = {"到维也纳：美术馆和咖啡": "Arriving in Vienna: a gallery and coffee",
               "旅伴 A 和 旅伴 B 看展": "Traveler A and Kasimir Beispiel see the exhibition"}
    result = build(tmp_path, english=english)
    html = (result["out"] / "2030-07-14.html").read_text()
    assert '<html lang="en" data-language="en">' in html
    assert 'data-language-button="en"' in html and 'data-language-button="zh"' in html
    assert '<span class="en">Arriving in Vienna: a gallery and coffee</span>' in html
    assert "Traveler A and Traveler B see the exhibition" in html and "Kasimir" not in html
    assert '<span class="zh" lang="zh-CN">到维也纳：美术馆和咖啡</span>' in html
    assert '<span class="en" lang="zh-CN">下雨就多待在美术馆 <span class="orig">(中文)</span></span>' in html
    assert "下雨就多待在美术馆" in result["untranslated"] and "到维也纳：美术馆和咖啡" not in result["untranslated"]
    assert not any(private in text for text in result["untranslated"] for private in PRIVATE)
    assert 'aria-pressed' not in html.split("<script>")[0]  # the buttons' state is set by the script from ?lang=


# --- the scan: legal controls pass, private data blocks -------------------------------------------------------------


def test_a_segment_to_or_from_the_stay_says_only_the_order(tmp_path):
    """S4h-b (verdict 17, mechanism 6): the day pages kept "22:47 U1 → our stop 22:52, back at about 22:55", which put
    the stay inside one station's walking circle. A timeline segment that starts or ends at a stay now shows no
    minute-level time, no walking minutes, no arrival times and no door wording, in either language; it says the order
    (and the line), while times between public places stay."""
    result = build(tmp_path, quiet_plan(), english=QUIET_ENGLISH)
    html = (result["out"] / "2030-07-14.html").read_text()
    for absent in ("09:30", "09:45", "09:31", "09:38", "步行 4 分钟", "4 minute walk", "21:00", "21:30",
                   "22:47", "22:52", "22:55", "我们那站", "our stop", "回到住处", "Back at the lodging by", "walk_minutes"):
        assert absent not in html, absent
    # the order is still said, with the line, in both languages
    assert "从住处出发" in html and "From the lodging" in html
    assert "回住处" in html and "Back to the lodging" in html
    assert "住在 1070 Wien（酒店）" in html and "Staying in 1070 Wien (hotel)" in html
    assert ">U6<" in html and ">U1<" in html  # the lines without their times
    # times between public places stay, in this plan and in the untouched one
    assert "10:05" in html and "10:00" in html
    assert "05:12" in (build(tmp_path)["out"] / "2030-07-15.html").read_text()


def test_stay_words_in_a_clean_segment_text_stay_visible(tmp_path):
    """A stay-touching block whose own words carry no time, walk or door keeps them: only what localizes is
    dropped (S4h-b)."""
    html = (build(tmp_path)["out"] / "2030-07-14.html").read_text()
    assert "回 住处 放行李" in html and "晚上回 住处" in html  # the evening block's text and the day's notes


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
    assert "Neubau 和 Austria 照写；住处 不写" in html and "Staying in 1070 Wien (hotel)" in html


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


FAKE_GH = r'''#!{python}
"""A stand-in for the GitHub CLI: repositories are bare repositories under $FAKE_GH_ROOT; every call is logged."""
import json, os, subprocess, sys
root = os.environ["FAKE_GH_ROOT"]
arguments = sys.argv[1:]
with open(os.path.join(root, "gh.log"), "a") as log:
    log.write(json.dumps(arguments) + "\n")
def bare(name):
    return os.path.join(root, name.split("/")[-1] + ".git")
if arguments[:2] == ["repo", "create"]:
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", bare(arguments[2])], check=True)
    print("https://github.com/friend-owner/" + arguments[2])
elif arguments[:2] == ["repo", "view"]:
    if not os.path.isdir(bare(arguments[2])):
        sys.exit("GraphQL: Could not resolve to a Repository")
    print("friend-owner/" + arguments[2].split("/")[-1])
elif arguments[:2] == ["repo", "clone"]:
    subprocess.run(["git", "clone", "-q", bare(arguments[2]), arguments[3]], check=True)
elif arguments[0] == "api" and "POST" in arguments:
    open(os.path.join(root, "pages-on"), "w").write(" ".join(arguments))
    print("{{}}")
elif arguments[0] == "api":
    if not os.path.exists(os.path.join(root, "pages-on")):
        sys.exit("HTTP 404: Not Found")
    print("https://friend-owner.github.io/" + arguments[1].split("/")[2] + "/")
else:
    sys.exit("fake gh: unsupported " + " ".join(arguments))
'''


@pytest.fixture
def github(tmp_path, monkeypatch):
    root = tmp_path / "github"
    binary = tmp_path / "bin"
    root.mkdir()
    binary.mkdir()
    (binary / "gh").write_text(FAKE_GH.format(python=sys.executable))
    (binary / "gh").chmod(0o755)
    monkeypatch.setenv("FAKE_GH_ROOT", str(root))
    monkeypatch.setenv("PATH", f"{binary}{os.pathsep}{os.environ['PATH']}")
    return root


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


def test_publish_creates_a_public_page_repository_holding_only_the_page(tmp_path, github):
    repo = trip_repo(tmp_path / "trip", sample_plan())
    outcome = publish(repo, name="trip-2030-wien", create_repo=True, now=NOW)
    calls = [json.loads(line) for line in (github / "gh.log").read_text().splitlines()]
    assert ["repo", "create", "trip-2030-wien", "--public", "--description", "Shared trip page (built by tripwright)"] in calls
    assert any(call[:4] == ["api", "-X", "POST", "repos/friend-owner/trip-2030-wien/pages"] and "source[branch]=main" in call
               for call in calls)
    assert outcome["ok"] and outcome["url"] == "https://friend-owner.github.io/trip-2030-wien/" and outcome["changed"]
    files = remote_files(github / "trip-2030-wien.git")
    assert sorted(files) == sorted([".nojekyll", ".tripwright-share", "2030-07-14.html", "2030-07-15.html", "index.html", "robots.txt"])
    assert not any(private in text for text in files.values() for private in PRIVATE)
    author = subprocess.run(["git", "-C", str(github / "trip-2030-wien.git"), "log", "-1", "--format=%an <%ae> %cn <%ce>", "main"],
                            capture_output=True, text=True, check=True).stdout.strip()
    assert author == "tripwright <share@tripwright.invalid> tripwright <share@tripwright.invalid>"
    config = json.loads((repo / ".tripwright/share/config.json").read_text())
    assert config["repository"] == "friend-owner/trip-2030-wien" and config["url"] == outcome["url"]
    assert subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True).stdout == ""


def test_republish_after_a_plan_commit_and_a_blocked_scan_keeps_the_page_online(tmp_path, github):
    plan = sample_plan()
    repo = trip_repo(tmp_path / "trip", plan)
    publish(repo, name="trip-2030-wien", create_repo=True, now=NOW)
    bare = github / "trip-2030-wien.git"
    first = head(bare)
    assert publish(repo, now=NOW)["changed"] is False and head(bare) == first

    changed = deepcopy(plan)
    changed["days"][0]["title"] = "到维也纳：改成先喝咖啡"
    trip_repo(repo, changed)
    assert publish(repo, now=NOW)["changed"] and head(bare) != first
    second = head(bare)
    assert "改成先喝咖啡" in remote_files(bare)["2030-07-14.html"]

    leaking = deepcopy(changed)
    leaking["days"][0]["notes"] = "有事发邮件 fiction.person@example.test"
    trip_repo(repo, leaking)
    with pytest.raises(ShareBlocked) as caught:
        publish(repo, now=NOW)
    assert list(caught.value.findings) == ["email"]
    assert head(bare) == second and "改成先喝咖啡" in remote_files(bare)["2030-07-14.html"]
    last = (repo / ".tripwright/share/last.json").read_text()
    assert json.loads(last)["ok"] is False and "example.test" not in last
    assert "改成先喝咖啡" in (repo / ".tripwright/share/site/2030-07-14.html").read_text()  # the last clean build stays


def test_publish_removes_anything_else_from_the_page_repository(tmp_path, github):
    repo = trip_repo(tmp_path / "trip", sample_plan())
    publish(repo, name="trip-2030-wien", create_repo=True, now=NOW)
    other = tmp_path / "other-clone"
    subprocess.run(["git", "clone", "-q", str(github / "trip-2030-wien.git"), str(other)], check=True)
    (other / "README.md").write_text("notes by hand")
    subprocess.run([*GIT, "-C", str(other), "add", "-A"], check=True)
    subprocess.run([*GIT, "-C", str(other), "commit", "-q", "-m", "hand"], check=True)
    subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "main"], check=True)
    publish(repo, now=NOW)
    assert "README.md" not in remote_files(github / "trip-2030-wien.git")


def test_publish_needs_create_the_first_time(tmp_path, github):
    from tripwright.site.publish import PublishError

    repo = trip_repo(tmp_path / "trip", sample_plan())
    with pytest.raises(PublishError, match="--create"):
        publish(repo, now=NOW)
    assert not (github / "gh.log").exists()


def test_cli_share_build_publish_and_the_blocked_notice(tmp_path, github, capsys):
    plan = sample_plan()
    repo = trip_repo(tmp_path / "trip", plan)
    english = tmp_path / "en.yaml"
    assert main(["--repo", str(repo), "share", "build", "--out", str(tmp_path / "preview"), "--english", str(english), "--todo"]) == 0
    built = json.loads(capsys.readouterr().out)
    assert built["pages"] == ["2030-07-14.html", "2030-07-15.html", "index.html"] and built["untranslated"] > 0
    todo = layout.parse("en.yaml", english.read_text())
    assert "下雨就多待在美术馆" in todo and todo["下雨就多待在美术馆"] == ""
    english.write_text(layout.dump(dict(todo, 下雨就多待在美术馆="If it rains, stay longer in the gallery")))
    assert main(["--repo", str(repo), "share", "build", "--out", str(tmp_path / "preview"), "--english", str(english)]) == 0
    assert "If it rains, stay longer in the gallery" in (tmp_path / "preview/2030-07-14.html").read_text()
    capsys.readouterr()

    assert main(["--repo", str(repo), "share", "publish", "--name", "trip-2030-wien", "--create"]) == 0
    assert json.loads(capsys.readouterr().out)["url"] == "https://friend-owner.github.io/trip-2030-wien/"
    leaking = deepcopy(plan)
    leaking["days"][1]["notes"] = "机场见，电话 +43 660 1234567"
    trip_repo(repo, leaking)
    assert main(["--repo", str(repo), "share", "publish"]) == 2
    captured = capsys.readouterr()
    assert "phone number (2030-07-15.html)" in captured.err and "1234567" not in captured.err + captured.out
    store = Store(repo / ".tripwright/state.sqlite")
    try:
        shown = open_notices(store, "local")
        assert [notice["kind"] for notice in shown] == ["share_blocked"]
        assert "phone number" in shown[0]["user_line"] and "1234567" not in json.dumps(shown, ensure_ascii=False)
    finally:
        store.close()
    trip_repo(repo, plan)
    assert main(["--repo", str(repo), "share", "publish"]) == 0
    store = Store(repo / ".tripwright/state.sqlite")
    try:
        assert open_notices(store, "local") == []
    finally:
        store.close()


def test_cli_share_needs_a_repository(capsys, monkeypatch):
    monkeypatch.delenv("TRIPWRIGHT_REPO", raising=False)
    assert main(["share", "build", "--out", "x"]) == 1
    assert "needs --repo" in capsys.readouterr().err
