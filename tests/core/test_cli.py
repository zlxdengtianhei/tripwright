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
        assert Tripwright(restored).state("alice") == seeded.state("alice")
    finally:
        restored.close()


def test_http_cli_refuses_missing_bearer_mapping(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TRIPWRIGHT_TOKENS", raising=False)
    assert main(["--db", str(tmp_path / "trip.sqlite3"), "serve", "--http"]) == 1
    assert "token-to-user" in capsys.readouterr().err
