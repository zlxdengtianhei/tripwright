"""Find the entries of the user's repository through a rebuildable index (Q19, 2026-11-23: the user's Git repository
is the database; the core is finding the right entries in it, fast, and saying where each one is stored).

`recall.search` reads and cuts every entry of the repository on every query: at two thousand entries one query took
about two seconds, and a real trip's repository passes that within a year. This module keeps what that cutting
produces in a SQLite FTS5 index beside the repository (`.tripwright/search.sqlite`, under the folder `.tripwright/`
that the repository already ignores and that a rebuild can throw away). A query asks the index which entries carry its
words, and only scores those.

The index holds, for every entry `recall.corpus` builds, the words it is searched by (cut by `segments.py`, the travel
word table and the place names, exactly as `recall.search` cuts them), the repository file the entry is stored in, the
line of its `id:` and the commit of the working tree that file is in. Chinese words go in as one word each, as they
are cut, not as character n-grams: a second FTS5 table with the trigram tokenizer holds the raw text and answers only
when the word table finds nothing (a place name the word table splits differently still matches).

Q20 (2026-11-23): every hit also carries the line of the file its match falls on (`snippet`, cut by FTS5's
`snippet()` from that line, and `snippet_line`). The third table holds each record's own text as its file has it, with
a space between two adjacent CJK characters so `unicode61` cuts Chinese into characters and a two-character word stays
a phrase; the snippet is that match's line read back from the file, so it is the record's own words, not a summary.

The index is derived: deleting it rebuilds the same rows from the repository's files and store, and the entries of a
query, their order and their scores are the same as the scan's. Each query compares the entries with the rows it holds
and cuts again only the entries that changed (one new record is one new entry); the commit and file of unchanged rows
are refreshed without cutting their words again.

The signature that decides whether to rebuild carries both word tables (`recall.yaml` and `place_aliases.yaml`):
editing a table rebuilds the index, so a question is never scored with words cut by the old table while the scan cuts
them with the new one. A hit's file is the record's own canonical file (`layout.record_path`, `plan/days/<day>.yaml`,
`layout.evidence_path`, the raw message file); a copy of its `id:` inside another file (the evidence a query writes)
never takes its place.
"""
from __future__ import annotations

import json
import re
import sqlite3
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

from tripwright import recall
from tripwright.repo import layout

SCHEMA = 2
FILE = "search.sqlite"
CANDIDATES = 8000  # more than a trip's repository holds today; the index is not the limit on a query's cost
ID_LINE = re.compile(r"^\s{0,8}id:\s*['\"]?([^'\"\s]+)['\"]?\s*$")
JSON_ID = re.compile(r'"id"\s*:\s*"([^"]+)"')
NOTE_LINE = re.compile(r"^\s{0,8}notes:")
FTS_TOKEN = re.compile(r"[^0-9a-z]+")
CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")
CJK_GAP = "\x01"  # between two adjacent CJK characters in the snippet table, so a two-character word is a phrase
SNIPPET_TOKENS = 60  # a snippet's window; the line it falls on is then read whole from the passage
SNIPPET_CHARS = 200  # a line longer than this is cut, so one hit does not carry a whole message


def index_path(repository) -> Path:
    return Path(repository.root) / ".tripwright" / FILE


@lru_cache(maxsize=None)
def codeword(term: str) -> str:
    """A term as one FTS5 token: unicode61 splits Chinese into single characters and `§` is a separator, so a term
    (a word, a travel-word group, a place, a day) is carried into the table as its hash. The same mapping on the query
    side finds it; the words themselves stay in `doc_terms` for the score."""
    return "t" + sha256(term.encode("utf-8")).hexdigest()[:16]


def plan_mark(plan: dict | None) -> str:
    return layout.plan_digest(plan) or "none"


def space_cjk(text: str) -> str:
    """The text with a separator between two adjacent CJK characters, so `unicode61` cuts them into one token each and
    a two-character Chinese word is a phrase the snippet table can match. The separator is U+0001, a control character
    `unicode61` treats like a space and no message carries, so `unspace_cjk` restores the text exactly (a plain space
    cannot: a text may hold one between two Chinese characters)."""
    out = []
    for before, after in zip(text, text[1:]):
        out.append(before + CJK_GAP if CJK.match(before) and CJK.match(after) else before)
    return "".join(out) + (text[-1:] if text else "")


def unspace_cjk(text: str) -> str:
    """`space_cjk` undone: every separator it put in is taken out, the text's own characters stay."""
    return text.replace(CJK_GAP, "")


def snippet_phrases(terms: set[str], plan: dict | None) -> list[str]:
    """The FTS5 phrases that find a match in the snippet table: every spelling a matched term can take, spaced so a
    Chinese word is a phrase. Empty when the matched terms can only be dates (which never make a hit alone)."""
    phrases = []
    for term in sorted(terms):
        for form in recall.term_forms(term, plan):
            spaced = space_cjk(str(form))
            if spaced.strip() and spaced not in phrases:
                phrases.append(spaced)
    return phrases[:40]


def match_expression(phrases: list[str]) -> str:
    return " OR ".join('"' + phrase.replace('"', '""') + '"' for phrase in phrases)


def matched_line(passage: str, start: int, stop: int, phrases: list[str]) -> tuple[int, bool]:
    """Where the line of `passage[start:stop]` that holds a matched spelling begins, as the index folds it (lower case,
    no accents), and whether one does. `start` and `False` when no line holds a spelling: the window's first line is
    all the index can say then (Q20: a Latin word in capitals or with an accent used to fall back to it, so the hit
    named a line the match was not on)."""
    needles = [folded for folded in (recall.fold(unspace_cjk(phrase).strip()) for phrase in phrases) if folded]
    begin = start
    while begin <= stop:
        finish = passage.find("\n", begin)
        finish = len(passage) if finish < 0 else finish
        if needles and any(needle in recall.fold(passage[begin:finish]) for needle in needles):
            return begin, True
        begin = finish + 1
    return start, False


class Index:
    def __init__(self, repository):
        self.repository = repository
        self.path = index_path(repository)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), timeout=5)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS docs (
                id INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL, found TEXT NOT NULL, record_id TEXT NOT NULL,
                path TEXT NOT NULL, line INTEGER NOT NULL, revision TEXT NOT NULL, digest TEXT NOT NULL,
                terms TEXT NOT NULL, words TEXT NOT NULL, body TEXT NOT NULL,
                passage TEXT NOT NULL DEFAULT '', passage_line INTEGER NOT NULL DEFAULT 0, snip TEXT NOT NULL DEFAULT '');
            CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(terms, content='docs', content_rowid='id', tokenize='unicode61');
            CREATE VIRTUAL TABLE IF NOT EXISTS docs_tri USING fts5(body, content='docs', content_rowid='id', tokenize='trigram');
            CREATE VIRTUAL TABLE IF NOT EXISTS docs_snip USING fts5(snip, content='docs', content_rowid='id', tokenize='unicode61');
            CREATE TABLE IF NOT EXISTS doc_terms (doc_id INTEGER NOT NULL, term TEXT NOT NULL, PRIMARY KEY(doc_id, term)) WITHOUT ROWID;
            CREATE INDEX IF NOT EXISTS doc_terms_term ON doc_terms(term);
        """)
        self._upgrade()
        self.db.commit()

    def _upgrade(self):
        """An index written by the first schema (no passage) gains its columns; its rows keep empty passages and the
        next query refills them (the schema mark moves, so the query rebuilds the rows)."""
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(docs)").fetchall()}
        for name, declaration in (("passage", "TEXT NOT NULL DEFAULT ''"), ("passage_line", "INTEGER NOT NULL DEFAULT 0"),
                                  ("snip", "TEXT NOT NULL DEFAULT ''")):
            if name not in columns:
                self.db.execute(f"ALTER TABLE docs ADD COLUMN {name} {declaration}")

    def close(self):
        self.db.close()

    # --- rows -----------------------------------------------------------------

    def meta(self) -> dict:
        return dict(self.db.execute("SELECT key, value FROM meta").fetchall())

    def set_meta(self, values: dict):
        self.db.executemany("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            list(values.items()))

    def rows(self) -> dict[str, tuple[str, str, int, int]]:
        return {key: (digest, path, line, passage_line) for key, digest, path, line, passage_line
                in self.db.execute("SELECT key, digest, path, line, passage_line FROM docs").fetchall()}

    def replace(self, key: str, found: str, record_id: str, digest: str, terms: set[str], body: str,
                path: str, line: int, commit: str, passage: str = "", passage_line: int = 0):
        old = self.db.execute("SELECT id FROM docs WHERE key=?", (key,)).fetchone()
        if old:
            self.db.execute("DELETE FROM doc_terms WHERE doc_id=?", (old[0],))
            self.db.execute("DELETE FROM docs WHERE id=?", (old[0],))
        cursor = self.db.execute("INSERT INTO docs(key, found, record_id, path, line, revision, digest, terms, words, body, "
                                 "passage, passage_line, snip) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                 (key, found, record_id, path, line, commit, digest,
                                  " ".join(sorted(codeword(term) for term in terms)),
                                  json.dumps(sorted(terms), ensure_ascii=False), body,
                                  passage, passage_line, space_cjk(passage)))
        self.db.executemany("INSERT INTO doc_terms(doc_id, term) VALUES(?, ?)", [(cursor.lastrowid, term) for term in terms])

    def remove(self, key: str):
        old = self.db.execute("SELECT id FROM docs WHERE key=?", (key,)).fetchone()
        if old:
            self.db.execute("DELETE FROM doc_terms WHERE doc_id=?", (old[0],))
            self.db.execute("DELETE FROM docs WHERE id=?", (old[0],))

    def reindex(self):
        self.db.execute("INSERT INTO docs_fts(docs_fts) VALUES('rebuild')")
        self.db.execute("INSERT INTO docs_tri(docs_tri) VALUES('rebuild')")
        self.db.execute("INSERT INTO docs_snip(docs_snip) VALUES('rebuild')")

    def clear(self):
        self.db.execute("DELETE FROM doc_terms")
        self.db.execute("DELETE FROM docs")

    # --- building -----------------------------------------------------------------

    def locations(self, files: dict[str, str]) -> dict[str, tuple[str, int]]:
        """Record ID → (file, line of its `id:`) over the working tree; one pass, first file wins. Only the last
        resort: a query writes its hits (with their ids) into an evidence file, so a copy of an entry can sit in a
        file the walk reaches before the entry's own file; `location_of` therefore tries the record's canonical file
        first and falls back here only when that file holds no `id:` line for it."""
        found: dict[str, tuple[str, int]] = {}
        for path, text in files.items():
            if not path.endswith((".yaml", ".md", ".jsonl")):
                continue
            for number, raw in enumerate(text.splitlines(), 1):
                match = ID_LINE.match(raw) or (JSON_ID.search(raw) if path.endswith(".jsonl") else None)
                if match:
                    found.setdefault(match.group(1), (path, number))
            stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            found.setdefault(stem, (path, 1))
        return found

    def line_in(self, path: str, text: str, record_id: str) -> int:
        """The line of `id: <record id>` in one file's text, 0 when that file does not hold it."""
        for number, raw in enumerate(text.splitlines(), 1):
            match = ID_LINE.match(raw) or (JSON_ID.search(raw) if path.endswith(".jsonl") else None)
            if match and match.group(1) == record_id:
                return number
        return 0

    def canonical_path(self, entry: dict, plan: dict | None, files: dict[str, str],
                       raw: dict[str, str] | None = None) -> str:
        """The file the entry's record is stored in, by the repository's own layout: a plan entry in
        `plan/days/<day>.yaml`, a place in `plan/places.yaml`, a booking in `plan/bookings.yaml`, a ledger record by
        `layout.record_path`, a web check or lookup by `layout.evidence_path`, the user's words in their raw file.
        Empty when the layout cannot name the file (no plan yet, a message the tree no longer holds)."""
        if not plan:
            return ""
        base = layout.trip_folder(plan["id"])
        found, record_id = entry["found"], entry["id"]
        if found == "plan":
            return f"{base}/plan/days/{record_id.partition('/')[0]}.yaml"
        if found == "place":
            return f"{base}/plan/places.yaml"
        if found == "booking":
            return f"{base}/plan/bookings.yaml"
        table = {"fact": "facts", "item": "items", "decision": "decisions", "check": "pending_checks"}.get(found)
        if table:
            return f"{base}/{layout.record_path(table, {'id': record_id})}"
        if found in ("web", "lookup"):
            try:
                return f"{base}/{layout.evidence_path({'id': record_id, 'date': entry.get('date'),
                                                     'fetched_at': entry.get('fetched_at')})}"
            except (TypeError, ValueError):
                return ""
        if found == "said":
            return (raw if raw is not None else layout.raw_paths(files)).get(record_id.rsplit("-", 1)[-1], "")
        return ""

    def committed_paths(self) -> set[str]:
        head = self.repository.git.rev()
        if not head:
            return set()
        try:
            return set(self.repository.git.run("ls-tree", "-r", "--name-only", head).splitlines())
        except Exception:
            return set()

    def location_of(self, entry: dict, plan: dict | None, located: dict[str, tuple[str, int]],
                    files: dict[str, str], raw: dict[str, str] | None = None) -> tuple[str, int]:
        record_id = entry["id"]
        path = self.canonical_path(entry, plan, files, raw)
        text = files.get(path) if path else None
        if path and text is not None:
            if entry["found"] == "plan" and record_id.endswith("/notes"):
                for number, line_text in enumerate(text.splitlines(), 1):
                    if NOTE_LINE.match(line_text):
                        return path, number
            # a plan entry's id carries its day ("2032-01-10/a1031"); the day file holds the block's own id
            wanted = record_id.partition("/")[2] if entry["found"] == "plan" and "/" in record_id else record_id
            number = self.line_in(path, text, wanted)
            if number:
                return path, number
        if record_id in located:
            return located[record_id]
        if entry["found"] == "plan" and plan and "/" in record_id:
            tail = record_id.partition("/")[2]
            if tail in located and located[tail][0] == path:
                return located[tail]
        # the canonical file is the right file but does not hold the id yet (a record written before its commit):
        # line 0 says "position unknown", so `refresh` relocates the row on the next query instead of keeping a guess
        return (path, 0) if path else ("", 0)

    def digest_of(self, entry: dict) -> str:
        return sha256(json.dumps([entry["haystack"], entry.get("text") or "", entry.get("source") or "",
                                  bool(entry.get("in_force", True))], ensure_ascii=False).encode()).hexdigest()

    # --- the passage a snippet is cut from ------------------------------------------

    def block(self, text: str, line: int) -> tuple[str, int]:
        """The lines of one record inside a YAML file, so a snippet is cut from the record's own text and not from a
        neighbour's: the list item that holds the `id:` line (the item's first line may be above the id, keys are
        sorted), a `notes` entry from its key to the next top-level key, and a file holding one record as a whole."""
        lines = text.splitlines()
        if not lines:
            return "", 0
        start = min(max(line - 1, 0), len(lines) - 1)
        raw = lines[start]
        stripped = raw.lstrip()
        indent = len(raw) - len(stripped)
        if stripped.startswith("- "):
            item = True
        elif NOTE_LINE.match(raw):
            item = False  # a day's notes: from their key to the next top-level key
        elif indent > 0:
            item = False
            for above in range(start - 1, -1, -1):
                other = lines[above]
                other_stripped = other.lstrip()
                other_indent = len(other) - len(other_stripped)
                if other_stripped.startswith("- ") and other_indent < indent:
                    item, start, indent = True, above, other_indent
                    break
            if not item:
                return text.rstrip("\n"), 1  # no list item above it: the whole file is this record
        else:
            return text.rstrip("\n"), 1  # one record per file: the whole file is its text
        end = start + 1
        while end < len(lines):
            other = lines[end]
            other_stripped = other.strip()
            if other_stripped:
                this = len(other) - len(other.lstrip())
                if other_stripped == "---" or this < indent:
                    break
                if this == indent and other_stripped.startswith("- "):
                    break
                if this == indent and not item and re.match(r"[A-Za-z_]+:", other_stripped):
                    break  # a top-level key ends a notes block
            end += 1
        return "\n".join(lines[start:end]), start + 1

    def passage(self, entry: dict, path: str, text: str | None, line: int) -> tuple[str, int]:
        """The record's own text as its file holds it, and the file line it starts on: a message's body without the
        front matter, a Markdown record's whole file, a YAML record's block. Empty when the file is not there."""
        if text is None or not path or not line:
            # no file, or the record's line in it is not known yet (`line: 0`: written but not committed): a line of
            # that file would be a guess at another record's text, so this row carries no snippet until it is known
            return "", 0
        if entry["found"] == "said":
            head_end = text.find("\n---\n", 4)
            if text.startswith("---\n") and head_end >= 0:
                return text[head_end + 5:].rstrip("\n"), text[:head_end + 5].count("\n") + 1
            return text.rstrip("\n"), 1
        if path.endswith(".md"):
            return text.rstrip("\n"), 1
        return self.block(text, line)

    def snippet(self, key: str, passage: str, passage_line: int, phrases: list[str]) -> tuple[str, int, bool] | None:
        """The line of the record's file the match falls on, cut by FTS5's `snippet()` and read whole from the passage
        so it is the file's own line; that line's number; and whether the line holds a spelling of the match (True) or
        is the window's first line because none of them is there (False). None when nothing in the passage matches."""
        if not passage or not phrases:
            return None
        try:
            row = self.db.execute("SELECT snippet(docs_snip, 0, '', '', '', ?) FROM docs_snip WHERE docs_snip MATCH ? AND "
                                  "rowid = (SELECT id FROM docs WHERE key=?)",
                                  (SNIPPET_TOKENS, match_expression(phrases), key)).fetchone()
        except sqlite3.OperationalError:
            return None
        if not row or not row[0]:
            return None
        window = unspace_cjk(row[0]).strip()
        at = passage.find(window) if window else -1
        if at < 0:
            return None
        # the window's lines, read from the passage so each is the file's own line; the first one that holds a matched
        # spelling is the match's line, and only when none does is the window's first line the answer
        begin, exact = matched_line(passage, passage.rfind("\n", 0, at) + 1, at + len(window), phrases)
        finish = passage.find("\n", begin)
        finish = len(passage) if finish < 0 else finish
        line = passage[begin:finish]
        if len(line) > SNIPPET_CHARS:
            line = line[:SNIPPET_CHARS - 1] + "…"
        return line, (passage_line + passage[:begin].count("\n")) if passage_line else 0, exact

    def rebuild(self, entries: list[dict], plan: dict | None, *, zone: str | None, language: str, owner: str):
        """The rows for every entry, cut again; the commit and the file of each come from the working tree."""
        files = self.repository.working()
        located = self.locations(files)
        raw = layout.raw_paths(files)
        head = self.repository.git.rev() or ""
        committed = self.committed_paths()
        with self.db:
            self.clear()
            for entry in entries:
                path, line = self.location_of(entry, plan, located, files, raw)
                commit = head if path and path in committed else ""
                passage, passage_line = self.passage(entry, path, files.get(path) if path else None, line)
                self.replace(f"{entry['found']}:{entry['id']}", entry["found"], entry["id"], self.digest_of(entry),
                             recall.entry_terms(entry, plan, zone=zone), entry["haystack"], path, line, commit,
                             passage, passage_line)
            self.reindex()
            self.set_meta(dict(schema=str(SCHEMA), owner=owner, language=language, zone=zone or "",
                               plan=plan_mark(plan), words=recall.word_tables_digest(), head=head,
                               built=str(len(entries))))

    def refresh(self, entries: list[dict], plan: dict | None, *, zone: str | None, language: str, owner: str):
        """Cut again only the entries that changed; refresh the file and the commit of the rest."""
        rows = self.rows()
        head = self.repository.git.rev() or ""
        current = {f"{entry['found']}:{entry['id']}": entry for entry in entries}
        changed = [key for key, entry in current.items() if key not in rows or rows[key][0] != self.digest_of(entry)]
        # an entry written while its file was not there yet (the turn records before the commit writes it): find it now
        relocate = [key for key in current if key in rows and (not rows[key][1] or rows[key][2] == 0 or rows[key][3] == 0)]
        removed = [key for key in rows if key not in current]
        meta = self.meta()
        if not changed and not removed and not relocate and meta.get("head") == head:
            return
        located, files, raw, committed = {}, {}, {}, set()
        if changed or relocate:
            files = self.repository.working()
            located = self.locations(files)
            raw = layout.raw_paths(files)
            committed = self.committed_paths() if head else set()
        elif head != meta.get("head"):
            committed = self.committed_paths() if head else set()
        with self.db:
            for key in removed:
                self.remove(key)
            for key in changed:
                entry = current[key]
                path, line = self.location_of(entry, plan, located, files, raw)
                commit = head if path and path in committed else ""
                passage, passage_line = self.passage(entry, path, files.get(path) if path else None, line)
                self.replace(key, entry["found"], entry["id"], self.digest_of(entry),
                             recall.entry_terms(entry, plan, zone=zone), entry["haystack"], path, line, commit,
                             passage, passage_line)
            for key in relocate:
                if key in changed:
                    continue
                entry = current[key]
                path, line = self.location_of(entry, plan, located, files, raw)
                commit = head if path and path in committed else ""
                passage, passage_line = self.passage(entry, path, files.get(path) if path else None, line)
                self.db.execute("UPDATE docs SET path=?, line=?, revision=?, passage=?, passage_line=?, snip=? WHERE key=?",
                                (path, line, commit, passage, passage_line, space_cjk(passage), key))
            if changed or removed or relocate:
                # a relocated row's passage and snippet source were just filled in; the snippet table reads the
                # content table, so it must be rebuilt with them
                self.reindex()
            if head != meta.get("head"):
                # the commit of unchanged rows follows the working tree's head; a file not in the head keeps none
                for key, path in self.db.execute("SELECT key, path FROM docs").fetchall():
                    if key in current:
                        commit = head if path and path in committed else ""
                        self.db.execute("UPDATE docs SET revision=? WHERE key=? AND revision<>?", (commit, key, commit))
            self.set_meta(dict(head=head, built=str(len(current))))

    def ensure(self, store, owner: str, plan: dict | None, *, at, zone: str | None, language: str) -> list[dict]:
        """The entries of the corpus, with the index brought up to date and closed."""
        entries = recall.corpus(store, owner, plan, at=at, zone=zone, language=language)
        meta = self.meta()
        signature = dict(schema=str(SCHEMA), owner=owner, language=language, zone=zone or "", plan=plan_mark(plan),
                         words=recall.word_tables_digest())
        if any(meta.get(key) != value for key, value in signature.items()):
            self.rebuild(entries, plan, zone=zone, language=language, owner=owner)
        else:
            self.refresh(entries, plan, zone=zone, language=language, owner=owner)
        return entries


def _matches(index: Index, query: set[str]) -> list[str]:
    words = sorted(codeword(term) for term in query)
    pattern = " OR ".join(f'"{word}"' for word in words)
    rows = index.db.execute("SELECT d.key FROM docs_fts JOIN docs d ON d.id = docs_fts.rowid "
                            "WHERE docs_fts MATCH ? LIMIT ?", (pattern, CANDIDATES)).fetchall()
    return [row[0] for row in rows]


def _trigram(index: Index, text: str) -> list[str]:
    """Fallback candidates: the entries whose raw text holds a three-character piece of the question."""
    folded = recall.fold(text)
    phrases = []
    for run in recall.CJK.findall(folded):
        for start in range(max(1, len(run) - 2)):
            if len(run) - start >= 3:
                phrases.append(run[start:start + 3])
    phrases += [word for word in recall.LATIN.findall(folded) if len(word) >= 3][:4]
    phrases = list(dict.fromkeys(phrases))[:12]
    if not phrases:
        return []
    pattern = " OR ".join(f'"{phrase}"' for phrase in phrases)
    try:
        rows = index.db.execute("SELECT d.key FROM docs_tri JOIN docs d ON d.id = docs_tri.rowid "
                                "WHERE docs_tri MATCH ? LIMIT ?", (pattern, CANDIDATES)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [row[0] for row in rows]


def _frequency(index: Index, query: set[str]) -> dict[str, int]:
    terms = sorted(query)
    rows = index.db.execute(f"SELECT term, count(*) FROM doc_terms WHERE term IN ({','.join('?' for _ in terms)}) "
                            f"GROUP BY term", terms).fetchall()
    found = dict(rows)
    return {term: found.get(term, 0) for term in terms}


def search(repository, store, owner: str, plan: dict | None, text: str, *, at, zone: str | None,
           language: str = "zh", limit: int = 8, kinds: tuple[str, ...] | None = None,
           exclude_events: set[str] | None = None, kind_cap: int | None = None) -> dict:
    """`recall.search` through the index: the same entries, order and scores, with `path`, `line` and `commit`, and
    for each hit the line of its file the match falls on (`snippet`, `snippet_line`) and a source a person can read
    back to the screenshot a transcript came from (`cited_as`)."""
    index = Index(repository)
    try:
        entries = index.ensure(store, owner, plan, at=at, zone=zone, language=language)
        pool = {f"{entry['found']}:{entry['id']}": entry for entry in entries
                if (not kinds or entry["found"] in kinds) and not (exclude_events and entry.get("event") in exclude_events)}
        query = recall.query_terms(text, plan, at=at, zone=zone)
        if not query:
            return dict(found=[], terms=[], rule="empty")
        keys = [key for key in _matches(index, query) if key in pool]
        widen = not keys
        if widen:
            keys = [key for key in _trigram(index, text) if key in pool]
        rows = {key: (words, path, line, revision, passage, passage_line) for key, words, path, line, revision, passage, passage_line in
                index.db.execute(f"SELECT key, words, path, line, revision, passage, passage_line FROM docs WHERE key IN "
                                 f"({','.join('?' for _ in keys)})", keys).fetchall()} if keys else {}
        candidates = []
        for key in keys:
            entry = dict(pool[key])
            entry["terms"] = set(json.loads(rows[key][0])) if key in rows else recall.entry_terms(entry, plan, zone=zone)
            entry["path"], entry["line"], entry["commit"] = rows[key][1], rows[key][2], rows[key][3]
            entry["passage"], entry["passage_line"] = (rows[key][4], rows[key][5]) if key in rows else ("", 0)
            candidates.append(entry)
        frequency = _frequency(index, query) if widen else {term: sum(term in entry["terms"] for entry in candidates) for term in query}
        result = recall.rank(candidates, query, plan=plan, at=at, zone=zone, language=language, limit=limit,
                            kind_cap=kind_cap, total=len(pool), frequency=frequency, widen=widen)
        _add_snippets(index, result, candidates, query, plan=plan, language=language, widen=widen)
        return result
    finally:
        index.close()


def _add_snippets(index: "Index", result: dict, candidates: list[dict], query: set[str], *,
                  plan: dict | None, language: str, widen: bool) -> None:
    """Put each hit's own line of its file on the hit: `snippet` and `snippet_line`, from FTS5's `snippet()`; and for
    a screenshot's transcript, `cited_as`: which day's screenshot, which one, and the raw file and line."""
    by_key = {f"{entry['found']}:{entry['id']}": entry for entry in candidates}
    for hit in result.get("found", []):
        entry = by_key.get(f"{hit['found']}:{hit['id']}")
        if entry is None:
            continue
        matched = query & entry["terms"]
        if widen:
            folded = recall.fold(entry["haystack"])
            matched |= {term for term in query - matched if not term.startswith("§") and recall.fold(term) in folded}
        passage, passage_line = entry.pop("passage", ""), entry.pop("passage_line", 0)
        origin = entry.get("origin")
        received = ""
        if origin and not origin.get("date") and entry.get("path"):
            # Q20: the raw folder's day is the day the message came in, not the day of the screenshot; when the host
            # gave no screenshot day, say only that the message was received then, and never pass it off as the day
            date = re.search(r"/raw/(\d{4}-\d{2}-\d{2})/", entry["path"])
            if date:
                received = recall.WORDS["en" if language == "en" else "zh"]["received"].format(date=date.group(1)[5:])
        found = index.snippet(f"{entry['found']}:{entry['id']}", passage, passage_line, snippet_phrases(matched, plan))
        if found is None:
            # a match the passage spells in no form this build can look up (a record whose file does not hold its
            # text yet): the id's own line is still the record's own line of the file
            found = index.snippet(f"{entry['found']}:{entry['id']}", passage, passage_line, snippet_phrases({entry['id']}, plan))
        if found:
            hit["snippet"], hit["snippet_line"], hit["snippet_exact"] = found
            if origin:
                where = f"{entry['path']} 第 {found[1]} 行" if entry.get("path") else f"第 {found[1]} 行"
                hit["cited_as"] = "，".join(part for part in (entry["source"], received, where) if part)
