"""One sentence takes a change back (analysis 5.13, 2026-11-13): propose(restore_version=N) makes version N's whole plan
the draft, through the same checks; the ledger is not touched and the change log says which version was restored. The
proactive save mode rests on it: a change saved without asking can be undone."""
import pytest
from test_flow_dispatch import seeded


def moved_dinner(plan):
    for day in plan["days"]:
        for option in day["plans"]:
            for track in option["tracks"]:
                for block in track["blocks"]:
                    if block["id"] == "d1-art":
                        block["start"], block["end"] = "11:00", "12:00"
                        return plan
    raise AssertionError("no art block")


def day_json(service, date="2030-06-10"):
    return service.state("alice", view="day", date=date)["json"]


def test_restoring_version_one_gives_its_day_back_and_leaves_the_ledger(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    before, ledger = day_json(service), service.ledger.current("alice")
    turn = service.turn("alice", "画廊挪到 11:00。", "move", scenario="replan")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], plan=moved_dinner(service.state("alice")["plan"]))
    assert service.commit("alice", draft["draft_id"], accept_unverified=True)["number"] == 2
    assert day_json(service) != before

    undo = service.turn("alice", "撤回", "undo", scenario="replan")
    back = service.propose("alice", ctx_id=undo["ctx_id"], restore_version=1)
    assert back["status"] != "rejected", back
    saved = service.commit("alice", back["draft_id"], accept_unverified=True)
    assert saved["number"] == 3 and day_json(service) == before
    assert service.ledger.current("alice")["items"] == ledger["items"]
    history = service.state("alice", view="history")["versions"]
    assert history[-1]["restored_from"] == 1
    assert any("恢复到第 1 版" in (line.get("why") or "") for line in history[-1]["changelog"])


def test_restore_refuses_an_unknown_version_and_a_plan_sent_with_it(service, plan, monkeypatch):
    seeded(service, plan, monkeypatch)
    turn = service.turn("alice", "撤回", "undo", scenario="replan")
    with pytest.raises(ValueError, match="No saved plan in version 7"):
        service.propose("alice", ctx_id=turn["ctx_id"], restore_version=7)
    with pytest.raises(ValueError, match="send no plan"):
        service.propose("alice", ctx_id=turn["ctx_id"], restore_version=1, plan=service.state("alice")["plan"])


def test_the_proactive_hint_says_how_to_undo_with_restore_version():
    from tripwright.service import TELL_USER

    assert "restore_version" in TELL_USER["zh"] and "restore_version" in TELL_USER["en"]


def test_the_propose_tool_says_what_restore_version_does(service):
    from test_iface_tools import listing

    propose = next(tool for tool in listing(service) if tool["name"] == "propose")
    assert "undo" in propose["inputSchema"]["properties"]["restore_version"]["description"]
