"""The verifier's verdict goes back to the service (analysis 5.14, 2026-11-13). Before, reading the wide pack counted as
checked and the verdict reached only the main agent: the verifier found 3 drafts of 3 incomplete and 2 were saved with
nothing of it on the version. Now result(job_id, report=...) records it on the draft and commit follows it: a conflict
is refused; lines it could not check need accept_unverified, and in the cautious mode the user's answer first."""
import pytest
from test_ask_before_saving import day_with, host_commit, trip  # noqa: F401  (trip is a fixture)

from tripwright.flow import Flow

TITLE = "把第一天的标题改成 Oslo day。"


def titled(service, key="title"):
    """A plan change that brings in no new unverified fact, so only the verifier's report can hold it back."""
    turn = service.turn("alice", TITLE, key, scenario="replan")
    day = day_with(service)
    day["title"] = "Oslo day"
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], days=[day], idempotency_key=key)
    assert "ask_user" not in draft
    service.jobs.run_once("alice")
    return draft


def report(service, draft, **verdict):
    return service.result("alice", draft["job_id"], report=verdict)


def test_a_conflict_the_verifier_found_is_not_saved(trip):
    draft = titled(trip)
    answer = report(trip, draft, verdict="conflict", conflicts=["票只在 Vy 的车上有效，草稿里是另一家公司的车"])
    assert answer["result"]["report"]["recorded"] and "存不下" in answer["result"]["report"]["next"]
    with pytest.raises(ValueError, match="verifier found a conflict: 票只在 Vy"):
        host_commit(trip, draft, accept_unverified=True)


def test_in_the_cautious_mode_unchecked_lines_wait_for_the_users_answer(trip):
    draft = titled(trip)
    answer = report(trip, draft, verdict="incomplete", unchecked=["行李归谁背没核到"])
    assert "① 照这样存" in answer["result"]["report"]["next"]
    with pytest.raises(ValueError, match="accept_unverified"):
        host_commit(trip, draft, "c1")
    with pytest.raises(ValueError, match="Ask the user first"):
        host_commit(trip, draft, "c2", accept_unverified=True)
    trip.turn("alice", "可以，照这样存", "yes", scenario="replan")
    version = host_commit(trip, draft, "c3", accept_unverified=True)
    assert version["verifier"] == dict(verdict="incomplete", unchecked=["行李归谁背没核到"], conflicts=[])


def test_in_the_proactive_mode_unchecked_lines_are_saved_and_kept_on_the_version(trip, monkeypatch):
    monkeypatch.setenv("TRIPWRIGHT_SAVE_UNVERIFIED", "agent")
    draft = titled(trip)
    answer = report(trip, draft, verdict="incomplete", unchecked=["行李归谁背没核到"])
    assert "主动模式" in answer["result"]["report"]["next"]
    version = trip.commit("alice", draft["draft_id"], "c", accept_unverified=True, ask_first=trip.asks_first("alice"))
    assert version["verifier"]["unchecked"] == ["行李归谁背没核到"]
    history = trip.state("alice", view="history")["versions"]
    assert history[-1]["version"] == version["number"]


def test_a_passed_report_saves_as_before_and_a_draft_without_report_is_unchanged(trip):
    draft = titled(trip)
    report(trip, draft, verdict="passed")
    assert host_commit(trip, draft)["verifier"]["verdict"] == "passed"
    other = titled(trip, "title2")
    assert "verifier" not in host_commit(trip, other, "c2")


@pytest.mark.parametrize("bad, message", [(dict(verdict="ok"), "passed, conflict or incomplete"),
                                          (dict(verdict="conflict"), "names each conflict"),
                                          (dict(verdict="incomplete", unchecked=[3]), "list of sentences"),
                                          (dict(verdict="passed", note="x"), "unknown: note")])
def test_a_malformed_report_is_refused_with_what_to_send(trip, bad, message):
    draft = titled(trip)
    with pytest.raises(ValueError, match=message):
        report(trip, draft, **bad)


def test_reading_the_pack_is_no_longer_counted_as_checked_and_the_report_is():
    from datetime import datetime, timezone

    flow, at = Flow(), datetime.now(timezone.utc)
    flow.tool("alice", at, "state_pack", language="zh")
    assert "verify" not in flow.seen["alice"].did
    flow.verified("alice")
    assert "verify" in flow.seen["alice"].did
