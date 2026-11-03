"""s4b: the tool annotations say what each tool does. ChatGPT reads `readOnlyHint` (a tool without it is a write, and Pro
offers only reads), so a tool marked read-only must change nothing, and a tool that writes must not be marked so.

Each tool is called through the MCP server on a trip repository with a remote; a call writes when the index database,
the working tree, HEAD or the remote's main differ afterwards (after the committer flushed)."""
from __future__ import annotations

import asyncio
import hashlib
import sqlite3

import pytest
from repo_fixtures import Writer, bare, git, new_repository, seeded

from tripwright.adapters.mcp import create_server

WHEN = "2030-06-10T10:00:00+02:00"
EXPECTED = {  # readOnly, destructive, idempotent, openWorld
    "state": (True, False, None, False),
    "explain": (True, False, None, False),
    "turn": (False, False, True, False),
    "query": (False, False, False, True),
    "propose": (False, False, True, False),
    "commit": (False, True, True, False),
    "result": (False, True, True, False),
}


def snapshot(writer: Writer, remote) -> dict:
    connection = sqlite3.connect(writer.repository.state_path)
    try:
        database = hashlib.sha256("\n".join(connection.iterdump()).encode()).hexdigest()
    finally:
        connection.close()
    files = sorted(path for path in writer.root.rglob("*") if path.is_file() and ".git" not in path.parts
                   and path.name != "state.sqlite" and not path.name.startswith("state.sqlite"))
    tree = hashlib.sha256(b"".join(str(path.relative_to(writer.root)).encode() + path.read_bytes() for path in files)).hexdigest()
    return dict(database=database, tree=tree, head=git(writer.root, "rev-parse", "HEAD"), status=git(writer.root, "status", "--porcelain"),
                remote=git(remote, "rev-parse", "main"))


def wire(server) -> dict[str, dict]:
    """The annotations as a client reads them (camelCase, unset hints left out)."""
    return {tool.name: tool.annotations.model_dump(by_alias=True, exclude_none=True) for tool in asyncio.run(server.list_tools())}


@pytest.fixture
def trip(tmp_path):
    remote = bare(tmp_path / "remote.git")
    writer = Writer(new_repository(tmp_path / "trip", remote=remote))
    seeded(writer, days=2)
    yield writer, remote
    writer.close()


def calls(writer: Writer) -> dict[str, list]:
    """Representative calls per tool, every state view included; each builds its arguments when it runs."""
    service, owner = writer.service, writer.owner
    asked = {}

    def turn_arguments(key):
        return dict(text="Where do we go after lunch?", request_id=key)

    def draft_arguments():
        turned = service.turn(owner, "Rename the first day.", "s4b-draft")
        plan = dict(service.state(owner)["plan"])
        plan["days"] = [dict(plan["days"][0], title="Slow first day"), *plan["days"][1:]]
        asked["draft"] = service.propose(owner, ctx_id=turned["ctx_id"], base_version=turned["base_version"],
                                         idempotency_key="s4b-draft", plan=plan)
        return dict(ctx_id=turned["ctx_id"], base_version=turned["base_version"], idempotency_key="s4b-propose", plan=plan)

    def job_arguments():
        job = service.jobs.enqueue(owner, "plan_day", dict(day={}, timeout=1), service.versions.current(owner)["number"], "s4b-job")
        return dict(job_id=job["id"], cancel=True)

    views = ["current", "ledger", "decisions", "history", "profile", "memory", "sync", "export"]
    return {
        "state": [lambda view=view: dict(view=view) for view in views] + [lambda: dict(view="day", date="2030-06-10")],
        "explain": [lambda: {}, lambda: dict(version=1)],
        "turn": [lambda: turn_arguments("s4b-turn")],
        "query": [lambda: dict(kind="transit", arguments=dict(origin="oslo-art", destination="oslo-station", when=WHEN)),
                  lambda: dict(kind="geocode", arguments=dict(text="Oslo"))],
        "propose": [draft_arguments],
        "commit": [lambda: dict(draft_id=asked["draft"]["draft_id"], idempotency_key="s4b-commit", accept_unverified=True)],
        "result": [job_arguments],
    }


def test_a_tool_marked_read_only_changes_nothing_and_a_tool_that_writes_is_not_marked_read_only(trip):
    writer, remote = trip
    server = create_server(writer.service, manage_jobs=False)
    annotations = wire(server)
    assert set(annotations) == set(EXPECTED)
    wrote = {}
    for name, arguments_list in calls(writer).items():
        for arguments in arguments_list:
            built = arguments()
            writer.service.committer.flush()
            before = snapshot(writer, remote)
            answer = asyncio.run(server.call_tool(name, built))
            assert not getattr(answer, "is_error", False), (name, built, answer)
            writer.service.committer.flush()
            changed = [key for key, value in snapshot(writer, remote).items() if before[key] != value]
            wrote.setdefault(name, []).append((built.get("view") or built.get("kind"), changed))
    for name, results in wrote.items():
        writes = any(changed for _, changed in results)
        assert annotations[name]["readOnlyHint"] is (not writes), (name, results)
    # a lookup is committed and pushed: it is a write that reaches the remote, not only the local index
    assert all("remote" in changed for _, changed in wrote["query"]), wrote["query"]
    assert all(not changed for _, changed in wrote["state"] + wrote["explain"]), wrote


def test_the_annotations_are_the_table_in_the_reply():
    from tripwright.service import Tripwright
    from tripwright.storage import Store

    store = Store(":memory:")
    try:
        server = create_server(Tripwright(store), manage_jobs=False)
        tools = wire(server)
    finally:
        store.close()
    found = {name: tuple(hint.get(key) for key in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"))
             for name, hint in tools.items()}
    assert found == EXPECTED
    assert sorted(name for name, hint in found.items() if hint[0]) == ["explain", "state"]
