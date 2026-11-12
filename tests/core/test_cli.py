from tripwright.cli import main
from tripwright.service import Tripwright
from tripwright.storage import Store


def test_cli_export_and_import_round_trip(seeded, tmp_path, capsys):
    destination = tmp_path / "export"
    restored_db = tmp_path / "import.sqlite3"
    assert main(["--db", str(tmp_path / "trip.sqlite3"), "--local-user", "alice", "export", str(destination)]) == 0
    assert main(["--db", str(restored_db), "--local-user", "alice", "import", str(destination)]) == 0
    restored = Store(restored_db)
    try:
        # flow is each service instance's reminder state, not trip data
        assert {**Tripwright(restored).state("alice"), "flow": None} == {**seeded.state("alice"), "flow": None}
    finally:
        restored.close()
