from __future__ import annotations

import asyncio
import threading

from tripwright.remote.app import create_app

KEY = "k" * 40


def test_cancelled_first_request_does_not_create_another_writer(remote_config, monkeypatch):
    async def run():
        app = create_app(remote_config, key=KEY)
        remote = app.state.remote
        opened = threading.Event()
        release = threading.Event()
        calls = []
        original = remote.repository.open

        def delayed():
            calls.append("personal")
            opened.set()
            assert release.wait(5)
            return original()

        monkeypatch.setattr(remote.repository, "open", delayed)
        async with app.router.lifespan_context(app):
            first = asyncio.create_task(remote.personal_app())
            assert await asyncio.to_thread(opened.wait, 5)
            first.cancel()
            try:
                await first
            except asyncio.CancelledError:
                pass
            second = asyncio.create_task(remote.personal_app())
            release.set()
            await asyncio.wait_for(second, 10)
            assert calls == ["personal"] and len(remote.repository.opened) == 1
    asyncio.run(run())
