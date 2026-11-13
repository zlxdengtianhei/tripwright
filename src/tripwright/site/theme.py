"""The look of the share pages, one scheme at a time (web_ui_spec D-16): the components (class names and structure) are
fixed by the templates and `share_static/share.css`; a scheme is a folder `themes/<name>/` with a `DESIGN.md` (its value
table, exported here as `:root` variables) and an optional `theme.css` (a few layout overrides).

Variable names, from the value table at the top of DESIGN.md:
- `colors.<name>` → `--<name>`;
- `typography.<name>` → `--<name>-font`, `--<name>-size`, `--<name>-weight`, `--<name>-lh`;
- `rounded.<name>` → `--r-<name>`; `spacing.<name>` → `--<name>`.
Every variable the shared stylesheet reads must be given by the scheme, or `style` raises naming the missing ones."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

THEMES = Path(__file__).parent / "themes"
SHARED = Path(__file__).parent / "share_static" / "share.css"
DEFAULT = "atlas"


class UnknownTheme(ValueError):
    pass


@dataclass(frozen=True)
class Theme:
    name: str
    variables: dict[str, str]
    overrides: str

    def root(self) -> str:
        return ":root{" + ";".join(f"{key}:{value}" for key, value in self.variables.items()) + "}"


def names() -> list[str]:
    return sorted(path.parent.name for path in THEMES.glob("*/DESIGN.md"))


def _table(design: str) -> dict:
    match = re.match(r"\s*---\n(.*?)\n---\n", design, flags=re.S)
    if not match:
        raise ValueError("DESIGN.md has no value table between two --- lines at the top")
    return yaml.safe_load(match.group(1)) or {}


def _variables(table: dict) -> dict[str, str]:
    # colours in lower case: an upper-case value such as #1B2430 reads as a flight number to the page's privacy scan
    variables = {f"--{name}": str(value).lower() for name, value in (table.get("colors") or {}).items()}
    for name, face in (table.get("typography") or {}).items():
        variables[f"--{name}-font"] = str(face["fontFamily"])
        variables[f"--{name}-size"] = str(face["fontSize"])
        variables[f"--{name}-weight"] = str(face.get("fontWeight", 400))
        variables[f"--{name}-lh"] = str(face.get("lineHeight", "normal"))
    variables.update({f"--r-{name}": str(value) for name, value in (table.get("rounded") or {}).items()})
    variables.update({f"--{name}": str(value) for name, value in (table.get("spacing") or {}).items()})
    return variables


@lru_cache(maxsize=None)
def load(name: str = DEFAULT) -> Theme:
    folder = THEMES / name
    if not re.fullmatch(r"[a-z][a-z0-9-]*", name or "") or not (folder / "DESIGN.md").is_file():
        raise UnknownTheme(f"No share page scheme {name!r}; known: {', '.join(names())}")
    overrides = folder / "theme.css"
    return Theme(name=name, variables=_variables(_table((folder / "DESIGN.md").read_text(encoding="utf-8"))),
                 overrides=overrides.read_text(encoding="utf-8") if overrides.is_file() else "")


def used(css: str) -> set[str]:
    """The variables a stylesheet reads."""
    return set(re.findall(r"var\((--[\w-]+)", css))


def style(name: str = DEFAULT) -> str:
    """The whole stylesheet of a page: the scheme's variables, the shared components, the scheme's overrides."""
    theme = load(name)
    shared = SHARED.read_text(encoding="utf-8")
    missing = sorted(used(shared + theme.overrides) - set(theme.variables))
    if missing:
        raise ValueError(f"Scheme {name!r} does not give {', '.join(missing)} (read by the shared stylesheet)")
    return "\n".join(part for part in (theme.root(), shared, theme.overrides) if part)
