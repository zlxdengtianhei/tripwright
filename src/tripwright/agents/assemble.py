"""Assemble skills and host agent files from one set of role and step sources (CONTRACTS section 4).

Sources: this package's `roles/*.md` and `steps/*.md`; the main role owns its answer and writing reference.
`core.yaml` holds the steps of every message (S4a), rendered the same way into the server instructions, the `steps`
that `turn` returns, the roles that include them and the AGENTS.md that `tripwright init` writes.
Block B's writing fragments remain available through B's `tripwright.render.writing.fragment(name, language)`
for explicit includes, copied verbatim. A role body is Markdown with
whole-line directives: `<!-- only: skill claude -->` ... `<!-- /only -->` keeps lines for the named targets;
`<!-- include: fragment writing zh -->`, `<!-- include: steps summary|tool|notool -->` and
`<!-- include: core steps|always -->` insert text.
A missing fragment becomes a visible placeholder line, so the skill can be assembled before B delivers.

`build(load_sources(root))` is pure: the same sources give the same bytes. Every output names its sources in a
notice; `check` reports outputs that differ from a fresh build.
"""
from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

import yaml

# B owns where its fragments live and which exist; FRAGMENTS below only names them in notices and placeholders.
from tripwright.render.writing import LANGUAGES, NAMES as FRAGMENT_NAMES, fragment as read_fragment

ROOT = Path(__file__).resolve().parents[3]
GENERATOR = "python -m tripwright.agents build"
NOTICE_PREFIX = f"由 {GENERATOR} 生成"
TARGETS = ("skill", "claude", "codex")
AGENTS = "src/tripwright/agents"
GENERATED = f"{AGENTS}/generated"
FRAGMENTS = "src/tripwright/render/fragments"
CORE = f"{AGENTS}/core.yaml"
AGENTS_TEMPLATE = f"{AGENTS}/templates/AGENTS.md.tmpl"
# S4a: the server instructions are the core steps and little else, for an entry that reads nothing but MCP (豆包工作)
INSTRUCTIONS_LIMIT = 3600
STEP_SECTIONS = ("## With tools", "## Without tools")
# Frontmatter fields documented on code.claude.com/docs/en/sub-agents (read 2026-10-31). Claude Code ignores an
# unknown field without an error, so a misspelt restriction would silently not apply: refuse it here instead.
CLAUDE_FIELDS = {"tools", "disallowedTools", "model", "permissionMode", "maxTurns", "skills", "mcpServers", "hooks",
                 "memory", "background", "omitClaudeMd", "effort", "isolation", "color"}
ROLE_FIELDS = {"name", "title", "description", "metadata", "targets", "claude"}
METADATA_FIELDS = ("trigger", "owns", "not", "layer")
DIRECTIVE = re.compile(r"<!--\s*(?:(only):\s*([\w ]+?)|(/only)|(include):\s*([\w ]+?)|(reference):\s*([\w.-]+?\.md)|(/reference))\s*-->")

SINGLE_AGENT_INTRO = """# Single-agent steps

Read this from `SKILL.md` when the host cannot dispatch subagents, or when a step is unclear. Each step has a version for hosts with the Tripwright tools and one for chat apps without them. Without tools, every reply ends with one 写回仓 block that collects what its steps add.

Choose the steps by scenario:
- Starting a new trip: 0, then the steps of the scenario.
- Quick question: 1 → 2 (only the people and time involved) → 3 → 4 (the facts the user will act on) → 5 (record only what the message carried).
- Status report only: 1 → 5.
- Replan or plan from scratch: 1 → 6.
- A large batch of new requirements: 1 → 2 → split into points and mark conflicts → 5 (proposals only)."""


class AssembleError(ValueError):
    pass


@dataclass(frozen=True)
class Role:
    name: str
    meta: dict
    body: str
    path: str


@dataclass(frozen=True)
class Step:
    number: int
    title: str
    summary: str
    tool: str
    notool: str
    path: str


@dataclass(frozen=True)
class Core:
    intro: str
    steps: tuple[tuple[str, str], ...]  # (do, done)
    always: tuple[str, ...]
    read: str
    path: str = CORE


@dataclass(frozen=True)
class Sources:
    roles: dict[str, Role]
    steps: tuple[Step, ...]
    fragments: dict[str, str]
    core: Core
    agents_template: str


# --- sources ------------------------------------------------------------------

def split_frontmatter(text: str, where: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise AssembleError(f"{where}: must start with a --- frontmatter line")
    head, separator, body = text[4:].partition("\n---\n")
    if not separator:
        raise AssembleError(f"{where}: frontmatter is not closed")
    try:
        meta = yaml.safe_load(head)
    except yaml.YAMLError as error:
        raise AssembleError(f"{where}: frontmatter is not valid YAML: {error}") from None
    if not isinstance(meta, dict):
        raise AssembleError(f"{where}: frontmatter is not a mapping")
    return meta, body.lstrip("\n")


def parse_role(text: str, path: str) -> Role:
    meta, body = split_frontmatter(text, path)
    name = Path(path).stem
    unknown = set(meta) - ROLE_FIELDS
    if unknown:
        raise AssembleError(f"{path}: unknown fields {sorted(unknown)}")
    if meta.get("name") != name or ":" in name:
        raise AssembleError(f"{path}: name must equal the file name {name!r} and contain no ':'")
    for key in ("title", "description"):
        if not isinstance(meta.get(key), str) or not meta[key].strip():
            raise AssembleError(f"{path}: {key} is required")
    if len(meta["description"]) > 1024:
        raise AssembleError(f"{path}: description is longer than 1024 characters")
    metadata = meta.get("metadata") or {}
    if any(not isinstance(metadata.get(key), str) or not metadata[key] for key in METADATA_FIELDS):
        raise AssembleError(f"{path}: metadata needs {', '.join(METADATA_FIELDS)}")
    targets = meta.get("targets") or []
    if not targets or any(target not in TARGETS for target in targets):
        raise AssembleError(f"{path}: targets must be a nonempty subset of {TARGETS}")
    claude = meta.get("claude") or {}
    if set(claude) - CLAUDE_FIELDS:
        raise AssembleError(f"{path}: Claude Code ignores {sorted(set(claude) - CLAUDE_FIELDS)}; use documented fields")
    if claude and "claude" not in targets:
        raise AssembleError(f"{path}: claude fields without the claude target")
    return Role(name, meta, body, path)


def parse_step(text: str, path: str) -> Step:
    meta, body = split_frontmatter(text, path)
    number = int(Path(path).name.split("-", 1)[0])
    if meta.get("step") != number or not meta.get("title") or not meta.get("summary"):
        raise AssembleError(f"{path}: needs step: {number}, title and summary")
    with_tools, without = STEP_SECTIONS
    if body.count(with_tools + "\n") != 1 or body.count(without + "\n") != 1 or body.index(with_tools) > body.index(without):
        raise AssembleError(f"{path}: needs one '{with_tools}' section followed by one '{without}' section")
    before, _, rest = body.partition(with_tools + "\n")
    if before.strip():
        raise AssembleError(f"{path}: text before '{with_tools}'")
    tool, _, notool = rest.partition(without + "\n")
    return Step(number, meta["title"], meta["summary"], tool.strip(), notool.strip(), path)


def plain(text) -> str:
    return " ".join(str(text).split())


def parse_core(text: str, path: str = CORE) -> Core:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise AssembleError(f"{path}: not valid YAML: {error}") from None
    if not isinstance(data, dict) or set(data) != {"intro", "steps", "always", "read"}:
        raise AssembleError(f"{path}: needs exactly intro, steps, always and read")
    steps = data["steps"]
    if not isinstance(steps, list) or not steps or any(not isinstance(step, dict) or set(step) != {"do", "done"}
                                                       or not all(isinstance(step[key], str) and step[key].strip() for key in step) for step in steps):
        raise AssembleError(f"{path}: steps must be a list of {{do, done}}, each one action and the check that it is done")
    always = data["always"]
    if not isinstance(always, list) or any(not isinstance(rule, str) or not rule.strip() for rule in always):
        raise AssembleError(f"{path}: always must be a list of sentences")
    if any(not isinstance(data[key], str) or not data[key].strip() for key in ("intro", "read")):
        raise AssembleError(f"{path}: intro and read are text")
    return Core(plain(data["intro"]), tuple((plain(step["do"]), plain(step["done"])) for step in steps),
                tuple(plain(rule) for rule in always), plain(data["read"]), path)


def b_fragments(read=read_fragment) -> dict[str, str]:
    """Every fragment B currently provides, as `<name>.<language>.md` → text; a missing one is left out."""
    found = {}
    for name in FRAGMENT_NAMES:
        for language in LANGUAGES:
            try:
                found[f"{name}.{language}.md"] = read(name, language) + "\n"
            except FileNotFoundError:
                pass
    return found


def load_sources(root: Path = ROOT, read=read_fragment) -> Sources:
    root = Path(root)
    roles = {}
    for file in sorted((root / AGENTS / "roles").glob("*.md")):
        role = parse_role(file.read_text(encoding="utf-8"), file.relative_to(root).as_posix())
        roles[role.name] = role
    steps = tuple(parse_step(file.read_text(encoding="utf-8"), file.relative_to(root).as_posix())
                  for file in sorted((root / AGENTS / "steps").glob("*.md")))
    # Step 0 (starting a new trip) is optional and comes before the six steps of every scenario.
    first = 0 if steps and steps[0].number == 0 else 1
    if [step.number for step in steps] != list(range(first, first + len(steps))):
        raise AssembleError(f"{AGENTS}/steps: steps must be numbered 0 or 1..n without gaps")
    fragments = b_fragments(read)
    if "tripwright-main" not in roles:
        raise AssembleError(f"{AGENTS}/roles: tripwright-main is required")
    core = parse_core((root / CORE).read_text(encoding="utf-8"))
    return Sources(roles, steps, fragments, core, (root / AGENTS_TEMPLATE).read_text(encoding="utf-8"))


# --- expansion ------------------------------------------------------------------

def fragment_text(sources: Sources, name: str, language: str, where: str) -> str:
    if name not in FRAGMENT_NAMES or language not in LANGUAGES:
        raise AssembleError(f"{where}: unknown fragment {name} {language}")
    file = f"{name}.{language}.md"
    if file in sources.fragments:
        return sources.fragments[file].strip("\n")
    return f"> 等 B 片段：{FRAGMENTS}/{file}"


def steps_text(sources: Sources, mode: str, where: str) -> str:
    if mode == "summary":
        return "\n".join(f"{step.number}. **{step.title}.** {step.summary}" for step in sources.steps)
    if mode not in ("tool", "notool"):
        raise AssembleError(f"{where}: unknown steps mode {mode!r}")
    return "\n\n".join(f"### {step.number}. {step.title}\n\n{step.tool if mode == 'tool' else step.notool}" for step in sources.steps)


def core_step_lines(core: Core) -> list[str]:
    """The numbered steps, each its action and the check that it is done, as every output shows them."""
    return [f"{number}. {do} Done when: {done}" for number, (do, done) in enumerate(core.steps, 1)]


def core_text(core: Core, part: str, where: str = CORE) -> str:
    if part == "steps":
        return "\n".join(core_step_lines(core))
    if part == "always":
        return "\n".join(f"- {rule}" for rule in core.always)
    raise AssembleError(f"{where}: unknown core part {part!r}")


def server_instructions_text(core: Core) -> str:
    # Chat apps receive the same six rules as turn's description, not the repository-only workflow.
    return core.intro + "\n" + "\n".join(f"{number}. {rule}" for number, rule in enumerate(core.always, 1)) + "\n"


def agents_md(sources: Sources) -> str:
    """The AGENTS.md `tripwright init` writes: the template with its core lines filled in."""
    lines = []
    for number, line in enumerate(sources.agents_template.splitlines(), 1):
        match = DIRECTIVE.fullmatch(line.strip())
        if match is None:
            lines.append(line)
        elif match.group(4) and match.group(5).split()[0] == "core" and len(match.group(5).split()) == 2:
            lines.extend(core_text(sources.core, match.group(5).split()[1], f"{AGENTS_TEMPLATE}:{number}").splitlines())
        else:
            raise AssembleError(f"{AGENTS_TEMPLATE}:{number}: only core includes belong in the template")
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class Expanded:
    body: str
    used: frozenset[str]
    references: dict[str, str]


def expand(body: str, target: str, sources: Sources, where: str) -> Expanded:
    """The body for one target, the kinds of included sources ("steps", "fragments") and, for a skill, the
    detail moved out to `references/`. A `reference: <file>` block stays inline for every other target, because a
    prompt or an agent file cannot open a skill's references."""
    body_lines, stack, used, references = [], [], set(), {}
    lines, reference = body_lines, None
    for number, line in enumerate(body.splitlines(), 1):
        place = f"{where}:{number}"
        match = DIRECTIVE.fullmatch(line.strip())
        if match is None:
            if DIRECTIVE.search(line) or re.search(r"<!--\s*/?(only|include|reference)\b", line):
                raise AssembleError(f"{place}: a directive must stand alone on its line")
            if all(target in kept for kept in stack):
                lines.append(line)
            continue
        if match.group(6):
            if reference is not None:
                raise AssembleError(f"{place}: reference blocks cannot nest")
            reference = match.group(7)
            if target == "skill":
                lines = references.setdefault(reference, [])
        elif match.group(8):
            if reference is None:
                raise AssembleError(f"{place}: /reference without reference")
            reference, lines = None, body_lines
        elif match.group(1):
            names = set(match.group(2).split())
            if names - set(TARGETS):
                raise AssembleError(f"{place}: unknown targets {sorted(names - set(TARGETS))}")
            stack.append(names)
        elif match.group(3):
            if not stack:
                raise AssembleError(f"{place}: /only without only")
            stack.pop()
        elif all(target in kept for kept in stack):
            kind, *arguments = match.group(5).split()
            if kind == "fragment" and len(arguments) == 2:
                lines.extend(fragment_text(sources, *arguments, place).splitlines())
                used.add("fragments")
            elif kind == "steps" and len(arguments) == 1:
                lines.extend(steps_text(sources, arguments[0], place).splitlines())
                used.add("steps")
            elif kind == "core" and len(arguments) == 1:
                lines.extend(core_text(sources.core, arguments[0], place).splitlines())
                used.add("core")
            else:
                raise AssembleError(f"{place}: unknown include {match.group(5)!r}")
    if stack or reference is not None:
        raise AssembleError(f"{where}: an only or reference block is not closed")
    return Expanded(tidy("\n".join(body_lines)), frozenset(used), {name: tidy("\n".join(text)) for name, text in references.items()})


def tidy(text: str) -> str:
    """One blank line between blocks, none at the ends, one final newline."""
    return re.sub(r"\n{3,}", "\n\n", "\n".join(line.rstrip() for line in text.splitlines())).strip("\n") + "\n"


# --- outputs --------------------------------------------------------------------

def notice(paths: list[str]) -> str:
    return f"{NOTICE_PREFIX}，源：{'、'.join(paths)}；不要手改"


def source_paths(role: Role, used: set[str]) -> list[str]:
    paths = [role.path]
    if "steps" in used:
        paths.append(f"{AGENTS}/steps/*.md")
    if "fragments" in used:
        paths.append(f"{FRAGMENTS}/*.md")
    if "core" in used:
        paths.append(CORE)
    return paths


def frontmatter(note: str, fields: dict) -> str:
    dumped = yaml.safe_dump(fields, allow_unicode=True, sort_keys=False, width=100000).rstrip("\n")
    return f"---\n# {note}\n{dumped}\n---\n"


def markdown(note: str, body: str) -> str:
    return f"<!-- {note} -->\n{body}"


def toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def toml_multiline(value: str) -> str:
    if "'''" in value:
        return toml_string(value)
    return "'''\n" + value + "'''"


def codex_name(role: str) -> str:
    # The documented examples only use underscores; hyphens are untested in Codex spawning.
    return role.replace("-", "_")


def codex_file(note: str, name: str, description: str, instructions: str) -> str:
    text = (f"# {note}\nname = {toml_string(name)}\ndescription = {toml_string(description)}\n"
            f"developer_instructions = {toml_multiline(instructions)}\n")
    parsed = tomllib.loads(text)
    if parsed != {"name": name, "description": description, "developer_instructions": instructions}:
        raise AssembleError(f"codex/{name}.toml does not round-trip through TOML")
    return text


def build(sources: Sources) -> dict[str, str]:
    """Every generated file, as repository-relative path → text."""
    outputs: dict[str, str] = {}
    for name, role in sorted(sources.roles.items()):
        targets = role.meta["targets"]
        description = " ".join(role.meta["description"].split())
        for target in targets:
            expanded = expand(role.body, target, sources, role.path)
            body, note = expanded.body, notice(source_paths(role, expanded.used))
            if target == "skill":
                fields = {"name": name, "description": description, "metadata": {key: role.meta["metadata"][key] for key in METADATA_FIELDS}}
                outputs[f"skills/{name}/SKILL.md"] = frontmatter(note, fields) + "\n" + body
                for file, text in expanded.references.items():
                    outputs[f"skills/{name}/references/{file}"] = markdown(notice([role.path]), text)
            elif target == "claude":
                fields = {"name": name, "description": description, **role.meta.get("claude", {})}
                outputs[f"{GENERATED}/claude/{name}.md"] = frontmatter(note, fields) + "\n" + body
            elif target == "codex":
                outputs[f"{GENERATED}/codex/{codex_name(name)}.toml"] = codex_file(f"{note}", codex_name(name), description, body)
    # S4a: the instructions are the core steps (they were a 5.7 KB run of the main role's rules).
    instructions = server_instructions_text(sources.core)
    if len(instructions.encode()) > INSTRUCTIONS_LIMIT:
        raise AssembleError(f"{CORE}: the server instructions are {len(instructions.encode())} bytes, over {INSTRUCTIONS_LIMIT}")
    outputs[f"{GENERATED}/server/instructions.md"] = markdown(notice([CORE]), instructions)
    # Not named AGENTS.md: a host working on this checkout would read it as this repository's instructions. init
    # writes it as the trip repository's AGENTS.md, without this notice (its own first line says init wrote it).
    outputs[f"{GENERATED}/repo/AGENTS.init.md"] = markdown(notice([AGENTS_TEMPLATE, CORE]), agents_md(sources))
    steps_note = notice([f"{AGENTS}/steps/*.md"])
    outputs["skills/tripwright-main/references/single-agent.md"] = markdown(steps_note, tidy("\n\n".join([
        SINGLE_AGENT_INTRO, "## With tools", steps_text(sources, "tool", "single-agent"),
        "## Without tools", steps_text(sources, "notool", "single-agent")])))
    return outputs


# --- disk -----------------------------------------------------------------------

def generated_on_disk(root: Path = ROOT) -> set[str]:
    """Files this generator owns on disk: everything under generated/ and every skill file carrying its notice."""
    root = Path(root)
    owned = {file.relative_to(root).as_posix() for file in (root / GENERATED).rglob("*") if file.is_file() and "__pycache__" not in file.parts}
    for file in (root / "skills").rglob("*"):
        if file.is_file():
            head = file.read_text(encoding="utf-8", errors="replace")[:600]
            if NOTICE_PREFIX in head:
                owned.add(file.relative_to(root).as_posix())
    return owned


def check(root: Path, outputs: dict[str, str]) -> list[str]:
    root = Path(root)
    problems = []
    for path, text in sorted(outputs.items()):
        file = root / path
        if not file.is_file():
            problems.append(f"{path}: missing; run {GENERATOR}")
        elif file.read_text(encoding="utf-8") != text:
            problems.append(f"{path}: differs from a fresh build; run {GENERATOR}")
    problems += [f"{path}: not produced by the build; run {GENERATOR}" for path in sorted(generated_on_disk(root) - set(outputs))]
    return problems


def write(root: Path, outputs: dict[str, str]) -> list[str]:
    root = Path(root)
    changed = []
    for path in sorted(generated_on_disk(root) - set(outputs)):
        (root / path).unlink()
        changed.append(path)
    for path, text in sorted(outputs.items()):
        file = root / path
        if not file.is_file() or file.read_text(encoding="utf-8") != text:
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(text, encoding="utf-8")
            changed.append(path)
    return changed
