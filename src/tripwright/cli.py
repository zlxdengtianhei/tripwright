from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import tempfile
from pathlib import Path

from tripwright.repo.git import GitError
from tripwright.storage import Store, now


def open_service(arguments, *, providers: str | None = None) -> tuple[Store, "Tripwright"]:
    """Single-database mode with --db; repository mode with --repo (the database is the repository's rebuildable index).
    `providers` (live or synthetic) overrides TRIPWRIGHT_PROVIDERS for this process's lookups."""
    from tripwright.query import QueryRouter
    from tripwright.service import Tripwright

    if arguments.repo:
        from tripwright.repo.workspace import Repository

        repository = Repository(arguments.repo, owner=arguments.local_user)
        store = Store(repository.state_path)
        try:
            return store, Tripwright(store, repository=repository, query_router=QueryRouter(store, mode=providers))
        except BaseException:
            store.close()
            raise
    store = Store(arguments.db)
    return store, Tripwright(store, query_router=QueryRouter(store, mode=providers))


def read_repository(arguments) -> int:
    """`check` and `status` only read the copy (s4b): the trip is not opened, so the index is not attached, nothing is
    fetched, committed or pushed and the working tree stays as it is. Opening it for a check attached the index (the
    local commits ahead of the remote became writes waiting to be pushed) and the shutdown flush pushed HEAD: on 11-03
    a check on a copy of my-travel pushed a merge commit still under review. Only `sync` and writes sync."""
    from tripwright.repo.committer import read_status
    from tripwright.repo.files import check
    from tripwright.repo.workspace import Repository

    try:
        repository = Repository(arguments.repo, owner=arguments.local_user)
        if arguments.command == "status":
            print(json.dumps(read_status(repository), ensure_ascii=False, indent=2))
            return 0
        report = check(repository)
    except (ValueError, OSError, GitError) as error:
        print(f"tripwright: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


# `tripwright query` flags and the query argument each fills (A5): the same lookups as the MCP tool, for a host whose
# MCP connection is down. --arguments takes the whole JSON object; a flag overrides its key.
QUERY_FLAGS = (("--origin", "--from", "origin", str), ("--destination", "--to", "destination", str), ("--when", None, "when", str),
               ("--anchor", None, "anchor_id", str), ("--by", None, "by", str), ("--deadline", None, "deadline", str),
               ("--external-deadline", None, "external_deadline", str), ("--after", None, "after", str),
               ("--date", None, "date", str), ("--timezone", None, "timezone", str), ("--text", None, "text", str),
               ("--limit", None, "limit", int), ("--url", None, "url", str), ("--quote", None, "quote", str),
               ("--claim", None, "claim", str), ("--type", None, "type", str), ("--read-from", None, "read_from", str),
               ("--detail", None, "detail", str), ("--part", None, "part", str), ("--request-id", None, "request_id", str),
               ("--near", None, "near", str), ("--at", None, "at", str), ("--km", None, "km", float))


def query_arguments(arguments) -> dict:
    try:
        given = json.loads(arguments.arguments) if arguments.arguments else {}
    except json.JSONDecodeError as error:
        raise ValueError(f"--arguments must be a JSON object: {error}") from None
    if not isinstance(given, dict):
        raise ValueError("--arguments must be a JSON object")
    for _, _, key, _ in QUERY_FLAGS:
        if getattr(arguments, key) is not None:
            given[key] = getattr(arguments, key)
    if arguments.arrive_by:
        given["arrive_by"] = True
    if arguments.action:
        given["action"] = arguments.action
    if arguments.no_quote_check:
        given["check_quote"] = False
    if arguments.text_file == "-" and arguments.question_file == "-":
        raise ValueError("--text-file - and --question - cannot both read standard input")
    for option, name, key in (("--text-file", "text_file", "text"), ("--question", "question_file", "question")):
        if getattr(arguments, name):
            try:
                given[key] = sys.stdin.read() if getattr(arguments, name) == "-" else Path(getattr(arguments, name)).read_text(encoding="utf-8")
            except OSError as error:
                raise ValueError(f"{option}: {error}") from None
    return given


def run_query(arguments) -> int:
    """Print the MCP query result as JSON on stdout and its one-line summary on stderr; refusals read as over MCP.

    S4h-b (off_r1): `check_answer` without `--request-id` (and without `--question`) passed a draft the check never saw
    this question's words for ("本问原话不明，吃的检查没做"). The CLI now takes the session's latest turn itself and says
    on stderr which request_id it took; with no recorded turn at all it refuses with a command that names
    `--request-id`."""
    import asyncio

    try:
        given = query_arguments(arguments)
        store, service = open_service(arguments, providers=arguments.providers)
        if arguments.kind == "check_answer" and not given.get("request_id") and not given.get("question"):
            event = latest_turn(store, arguments.local_user)
            if event is None:
                raise ValueError("check_answer: this session has no turn of the last minutes to take a request_id from; pass this "
                                 f"question's turn: {check_answer_command(arguments, given)} "
                                 "(or its words with --question <file>)")
            given["request_id"] = event["request_id"]
            print(f"check_answer: using the latest turn's request_id '{event['request_id']}' "
                  f"(received {event['receipt']['received_at']}); pass --request-id to name another one", file=sys.stderr)
    except (ValueError, OSError, GitError) as error:
        print(f"tripwright: {error}", file=sys.stderr)
        return 1
    try:
        result = asyncio.run(service.query(arguments.local_user, arguments.kind, given))
    except (ValueError, KeyError, TypeError) as error:
        print(f"tripwright: {error}", file=sys.stderr)
        return 1
    finally:
        service.jobs.stop()
        if service.committer is not None:
            service.committer.stop()
        store.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("summary") or result.get("reason"):
        print(result.get("summary") or result["reason"], file=sys.stderr)
    return 0


POINTS_EXAMPLE = ('for example --points \'[{"kind": "go", "said": "最晚几点出发去机场", "place": "<place ID or stored name>"}, '
                  '{"kind": "eat", "said": "想吃点东西"}]\'')
# S4h-b (quality judge 9, premise C; the off_r1 replay): without the tools the host wrote its own words for a point's
# kind (deadline, route, time, each tried and refused) and every retry cost minutes before the first part. Common
# synonyms are read as the kind they mean; a kind nothing maps to is still refused, with a whole command to paste.
KIND_SYNONYMS = dict(deadline="go", route="go", time="go", latest_departure="go", transit="go", departure="go",
                     food="eat", meal="eat", event="do", activity="do", place="at", where="at")
VALID_KINDS = ("at", "go", "eat", "do", "ticket", "other")


def latest_turn(store: Store, owner: str) -> dict | None:
    """The latest turn this session recorded (its event record), or None when there is none (S4h-b). System-channel
    messages are not questions, and only a message of the last RECENT_QUESTION_MINUTES counts, as in the service's own
    recent-question fallback (S4h2 replay, off_r1: with no turn for this question the CLI took one recorded on another
    day, so the check held the draft to a question nobody had asked)."""
    from datetime import timedelta

    from tripwright.repo.snapshot import received_events
    from tripwright.service import RECENT_QUESTION_MINUTES
    from tripwright.storage import now
    from tripwright.trip import aware

    since = aware(now()) - timedelta(minutes=RECENT_QUESTION_MINUTES)
    events = [event for event in received_events(store, owner) if event["received"].get("channel") != "system"
              and aware(event["receipt"]["received_at"]) >= since]
    return max(reversed(events), key=lambda event: aware(event["receipt"]["received_at"]), default=None)


def check_answer_command(arguments, given: dict) -> str:
    """This check_answer call as a whole command with `--request-id`, to paste (S4h-b)."""
    parts = ["tripwright"]
    if arguments.repo:
        parts += ["--repo", arguments.repo]
    parts += ["query", "check_answer", "--part", str(given.get("part") or "second")]
    if arguments.text_file:
        parts += ["--text-file", arguments.text_file]
    else:
        parts += ["--text", str(given.get("text") or "<draft>")]
    parts += ["--request-id", "<request_id of this question's turn>"]
    return shlex.join(parts)


def first_part_command(arguments, points) -> str:
    """This first-part call with points that pass, as a whole command to paste (S4h-b)."""
    parts = ["tripwright"]
    if arguments.repo:
        parts += ["--repo", arguments.repo]
    elif arguments.db and arguments.db != os.environ.get(
            "TRIPWRIGHT_DB", str(Path.home() / ".local/share/tripwright/tripwright.sqlite3")):
        parts += ["--db", arguments.db]
    parts.append("first-part")
    if arguments.text_file:
        parts += ["--text-file", arguments.text_file]
    elif arguments.text:
        parts += ["--text", arguments.text]
    if arguments.speaker:
        parts += ["--speaker", arguments.speaker]
    parts += ["--points", json.dumps(points, ensure_ascii=False)]
    return shlex.join(parts)


def read_points(arguments) -> tuple[list[dict] | None, str]:
    """first-part's --points as turn takes them, and the note of what was read as what (S4h-b): a wrong shape is
    refused with a right one to copy (A17, self replay off_r1: a list of plain strings was refused with pydantic's
    words alone); plain strings stay refused (A17b: read as points of kind other, the first part answered none of what
    was asked). A kind the host wrote for itself is read as the kind it means (an empty place is no place); a kind
    nothing maps to is refused with a whole corrected command to paste."""
    from tripwright.render.first_part import checked_points

    raw = getattr(arguments, "points", arguments)  # the parsed arguments, or the raw --points string
    if not raw:
        return None, ""
    try:
        points = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"--points must be a JSON list: {error}; {POINTS_EXAMPLE}") from None
    read: list[tuple[str, str]] = []
    if isinstance(points, list):
        entries = [isinstance(point, dict) and isinstance(point.get("kind"), str) for point in points]
        for index, point in enumerate(points):
            if not entries[index]:
                continue
            written = point["kind"]
            folded = written.strip().casefold()
            target = folded if folded in VALID_KINDS else KIND_SYNONYMS.get(folded)
            if target is not None and (target != written or point.get("place") == ""):
                points[index] = dict(point, kind=target, place=None if point.get("place") == "" else point.get("place"))
            if target is not None and folded not in VALID_KINDS:
                read.append((written, target))
        if entries and all(entries):
            kinds = [point["kind"].strip().casefold() for point in points]
            unknown = next((kind for kind in kinds if kind not in VALID_KINDS and kind not in KIND_SYNONYMS), None)
            if unknown is not None:
                fixed = [dict(point, kind=point["kind"].strip().casefold() if point["kind"].strip().casefold() in VALID_KINDS
                              else KIND_SYNONYMS.get(point["kind"].strip().casefold(), "other"),
                              place=None if point.get("place") == "" else point.get("place")) for point in points]
                raise ValueError(f"Invalid points: points.{kinds.index(unknown)}.kind: {unknown!r} is not a kind turn takes "
                                 f"(at, go, eat, do, ticket, other); {POINTS_EXAMPLE}; "
                                 f"try: {first_part_command(arguments, fixed)}")
    try:
        note = "--points: read kind " + ", ".join(f"'{written}' as '{target}'" for written, target in read) if read else ""
        return checked_points(points), note
    except ValueError as error:
        raise ValueError(f"{error}; {POINTS_EXAMPLE}") from None


def run_first_part(arguments) -> int:
    """turn's first part for a host whose Tripwright tools are down (A16): the draft from the stored trip as JSON on
    stdout (`first_part.text` is what to send, `steps` and `next` what to do). Records nothing.

    Nothing else is printed (S4a). The text used to go to stderr as well: in the self replays without MCP (a17b, a16,
    a17) every turn saw the first part as plain lines in a tool result before sending it; two hosts then wrote that it
    was “already above” and others a status line instead of it, and `2>&1 | json.load` failed four times on the mixed
    output. With MCP the same text comes inside turn's JSON, and those runs had no status line."""
    try:
        text = sys.stdin.read() if arguments.text_file == "-" else Path(arguments.text_file).read_text(encoding="utf-8") \
            if arguments.text_file else arguments.text
        if not str(text or "").strip():
            raise ValueError("first-part needs the user's message: --text or --text-file")
        points, note = read_points(arguments)
        if note:
            print(note, file=sys.stderr)
        store, service = open_service(arguments, providers="synthetic")
    except (ValueError, OSError, GitError) as error:
        print(f"tripwright: {error}", file=sys.stderr)
        return 1
    try:
        result = service.draft_first_part(arguments.local_user, text, points=points, speaker=arguments.speaker)
    except (ValueError, KeyError, TypeError) as error:
        print(f"tripwright: {error}", file=sys.stderr)
        return 1
    finally:
        service.jobs.stop()
        if service.committer is not None:
            service.committer.stop()
        store.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def serve_stdio(arguments) -> int:
    """The stdio server answers the host's handshake first and opens the trip repository behind it (A2): a new clone
    rebuilds its index from every file, and a repository that cannot be opened must still let the host connect and
    read the reason from each tool call instead of a crashed process."""
    from tripwright.adapters.mcp import Opening, create_server

    opened: list[Store] = []

    def open_now():
        store, service = open_service(arguments)
        opened.append(store)
        return service

    opening = Opening(open_now)
    try:
        create_server(opening, local_user=arguments.local_user).run("stdio")
    finally:
        opening.stop()
        for store in opened:
            store.close()
    return 0


def run_hook(arguments) -> int:
    """Claude Code hooks for a trip repository (A7). Each reads the hook input on stdin and the session transcript, never
    opens the trip, logs one line per call to `.tripwright/hook.log` (`<time> <event> block|pass|error <note>`) and on
    its own error lets the call through (exit 0, no output).

    - `stop` (Stop hook): print `{"decision": "block", "reason": …}` when the text the host just sent is a second part
      and differs from the last one check_answer passed during this question, or none passed; when the main agent
      dispatched a subagent or looked something up and sent no text with a first part's shape (D-46, D-47); or when a
      first part said a second part follows, or the main agent dispatched or looked something up, and no second part
      is out and no background check is running (D-48). At most 3 blocks per question, then it lets the turn end.
    - `first-part` (PreToolUse hook): when the main agent starts a lookup (a subagent in the foreground, Tripwright's
      query other than check_answer, WebSearch, WebFetch, the tripwright query command line) before any text shown
      after the user's last message passes the first part's checks, print the deny decision with the reason and the
      failing checks. A subagent dispatched in the background (`run_in_background`) goes through (D-46).

    Both read the clock in the trip's zone: the zone check_answer last used, else the repository's tripwright.yaml."""
    import re
    import time
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from tripwright.storage import answers_path, clock

    path = answers_path(arguments.repo, None if arguments.repo else arguments.db)
    log = path.with_name("hook.log") if path else None
    event = arguments.event

    def note(verdict: str, text: str):
        if log is not None:
            try:
                log.parent.mkdir(parents=True, exist_ok=True)
                with log.open("a", encoding="utf-8") as sink:
                    sink.write(f"{datetime.now().astimezone().isoformat(timespec='seconds')} {event} {verdict} {text}\n")
            except OSError:
                pass

    try:
        from tripwright.render import check_answer as checks

        payload = json.loads(sys.stdin.read() or "{}")
        payload = payload if isinstance(payload, dict) else {}
        transcript = []
        source = payload.get("transcript_path")
        if source and Path(source).expanduser().is_file():
            for line in Path(source).expanduser().read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict):
                    transcript.append(entry)
        stored = json.loads(path.read_text(encoding="utf-8")) if path is not None and path.is_file() else {}
        stored = stored if isinstance(stored, dict) else {}
        # the trip's zone: the one check_answer last used (the plan's), else the repository's tripwright.yaml, else UTC
        config = Path(arguments.repo).expanduser() / "tripwright.yaml" if arguments.repo else None
        found = re.search(r"^timezone:\s*(\S+)", config.read_text(encoding="utf-8"), re.M) if config is not None and config.is_file() else None
        zone = ZoneInfo(stored.get("zone") or (found.group(1).strip("'\"") if found else "UTC"))

        def minute_at(shown: float | None) -> int:
            # the service's clock at the moment a text was shown: pinned in replays (TRIPWRIGHT_NOW)
            local = (clock() - timedelta(seconds=time.time() - shown if shown else 0)).astimezone(zone)
            return local.hour * 60 + local.minute

        # each hook counts its blocks per question (session and the last real user message), so it gives way at a cap
        state_path = path.with_name("hook-state.json") if path else None
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path is not None and state_path.is_file() else {}
        question = f"{payload.get('session_id')}:{checks.turn_texts(transcript)[0]}"
        if event == "stop":
            key = f"stop:{question}"
            reason, verdict = checks.stop_decision(payload, stored.get("answers") or {}, transcript, minute_at, blocked=state.get(key, 0))
            decision = {"decision": "block", "reason": reason}
        else:
            key = question
            reason, verdict = checks.first_part_decision(payload, transcript, minute_at, denied=state.get(key, 0))
            decision = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}
        if reason and state_path is not None:
            state = dict(list({**state, key: state.get(key, 0) + 1}.items())[-50:])
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(state), encoding="utf-8")
    except Exception as error:  # a broken hook must not hold the user's answer
        note("error", f"let through: {type(error).__name__}: {error}")
        return 0
    note("block" if reason else "pass", verdict)
    if reason:
        print(json.dumps(decision, ensure_ascii=False))
    return 0


def run_share(arguments) -> int:
    """`tripwright share build|publish`: the page for friends (tripwright.site.share, .publish). Reads the trip from Git
    without opening the service; a blocked scan exits 2 and names the categories and pages, never the values."""
    from tripwright.repo import layout
    from tripwright.site import publish as share_publish
    from tripwright.site.share import ShareBlocked, load_trip, share_site

    state = share_publish.state_dir(arguments.repo)
    english_path = Path(arguments.english).expanduser() if arguments.english else state / "en.yaml"
    try:
        english = layout.parse(english_path.name, english_path.read_text(encoding="utf-8")) if english_path.is_file() else {}
        if not isinstance(english or {}, dict):
            raise ValueError(f"{english_path}: must map each Chinese text to its English")
        english = {str(key): "" if value is None else str(value) for key, value in (english or {}).items()}
        if arguments.action == "build":
            if not arguments.out:
                raise ValueError("share build needs --out <folder>")
            plan, people = load_trip(arguments.repo, trip=arguments.trip)
            result = share_site(plan, arguments.out, people=people, english=english)
            result = dict(out=str(result["out"]), pages=result["pages"], untranslated=len(result["untranslated"]),
                          untranslated_texts=result["untranslated"])
        else:
            result = share_publish.publish(arguments.repo, name=arguments.name, create_repo=arguments.create,
                                           visibility="private" if arguments.private else "public", english=english,
                                           trip=arguments.trip)
            result["untranslated_texts"] = []
    except ShareBlocked as blocked:
        if arguments.action == "publish":
            share_notice(arguments, blocked.findings)
        print(f"tripwright: {blocked}. Nothing was published; the page online stays the previous version.", file=sys.stderr)
        print(json.dumps(dict(ok=False, blocked=blocked.findings), ensure_ascii=False, indent=2))
        return 2
    except (ValueError, OSError, GitError, share_publish.PublishError) as error:
        print(f"tripwright: {error}", file=sys.stderr)
        return 1
    if arguments.action == "publish":
        share_notice(arguments, None)
    if arguments.todo:
        missing = {text: "" for text in (result.get("untranslated_texts") or []) if not english.get(text)}
        if arguments.action == "publish":
            plan, people = load_trip(arguments.repo, trip=arguments.trip)
            from tripwright.site.share import Rules, render_pages

            missing = {text: "" for text in render_pages(plan, Rules.of(plan, people), english=english)[1]}
        if missing:
            english_path.parent.mkdir(parents=True, exist_ok=True)
            english_path.write_text(layout.dump(dict(english, **missing)), encoding="utf-8")
        result["english_file"] = str(english_path)
    result.pop("untranslated_texts", None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def share_notice(arguments, findings):
    """The next turn says the share page is blocked (or no longer): a notice in the repository's index."""
    from tripwright.repo.workspace import Repository
    from tripwright.site.publish import notice

    try:
        store = Store(Repository(arguments.repo, owner=arguments.local_user).state_path)
    except (ValueError, OSError):
        return
    try:
        notice(store, arguments.local_user, findings)
    finally:
        store.close()


def server_logging():
    """One line per event on stderr for the operator (the container log): Tripwright's own at INFO, libraries' at
    WARNING. Nothing logged carries a token, key or connector URL."""
    import logging

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("tripwright").setLevel(os.environ.get("TRIPWRIGHT_LOG_LEVEL", "INFO").upper())


def serve_http(app, arguments):
    import uvicorn

    # No access log: a URL-key connector's path is its credential.
    uvicorn.run(app, host=arguments.host, port=arguments.port, proxy_headers=False, log_level="warning",
                access_log=False, timeout_graceful_shutdown=5)


def environment_config() -> bool:
    from tripwright.remote.envconfig import wanted

    return wanted()


def http_tokens() -> dict[str, str]:
    try:
        tokens = json.loads(os.environ.get("TRIPWRIGHT_TOKENS", "{}"))
        if not isinstance(tokens, dict) or any(not isinstance(token, str) or not token or
                                              not isinstance(owner, str) or not owner for token, owner in tokens.items()):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("TRIPWRIGHT_TOKENS must contain a token-to-user JSON mapping") from None
    return tokens


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tripwright", description="Travel planning with a scoped requirements ledger and committed versions.")
    parser.add_argument("--db", default=os.environ.get("TRIPWRIGHT_DB", str(Path.home() / ".local/share/tripwright/tripwright.sqlite3")))
    parser.add_argument("--repo", default=os.environ.get("TRIPWRIGHT_REPO"),
                        help="Trip repository folder: its files are the canonical records; the index lives in <repo>/.tripwright/")
    parser.add_argument("--local-user", default="local", help="Local CLI/stdio identity; HTTP always uses the bearer mapping")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Expose exactly seven MCP tools")
    transports = serve.add_mutually_exclusive_group(required=True)
    transports.add_argument("--stdio", action="store_true")
    transports.add_argument("--http", action="store_true")
    serve.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8000), help="Default: $PORT, else 8000")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--remote-config", default=os.environ.get("TRIPWRIGHT_REMOTE_CONFIG"),
                       help="HTTP: operator YAML mapping GitHub accounts to tenant repositories; secrets stay in env or a mode-600 file")
    commands.add_parser("demo", help="Run the fictional vertical slice in a temporary database")
    hook = commands.add_parser("hook", help="Claude Code hooks: stop blocks a second part that differs from what check_answer passed; "
                                            "first-part denies lookups until the first part is sent")
    hook.add_argument("event", choices=["stop", "first-part"])
    query = commands.add_parser("query", help="Look up transit, the latest departure for a deadline, record a web check, or "
                                              "check an answer draft: the MCP query tool's JSON on stdout, a one-line summary on stderr")
    query.add_argument("kind", choices=["transit", "latest_departure", "web_evidence", "check_answer", "open_places", "weather", "geocode",
                                        "nearby", "saved", "opening"])
    query.add_argument("--arguments", help="The query arguments as one JSON object (flags below override its keys)")
    for flag, alias, key, kind in QUERY_FLAGS:
        query.add_argument(*[name for name in (flag, alias) if name], dest=key, type=kind)
    query.add_argument("--action", choices=["stops"], help="transit: stops looks up stop names (with --text)")
    query.add_argument("--arrive-by", action="store_true", help="transit: --when is the arrival time")
    query.add_argument("--no-quote-check", action="store_true", help="web_evidence: do not fetch the page to look for the quote")
    query.add_argument("--text-file", help="check_answer: the draft's file (- for standard input), instead of --text")
    query.add_argument("--question", dest="question_file", help="check_answer: a file (- for standard input) with this question's words "
                       "as the user sent them; without it or --request-id, the latest user message of the last 30 minutes")
    query.add_argument("--providers", choices=["live", "synthetic"], default=os.environ.get("TRIPWRIGHT_PROVIDERS", "live"),
                       help="live data (default) or the fictional demo data")
    first = commands.add_parser("first-part", help="Without the MCP tools: turn's first part from the stored trip (JSON on stdout; "
                                                    "first_part.text is what to send). Records nothing")
    first.add_argument("--text", help="The user's message")
    first.add_argument("--text-file", help="A file with the user's message (- for standard input), instead of --text")
    first.add_argument("--points", help="This question's points as a JSON list, as turn takes them: "
                                        "[{\"kind\": \"at|go|eat|do|ticket|other\", \"said\": …, \"place\": …}]")
    first.add_argument("--speaker", help="The writer's person ID")
    init = commands.add_parser("init", help="Create a trip repository (Git): profile/, trips/<trip>/, agent files")
    init.add_argument("folder")
    init.add_argument("--trip", help="First trip folder name, e.g. 2031-05-wien")
    init.add_argument("--timezone", help="Default IANA time zone for messages before a plan exists")
    init.add_argument("--remote", help="URL of the canonical remote (added as origin)")
    init.add_argument("--upgrade", action="store_true", help="Refresh the tool's files in an existing trip repository")
    init.add_argument("--from-db", help="Write this database's trip (for --local-user) into the new repository, one commit per version")
    commands.add_parser("sync", help="Commit pending writes, fetch the canonical branch, replay on a new head and push")
    commands.add_parser("check", help="Check the repository: files parse, messages unchanged, regenerated = committed (read only: no fetch, push or commit)")
    commands.add_parser("status", help="Show the canonical head, unsynced writes and the oldest one, from the index (read only: no fetch, push or commit)")
    new_trip = commands.add_parser("new-trip", help="Open another trip folder and import long-term entries as pending")
    new_trip.add_argument("trip")
    new_trip.add_argument("--message", help="What the user said (recorded as a host restatement)")
    share = commands.add_parser("share", help="The page for friends: build it with names, stays and flight numbers rewritten and "
                                              "scanned, or publish it to its own GitHub Pages repository (needs --repo)")
    share.add_argument("action", choices=["build", "publish"])
    share.add_argument("--out", help="build: the folder to write; it is replaced only by a build that passed the scan")
    share.add_argument("--trip", help="Trip folder name (default: the repository's current trip)")
    share.add_argument("--english", help="YAML mapping each rewritten Chinese text to its English "
                                         "(default <repo>/.tripwright/share/en.yaml)")
    share.add_argument("--todo", action="store_true", help="Add the texts still without English to the --english file, "
                                                           "with empty values to fill in")
    share.add_argument("--name", help="publish: the share repository's name, e.g. trip-2026-wien (first time)")
    share.add_argument("--create", action="store_true", help="publish, first time: gh repo create --public, push, turn on Pages")
    share.add_argument("--private", action="store_true", help="publish --create: a private repository instead "
                                                              "(GitHub serves Pages from private repositories only on paid plans)")
    agents = commands.add_parser("agents", help="Rebuild or check the generated role files in a checkout")
    agents.add_argument("agents_args", nargs=argparse.REMAINDER)
    export = commands.add_parser("export", help="Export the selected local user's round-trip directory")
    export.add_argument("destination")
    restore = commands.add_parser("import", help="Import an exported directory into an empty user store")
    restore.add_argument("source")
    arguments = parser.parse_args(argv)
    if arguments.command == "agents":
        from tripwright.agents.__main__ import main as agents_main

        return agents_main(arguments.agents_args)
    if arguments.command == "demo":
        from tripwright.demo import run_demo
        from tripwright.service import Tripwright

        with tempfile.TemporaryDirectory(prefix="tripwright-demo-") as directory:
            store = Store(Path(directory) / "demo.sqlite3")
            try:
                run_demo(Tripwright(store))
            finally:
                store.close()
        return 0
    if arguments.command == "init":
        from tripwright.repo import workspace

        try:
            if arguments.from_db:
                from tripwright.repo.migrate import export_store

                source = Store(arguments.from_db)
                try:
                    repository = export_store(source, arguments.local_user, arguments.folder, remote=arguments.remote)
                finally:
                    source.close()
            else:
                repository = workspace.init(arguments.folder, trip=arguments.trip, timezone=arguments.timezone,
                                            remote=arguments.remote, upgrade=arguments.upgrade)
        except (ValueError, OSError) as error:
            print(f"tripwright: {error}", file=sys.stderr)
            return 1
        print(f"Trip repository ready: {repository.root}")
        print(f"Use it with: tripwright --repo {repository.root} serve --stdio")
        return 0
    if arguments.command in ("sync", "check", "status", "new-trip", "share") and not arguments.repo:
        print(f"tripwright: {arguments.command} needs --repo <folder> (or TRIPWRIGHT_REPO)", file=sys.stderr)
        return 1
    if arguments.command == "serve" and arguments.stdio:
        return serve_stdio(arguments)
    if arguments.command == "query":
        return run_query(arguments)
    if arguments.command == "first-part":
        return run_first_part(arguments)
    if arguments.command == "serve" and arguments.http:
        server_logging()
    if arguments.command == "serve" and arguments.http and (arguments.remote_config or environment_config()):
        from tripwright.remote.app import create_app, url_keys
        from tripwright.remote.config import load_config
        from tripwright.remote.envconfig import config_from_env
        from tripwright.remote.urlkey import key_file

        try:
            config = load_config(arguments.remote_config) if arguments.remote_config else config_from_env()
            app = create_app(config, http_tokens(), host=arguments.host, keys=url_keys(),
                             key_file=key_file(config.data_dir))
            serve_http(app, arguments)
        except (ValueError, OSError, GitError) as error:
            print(f"tripwright: {error}", file=sys.stderr)
            return 1
        return 0
    if arguments.command == "hook":
        return run_hook(arguments)
    if arguments.command == "share":
        return run_share(arguments)
    if arguments.command in ("check", "status"):
        return read_repository(arguments)
    try:
        if arguments.command == "serve":
            from tripwright.remote.volume import check_volume

            # The marker goes beside the database: in a trip repository that is the ignored .tripwright/ folder.
            check_volume(Path(arguments.repo).expanduser().resolve() / ".tripwright" if arguments.repo
                         else Path(arguments.db).expanduser().resolve().parent)
            # A served instance answers other people's questions: live data unless the demo data is asked for by name.
            store, service = open_service(arguments, providers=os.environ.get("TRIPWRIGHT_PROVIDERS") or "live")
        else:
            store, service = open_service(arguments)
    except (ValueError, OSError, GitError) as error:
        print(f"tripwright: {error}", file=sys.stderr)
        return 1
    try:
        if arguments.command == "export":
            print(store.export(arguments.local_user, arguments.destination))
        elif arguments.command == "import":
            store.import_directory(arguments.local_user, arguments.source)
            print("Imported into the empty local user store.")
        elif arguments.command == "sync":
            print(json.dumps(service.committer.sync(), ensure_ascii=False, indent=2, default=str))
            print(json.dumps(service.committer.status(), ensure_ascii=False, indent=2))
        elif arguments.command == "new-trip":
            opened = service.turn(arguments.local_user, arguments.message or f"开新一趟：{arguments.trip}", f"cli-new-trip-{arguments.trip}-{now()}",
                                  new_trip=arguments.trip)
            service.committer.flush()
            print(json.dumps(dict(trip=arguments.trip, write=opened["write"], pending=opened.get("memory", {}).get("pending", [])),
                             ensure_ascii=False, indent=2))
        else:
            from tripwright.adapters.mcp import http_app

            serve_http(http_app(service, http_tokens(), host=arguments.host, health=True), arguments)
    except (ValueError, OSError, GitError) as error:
        print(f"tripwright: {error}", file=sys.stderr)
        return 1
    finally:
        service.jobs.stop()
        if service.committer is not None:
            service.committer.stop()
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
