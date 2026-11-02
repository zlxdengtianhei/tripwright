from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from tripwright.adapters.web import ResultLinks, web_app
from tripwright.demo import run_demo
from tripwright.service import Tripwright
from tripwright.site import Policy, PrivacyError, build_site
from tripwright.site.privacy import scan_text
from tripwright.storage import Store


class Text(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.parts = []
        self.feed(html)

    def handle_data(self, data):
        self.parts.append(data)

    def __str__(self):
        return " ".join(part.strip() for part in self.parts if part.strip())


@pytest.fixture
def service(tmp_path):
    store = Store(tmp_path / "trip.sqlite3")
    service = Tripwright(store)
    run_demo(service, output=lambda *args: None)
    yield service
    service.jobs.stop()
    store.close()


def trip_time(service):
    plan = service.state("demo")["plan"]
    return datetime.fromisoformat(plan["days"][0]["date"] + "T12:00:00").replace(tzinfo=ZoneInfo(plan["timezone"]))


def replace_title(service, owner, title):
    version = service.state(owner)
    version["plan"]["title"] = title
    service.store.put("versions", owner, version)


def check_job(service):
    current = service.state("demo")
    turn = service.turn("demo", "Please check the full current itinerary", "views-check")
    return service.propose("demo", ctx_id=turn["ctx_id"], base_version=current["number"],
                           idempotency_key="views-check", plan=current["plan"], usages=current["usages"])


def test_site_has_every_day_version_checks_and_decisions(service, tmp_path):
    version = service.state("demo")
    output = build_site(service, "demo", tmp_path / "site", now=trip_time(service))
    for day in version["plan"]["days"]:
        html = (output / f"{day['date']}.html").read_text()
        text = str(Text(html))
        assert f"Version {version['number']}" in text
        assert "Generated" in text
        assert "Unverified" in text
        assert "Consequence" in text
        assert "This part of the plan may be infeasible." in text
        positions = [text.index(label) for label in ("At a glance", "Hard nodes", "Day timeline", "Places and maps", "Candidates by time slot", "Rain plan", "Transport costs")]
        assert positions == sorted(positions)
    decisions = str(Text((output / "decisions.html").read_text()))
    assert "What happens after dinner on the first day?" in decisions
    assert "Agreed café after dinner" in decisions
    assert "Revisit when" in decisions
    index = str(Text((output / "index.html").read_text()))
    assert "Today" in index and "Tomorrow" in index
    assert "Prepare ahead" in index and "Pending decisions" in index
    assert "Mira" not in "".join(page.read_text() for page in output.glob("*.html"))


def test_language_and_trip_timezone(service, tmp_path):
    plan = service.state("demo")["plan"]
    local_midnight = trip_time(service).replace(hour=0, minute=30)
    output = build_site(service.store, "demo", tmp_path / "zh", language="zh-CN", now=local_midnight.astimezone(timezone.utc))
    html = (output / "index.html").read_text()
    assert 'data-language="zh"' in html
    assert 'data-language-button="en"' in html
    assert plan["days"][0]["date"] in html
    assert local_midnight.isoformat(timespec="seconds") in html


def test_chinese_trip_title_defaults_to_chinese_labels(service, tmp_path):
    replace_title(service, "demo", "虚构的同行者行程")
    output = build_site(service, "demo", tmp_path / "default-zh")
    assert 'data-language="zh"' in (output / "index.html").read_text()


@pytest.mark.parametrize("private", ["invented.person@example.test", "PNR: ABC12345", "HMABCD123456", "123456789012"])
def test_privacy_blocks_without_creating_output(service, tmp_path, private):
    replace_title(service, "demo", private)
    output = tmp_path / "blocked"
    with pytest.raises(PrivacyError, match="Publication blocked") as caught:
        build_site(service, "demo", output)
    assert private not in str(caught.value)
    assert not output.exists()


def test_privacy_policy_and_existing_output(service, tmp_path):
    assert scan_text("invented@example.test")
    assert scan_text("Booking number: ABC123456")
    assert scan_text("invented%40example.test")
    replace_title(service, "demo", "FictionalName's journey")
    output = build_site(service, "demo", tmp_path / "alias", privacy_policy=Policy(["FictionalName => Traveler"]))
    html = (output / "index.html").read_text()
    assert "FictionalName" not in html and "Traveler" in html
    with pytest.raises(ValueError, match="empty"):
        build_site(service, "demo", output)
    assert (output / "index.html").read_text() == html
    with pytest.raises(PrivacyError):
        build_site(service, "demo", tmp_path / "denied", privacy_policy=Policy(["FictionalName"]))


def test_static_markup_is_escaped(service, tmp_path):
    replace_title(service, "demo", "<script>alert('example')</script>")
    output = build_site(service, "demo", tmp_path / "escaped")
    html = (output / "index.html").read_text()
    assert "<script>alert(" not in html
    assert "&lt;script&gt;" in html


def test_replacements_are_escaped_as_data_before_rendering(service, tmp_path):
    replace_title(service, "demo", "Fictional O'Name's journey")
    output = build_site(service, "demo", tmp_path / "replacement", privacy_policy=Policy(["O'Name => <script>example()</script>"]))
    html = (output / "index.html").read_text()
    assert "O&#39;Name" not in html
    assert "<script>example()" not in html
    assert "&lt;script&gt;example()&lt;/script&gt;" in html


def test_site_uses_committed_version_and_exposes_conflicts(service, tmp_path):
    current = service.state("demo")
    turn = service.turn("demo", "Prepare an uncommitted candidate", "views-uncommitted")
    candidate = dict(current["plan"], title="UNCOMMITTED_CANDIDATE_TITLE")
    service.propose("demo", ctx_id=turn["ctx_id"], base_version=current["number"],
                    idempotency_key="views-uncommitted", plan=candidate, usages=current["usages"])
    current["checks"]["checks"].append(dict(id="public-test-conflict", status="conflict", text="Fictional hard conflict",
                                                consequence="The transfer cannot be made.", alternative="Choose a later route.", details={}))
    service.store.put("versions", "demo", current)
    output = build_site(service, "demo", tmp_path / "committed")
    html = (output / "index.html").read_text()
    assert "UNCOMMITTED_CANDIDATE_TITLE" not in html
    assert "Fictional hard conflict" in html
    assert "The transfer cannot be made." in html
    assert "Choose a later route." in html
    assert 'class="check conflict"' in html


def test_module_entry_uses_committed_example(service, tmp_path):
    output = tmp_path / "module-site"
    database = tmp_path / "module.sqlite3"
    store = Store(database)
    run_demo(Tripwright(store), output=lambda *args: None)
    store.close()
    result = subprocess.run([sys.executable, "-m", "tripwright.site", str(database), "demo", str(output)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (output / "index.html").is_file()
    assert len(list(output.glob("20*.html"))) == len(service.state("demo")["plan"]["days"])


def test_invalid_and_revoked_links_are_404(service):
    links = ResultLinks()
    link = links.issue("demo")
    with TestClient(web_app(service, links)) as client:
        assert client.get("/r/invalid").status_code == 404
        assert client.get("/r/" + link).status_code == 200
        links.revoke(link)
        assert client.get("/r/" + link).status_code == 404


def test_link_expiry_and_live_mapping_revocation(service, monkeypatch):
    links = ResultLinks()
    link = links.issue("demo", ttl=10)
    monkeypatch.setattr("tripwright.adapters.web.time.time", lambda: 10**12)
    with TestClient(web_app(service, links)) as client:
        assert client.get("/r/" + link).status_code == 404
    mapping = {"independent-view-capability": "demo"}
    with TestClient(web_app(service, mapping)) as client:
        assert client.get("/r/independent-view-capability").status_code == 200
        del mapping["independent-view-capability"]
        assert client.get("/r/independent-view-capability").status_code == 404


def test_result_is_current_owner_only_and_strictly_read_only(service, monkeypatch):
    run_demo(service, owner="other", output=lambda *args: None)
    replace_title(service, "other", "OTHER_OWNER_PRIVATE_PLAN")
    service.turn("other", "OTHER_OWNER_PRIVATE_FIRST_RESPONSE", "other-response")
    check_job(service)
    service.jobs.run_once("demo")
    links = ResultLinks()
    link = links.issue("demo")
    tables = ("contexts", "versions", "inbox", "jobs", "drafts", "events", "profiles")
    before = {table: service.store.all(table, "demo") for table in tables}

    def reject_write(*args, **kwargs):
        pytest.fail("Result page attempted a write or marked a result retrieved")

    monkeypatch.setattr(service.store, "put", reject_write)
    monkeypatch.setattr(service, "result", reject_write)
    with TestClient(web_app(service, links, now=trip_time(service))) as client:
        response = client.get("/r/" + link)
        assert response.status_code == 200
        text = str(Text(response.text))
        assert f"Version {service.state('demo')['number']}" in text
        assert "First response material" in text
        assert "Second response: differences first" in text
        assert "Current version: today" in text
        assert "Complete candidate itinerary checked in this result" in text
        assert "OTHER_OWNER_PRIVATE" not in response.text
        assert response.headers["cache-control"] == "no-store, private"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert client.post("/r/" + link).status_code == 405
    assert {table: service.store.all(table, "demo") for table in tables} == before


def test_pending_second_response_and_mount(service):
    check_job(service)
    links = ResultLinks()
    link = links.issue("demo")
    app = Starlette(routes=[Mount("/r", app=web_app(service, links))])
    with TestClient(app) as client:
        response = client.get("/r/" + link)
        assert response.status_code == 200
        text = str(Text(response.text))
        assert "Verification pending" in text
        assert "Expected" in text
        assert "No completion time supplied" in text
        assert "Most recent available result (earlier job)" in text


def test_empty_identity_and_failed_job(service):
    draft = check_job(service)
    job = service.store.get("jobs", "demo", draft["job_id"])
    job.update(status="failed", error="internal error detail that must not appear")
    service.store.put("jobs", "demo", job)
    links = ResultLinks()
    with TestClient(web_app(service, links)) as client:
        response = client.get("/r/" + links.issue("demo"))
        assert "Verification failed" in response.text
        assert "internal error detail" not in response.text
        assert client.get("/r/" + links.issue("empty-owner")).status_code == 404


def test_stale_inbox_is_labelled(service):
    draft = check_job(service)
    service.jobs.run_once("demo")
    service.commit("demo", draft["draft_id"], "views-new-version", accept_unverified=True)
    links = ResultLinks()
    with TestClient(web_app(service, links)) as client:
        response = client.get("/r/" + links.issue("demo"))
    assert response.status_code == 200
    assert "This result is stale" in response.text


def test_pages_workflow_is_manual_and_homepage_has_local_commands():
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/pages.yml").read_text()
    assert "workflow_dispatch:" in workflow
    assert re.search(r"^\s+(push|pull_request|schedule):", workflow, re.M) is None
    assert "path: homepage" in workflow
    homepage = (root / "homepage/index.html").read_text()
    for command in ("uv sync --extra dev", "uv run tripwright demo", "uv run tripwright serve --stdio"):
        assert command in homepage
    assert "还没有可运行的版本" not in homepage
    assert "not usable yet" not in homepage


def memory_sample() -> dict:
    path = Path(__file__).resolve().parents[1] / "render/fixtures/memory.json"
    memory = json.loads(path.read_text(encoding="utf-8"))
    memory["entries"][2]["text"] = "Mira 的同行人：不爬山，喜欢书店"
    return memory


def test_memory_page_lists_long_term_content_with_status_and_origin(service, tmp_path):
    output = build_site(service, "demo", tmp_path / "site", now=trip_time(service), memory=memory_sample())
    html = (output / "memory.html").read_text()
    text = str(Text(html))
    for words in ("记忆（长期内容）", "Memory (long-term content)", "待确认", "waiting for confirmation", "已确认沿用", "这趟不用",
                  "「早上别排太早，九点前我起不来。」", "来自 2030-05-alpen 的 E-300513-b02（u1，05-13，逐字）", "最近用在 2030-05-alpen 05-14"):
        assert words in text
    assert text.index("偏好") < text.index("同行人") < text.index("常驻规矩") < text.index("资料")
    assert 'href="memory.html"' in (output / "index.html").read_text()
    assert "Mira" not in html  # the site's privacy replacement covers the memory page too


def test_site_without_memory_has_no_memory_page(service, tmp_path):
    output = build_site(service, "demo", tmp_path / "site", now=trip_time(service))
    assert not (output / "memory.html").exists() and 'href="memory.html"' not in (output / "index.html").read_text()


def result_text(service):
    links = ResultLinks()
    with TestClient(web_app(service, links, now=trip_time(service))) as client:
        response = client.get("/r/" + links.issue("demo"))
    assert response.status_code == 200
    return response.text


def test_result_page_shows_the_pack_the_host_got_and_the_repository_day_view(service):
    service.turn("demo", "What do we do this afternoon?", "result-pack", scenario="quick")
    html = result_text(service)
    text = str(Text(html))
    latest = max(service.store.all("contexts", "demo"), key=lambda item: (item["created_at"], item["id"]))
    assert "Answer outline: quick" in text and "Constraint pack given to the host" in text
    assert f"约束包 {latest['id']}｜快问" in text and latest["pack"]["required"]["items"]["ids"][0] in text
    assert "写法" not in text.split("Constraint pack given to the host")[1].split("Second response")[0]  # no writing rules
    day = service.state("demo", view="day", date=service.state("demo")["plan"]["days"][0]["date"])["text"]
    for line in day.splitlines()[1:3]:
        assert line.lstrip("# ") in text
    assert "Google 地图搜 Example Oslo Gallery" in text
    assert 'href="https://www.google.com/maps/search/?api=1&amp;query=Example%20Oslo%20Gallery"' in html


def test_result_page_without_a_stored_pack_says_so(service):
    for context in service.store.all("contexts", "demo"):
        context.pop("pack", None)
        service.store.put("contexts", "demo", context)
    assert "This material has no stored pack" in str(Text(result_text(service)))


def test_view_text_is_escaped_and_only_https_links_become_anchors():
    from tripwright.site import _text_html

    html = str(_text_html(["<!-- 由 tripwright 生成 -->", "# 07-15 <b>", "<script>alert(1)</script>",
                           "坏链接 [x](javascript:alert(1))", "- 好链接 [地图](https://example.com/a?b=1&c=2)",
                           "[<i>名</i>](https://example.com/\"onmouseover=x)"]))
    assert "<script>" not in html and "&lt;script&gt;" in html and "由 tripwright 生成" not in html
    assert 'href="javascript' not in html and "<h3>07-15 &lt;b&gt;</h3>" in html
    assert '<li>好链接 <a href="https://example.com/a?b=1&amp;c=2">地图</a></li>' in html
    assert "<i>" not in html and '<a href="https://example.com/&#34;onmouseover=x">&lt;i&gt;名&lt;/i&gt;</a>' in html


def test_the_old_answer_skeleton_and_day_text_are_gone():
    import importlib.util

    assert importlib.util.find_spec("tripwright.views") is None
