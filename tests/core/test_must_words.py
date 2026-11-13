"""A must-locked field follows the same words check as a hard node (D-19, 2026-11-13; fix3-judge ③, probe
test_judge_hard_must.py). The user: “不只是车次，其他类似的问题也都要这样处理。” A user decision that changes a must takes
its value from the user's words, in this turn's message or the one just before it; an explicit no in this turn's
answer keeps the words before it from counting (the main session's recommended default, from “来自用户的说明肯定是最重要的”)."""
import pytest
from test_b3f_three_paths import commit, locked, message, propose, report, with_entry  # noqa: F401
from test_b3h_authority import decided, where


def locked_at_a(service):
    first = message(service, "门票从 A 窗口买，定了。")
    commit(service, where(service, first, "A counter"), accept_unverified=True)


def test_mu1_vague_words_do_not_move_a_must_to_the_agents_pick(locked):
    locked_at_a(locked)
    vague = message(locked, "门票换个窗口买吧。")
    with pytest.raises(ValueError, match="not admitted \\(definite value\\).*name no value.*ask the user"):
        where(locked, vague, "B counter")


def test_mu2_the_previous_messages_definite_words_count_from_the_answer_turn(locked):
    locked_at_a(locked)
    said = message(locked, "门票改从 C 窗口买。")
    answer = message(locked, "对。")
    plan = with_entry(locked, where=["C counter"])
    draft = propose(locked, answer, plan=plan, decisions=[decided(said, plan)])
    assert draft["status"] == "proposed", draft.get("refused")


def test_a_must_said_in_its_own_turn_still_counts(locked):
    locked_at_a(locked)
    turn = message(locked, "门票改从 C 窗口买。")
    assert where(locked, turn, "C counter")["status"] == "proposed"


@pytest.mark.parametrize("answer", ["不。", "不行，还是原来那班。", "算了，不改了。", "No, keep the old one."])
def test_an_explicit_no_in_the_answer_keeps_the_words_before_it_from_counting(locked, answer):
    locked_at_a(locked)
    said = message(locked, "门票改从 C 窗口买。")
    no = message(locked, answer)
    plan = with_entry(locked, where=["C counter"])
    with pytest.raises(ValueError, match="not admitted \\(declined\\)"):
        propose(locked, no, plan=plan, decisions=[decided(said, plan)])


@pytest.mark.parametrize("answer", ["不。", "不行，还是原来那班。", "算了，不改了。", "No, keep the old one."])
def test_the_same_no_holds_for_a_report(locked, answer):
    said = message(locked, "我买了票。")
    no = message(locked, answer)
    with pytest.raises(ValueError, match="not admitted \\(declined\\)"):
        propose(locked, no, plan=with_entry(locked, status="bought"), reports=[report(said, quote="我买了票")])
