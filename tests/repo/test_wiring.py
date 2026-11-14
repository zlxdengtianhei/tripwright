"""Promise 7: the CLI and the MCP server run on a trip repository; the server uses block C's instructions and role
instructions; `check` compares regenerated with committed files."""
from __future__ import annotations

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client

from repo_fixtures import BY_HAND, TRIP, Writer, bare, git, new_repository, plan, seeded
from tripwright.adapters.mcp import create_server
from tripwright.agents import server_instructions
from tripwright.cli import main
from tripwright.repo import layout


def test_cli_init_sync_status_and_check(tmp_path, capsys):
    remote = bare(tmp_path / "remote.git")
    folder = tmp_path / "my-travel"
    assert main(["init", str(folder), "--trip", TRIP, "--timezone", "Europe/Oslo", "--remote", str(remote)]) == 0
    assert main(["init", str(folder)]) == 1 and "already a Git repository" in capsys.readouterr().err
    writer = Writer(folder)
    seeded(writer)
    writer.turn("No luggage.", "fact")
    writer.close()
    assert main(["--repo", str(folder), "sync"]) == 0
    assert '"unsynced": 0' in capsys.readouterr().out
    assert main(["--repo", str(folder), "status"]) == 0 and '"canonical": "origin/main"' in capsys.readouterr().out
    assert main(["--repo", str(folder), "check"]) == 0 and '"ok": true' in capsys.readouterr().out
    assert git(folder, "rev-parse", "HEAD") == git(remote, "rev-parse", "main")


def test_check_reports_a_generated_file_that_differs_from_its_regeneration(tmp_path, capsys):
    writer = Writer(new_repository(tmp_path / "repo"))
    seeded(writer)
    writer.close()
    view = tmp_path / "repo" / layout.trip_folder(TRIP) / "views/today.md"
    view.write_text(view.read_text() + "hand note\n")
    git(tmp_path / "repo", *BY_HAND, "commit", "-qam", "hand edit committed outside the tool")
    assert main(["--repo", str(tmp_path / "repo"), "check"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert any(problem.startswith(f"{layout.trip_folder(TRIP)}/views/today.md: differs") for problem in report["problems"])
    (tmp_path / "repo" / "AGENTS.md").write_text("my own rules\n")
    git(tmp_path / "repo", *BY_HAND, "commit", "-qam", "changed agents file")
    main(["--repo", str(tmp_path / "repo"), "check"])
    assert any(reminder.startswith("AGENTS.md: differs") for reminder in json.loads(capsys.readouterr().out)["reminders"])


def test_commands_that_need_a_repository_say_so(tmp_path, capsys):
    assert main(["--db", str(tmp_path / "x.sqlite3"), "sync"]) == 1 and "needs --repo" in capsys.readouterr().err
    assert main(["--repo", str(tmp_path / "missing"), "status"]) == 1 and "not a Git repository" in capsys.readouterr().err


def test_cli_agents_hands_its_arguments_to_block_cs_command(monkeypatch):
    import tripwright.agents.__main__ as agents

    seen = []
    monkeypatch.setattr(agents, "main", lambda arguments: seen.append(arguments) or 7)
    assert main(["agents", "build", "--check"]) == 7 and seen == [["build", "--check"]]


def test_init_from_a_database_writes_one_commit_per_version(tmp_path, capsys):
    from tripwright.service import Tripwright
    from tripwright.storage import Store

    store = Store(tmp_path / "single.sqlite3")
    service = Tripwright(store)
    turn = service.turn("alice", "Create", "seed")
    service.commit("alice", service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=plan(), idempotency_key="seed")["draft_id"],
                   "seed", accept_unverified=True)
    service.jobs.stop()
    store.close()
    assert main(["--local-user", "alice", "init", str(tmp_path / "repo"), "--from-db", str(tmp_path / "single.sqlite3")]) == 0
    assert git(tmp_path / "repo", "log", "--format=%B").count("Tripwright-Version:") == 1


def test_mcp_server_uses_instructions_and_the_new_parameters_without_prompts(tmp_path):
    writer = Writer(new_repository(tmp_path / "repo"))
    try:
        server = create_server(writer.service, manage_jobs=False)
        assert server.instructions == server_instructions()
        assert not asyncio.run(server.list_prompts())
        assert not asyncio.run(server.list_resources())
        tools = {tool.name: tool.model_dump(by_alias=True)["inputSchema"] for tool in asyncio.run(server.list_tools())}
        assert len(tools) == 7
        assert {"speaker", "record", "new_trip"} <= set(tools["turn"]["properties"])
        assert "ack_events" in tools["result"]["properties"] and "draft_id" in tools["state"]["properties"]
    finally:
        writer.close()


async def stdio_round(folder) -> dict:
    # the cautious mode, which waits for the user's answer, is no longer the default (2026-11-14): this round selects it
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "tripwright.cli", "--repo", str(folder), "serve", "--stdio"],
                                       env=dict(get_default_environment(), TRIPWRIGHT_SAVE_UNVERIFIED="ask"))
    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()

            async def call(tool, arguments):
                response = await session.call_tool(tool, arguments)
                assert not response.is_error, response.content
                return response.structured_content

            turn = await call("turn", dict(text="创建行程。", request_id="seed"))
            draft = await call("propose", dict(ctx_id=turn["ctx_id"], base_version=0, plan=plan(), idempotency_key="seed"))
            # a new plan's facts are all unverified: the commit waits for the user's answer (2026-11-12)
            refused = await session.call_tool("commit", dict(draft_id=draft["draft_id"], idempotency_key="seed", accept_unverified=True))
            assert refused.is_error and "Not saved yet" in refused.content[0].text and draft["ask_user"]["unverified"]
            answer = await call("turn", dict(text="可以，照这样存。", request_id="seed-yes"))
            assert answer["waiting_draft"]["draft_id"] == draft["draft_id"] and answer["waiting_draft"]["stale"] is False
            committed = await call("commit", dict(draft_id=draft["draft_id"], idempotency_key="seed", accept_unverified=True))
            verify = await call("state", dict(view="pack", draft_id=draft["draft_id"]))
            memory = await call("state", dict(view="memory"))
            return dict(turn=turn, committed=committed, verify=verify, memory=memory)


def test_a_round_over_real_stdio_on_a_repository_reaches_the_remote(tmp_path):
    remote = bare(tmp_path / "remote.git")
    folder = new_repository(tmp_path / "repo", remote=remote)
    result = asyncio.run(stdio_round(folder))
    title = result["turn"]["pack"]["markdown"].splitlines()[0]
    assert result["turn"]["write"]["state"] == "received" and title.startswith("# 现在 ") and "｜约束包 CTX-" in title
    # commit returns before Git (2026-11-14); the background loop, or the shutdown flush at the latest, pushes it
    assert result["committed"]["accepted"] and result["committed"]["write"]["state"] in ("received", "pending_sync", "effective")
    assert result["verify"]["markdown"].count("逐条处置") == 1 and result["verify"]["candidate"]["id"] == TRIP
    assert result["memory"]["schema"] == "tripwright.memory/1"
    assert git(folder, "rev-parse", "HEAD") == git(remote, "rev-parse", "main")
    assert (folder / layout.trip_folder(TRIP) / "plan/versions.jsonl").exists()
