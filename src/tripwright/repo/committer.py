"""The only writer of the trip repository: outbox → commits → canonical branch, and new heads → index (design 5.3).

`flush` writes every operation the outbox holds into one commit (one commit per new plan version, so each version's
plan stays in history), with `Tripwright-Op` and `Tripwright-Request` trailers, then pushes without force. A rejected
push or a newer head seen by `sync` builds the head's generation beside the live index, replays the operations that
are not in it (never a text merge), and swaps the result in at once; a replayed plan commit whose base changed becomes
redone on the new plan (D-75). Plan files edited without the tool, in the working copy or in a commit, are taken in
when the index attaches, after a new head and before each write and commit (A15). A remote that cannot be reached leaves operations pending and retries with backoff; answers
never wait for it.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from tripwright.repo import index
from tripwright.repo.files import view_repo
from tripwright.repo.git import GitError
from tripwright.repo.outbox import Outbox
from tripwright.repo.snapshot import repo_head
from tripwright.storage import now

RUNTIME = ("drafts", "contexts")
log = logging.getLogger("tripwright.committer")

# What the host and the operator are told when writes stay on this server, by the kind of failure.
HINTS = {
    "auth": "The server's deploy key cannot write to the trip repository (read-only, removed, or the wrong repository). "
            "The operator must give the key write access; writes stay on the server until then.",
    "protected": "The branch is protected and refuses the server's pushes. The operator must allow the deploy key to "
                 "push to it; writes stay on the server until then.",
    "network": "The server cannot reach the Git host now. Writes stay on the server and are pushed when it is back.",
    "behind": "The repository changed elsewhere and replaying these writes on it has not succeeded yet; the server retries.",
    "other": "Pushing to the trip repository failed; the operator should read the server log.",
    "commit": "The server cannot commit to its copy of the repository (for example a lock left by a crash). "
              "Restarting the service clears leftover locks; the operator should read the server log.",
}
KINDS = {"unreachable": "network", "denied": "auth", "protected": "protected", "rejected": "behind", "failed": "other"}
IDLE_SECONDS = 1.0  # the longest the background loop sleeps between cheap "did anything change" checks


def second(stamp: str | None = None) -> str:
    """A commit time Git can store exactly, so regenerating a commit's views uses the same moment."""
    moment = datetime.fromisoformat(stamp) if stamp else datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def short(commit: str | None) -> str | None:
    return commit[:7] if commit else None


class Committer:
    def __init__(self, service, repository, *, batch_seconds: float = 2.0, poll_seconds: float = 45.0):
        self.service = service
        self.repo = repository
        self.store = service.store
        self.owner = repository.owner
        self.outbox = Outbox(self.store)
        self.batch_seconds = batch_seconds
        self.poll_seconds = poll_seconds
        self.lock = threading.RLock()
        self.wakeup = threading.Event()
        self.stopping = threading.Event()
        self.thread: threading.Thread | None = None
        self.last_poll = 0.0
        self.reachable: bool | None = None
        self.retry_at = 0.0
        self.failures = 0
        self.seen: tuple | None = None  # the store's change marker at the last full background step
        self.next_due = 0.0  # monotonic time of the next timed step (batch window, push retry, poll)
        self.pushed_at = 0.0  # monotonic time of the last push the remote accepted
        # A server sets active_window: it polls the remote only that many seconds after the last request, so an idle
        # instance sends no packets (a platform's sleep can then start, and nothing is spent); None polls always.
        self.active_window: float | None = None
        self.active_until = 0.0

    # --- index ---------------------------------------------------------------------

    def head_record(self) -> dict | None:
        return repo_head(self.store, self.owner)

    def set_head(self, commit: str, *, trip: str | None, checked_at: str | None = None):
        record = self.head_record() or dict(id="head")
        record.update(canonical=self.repo.canonical(), head=short(commit), commit=commit, trip=trip,
                      head_checked_at=checked_at or record.get("head_checked_at") or now())
        self.store.put("repo", self.owner, record)

    def attach(self) -> dict:
        """Make the index follow HEAD: build it when the database is new or was built from another commit, and recover
        the state of operations from commit trailers when the database was lost (CONTRACTS 2.2)."""
        with self.lock:
            head = self.repo.git.rev("HEAD")
            record = self.head_record()
            if record is not None and record.get("commit") == head:
                return dict(rebuilt=False, problems=[], adopted=self.service.adopt_manual_edits())
            with self.store.transaction():
                previous = index.tables_of(self.store, self.owner)
                in_head = {op for commit in self.repo.git.log(head) for op in commit["trailers"].get("Tripwright-Op", [])} if head else set()
                # HEAD moved outside the tool (a pull, a checkout): writes it does not contain are replayed on it.
                waiting = [op for op in self.outbox.open(self.owner) if op["id"] not in in_head and "recovered" not in op["replay"]]
            generation = self.repo.load("HEAD", trip=record.get("trip") if record and waiting else None)
            tables = index.kept(previous, generation["tables"], generation["problems"])
            outcome = self._replayed(tables, waiting, head) if waiting else dict(tables=tables, conflicts=[], replayed=[])
            with self.store.transaction():
                self.store.replace(self.owner, outcome["tables"])
                self._conflicts(outcome["conflicts"], head)
                self.set_head(head, trip=generation["trip"], checked_at=now())
                self._recover(head)
                self.service.jobs.invalidate(self.owner)
            # A plan edit HEAD brought without the tool (A15) is taken in now, before any write regenerates its files.
            adopted = self.service.adopt_manual_edits()
            return dict(rebuilt=True, problems=generation["problems"], manual_plan=generation["manual_plan"] is not None,
                        replayed=[op["id"] for op in outcome["replayed"]], conflicts=[op["id"] for op, _ in outcome["conflicts"]],
                        adopted=adopted)

    def _replayed(self, tables: dict, ops: list[dict], head: str) -> dict:
        """One generation: `tables` plus the operations replayed on it, built beside the live index."""
        with self.store.transaction():
            runtime = {table: self.store.all(table, self.owner) for table in RUNTIME}
        scratch = index.scratch(tables, self.owner, runtime)
        conflicts, replayed = [], []
        try:
            for op in sorted(ops, key=lambda op: op["created_at"]):
                reason = self.service.replay(scratch, self.owner, op)
                if reason:
                    conflicts.append((op, reason))
                else:
                    replayed.append(op)
            return dict(tables=index.tables_of(scratch, self.owner), conflicts=conflicts, replayed=replayed)
        finally:
            scratch.close()

    def _conflicts(self, conflicts: list, head: str):
        for op, reason in conflicts:
            notice = self.service.conflict_notice(self.owner, op, reason, head)
            self.outbox.move(self.owner, op["id"], "conflict", reason=reason["reason"], detail=reason["detail"],
                             remote_head=short(head), notice_id=notice["notice_id"], question_id=reason.get("question_id"))

    def _recover(self, head: str):
        canonical = self.repo.git.rev(self.repo.canonical_ref())
        known = {op["id"] for op in self.store.all("outbox", self.owner)}
        local = self.repo.git.log(head, since=canonical) if canonical and canonical != head else []
        landed = self.repo.git.log(canonical) if canonical else []
        for commits, state in ((landed, "effective"), (local, "pending_sync")):
            for commit in reversed(commits):
                ops, requests = commit["trailers"].get("Tripwright-Op", []), commit["trailers"].get("Tripwright-Request", [])
                for position, op_id in enumerate(ops):
                    if op_id in known:
                        continue
                    request = requests[position] if position < len(requests) else op_id
                    tool, _, key = request.partition(":")
                    self.store.put("outbox", self.owner, dict(
                        id=op_id, kind=tool or "turn", request=dict(tool=tool or "turn", key=key or request), records=[],
                        replay=dict(recovered=commit["commit"]), state=state, commit=commit["commit"],
                        transitions=[dict(state=state, at=second(commit["date"]), commit=commit["commit"])],
                        attempts=0, next_attempt=0.0, error=None, created_at=second(commit["date"])), insert=True)
                    known.add(op_id)

    # --- commits -------------------------------------------------------------------

    def _local_commits(self) -> set[str]:
        """Operations already in a local commit that the canonical branch does not have yet."""
        head = self.repo.git.rev("HEAD")
        canonical = self.repo.git.rev(self.repo.canonical_ref())
        if not self.repo.remote() or canonical == head:
            return set()
        # A remote without the branch yet has none of our commits.
        return {op for commit in self.repo.git.log(head, since=canonical) for op in commit["trailers"].get("Tripwright-Op", [])}

    def flush(self, *, push: bool = True) -> dict:
        """Commit what the outbox holds and, with a remote, push it. Returns what happened, for logs and tests."""
        with self.lock:
            adopted = self.service.adopt_manual_edits()
            committed = self._commit()
            pushed = self._push() if push and self.repo.remote() and self.outbox.open(self.owner, ("pending_sync",)) else None
            return dict(committed=committed, pushed=pushed, adopted=adopted, unsynced=len(self.outbox.open(self.owner)))

    def _commit(self) -> list[str]:
        local = self._local_commits()
        with self.store.transaction():
            ops = [op for op in self.outbox.open(self.owner)
                   if op["state"] == "received" or (op["state"] == "pending_sync" and op["id"] not in local)]
            ops = [op for op in ops if "recovered" not in op["replay"]]
            tables = index.tables_of(self.store, self.owner)
            head = self.head_record()
            notices = self.service.open_notices(self.owner)
        if not ops:
            return []
        segments, current = [], []
        for op in sorted(ops, key=lambda op: op["created_at"]):
            current.append(op)
            if op.get("version_id"):
                segments.append(current)
                current = []
        if current:
            segments.append(current)
        commits = []
        for segment in segments:
            versions = [op["version_id"] for op in segment if op.get("version_id")]
            when = second()
            parent = self.repo.git.rev("HEAD")
            snapshot = view_repo(self.repo.canonical(), parent, [op["id"] for op in segment], when)
            files, remove = self.service.repository_files(tables, trip=head["trip"] if head else None, upto=versions[-1] if versions else None,
                                                          repo=snapshot, at=when, notices=notices)
            self.repo.write(files, remove)
            # Stage every tool-written path that differs from HEAD, including one a hand edit already brought to the
            # bytes the tool writes (an adopted plan edit); held paths are not in `files` and stay out.
            pending = self.repo.git.changed()
            changed = [path for path in pending if path in files or path in remove]
            message = self._message(segment, versions, notices)
            try:
                commit = self.repo.git.commit(message, add=[path for path in changed if path not in remove],
                                              remove=[path for path in changed if path in remove], when=when)
            except GitError as error:
                with self.store.transaction():
                    for op in segment:
                        stored = self.store.get("outbox", self.owner, op["id"])
                        stored["error"] = str(error)
                        self.outbox.update(self.owner, stored)
                    self.block("commit", str(error))
                raise
            effective = not self.repo.remote()
            with self.store.transaction():
                if (self.blocked() or {}).get("kind") == "commit":
                    self.unblock()
                for op in segment:
                    stored = self.store.get("outbox", self.owner, op["id"])
                    self.outbox.move(self.owner, op["id"], "effective" if effective else "pending_sync", commit=commit)
                    stored = self.store.get("outbox", self.owner, op["id"])
                    stored.update(commit=commit, error=None)
                    self.outbox.update(self.owner, stored)
                    if effective:
                        self.service.settle_notices(self.owner, stored)
                self.set_head(commit, trip=head["trip"] if head else None, checked_at=when if effective else None)
            commits.append(commit)
        return commits

    def _message(self, ops: list[dict], versions: list[str], notices: list[dict]) -> str:
        kinds = sorted({op["kind"] for op in ops})
        lines = [f"tripwright: {len(ops)} 条写入（{'、'.join(kinds)}）", ""]
        lines += [f"Tripwright-Op: {op['id']}" for op in ops]
        lines += [f"Tripwright-Request: {op['request']['tool']}:{op['request']['key']}" for op in ops]
        lines += [f"Tripwright-Version: {version}" for version in versions]
        lines += [f"Tripwright-Notice: {notice['notice_id']} {notice['user_line']}" for notice in notices]
        return "\n".join(lines) + "\n"

    def _push(self, *, timeout: float | None = None) -> str:
        # The time limit is passed only at shutdown, so a stand-in push with the plain signature still works.
        outcome = self.repo.git.push(self.repo.remote(), self.repo.branch, **({"timeout": timeout} if timeout else {}))
        if outcome == "rejected" and timeout is None:
            self.reachable = True
            self.sync(push=False)
            self._commit()
            outcome = self.repo.git.push(self.repo.remote(), self.repo.branch)
        if outcome == "ok":
            self.repo.git.fetch(self.repo.remote(), self.repo.branch)
            self._settle(self.repo.git.rev(self.repo.canonical_ref()))
            self.failures, self.retry_at, self.reachable, self.pushed_at = 0, 0.0, True, time.monotonic()
            if self.blocked():
                self.unblock()
        else:
            self.reachable = outcome != "unreachable"
            self.failures += 1
            self.retry_at = time.monotonic() + min(300, 2 ** self.failures)
            self.block(KINDS.get(outcome, "other"), getattr(self.repo.git, "last_error", None) or outcome)
        return outcome

    # --- why writes are stuck ----------------------------------------------------------

    def blocked(self) -> dict | None:
        try:
            return self.store.get("repo", self.owner, "blocked")
        except ValueError:
            return None

    def block(self, kind: str, error: str):
        """Keep why writes cannot leave this server ({kind, since, hint}) for write receipts and state(view="sync"), and
        log one line when the reason first appears or changes (a retry with the same reason logs nothing)."""
        record = self.blocked() or {}
        if record.get("kind") != kind:
            record = dict(id="blocked", kind=kind, since=now(), hint=HINTS.get(kind, HINTS["other"]))
            log.warning("writes blocked for %s: %s: %s", self.repo.root.parent.name, kind, _tail(error))
        record.update(error=_tail(error), last_at=now(), attempts=record.get("attempts", 0) + 1)
        self.store.put("repo", self.owner, record)

    def unblock(self):
        record = self.blocked()
        if record and record.get("kind"):
            log.warning("writes unblocked for %s after %s (%s attempts)", self.repo.root.parent.name, record["kind"],
                        record.get("attempts", 0))
            self.store.put("repo", self.owner, dict(id="blocked", kind=None, cleared_at=now()))

    def _settle(self, canonical: str | None):
        """Operations whose commit the canonical branch now contains are effective (by trailer, so a push that landed
        although its reply was lost counts too)."""
        if not canonical:
            return
        landed = {}
        for commit in self.repo.git.log(canonical, paths=()):
            for op_id in commit["trailers"].get("Tripwright-Op", []):
                landed.setdefault(op_id, commit["commit"])
            if len(landed) > 10000:
                break
        with self.store.transaction():
            for op in self.outbox.open(self.owner):
                if op["id"] in landed:
                    self.outbox.move(self.owner, op["id"], "effective", commit=landed[op["id"]])
                    self.service.settle_notices(self.owner, self.store.get("outbox", self.owner, op["id"]))
            record = self.head_record()
            if record is not None:
                record["head_checked_at"] = now()
                self.store.put("repo", self.owner, record)

    # --- new heads -------------------------------------------------------------------

    def sync(self, *, push: bool = True) -> dict:
        """Fetch the canonical branch; on a head we do not have, rebuild beside the index, replay, swap, recommit."""
        with self.lock:
            remote = self.repo.remote()
            if not remote:
                return self.flush(push=False) | dict(fetched=None)
            reached = self.repo.git.fetch(remote, self.repo.branch)
            self.reachable = reached
            self.last_poll = time.monotonic()
            if not reached:
                return dict(fetched=False, unsynced=len(self.outbox.open(self.owner)))
            canonical = self.repo.git.rev(self.repo.canonical_ref())
            head = self.repo.git.rev("HEAD")
            self._settle(canonical)
            outcome = dict(fetched=True, replayed=[], conflicts=[])
            if canonical and canonical != head and not self.repo.git.is_ancestor(canonical, head):
                outcome.update(self._rebase(canonical))
            if push:
                outcome["flush"] = self.flush()
            return outcome

    def _rebase(self, canonical: str) -> dict:
        """Move to the canonical head and replay our operations that it does not contain, as one generation swap."""
        with self.store.transaction():
            ops = self.outbox.open(self.owner)
            trip = (self.head_record() or {}).get("trip")
        # Replay continues the trip this index follows, even if the other writer's configuration names another one.
        generation = self.repo.load(canonical, trip=trip)
        outcome = self._replayed(generation["tables"], ops, canonical)
        try:
            self.repo.git.run("reset", "-q", "--keep", canonical)
        except GitError as error:
            raise GitError(f"Cannot move to the new head without losing uncommitted edits in the working copy: {error}") from None
        with self.store.transaction():
            self.store.replace(self.owner, outcome["tables"])
            self._conflicts(outcome["conflicts"], canonical)
            self.set_head(canonical, trip=generation["trip"] or trip, checked_at=now())
            self.service.jobs.invalidate(self.owner)
        # The new head may carry a plan edit made on the Git host or by another agent (A15): taken in before we recommit.
        adopted = self.service.adopt_manual_edits()
        return dict(replayed=[op["id"] for op in outcome["replayed"]], conflicts=[op["id"] for op, _ in outcome["conflicts"]],
                    adopted=adopted)

    # --- waiting and background -------------------------------------------------------

    def adopt(self) -> dict | None:
        """Take in edits made to the files without the tool now (A15), so the write that follows builds on them."""
        with self.lock:
            return self.service.adopt_manual_edits()

    def wait(self, op_id: str, timeout: float) -> str:
        """Flush now and wait up to `timeout` seconds for the operation to become effective (CONTRACTS 2.4)."""
        deadline = time.monotonic() + timeout
        try:
            self.flush()
        except GitError:
            pass
        state = self.store.get("outbox", self.owner, op_id)["state"]
        while state in ("received", "pending_sync") and time.monotonic() < deadline and self.reachable is not False:
            self.wakeup.set()
            time.sleep(0.1)
            state = self.store.get("outbox", self.owner, op_id)["state"]
        return state

    def poke(self):
        self.wakeup.set()

    def activity(self) -> bool:
        """A request arrived: keep polling for active_window seconds. True when the instance was idle, so the caller
        fetches once before answering (the remote may have moved while nobody asked)."""
        if self.active_window is None:
            return False
        current = time.monotonic()
        idle = current >= self.active_until
        self.active_until = current + self.active_window
        if idle:
            # The caller fetches now, so the loop counts that as its poll and polls again one interval later.
            self.last_poll = current
            self.next_due = min(self.next_due, current + self.poll_seconds)
        return idle

    def polling(self) -> bool:
        return self.active_window is None or time.monotonic() < self.active_until

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stopping.clear()

        def work():
            reported = None
            while not self.stopping.is_set():
                woken = self.wakeup.is_set()
                self.wakeup.clear()
                try:
                    if woken or self.changed():
                        self.tick()
                    reported = None
                except Exception as error:
                    # One line per distinct failure, not one per retry; the reason also reaches block() when it is Git's.
                    text = f"{type(error).__name__}: {_tail(str(error))}"
                    if text != reported:
                        log.error("committer step failed for %s: %s", self.repo.root.parent.name, text)
                        reported = text
                    self.next_due = time.monotonic() + 1.0
                self.wakeup.wait(max(0.05, min(IDLE_SECONDS, self.next_due - time.monotonic())))

        self.thread = threading.Thread(target=work, name="tripwright-committer", daemon=True)
        self.thread.start()

    def changed(self) -> bool:
        """Whether a full step is needed: something was written since the last one, or a timer is due. Reads one
        SQLite pragma, so the idle loop decodes nothing."""
        return self.store.changes() != self.seen or time.monotonic() >= self.next_due

    def tick(self):
        """One background step: commit operations older than the batch window, retry pushes, poll the remote; then
        note when the next timed step is due."""
        self.seen = self.store.changes()
        received = self.outbox.open(self.owner, ("received",))
        oldest = min((datetime.fromisoformat(op["created_at"]).timestamp() for op in received), default=None)
        if oldest is not None and time.time() - oldest >= self.batch_seconds:
            self.flush()
        elif self.outbox.open(self.owner, ("pending_sync",)) and time.monotonic() >= self.retry_at and self.repo.remote():
            with self.lock:
                self._push()
        if self.repo.remote() and self.polling() and time.monotonic() - self.last_poll >= self.poll_seconds:
            self.sync()
        self.seen = self.store.changes()
        current, due = time.monotonic(), []
        received = self.outbox.open(self.owner, ("received",))
        if received:
            first = min(datetime.fromisoformat(op["created_at"]).timestamp() for op in received)
            due.append(current + max(0.0, first + self.batch_seconds - time.time()))
        if self.repo.remote():
            if self.outbox.open(self.owner, ("pending_sync",)):
                due.append(self.retry_at)
            if self.polling():
                due.append(min(self.last_poll + self.poll_seconds, self.active_until if self.active_window else float("inf")))
        self.next_due = min(due, default=current + 3600)

    def stop(self, *, budget: float = 15.0):
        """Stop the loop, commit what is waiting and try one push, all within `budget` seconds: the container's stop
        grace period must not end in a kill in the middle of a Git command (that leaves a lock behind)."""
        deadline = time.monotonic() + budget
        self.stopping.set()
        self.wakeup.set()
        if self.thread:
            self.thread.join(timeout=max(0.1, min(10, deadline - time.monotonic() - 2)))
        if not self.lock.acquire(timeout=max(0.1, deadline - time.monotonic() - 1)):
            log.warning("committer for %s still busy at shutdown; pending writes stay in the outbox", self.repo.root.parent.name)
            return
        try:
            self.service.adopt_manual_edits()
            self._commit()
            left = deadline - time.monotonic()
            if self.repo.remote() and left > 1 and self.outbox.open(self.owner, ("pending_sync",)):
                self._push(timeout=min(self.repo.git.network_timeout, left - 0.5))
        except Exception as error:
            log.warning("shutdown flush for %s did not finish: %s: %s", self.repo.root.parent.name, type(error).__name__,
                        _tail(str(error)))
        finally:
            self.lock.release()

    def status(self) -> dict:
        record = self.head_record() or {}
        checked = record.get("head_checked_at")
        age = None
        if checked:
            age = int((datetime.now(timezone.utc) - datetime.fromisoformat(checked)).total_seconds())
        status = dict(canonical=self.repo.canonical(), head=record.get("head"), head_checked_at=checked, head_age_seconds=age,
                      remote_reachable=self.reachable, unsynced=len(self.outbox.open(self.owner)),
                      oldest_unsynced_at=self.outbox.oldest(self.owner))
        blocked = self.outbox.blocked(self.owner)
        if blocked:
            status["blocked"] = blocked
        if not self.repo.remote():
            status["storage"] = dict(kind="local", note=LOCAL_NOTE)
        return status


LOCAL_NOTE = ("No remote repository: the trip's files exist only in this copy (on the server's data volume, or in this "
              "folder on the computer). Back it up yourself; nothing is copied elsewhere.")


def _tail(text: str, limit: int = 300) -> str:
    """The last lines of a Git error, on one line and bounded, for a log line or a stored reason."""
    return " | ".join(line.strip() for line in str(text).strip().splitlines()[-3:] if line.strip())[-limit:]
