"""M1 over a real MCP stdio server: turn returns consider, ask, offer and next; a setting sent as turn.record comes back in
the next turn's consider, and silence after a question settles it."""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_b3d_mcp import environment


async def call(session, tool, arguments):
    response = await session.call_tool(tool, arguments)
    text = " ".join(getattr(content, "text", "") for content in response.content)
    assert not response.is_error, text
    return response.structured_content or json.loads(text)


async def scenario(directory: Path) -> list[dict]:
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "tripwright.cli", "--db", str(directory / "m1.sqlite3"), "serve", "--stdio"],
                                       env=environment())
    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            first = await call(session, "turn", dict(text="我们想去维也纳玩三天。", request_id="m1-1", nature="user_verbatim",
                                                    native_ref="fixture:m1-1", speaker="u1", scenario="plan"))
            setting = dict(kind="setting", key="ask_languages", value=["de"], quote="问人用英语和德语", text="问人用英语和德语",
                           subject=["*"], strength="prefer", long_term=True, source=dict(event="this", basis="user_explicit"))
            second = await call(session, "turn", dict(text="问人用英语和德语就行。", request_id="m1-2", nature="user_verbatim",
                                                     native_ref="fixture:m1-2", speaker="u1", scenario="plan", record=dict(items=[setting])))
            return [first, second]


def test_turn_over_stdio_carries_consider_ask_offer_and_next(tmp_path):
    first, second = asyncio.run(scenario(tmp_path))
    for key in ("consider", "ask", "offer", "next"):
        assert key in first and key in second, key
    assert [question["id"] for question in first["ask"]] == ["must_go"]
    assert first["offer"]["say"] == "我可以帮你起草询问内容（英语）。"
    assert any(entry["kind"] == "ask_languages" and entry["text"] == "英语、德语" and entry["from"]["tier"] == "long_term"
               for entry in second["consider"]["entries"])
    assert second["offer"]["languages"] == ["en", "de"]
    assert second["recorded"]["defaulted"] == [dict(id="must_go", scope={}, text="用户没提必须去的地方，按没有算")]
    assert "must_go" not in [question["id"] for question in second["ask"]]
    (tmp_path / "turn.json").write_text(json.dumps(second, ensure_ascii=False, indent=1), encoding="utf-8")
