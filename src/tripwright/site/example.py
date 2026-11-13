"""The share page of an example file: `python -m tripwright.site.example examples/<file>.yaml <out-folder>`.

An example file holds `trip` (the Trip shape, Chinese as the original text), `english` (each Chinese text → its English)
and optionally `share` (the home page's `intro` and `route`, and `changes`, what really changed; see `share.render_pages`). It is built the same way as a
trip repository's page, privacy scan included, but nothing is read from or written to a repository."""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from tripwright.trip import Trip

from .share import ShareBlocked, share_site


def load_example(path) -> tuple[dict, dict[str, str], dict]:
    """The plan (validated as a Trip), the English pairs and the home page extras of an example file."""
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    plan = Trip.model_validate(document["trip"]).model_dump(mode="json")
    extras = dict(document.get("share") or {})
    if "log" in document:
        extras["log"] = document["log"]
    return plan, dict(document.get("english") or {}), extras


def build_example(path, out_dir, **options) -> dict:
    plan, english, extras = load_example(path)
    return share_site(plan, out_dir, english=english, extras=extras, **options)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the share page of an example trip file")
    parser.add_argument("file", type=Path)
    parser.add_argument("out", type=Path, help="the folder to write; replaced only by a build that passed the scan")
    arguments = parser.parse_args(argv)
    try:
        result = build_example(arguments.file, arguments.out)
    except ShareBlocked as blocked:
        print(blocked)
        return 2
    print(f"{result['out']}: {len(result['pages'])} pages, {len(result['untranslated'])} texts without English, "
          f"swapped {sum(map(len, result['swapped'].values()))} page(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
