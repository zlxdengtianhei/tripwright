"""MCP prompts for the four Tripwright roles (block C). The texts are generated from the role sources."""
from __future__ import annotations

from tripwright.agents import prompts


def register_prompts(server) -> list[str]:
    """Register one prompt per role and return their names.

    A prompt already registered under a role name (an older single-prompt server) is replaced, because the SDK
    would otherwise keep the old text and only log a warning.
    """
    names = []
    for entry in prompts():
        try:
            server.remove_prompt(entry["name"])
        except ValueError:
            pass
        server.prompt(name=entry["name"], title=entry["title"], description=entry["description"])(_render(entry["text"]))
        names.append(entry["name"])
    return names


def _render(text: str):
    def render() -> str:
        return text
    return render
