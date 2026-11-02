"""Which tool finds places and which shows timetables in a region (design 9.3).

Sources in order of trust: what the user said about this region ("Google shows no buses here"), the app a user's
screenshot came from, then the maintained table in `regions.yaml` (each row verified or inferred). A report without a
region applies to the region being asked about.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib.resources import files

import yaml

KINDS = ("user_said", "screenshot")


@cache
def table() -> dict:
    data = yaml.safe_load((files(__package__) / "regions.yaml").read_text(encoding="utf-8"))
    for row in data["regions"]:
        for key in ("places", "timetable"):
            if row[key] not in data["tools"]:
                raise ValueError(f"Region {row['id']} names unknown tool {row[key]!r}")
    for name, tool in data["tools"].items():
        if not (tool.get("usage", {}).get("zh") and tool.get("usage", {}).get("en")):
            raise ValueError(f"Tool {name} needs a usage line in zh and en: what to type and where to tap")
        if row["status"] not in ("verified", "inferred"):
            raise ValueError(f"Region {row['id']} must be verified or inferred")
    return data


@dataclass(frozen=True)
class Tool:
    id: str

    def app(self, language: str = "zh") -> str:
        return table()["tools"][self.id]["en" if language == "en" else "zh"]

    @property
    def link(self) -> bool:
        return table()["tools"][self.id]["link"]

    def usage(self, language: str = "zh") -> str:
        return table()["tools"][self.id]["usage"]["en" if language == "en" else "zh"]


@dataclass(frozen=True)
class Choice:
    region: str
    places: Tool
    timetable: Tool
    why: dict
    status: str
    source: str


def row(region: str | None) -> dict:
    rows = {entry["id"]: entry for entry in table()["regions"]}
    return rows.get(region or "default", rows["default"])


def choose(region: str | None, *, reports: list[dict] = ()) -> Choice:
    base = row(region)
    applicable = [report for report in reports if report.get("region") in (None, region) and report.get("kind") in KINDS]
    for kind in KINDS:
        latest = [report for report in applicable if report["kind"] == kind]
        if latest:
            report = latest[-1]
            if report["timetable"] not in table()["tools"]:
                raise ValueError(f"Unknown tool {report['timetable']!r} in a {kind} report")
            why = report.get("why") or base["why"]
            return Choice(region=region or "default", places=Tool(base["places"]), timetable=Tool(report["timetable"]),
                          why=why if isinstance(why, dict) else {"zh": why, "en": why},
                          status=kind, source=report.get("source_event") or kind)
    return Choice(region=region or "default", places=Tool(base["places"]), timetable=Tool(base["timetable"]),
                  why=base["why"], status=base["status"], source=base["source"])


def tool_hint(region: str | None, *, reports: list[dict] = (), language: str = "zh") -> dict | None:
    """One line on a less common timetable tool, for the first answer that needs it; None when it is the map app."""
    choice = choose(region, reports=reports)
    if choice.timetable.id in ("google-maps", "apple-maps"):
        return None
    key = "en" if language == "en" else "zh"
    if key == "en":
        line = f"{choice.timetable.app('en')} ({choice.why['en']}): {choice.timetable.usage('en')}"
        line = line[0].upper() + line[1:]
    else:
        line = f"{choice.timetable.app('zh')}（{choice.why['zh']}）：{choice.timetable.usage('zh')}"
    return {"tool": choice.timetable.id, "line": line}
