"""D-20 (2026-11-12): a prohibition said again under the same key stands beside the one in force, with no question.

Two prohibitions always hold together (avoid both), so neither has to give way and the user has nothing to decide
(deny-overrides). Before, any difference in wording under one key raised “New statement conflicts with an existing hard
condition”: in the layer-4 round (Claude through Anthropic's MCP connector) 8 of 10 conversations that restated a peanut
allergy got a pending question for it. The receipt quotes the one in force, so a host that meant to replace or narrow it
can record it again with exclusive_with and the user is asked, as before. A `must`, a prohibition against a `must`, and
two prohibitions with different values still raise the question."""
from copy import deepcopy

from tripwright.demo import example

OLD = "我对花生过敏，以后别给我安排含花生的菜"


def item(**changes):
    base = dict(kind="constraint", key="peanut_allergy", quote=OLD, text="Never plan dishes containing peanuts",
                subject=["p1"], strength="forbid", source=dict(event="this", basis="user_explicit"))
    return {**base, **changes}


def trip(service):
    plan = deepcopy(example()["trip"])
    turn = service.turn("alice", "创建示例行程。", "seed")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=plan, idempotency_key="seed")
    service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)
    first = service.turn("alice", OLD, "r1", record=dict(items=[item()]))
    return first["recorded"]["changed"][0]


def say_again(service, request, **changes):
    entry = item(**changes)
    return service.turn("alice", entry["quote"], request, record=dict(items=[entry]))["recorded"]


def status(service, item_id):
    return service.store.get("items", "alice", item_id)["status"]


def open_questions(service):
    return [question for question in service.store.all("undecided", "alice") if question["status"] == "open"]


def test_a_prohibition_said_again_in_other_words_stands_beside_the_first(service):
    old = trip(service)
    recorded = say_again(service, "r2", quote="I'm allergic to peanuts", text="Never plan dishes containing peanuts for her")
    new = recorded["changed"][0]
    assert status(service, old) == status(service, new) == "active"  # both hold
    assert open_questions(service) == []
    [note] = recorded["alongside"]
    assert note["id"] == new and note["alongside"] == old
    assert "Never plan dishes containing peanuts" in note["note"] and "exclusive_with" in note["note"]  # the old one, quoted


def test_a_must_said_again_in_other_words_still_asks(service):
    trip(service)
    service.turn("alice", "晚饭必须 19:00", "r2", record=dict(items=[item(key="dinner", strength="must", text="Dinner at 19:00", quote="晚饭必须 19:00")]))
    recorded = say_again(service, "r3", key="dinner", strength="must", text="Dinner at 18:00", quote="晚饭必须 18:00")
    assert status(service, recorded["changed"][0]) == "pending" and "alongside" not in recorded
    assert any(question["question"] == "New statement conflicts with an existing hard condition" for question in open_questions(service))


def test_a_must_against_a_prohibition_still_asks(service):
    trip(service)
    recorded = say_again(service, "r2", strength="must", text="Plan a peanut tasting", quote="安排一次花生品尝")
    assert status(service, recorded["changed"][0]) == "pending" and "alongside" not in recorded
    assert open_questions(service)


def test_two_prohibitions_with_different_values_still_ask(service):
    old = trip(service)
    service.store.put("items", "alice", {**service.store.get("items", "alice", old), "value": "all peanut products"})
    recorded = say_again(service, "r2", value="peanut sauce only", text="Avoid peanut sauce", quote="只是不吃花生酱")
    assert status(service, recorded["changed"][0]) == "pending" and "alongside" not in recorded
    assert open_questions(service)


def test_a_host_that_means_to_replace_the_old_one_says_so_and_the_user_is_asked(service):
    old = trip(service)
    recorded = say_again(service, "r2", text="Avoid peanut sauce only", quote="现在只是不吃花生酱了", exclusive_with=[old])
    assert status(service, recorded["changed"][0]) == "pending" and "alongside" not in recorded
    [question] = open_questions(service)
    assert set(question["item_ids"]) == {old, recorded["changed"][0]}
    assert status(service, old) == "active"  # the safer one holds until the user answers


def test_the_same_prohibition_labelled_rule_the_second_time_stands_beside_the_first(service):
    # DeepSeek Flash, 2026-11-12: “constraint” first, “rule” when the user said it again; both state a limit
    old = trip(service)
    recorded = say_again(service, "r2", kind="rule", quote="花生我是真的不能碰", text="No peanuts at all")
    assert status(service, recorded["changed"][0]) == "active" and open_questions(service) == []
    assert recorded["alongside"][0]["alongside"] == old


def test_a_prohibition_of_another_kind_still_asks(service):
    trip(service)
    recorded = say_again(service, "r2", kind="goal", quote="不要花生", text="No peanuts")
    assert status(service, recorded["changed"][0]) == "pending" and "alongside" not in recorded
