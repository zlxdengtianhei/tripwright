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
