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

The index is derived: deleting it rebuilds the same rows from the repository's files and store, and the entries of a
query, their order and their scores are the same as the scan's. Each query compares the entries with the rows it holds
and cuts again only the entries that changed (one new record is one new entry); the commit and file of unchanged rows
are refreshed without cutting their words again.
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

SCHEMA = 1
FILE = "search.sqlite"
CANDIDATES = 8000  # more than a trip's repository holds today; the index is not the limit on a query's cost
ID_LINE = re.compile(r"^\s{0,8}id:\s*['\"]?([^'\"\s]+)['\"]?\s*$")
JSON_ID = re.compile(r'"id"\s*:\s*"([^"]+)"')
NOTE_LINE = re.compile(r"^\s{0,8}notes:")
FTS_TOKEN = re.compile(r"[^0-9a-z]+")


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
                terms TEXT NOT NULL, words TEXT NOT NULL, body TEXT NOT NULL);
            CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(terms, content='docs', content_rowid='id', tokenize='unicode61');
            CREATE VIRTUAL TABLE IF NOT EXISTS docs_tri USING fts5(body, content='docs', content_rowid='id', tokenize='trigram');
            CREATE TABLE IF NOT EXISTS doc_terms (doc_id INTEGER NOT NULL, term TEXT NOT NULL, PRIMARY KEY(doc_id, term)) WITHOUT ROWID;
            CREATE INDEX IF NOT EXISTS doc_terms_term ON doc_terms(term);
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    # --- rows -----------------------------------------------------------------

    def meta(self) -> dict:
        return dict(self.db.execute("SELECT key, value FROM meta").fetchall())

    def set_meta(self, values: dict):
        self.db.executemany("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            list(values.items()))

    def rows(self) -> dict[str, tuple[str, str, int]]:
        return {key: (digest, path, line) for key, digest, path, line
                in self.db.execute("SELECT key, digest, path, line FROM docs").fetchall()}

    def replace(self, key: str, found: str, record_id: str, digest: str, terms: set[str], body: str,
                path: str, line: int, commit: str):
        old = self.db.execute("SELECT id FROM docs WHERE key=?", (key,)).fetchone()
        if old:
            self.db.execute("DELETE FROM doc_terms WHERE doc_id=?", (old[0],))
            self.db.execute("DELETE FROM docs WHERE id=?", (old[0],))
        cursor = self.db.execute("INSERT INTO docs(key, found, record_id, path, line, revision, digest, terms, words, body) "
                                 "VALUES(?,?,?,?,?,?,?,?,?,?)",
                                 (key, found, record_id, path, line, commit, digest,
                                  " ".join(sorted(codeword(term) for term in terms)),
                                  json.dumps(sorted(terms), ensure_ascii=False), body))
        self.db.executemany("INSERT INTO doc_terms(doc_id, term) VALUES(?, ?)", [(cursor.lastrowid, term) for term in terms])

    def remove(self, key: str):
        old = self.db.execute("SELECT id FROM docs WHERE key=?", (key,)).fetchone()
        if old:
            self.db.execute("DELETE FROM doc_terms WHERE doc_id=?", (old[0],))
            self.db.execute("DELETE FROM docs WHERE id=?", (old[0],))

    def reindex(self):
        self.db.execute("INSERT INTO docs_fts(docs_fts) VALUES('rebuild')")
        self.db.execute("INSERT INTO docs_tri(docs_tri) VALUES('rebuild')")

    def clear(self):
        self.db.execute("DELETE FROM doc_terms")
        self.db.execute("DELETE FROM docs")

    # --- building -----------------------------------------------------------------

    def locations(self, files: dict[str, str]) -> dict[str, tuple[str, int]]:
        """Record ID → (file, line of its `id:`) over the working tree; one pass, first file wins."""
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

    def committed_paths(self) -> set[str]:
        head = self.repository.git.rev()
        if not head:
            return set()
        try:
            return set(self.repository.git.run("ls-tree", "-r", "--name-only", head).splitlines())
        except Exception:
            return set()

    def location_of(self, entry: dict, plan: dict | None, located: dict[str, tuple[str, int]],
                    files: dict[str, str]) -> tuple[str, int]:
        record_id = entry["id"]
        if record_id in located:
            return located[record_id]
        if "/" in record_id and plan:
            day, _, tail = record_id.partition("/")
            path = f"{layout.trip_folder(plan['id'])}/plan/days/{day}.yaml"
            if tail == "notes":
                for number, raw in enumerate((files.get(path) or "").splitlines(), 1):
                    if NOTE_LINE.match(raw):
                        return path, number
            if tail in located and located[tail][0] == path:
                return located[tail]
            return path, 1
        return "", 0

    def digest_of(self, entry: dict) -> str:
        return sha256(json.dumps([entry["haystack"], entry.get("text") or "", entry.get("source") or "",
                                  bool(entry.get("in_force", True))], ensure_ascii=False).encode()).hexdigest()

    def rebuild(self, entries: list[dict], plan: dict | None, *, zone: str | None, language: str, owner: str):
        """The rows for every entry, cut again; the commit and the file of each come from the working tree."""
        files = self.repository.working()
        located = self.locations(files)
        head = self.repository.git.rev() or ""
        committed = self.committed_paths()
        with self.db:
            self.clear()
            for entry in entries:
                path, line = self.location_of(entry, plan, located, files)
                commit = head if path and path in committed else ""
                self.replace(f"{entry['found']}:{entry['id']}", entry["found"], entry["id"], self.digest_of(entry),
                             recall.entry_terms(entry, plan, zone=zone), entry["haystack"], path, line, commit)
            self.reindex()
            self.set_meta(dict(schema=str(SCHEMA), owner=owner, language=language, zone=zone or "",
                               plan=plan_mark(plan), head=head, built=str(len(entries))))

    def refresh(self, entries: list[dict], plan: dict | None, *, zone: str | None, language: str, owner: str):
        """Cut again only the entries that changed; refresh the file and the commit of the rest."""
        rows = self.rows()
        head = self.repository.git.rev() or ""
        current = {f"{entry['found']}:{entry['id']}": entry for entry in entries}
        changed = [key for key, entry in current.items() if key not in rows or rows[key][0] != self.digest_of(entry)]
        # an entry written while its file was not there yet (the turn records before the commit writes it): find it now
        relocate = [key for key in current if key in rows and (not rows[key][1] or rows[key][2] == 0)]
        removed = [key for key in rows if key not in current]
        meta = self.meta()
        if not changed and not removed and not relocate and meta.get("head") == head:
            return
        located, files, committed = {}, {}, set()
        if changed or relocate:
            files = self.repository.working()
            located = self.locations(files)
            committed = self.committed_paths() if head else set()
        elif head != meta.get("head"):
            committed = self.committed_paths() if head else set()
        with self.db:
            for key in removed:
                self.remove(key)
            for key in changed:
                entry = current[key]
                path, line = self.location_of(entry, plan, located, files)
                commit = head if path and path in committed else ""
                self.replace(key, entry["found"], entry["id"], self.digest_of(entry),
                             recall.entry_terms(entry, plan, zone=zone), entry["haystack"], path, line, commit)
            for key in relocate:
                if key in changed:
                    continue
                path, line = self.location_of(current[key], plan, located, files)
                commit = head if path and path in committed else ""
                self.db.execute("UPDATE docs SET path=?, line=?, revision=? WHERE key=?", (path, line, commit, key))
            if changed or removed:
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
        signature = dict(schema=str(SCHEMA), owner=owner, language=language, zone=zone or "", plan=plan_mark(plan))
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
    """`recall.search` through the index: the same entries, order and scores, with `path`, `line` and `commit`."""
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
        rows = {key: (words, path, line, revision) for key, words, path, line, revision in index.db.execute(
            f"SELECT key, words, path, line, revision FROM docs WHERE key IN ({','.join('?' for _ in keys)})", keys).fetchall()} if keys else {}
        candidates = []
        for key in keys:
            entry = dict(pool[key])
            entry["terms"] = set(json.loads(rows[key][0])) if key in rows else recall.entry_terms(entry, plan, zone=zone)
            entry["path"], entry["line"], entry["commit"] = rows[key][1], rows[key][2], rows[key][3]
            candidates.append(entry)
        frequency = _frequency(index, query) if widen else {term: sum(term in entry["terms"] for entry in candidates) for term in query}
        return recall.rank(candidates, query, plan=plan, at=at, zone=zone, language=language, limit=limit,
                           kind_cap=kind_cap, total=len(pool), frequency=frequency, widen=widen)
    finally:
        index.close()
