import asyncio
from pathlib import Path

import pytest
import yaml

from tripwright.adapters.mcp import BearerIdentity, create_server, request_user


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
    assert "Trip" in proposal["inputSchema"]["$defs"]
    assert "Changes" in proposal["inputSchema"]["$defs"]


def test_bearer_identity_only_comes_from_token_and_is_reset():
    received = []

    async def app(scope, receive, send):
        received.append(request_user.get())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"OK"})

    middleware = BearerIdentity(app, {"test-only-token": "alice"}, rate_per_minute=1)

    async def request(headers):
        messages = []

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            messages.append(message)

        await middleware({"type": "http", "headers": headers}, receive, send)
        return messages[0]["status"]

    assert asyncio.run(request([])) == 401
    assert asyncio.run(request([(b"authorization", b"Bearer wrong"), (b"user_id", b"alice")])) == 401
    assert asyncio.run(request([(b"authorization", b"Bearer test-only-token"), (b"user_id", b"bob")])) == 200
    assert received == ["alice"]
    assert request_user.get() is None
    assert asyncio.run(request([(b"authorization", b"Bearer test-only-token")])) == 429


def test_empty_http_mapping_is_rejected():
    with pytest.raises(ValueError, match="nonempty"):
        BearerIdentity(None, {})


def test_skill_frontmatter_and_seven_tool_workflow():
    text = (Path(__file__).resolve().parents[2] / "skills/tripwright-main/SKILL.md").read_text()
    frontmatter = yaml.safe_load(text.split("---", 2)[1])
    assert frontmatter["name"] == "tripwright-main"
    assert frontmatter["description"]
    assert all(f"`{name}`" in text for name in ("turn", "query", "propose", "commit", "result", "state", "explain"))
    assert "中文说明" in text
