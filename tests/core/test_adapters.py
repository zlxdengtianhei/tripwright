import asyncio
from pathlib import Path

import yaml

from tripwright.adapters.mcp import create_server


def test_tool_contract_is_exactly_seven_with_annotations_and_no_user_parameter(service):
    server = create_server(service)
    tools = asyncio.run(server.list_tools())
    assert {tool.name for tool in tools} == {"turn", "query", "propose", "commit", "result", "state", "explain"}
    for tool in tools:
        document = tool.model_dump(by_alias=True)
        assert document["title"]
        assert "user_id" not in document["inputSchema"]["properties"]
        assert "readOnlyHint" in document["annotations"]
    proposal = next(tool for tool in tools if tool.name == "propose").model_dump(by_alias=True)
    assert "$defs" not in proposal["inputSchema"]
    assert all("properties" not in proposal["inputSchema"]["properties"][name] for name in ("plan", "changes"))


def test_skill_frontmatter_and_seven_tool_workflow():
    text = (Path(__file__).resolve().parents[2] / "skills/tripwright-main/SKILL.md").read_text()
    frontmatter = yaml.safe_load(text.split("---", 2)[1])
    assert frontmatter["name"] == "tripwright-main"
    assert frontmatter["description"]
    assert all(name in text for name in ("turn", "query", "propose", "commit", "result", "state", "explain"))
    assert "中文说明" in text
