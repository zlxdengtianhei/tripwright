from __future__ import annotations

import argparse
from pathlib import Path

from tripwright.storage import Store

from . import build_site
from .privacy import Policy


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a private-data-scanned static trip site from a committed version")
    parser.add_argument("db", type=Path)
    parser.add_argument("owner")
    parser.add_argument("out", type=Path)
    parser.add_argument("--language", choices=("zh", "en"))
    parser.add_argument("--privacy-policy", type=Path, help="Private deny/replacement list; one word or word => alias per line")
    arguments = parser.parse_args()
    if not arguments.db.is_file():
        parser.error("Database must already exist")
    policy = Policy(arguments.privacy_policy.read_text(encoding="utf-8").splitlines()) if arguments.privacy_policy else None
    store = Store(arguments.db)
    try:
        output = build_site(store, arguments.owner, arguments.out, language=arguments.language, privacy_policy=policy)
    finally:
        store.close()
    print(f"Built static trip site: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
