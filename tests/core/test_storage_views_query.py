import asyncio
from datetime import datetime, timezone

import pytest
import yaml

from tripwright.demo import run_demo
from tripwright.providers.base import ProviderUnavailable
from tripwright.query import QueryRouter
from tripwright.service import Tripwright
from tripwright.storage import Store
from core_helpers import package


def test_directory_export_import_preserves_current_view_and_audit(seeded, item, tmp_path):
    seeded.ledger.apply("alice", {"items": [item]})
    directory = seeded.store.export("alice", tmp_path / "export")
    assert (directory / "trip.yaml").exists()
    assert (directory / "days/2030-06-10.yaml").exists()
    exported_day = yaml.safe_load((directory / "days/2030-06-10.yaml").read_text())
    assert exported_day["verification"]["version"] == 1
    assert exported_day["verification"]["checks"]
    assert (directory / "decisions.md").exists()
    other_store = Store(tmp_path / "restored.sqlite3")
    other_store.import_directory("alice", directory)
    restored = Tripwright(other_store)
    assert restored.ledger.current("alice") == seeded.ledger.current("alice")
    assert restored.state("alice") == seeded.state("alice")
    assert restored.store.all("events", "alice") == seeded.store.all("events", "alice")
    with pytest.raises(ValueError, match="empty"):
        other_store.import_directory("alice", directory)
    assert other_store.all("events", "bob") == []
    other_store.close()


def test_import_detects_edited_visible_day_before_writing_anything(seeded, tmp_path):
    directory = seeded.store.export("alice", tmp_path / "export")
    day_path = directory / "days/2030-06-10.yaml"
    day = yaml.safe_load(day_path.read_text())
    day["title"] = "Edited"
    day_path.write_text(yaml.safe_dump(day))
    with pytest.raises(ValueError, match="differs"):
        seeded.store.import_directory("bob", directory)
    assert seeded.store.all("versions", "bob") == []


def test_import_rejects_forged_export_check_conclusions(seeded, tmp_path):
    directory = seeded.store.export("alice", tmp_path / "export")
    day_path = directory / "days/2030-06-10.yaml"
    day = yaml.safe_load(day_path.read_text())
    day["verification"]["checks"][0]["status"] = "unverified"
    day_path.write_text(yaml.safe_dump(day))
    with pytest.raises(ValueError, match="verification view differs"):
        seeded.store.import_directory("bob", directory)
    assert seeded.store.all("versions", "bob") == []


def test_plain_text_day_view_is_derived_from_committed_version(seeded):
    # The day view is block B's (render.screens.day_view): the version in the base line, each local place name on its
    # own copyable line followed by its explanation and the app search.
    lines = seeded.state("alice", "day", "2030-06-10")["text"].splitlines()
    assert any(line.startswith("基于：V1") for line in lines)
    place = seeded.state("alice")["plan"]["places"][0]
    position = lines.index(place["name_local"])
    assert f"Google 地图搜 {place['name_local']}" in lines[position + 1] and place["name"] in lines[position + 1]
    with pytest.raises(ValueError, match="Unknown trip date"):
        seeded.state("alice", "day", "2030-06-15")


def test_one_off_query_does_not_create_items(seeded):
    turn = seeded.turn("alice", "Where is that station?", "quick", scenario="quick")
    result = asyncio.run(seeded.query("alice", "transit", {"action": "stops", "text": "Example station"}))
    assert result["status"] == "unverified"
    assert result["evidence"]["source"].startswith("demo:")
    assert seeded.store.all("items", "alice") == []
    assert package(seeded, turn)["items"] == [] and turn["pack"]["meta"]["required"]["items"]["ids"] == []


def test_provider_failure_is_an_unverified_result(service):
    class FailingTransit:
        async def stops(self, text, *, limit=8):
            raise ProviderUnavailable("fixture", "Timed out upstream")

    router = QueryRouter(service.store, transit=FailingTransit())
    result = asyncio.run(router.query("alice", "transit", {"action": "stops", "text": "station"}, None))
    assert result["status"] == "unverified"
    assert result["reason"] == "Timed out upstream"
    assert result["consequence"] and result["alternative"]
    assert service.store.all("evidence", "alice") == []


def test_provider_timeout_is_bounded_and_remains_unverified(service):
    class SlowTransit:
        async def stops(self, text, *, limit=8):
            await asyncio.sleep(60)

    service.query_router = QueryRouter(service.store, transit=SlowTransit())
    service.query_timeout = 0.01
    result = asyncio.run(service.query("alice", "transit", {"action": "stops", "text": "station"}))
    assert result["status"] == "unverified"
    assert "time budget" in result["reason"]
    assert service.store.all("evidence", "alice") == []


def test_demo_runs_all_steps_and_preserves_soft_strength_and_reason(service):
    output = []
    final = run_demo(service, output=output.append)
    assert final["number"] == 3
    assert any(line.startswith("8.") for line in output)
    assert any("Rejected" in line for line in output)
    items = service.ledger.current("demo")["items"]
    assert any(item["strength"] == "optional" for item in items)
    assert any(item["reason"] for item in items)


def test_explain_includes_original_sources_and_version_usage(seeded, item):
    turn = seeded.turn("alice", "Keep my preference", "explain")
    draft = seeded.propose("alice", ctx_id=turn["ctx_id"], base_version=1, changes={"items": [item]}, idempotency_key="pref")
    seeded.commit("alice", draft["draft_id"], "pref")
    explanation = seeded.explain("alice", version=2)
    assert explanation["journal"][0]["source_event"] == item["source"]["event"]
    assert explanation["version_usage"][0]["version"] == 2
    assert "Dependencies" in explanation["note"]
