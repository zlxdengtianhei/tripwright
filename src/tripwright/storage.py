from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml


TABLES = (
    "events", "items", "facts", "decisions", "pending_checks", "undecided",
    "journal", "versions", "evidence", "jobs", "inbox", "profiles", "contexts", "drafts",
    "carry", "outbox", "notices", "repo",
)


# A pinned clock for replays and tests (A7): TRIPWRIGHT_NOW is the moment (ISO with offset) the clock shows when it is
# first read, or at TRIPWRIGHT_NOW_SET_AT (Unix seconds) when that is set; from there it runs in real time, so a later
# message is still later. Every "now" of the service reads it: turn, packs, lookups, check_answer, evidence times.
_PINNED_FROM: dict[str, float] = {}


def pinned() -> datetime | None:
    value = os.environ.get("TRIPWRIGHT_NOW")
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            raise ValueError
        since = float(os.environ["TRIPWRIGHT_NOW_SET_AT"]) if os.environ.get("TRIPWRIGHT_NOW_SET_AT") else _PINNED_FROM.setdefault(value, time.time())
    except ValueError:
        raise ValueError(f"TRIPWRIGHT_NOW must be an ISO time with offset (and TRIPWRIGHT_NOW_SET_AT Unix seconds), got {value!r}") from None
    return (moment + timedelta(seconds=time.time() - since)).astimezone(timezone.utc)


def clock() -> datetime:
    """The service's current moment (UTC): the pinned clock when TRIPWRIGHT_NOW is set, else the system clock."""
    return pinned() or datetime.now(timezone.utc)


def pinned_since() -> float | None:
    """The wall time (Unix seconds) the pinned clock began to run from its moment, or None without a pin. The pinned
    clock and the wall clock keep step only from then: a wall time before it has no place on the pinned clock (S4h-a)."""
    value = os.environ.get("TRIPWRIGHT_NOW")
    if not value or pinned() is None:
        return None
    return float(os.environ["TRIPWRIGHT_NOW_SET_AT"]) if os.environ.get("TRIPWRIGHT_NOW_SET_AT") else _PINNED_FROM[value]


def now() -> str:
    return clock().isoformat()


_BASE36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def identifier(prefix: str) -> str:
    """Type, UTC creation date and six random base-36 characters (`I-301031-k3f9qa`): readable, sortable by day, and two
    writers that create records offline on the same day collide with probability about n²/4·10⁹ per type."""
    return f"{prefix}-{clock():%y%m%d}-{''.join(secrets.choice(_BASE36) for _ in range(6))}"


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def day_document(version: dict, day: dict) -> dict:
    checks = [check for check in version.get("checks", {}).get("checks", [])
              if check.get("details", {}).get("date") in (None, day["date"])]
    return dict(day, verification=dict(version=version["number"], checked_at=version.get("checks", {}).get("checked_at"), checks=checks))


def answers_path(repo: str | Path | None = None, db: str | Path | None = None) -> Path | None:
    """Where the second parts that passed check_answer are kept for the Stop hook (A7): beside the repository's index,
    or beside a single database; None for an in-memory store. The hook reads this file without opening the trip."""
    if repo:
        return Path(repo).expanduser().resolve() / ".tripwright" / "answers.json"
    if db and str(db) != ":memory:":
        path = Path(db).expanduser()
        return path.with_name(path.stem + ".answers.json")
    return None


def remember_answer(path: Path | None, request_id: str | None, text: str, digest: str, at: str, *, zone: str | None = None,
                    keep: int = 50) -> bool:
    """Record a second part that passed check_answer: its hash and full text, the service's moment and the wall clock
    (the hook compares the wall clock with the transcript's), and the trip's zone it was checked in (the hooks read
    the clock there). The latest `keep` entries stay; written atomically."""
    if path is None:
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        data = {}
    answers = data.get("answers") if isinstance(data.get("answers"), dict) else {}
    answers[request_id or ""] = dict(request_id=request_id, sha256=digest, text=text, at=at, wall=time.time())
    answers = dict(sorted(answers.items(), key=lambda item: item[1].get("wall", 0))[-keep:])
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(path.name + f".{os.getpid()}.tmp")
    zone = zone or (data.get("zone") if isinstance(data.get("zone"), str) else None)
    scratch.write_text(json.dumps(dict(answers=answers, **({"zone": zone} if zone else {})), ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(scratch, path)
    return True


class Store:
    def __init__(self, path: str | Path = ":memory:", *, read_only: bool = False):
        """`read_only` opens an existing database for reading only (`tripwright status`, s4b): no tables are created
        and any write fails; SQLite may still leave its -wal and -shm files beside it."""
        self.path = str(path)
        if read_only:
            self.connection = sqlite3.connect(Path(path).expanduser().resolve().as_uri() + "?mode=ro", uri=True,
                                              check_same_thread=False, isolation_level=None)
        else:
            if str(path) != ":memory:":
                Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(str(Path(path).expanduser()), check_same_thread=False, isolation_level=None)
        self.lock = threading.RLock()
        # Bumped by every write through this object; with SQLite's data_version (other connections' commits) it lets a
        # background loop see "nothing changed" without decoding a table.
        self.generation = 0
        self.connection.execute("PRAGMA busy_timeout=5000")
        if read_only:
            return
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        for table in TABLES:
            self.connection.execute(
                f"CREATE TABLE IF NOT EXISTS {table} (id TEXT NOT NULL, owner TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(owner,id))"
            )
        for action in ("UPDATE", "DELETE"):
            self.connection.execute(
                f"CREATE TRIGGER IF NOT EXISTS events_no_{action.lower()} BEFORE {action} ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END"
            )
        # Open writes are found by state without reading the whole outbox (it keeps every write ever made).
        self.connection.execute("CREATE INDEX IF NOT EXISTS outbox_state ON outbox(owner, json_extract(payload, '$.state'))")

    @contextmanager
    def transaction(self):
        with self.lock:
            nested = self.connection.in_transaction
            if not nested:
                self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self
            except BaseException:
                if not nested:
                    self.connection.rollback()
                raise
            else:
                if not nested:
                    self.connection.commit()

    def _table(self, table: str):
        if table not in TABLES:
            raise ValueError("Unknown storage table")

    @contextmanager
    def attempt(self):
        """A write undone as a whole when it fails, also inside an open transaction (whose nesting is flat): a turn's
        record the ledger refuses leaves nothing half-written while the message itself is kept (S5)."""
        with self.transaction():
            self.connection.execute("SAVEPOINT attempt")
            try:
                yield
            except BaseException:
                self.connection.execute("ROLLBACK TO attempt")
                self.connection.execute("RELEASE attempt")
                raise
            self.connection.execute("RELEASE attempt")

    @contextmanager
    def preview(self):
        with self.transaction():
            self.connection.execute("SAVEPOINT preview_changes")
            try:
                yield
            finally:
                self.connection.execute("ROLLBACK TO preview_changes")
                self.connection.execute("RELEASE preview_changes")

    def all(self, table: str, owner: str) -> list[dict]:
        self._table(table)
        with self.lock:
            return [json.loads(row[0]) for row in self.connection.execute(
                f"SELECT payload FROM {table} WHERE owner=? ORDER BY rowid", (owner,)
            )]

    def where(self, table: str, owner: str, field: str, values) -> list[dict]:
        """Records whose `field` (dotted for a nested one) is one of `values`, in insertion order, decoding only those;
        an index on the field is used when one exists."""
        self._table(table)
        if not all(part.isidentifier() for part in field.split(".")):
            raise ValueError("Unknown storage field")
        values = list(values)
        marks = ",".join("?" * len(values))
        with self.lock:
            return [json.loads(row[0]) for row in self.connection.execute(
                f"SELECT payload FROM {table} WHERE owner=? AND json_extract(payload, '$.{field}') IN ({marks}) ORDER BY rowid",
                (owner, *values))]

    def changes(self) -> tuple[int, int]:
        """A value that differs whenever this object or another connection has written since it was last read."""
        with self.lock:
            return self.generation, self.connection.execute("PRAGMA data_version").fetchone()[0]

    def get(self, table: str, owner: str, record_id: str) -> dict:
        self._table(table)
        with self.lock:
            row = self.connection.execute(
                f"SELECT payload FROM {table} WHERE owner=? AND id=?", (owner, record_id)
            ).fetchone()
        if row is None:
            raise ValueError(f"Unknown {table} handle")
        return json.loads(row[0])

    def put(self, table: str, owner: str, record: dict, *, insert: bool = False):
        self._table(table)
        statement = f"INSERT INTO {table}(owner,id,payload) VALUES(?,?,?)"
        if not insert:
            if table == "events":
                raise ValueError("Events can only be appended")
            statement += " ON CONFLICT(owner,id) DO UPDATE SET payload=excluded.payload"
        with self.lock:
            self.generation += 1
            self.connection.execute(statement, (owner, record["id"], encode(record)))

    def replace(self, owner: str, tables: dict[str, list[dict]]):
        """Swap whole tables of one owner for a rebuilt generation, in one transaction: a reader sees the old generation
        or the new one, never a mix. Messages stay append-only for every other writer; only this swap may remove them."""
        with self.transaction():
            self.generation += 1
            for table, records in tables.items():
                self._table(table)
                if table == "events":
                    self.connection.execute("DROP TRIGGER IF EXISTS events_no_delete")
                self.connection.execute(f"DELETE FROM {table} WHERE owner=?", (owner,))
                if table == "events":
                    self.connection.execute(
                        "CREATE TRIGGER events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END")
                for record in records:
                    self.connection.execute(f"INSERT INTO {table}(owner,id,payload) VALUES(?,?,?)", (owner, record["id"], encode(record)))

    def owners(self, table: str) -> list[str]:
        self._table(table)
        with self.lock:
            return [row[0] for row in self.connection.execute(f"SELECT DISTINCT owner FROM {table}")]

    def close(self):
        with self.lock:
            self.connection.close()

    def export(self, owner: str, destination: str | Path) -> Path:
        destination = Path(destination)
        if destination.exists() and any(destination.iterdir()):
            raise ValueError("Export destination must be empty")
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "ledger").mkdir(exist_ok=True)
        (destination / "days").mkdir(exist_ok=True)
        with self.transaction():
            snapshot = {table: self.all(table, owner) for table in TABLES}
        versions = snapshot["versions"]
        current = max(versions, key=lambda version: version["number"], default={})
        plan = current.get("plan", {})
        metadata = {key: value for key, value in plan.items() if key not in ("days", "places", "bookings")}
        metadata["export_format"] = 1
        metadata["version"] = current.get("number", 0)
        (destination / "trip.yaml").write_text(yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False))
        for name in ("places", "bookings"):
            (destination / f"{name}.yaml").write_text(yaml.safe_dump(plan.get(name, []), allow_unicode=True))
        for day in plan.get("days", []):
            (destination / "days" / f"{day['date']}.yaml").write_text(yaml.safe_dump(day_document(current, day), allow_unicode=True, sort_keys=False))
        for table, records in snapshot.items():
            (destination / "ledger" / f"{table}.jsonl").write_text("".join(encode(record) + "\n" for record in records))
        explanations = [f"# Decisions — version {metadata['version']}\n"]
        for decision in snapshot["decisions"]:
            explanations.append(
                f"\n## {decision['id']}: {decision['question']}\n"
                f"Options: {encode(decision['options'])}\n\nChoice: {decision['choice']}\n\n"
                f"By: {decision['decided_by']}\n\nReason: {decision.get('reason') or 'Unknown'}\n\n"
                f"Revisit when: {decision.get('revisit_when') or 'Unknown'}\n"
            )
        (destination / "decisions.md").write_text("".join(explanations))
        return destination

    def import_directory(self, owner: str, source: str | Path):
        from tripwright.trip import Trip

        source = Path(source)
        metadata = yaml.safe_load((source / "trip.yaml").read_text())
        if metadata.get("export_format") != 1:
            raise ValueError("Unsupported export format")
        snapshot = {}
        for table in TABLES:
            snapshot[table] = [json.loads(line) for line in (source / "ledger" / f"{table}.jsonl").read_text().splitlines() if line]
        current = max(snapshot["versions"], key=lambda version: version["number"], default={})
        if current:
            visible = {key: value for key, value in metadata.items() if key not in ("version", "export_format")}
            visible["places"] = yaml.safe_load((source / "places.yaml").read_text())
            visible["bookings"] = yaml.safe_load((source / "bookings.yaml").read_text())
            visible["days"] = []
            for path in sorted((source / "days").glob("*.yaml")):
                day = yaml.safe_load(path.read_text())
                verification = day.pop("verification", None)
                if verification != day_document(current, day)["verification"]:
                    raise ValueError("Export verification view differs from its version snapshot")
                visible["days"].append(day)
            normalized = Trip.model_validate(visible).model_dump(mode="json")
            if normalized != Trip.model_validate(current["plan"]).model_dump(mode="json"):
                raise ValueError("Export view differs from its version snapshot")
            for version in snapshot["versions"]:
                Trip.model_validate(version["plan"])
        with self.transaction():
            if any(self.all(table, owner) for table in TABLES):
                raise ValueError("Import requires an empty user store")
            for table, records in snapshot.items():
                for record in records:
                    if table == "jobs" and record["status"] == "running":
                        record.update(status="queued", lease_until=0, lease_token=None)
                    self.put(table, owner, record, insert=True)
