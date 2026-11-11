"""`python -m tripwright.agents build [--check]`: rebuild or check the generated role files in a checkout."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tripwright.agents.assemble import GENERATOR, ROOT, AssembleError, build, check, load_sources, write
from tripwright.agents.promises import promise_problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tripwright.agents", description="Assemble Tripwright skills and agent files.")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("build", help="Regenerate every generated file from the role, step and fragment sources")
    command.add_argument("--check", action="store_true", help="Change nothing; exit 1 if a generated file differs from a fresh build")
    command.add_argument("--root", default=str(ROOT), help="Repository checkout to build (default: this checkout)")
    arguments = parser.parse_args(argv)
    root = Path(arguments.root)
    try:
        sources = load_sources(root)
        outputs = build(sources)
    except AssembleError as error:
        print(f"tripwright.agents: {error}", file=sys.stderr)
        return 1
    problems = promise_problems(outputs)
    if arguments.check:
        problems += check(root, outputs)
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1 if problems else 0
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        print(f"tripwright.agents: nothing written; fix the sources and run {GENERATOR} again", file=sys.stderr)
        return 1
    changed = write(root, outputs)
    print(f"{len(changed)} file(s) written" + "".join(f"\n  {path}" for path in changed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
