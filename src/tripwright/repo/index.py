"""Build an index generation from repository files and swap it in atomically (CONTRACTS 1.3).

The index holds the current trip's records and the profile defaults, exactly as the files say. A generation is built
beside the live tables (in a separate in-memory store), operations not yet in the canonical branch are replayed on it,
and only then does one transaction replace the live tables, so a reader never sees a head without its replayed writes.
"""
from __future__ import annotations

from tripwright.repo import layout
from tripwright.storage import Store

CONFIG = "tripwright.yaml"
CONVERSATION = "profile/conversation.yaml"
TRIP_TABLES = ("events", "items", "facts", "decisions", "pending_checks", "undecided", "journal", "versions", "evidence", "carry")
INDEX_TABLES = TRIP_TABLES + ("profiles",)


def text_files(files: dict[str, bytes]) -> dict[str, str]:
    return {path: data.decode("utf-8") if isinstance(data, bytes) else data for path, data in files.items()}


def config_of(files: dict[str, str]) -> dict:
    if CONFIG not in files:
        raise ValueError(f"{CONFIG} is missing: run `tripwright init` in this folder")
    config = layout.parse(CONFIG, files[CONFIG]) or {}
    if config.get("format") != layout.FORMAT:
        raise ValueError(f"{CONFIG}: unsupported repository format {config.get('format')!r}")
    return config


def trip_part(files: dict[str, str], trip_id: str) -> dict[str, str]:
    prefix = layout.trip_folder(trip_id) + "/"
    return {path[len(prefix):]: text for path, text in files.items() if path.startswith(prefix)}


def generation(files: dict[str, str], *, plans: dict[str, dict] | None = None) -> dict:
    """`{config, trip, tables, problems, manual_plan}` for the repository files given (path → text)."""
    config = config_of(files)
    trip = config.get("current_trip")
    if trip:
        loaded = layout.trip_tables(trip_part(files, trip), plans=plans)
    else:
        loaded = dict(tables={name: [] for name in TRIP_TABLES}, problems=[], manual_plan=None)
    tables = dict(loaded["tables"])
    tables["profiles"] = []
    if CONVERSATION in files:
        try:
            tables["profiles"] = [layout.parse(CONVERSATION, files[CONVERSATION])]
        except ValueError as error:
            loaded["problems"].append(str(error))
    return dict(config=config, trip=trip, tables=tables, problems=loaded["problems"], manual_plan=loaded["manual_plan"])


def scratch(tables: dict[str, list[dict]], owner: str, runtime: dict[str, list[dict]] | None = None) -> Store:
    """An in-memory store holding one generation, where operations can be replayed before the swap."""
    store = Store(":memory:")
    with store.transaction():
        for table, records in {**tables, **(runtime or {})}.items():
            for record in records:
                store.put(table, owner, record, insert=True)
    return store


def tables_of(store: Store, owner: str) -> dict[str, list[dict]]:
    return {table: store.all(table, owner) for table in INDEX_TABLES}


def kept(previous: dict[str, list[dict]], fresh: dict[str, list[dict]], problems: list[str]) -> dict[str, list[dict]]:
    """A record file that no longer parses keeps its previous valid content in the index (design 5.6)."""
    broken = {problem.split(":", 1)[0].rsplit("/", 1)[-1].rsplit(".", 1)[0] for problem in problems}
    merged = {}
    for table, records in fresh.items():
        present = {record["id"] for record in records}
        merged[table] = records + [record for record in previous.get(table, []) if record["id"] in broken and record["id"] not in present]
    return merged
