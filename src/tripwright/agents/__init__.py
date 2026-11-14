"""Tripwright roles and the files generated from them (block C).

Runtime readers only use the generated files, which ship inside the package: server instructions and the files
`tripwright init` writes into a user's trip repository. `assemble` rebuilds them from the sources.
"""
from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from tripwright import __version__

ROLES = ("tripwright-main", "tripwright-requirements", "tripwright-verifier", "tripwright-researcher")
_NOTICE = "<!-- 由 "
_CHECKOUT = Path(__file__).resolve().parents[3]


def _generated(path: str) -> str:
    return files(__package__).joinpath("generated", path).read_text(encoding="utf-8")


def _without_notice(text: str) -> str:
    first, newline, rest = text.partition("\n")
    return rest if first.startswith(_NOTICE) else text


def server_instructions() -> str:
    return _without_notice(_generated("server/instructions.md")).strip()


TOOL_FILE_PATTERNS = (".claude/agents/tripwright-*.md", ".codex/agents/tripwright_*.toml",
                      ".claude/skills/tripwright-*/*", ".agents/skills/tripwright-*/*")


def user_repo_files() -> dict[str, bytes]:
    """Repository-relative path → bytes for the files block C decides in a user's trip repository."""
    base = files(__package__)
    # AGENTS.md is the template with the core steps filled in by the build (S4a); CLAUDE.md imports it.
    output = {"AGENTS.md": _without_notice(_generated("repo/AGENTS.init.md")).encode(),
              "CLAUDE.md": base.joinpath("templates/CLAUDE.md.tmpl").read_bytes(),
              # Registers the server as `tripwright`, the name the subagents' tool restrictions use.
              ".mcp.json": base.joinpath("templates/mcp.json.tmpl").read_bytes(),
              # Pre-approves the tools every road answer uses (the trip server, web search and fetch, subagents,
              # read-only shell commands), so a phone or cloud session does not ask for each call. The one command
              # that writes is the trip's bundled tripwright running `query`, which the researcher uses when MCP is
              # missing (D-43); Claude Code matches rules on whole words, so the rule names the exact wheel file.
              ".claude/settings.json": base.joinpath("templates/settings.json.tmpl").read_bytes().replace(
                  b"{{wheel}}", f"tripwright-{__version__}-py3-none-any.whl".encode())}
    for folder, destination, suffix in (("claude", ".claude/agents", ".md"), ("codex", ".codex/agents", ".toml")):
        for entry in sorted(base.joinpath("generated", folder).iterdir(), key=lambda item: item.name):
            if entry.name.endswith(suffix):
                output[f"{destination}/{entry.name}"] = entry.read_bytes()
    # Claude Code reads .claude/skills/, Codex reads .agents/skills/ (CONTRACTS 4); both get the same bytes.
    for role in ROLES:
        for path, data in _host_skill_files(_skill_dir(role)):
            for destination in (".claude/skills", ".agents/skills"):
                output[f"{destination}/{role}/{path}"] = data
    return output


def role_steps(role: str) -> dict:
    """A role's steps as its skill holds them, for a host without the skill files (a chat app): `state(view="role")`.
    `role` is a role name, `<role>/<file>` for one of its `references/` files, or `single`: the order in which one agent
    without subagents does the roles (the main role's `single-agent.md`). The same bytes the skill ships,
    without the generator's front matter, so a chat app and a repository host follow one text (2026-11-13, G1)."""
    if role == "single":  # the order in which one agent does the roles (references/single-agent.md, 2026-11-14)
        role = "tripwright-main/single-agent.md"
    name, _, file = role.partition("/")
    if name not in ROLES:
        raise ValueError(f"Unknown role {role!r}; one of: {', '.join(ROLES)}")
    folder = _skill_dir(name)
    references = sorted(entry.name for entry in folder.joinpath("references").iterdir()
                        if entry.name.endswith(".md")) if folder.joinpath("references").is_dir() else []
    if file:
        if file not in references:
            raise ValueError(f"Unknown file {file!r} for {name}; one of: {', '.join(references) or 'none'}")
        return dict(role=name, file=file, text=_without_notice(folder.joinpath("references", file).read_text(encoding="utf-8")).strip())
    text = folder.joinpath("SKILL.md").read_text(encoding="utf-8")
    front, body = _front_matter(text)
    out = dict(role=name, description=" ".join(str(front.get("description", "")).split()), text=body.strip())
    if references:
        out["references"] = {file: f'state(view="role", role="{name}/{file}")' for file in references}
    return out


def _front_matter(text: str) -> tuple[dict, str]:
    import yaml

    if not text.startswith("---\n"):
        return {}, text
    head, _, body = text[4:].partition("\n---\n")
    return yaml.safe_load(head) or {}, body


def _skill_dir(role: str):
    """The role's skill folder: bundled in the wheel, else the checkout's `skills/`."""
    bundled = files("tripwright").joinpath("skills", role)
    if bundled.is_dir():
        return bundled
    checkout = _CHECKOUT / "skills" / role
    if checkout.is_dir():
        return checkout
    raise LookupError(f"Skill {role} is neither bundled in the package nor in a checkout; add skills/{role} to the wheel's force-include")


def _host_skill_files(folder, prefix: str = ""):
    """SKILL.md and references/ only: MAINTAINING.md and evals/ are for maintainers, not for the host."""
    for entry in sorted(folder.iterdir(), key=lambda item: item.name):
        if entry.is_dir():
            if prefix or entry.name == "references":
                yield from _host_skill_files(entry, f"{prefix}{entry.name}/")
        elif prefix or entry.name == "SKILL.md":
            yield f"{prefix}{entry.name}", entry.read_bytes()
