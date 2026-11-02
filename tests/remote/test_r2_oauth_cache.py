"""R2 item 6: a verified GitHub token is cached, so an MCP request does not call GitHub twice every time."""
from __future__ import annotations

import asyncio

import httpx2

from tripwright.remote.auth import AllowedGitHub


def verify_three_times(cache_seconds):
    calls = []

    def github(request):
        calls.append(request.url.path)
        if request.url.path == "/user":
            return httpx2.Response(200, json={"id": 123, "login": "owner"})
        return httpx2.Response(200, json=[])

    async def run():
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(github)) as client:
            verifier = AllowedGitHub({"owner": "personal"}, http_client=client, cache_seconds=cache_seconds)
            results = [await verifier.verify_token("ghu_owner") for _ in range(3)]
            stranger = AllowedGitHub({"someone-else": "personal"}, http_client=client, cache_seconds=cache_seconds)
            assert await stranger.verify_token("ghu_owner") is None, "a cached token still meets the allowlist"
            return results
    results = asyncio.run(run())
    assert all(result is not None for result in results)
    return [path for path in calls if path == "/user"]


def test_github_is_asked_once_per_token_within_the_cache_window():
    assert len(verify_three_times(300)) == 2  # once for the allowed verifier, once for the stranger's


def test_without_the_cache_every_verification_calls_github():
    assert len(verify_three_times(0)) == 4
