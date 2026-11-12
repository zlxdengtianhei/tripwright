"""What a model reads of a tool result is the text block. The MCP library writes a dict result as JSON indented by two
spaces, and the indentation was about 29% of a `turn` result's bytes (2026-11-12: 46,881 bytes, 33,225 compact). The
text is the same JSON without the indentation; the structured content is unchanged."""
import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from test_b3d_mcp import environment


def test_tool_text_is_compact_json_equal_to_the_structured_content(tmp_path):
    async def scenario():
        parameters = StdioServerParameters(command=sys.executable, args=["-m", "tripwright.cli", "--db", str(tmp_path / "c.sqlite3"),
                                                                         "serve", "--stdio"], env=environment())
        async with stdio_client(parameters) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                return [await session.call_tool(name, arguments) for name, arguments in
                        (("turn", {"text": "Which trips are stored?", "scenario": "status"}), ("state", {}))]

    for response in asyncio.run(scenario()):
        assert not response.is_error
        text = response.content[0].text
        assert json.loads(text) == response.structured_content
        assert "\n  " not in text
        assert text == json.dumps(response.structured_content, ensure_ascii=False, separators=(",", ":"))
