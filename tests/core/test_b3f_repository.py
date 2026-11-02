"""B3f with the trip repository as the canonical store: a report and a confirmed correction are new decision records
and a closed question, the replaced decision changes only its status, and an index rebuilt from the repository files
still holds the changed values (an agent moving them back is refused). Trip repositories live in pytest's temporary
folder, outside the code repository."""
from __future__ import annotations

import subprocess
from copy import deepcopy

from test_b3e_reference_graph import GROUP, LOCKED, node
from test_b3f_three_paths import ENTRY, HALL, NEAR, fixture
from tripwright import locks
from tripwright.repo import layout, workspace
from tripwright.repo.workspace import Repository
from tripwright.service import Tripwright
from tripwright.storage import Store

TRIP = "2032-04-lakes"
OWNER = "local"


class Writer:
    def __init__(self, root, *, keys=0):
        self.repository = Repository(root, owner=OWNER)
        self.store = Store(self.repository.state_path)
        self.service = Tripwright(self.store, repository=self.repository, commit_wait=0)
        self.keys = keys

    def say(self, text, *, nature="user_verbatim", speaker="u1"):
        self.keys += 1
        key = f"k{self.keys}"
        return self.service.turn(OWNER, text, key, nature=nature, native_ref=f"fixture:{key}" if nature == "user_verbatim" else None, speaker=speaker)

    def propose(self, turn, *, plan=None, **changes):
        self.keys += 1
        return self.service.propose(OWNER, ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=plan, changes=changes or None,
                                    idempotency_key=f"p{self.keys}")

    def commit(self, draft, **options):
        return self.service.commit(OWNER, draft["draft_id"], draft["draft_id"], **options)

    def plan_with(self, key, **fields):
        plan = deepcopy(self.service.state(OWNER)["plan"])
        node(plan, key).update(fields)
        return plan

    def lock_conflicts(self, plan):
        draft = self.propose(self.say("agent edits", nature="agent_inference", speaker=None), plan=plan)
        return [check["details"]["changed"] for check in draft["checks"]["checks"] if check["id"].startswith("locked:") and check["status"] == "conflict"]

    def close(self):
        self.service.jobs.stop()
        self.store.close()


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout


def test_reports_and_confirmed_corrections_survive_a_rebuild_from_the_repository(tmp_path):
    root = workspace.init(tmp_path / "repo", trip=TRIP, remote=None).root
    writer = Writer(root)
    try:
        seeded = dict(fixture(), id=TRIP)
        writer.commit(writer.propose(writer.say("Create the fictional trip."), plan=seeded), accept_unverified=True)
        turn = writer.say("整个活动和门票都定了。")
        plan = writer.service.state(OWNER)["plan"]
        writer.commit(writer.propose(turn, decisions=[dict(
            question="活动与门票", options=["照现在"], choice="照现在", decided_by="user", must=True, subject=GROUP,
            source=dict(event=turn["receipt"]["event_id"], basis="user_explicit"),
            locked_paths={f"/blocks/{LOCKED}": locks.pointer(plan, f"/blocks/{LOCKED}")})]))
        turn = writer.say("我买了票。")
        writer.commit(writer.propose(turn, plan=writer.plan_with(ENTRY, status="bought"), reports=[dict(
            target=ENTRY, field="status", old="on_site", new="bought", quote="我买了票", source=dict(event=turn["receipt"]["event_id"]))]),
            accept_unverified=True)
        turn = writer.say("agent checks the map", nature="agent_inference", speaker=None)
        opened = writer.commit(writer.propose(turn, corrections=[dict(
            target=HALL, field="coords", old=[47.80, 13.04], new=NEAR, basis="The map shows the north entrance",
            ask="Do you mean the north entrance?", source=dict(event=turn["receipt"]["event_id"]))]))["lock_changes"]["corrections"][0]
        turn = writer.say("对，北门。", speaker="u2")
        writer.commit(writer.propose(turn, plan=writer.plan_with(HALL, coords=NEAR), confirm_corrections=[dict(
            id=opened["id"], answer="confirmed", quote="北门", source=dict(event=turn["receipt"]["event_id"]))]), accept_unverified=True)
        writer.service.committer.flush()
        folder = root / layout.trip_folder(TRIP)
        decisions = sorted((folder / "ledger/decisions").glob("*.md"))
        assert len(decisions) == 3 and (folder / "ledger/questions" / f"{opened['id']}.yaml").exists()
        superseded = decisions_by_status(writer)["superseded"]
        assert len(superseded) == 2
        for decision_id in superseded:
            path = (folder / "ledger/decisions" / f"{decision_id}.md").relative_to(root)
            log = git(root, "log", "-p", "--format=format:COMMIT", "--", str(path))
            removed = [row[1:] for row in log.splitlines() if row.startswith("-") and not row.startswith("---")]
            # Only the status line ever goes; a decision created and replaced between two repository commits has none.
            assert set(removed) <= {"status: active"}, (decision_id, removed)
            assert "status: superseded" in (root / path).read_text()
    finally:
        writer.close()
    (root / ".tripwright" / "state.sqlite").unlink()
    rebuilt = Writer(root, keys=100)
    try:
        current = rebuilt.service.state(OWNER)["plan"]
        assert node(current, ENTRY)["status"] == "bought" and node(current, HALL)["coords"] == NEAR
        assert [decision["amends"]["via"] for decision in rebuilt.service.ledger.current(OWNER)["decisions"]] == ["confirmed_correction"]
        assert rebuilt.lock_conflicts(rebuilt.plan_with(ENTRY, status="on_site")) == [[f"{ENTRY}/status"]]
        assert rebuilt.lock_conflicts(rebuilt.plan_with(HALL, coords=[47.80, 13.04])) == [[f"{HALL}/coords"]]
        assert rebuilt.store.get("undecided", OWNER, opened["id"])["status"] == "answered"
    finally:
        rebuilt.close()


def decisions_by_status(writer):
    found = {}
    for decision in writer.store.all("decisions", OWNER):
        found.setdefault(decision["status"], []).append(decision["id"])
    return found
