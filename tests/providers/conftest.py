import pytest

from tripwright.providers import common


@pytest.fixture(autouse=True)
def quick_retries(monkeypatch):
    """A refused or overloaded request is retried after a pause (s4b); the provider tests do not wait for it."""
    monkeypatch.setattr(common, "RETRY_AFTER", (0.0, 0.0))
