"""A setting that is recorded but does not take effect is said in the receipt, with why and how to record it again
(2026-11-13, tw-impl-a on 0bcbe22: 2 of 6 proactive runs never switched, GPT wrote key=setting:save_mode, GLM passed no
speaker, and neither model knew). One check for every setting key, not a patch for one wrong key."""
import pytest
from test_ask_before_saving import trip  # noqa: F401  (trip is a fixture)

WORDS = "以后改计划直接改就行，不用先问我。"


def said(service, key="save_mode", value="proactive", text=WORDS, speaker="p1", basis="user_explicit", request="s"):
    return service.turn("alice", WORDS, request, scenario="replan", speaker=speaker, record=dict(items=[dict(
        kind="setting", key=key, value=value, subject=["*"], strength="prefer", long_term=True, quote=WORDS, text=text,
        source={"event": "this", "basis": basis})]))


@pytest.mark.parametrize("arguments, why, how", [
    (dict(key="setting:save_mode", value=None), "像是「save_mode」", "key=save_mode，value=proactive 或 cautious"),
    (dict(value=None, text="用户想要这样"), "value 缺或不对：proactive or cautious", "key=save_mode"),
    (dict(speaker=None), "没有行程里的说话人", "turn 带 speaker"),
    (dict(basis="agent_inference"), "agent 的推断", "kind=setting"),
])
def test_a_save_mode_that_does_not_count_is_said_with_why_and_how(trip, arguments, why, how, monkeypatch):
    monkeypatch.setenv("TRIPWRIGHT_SAVE_UNVERIFIED", "ask")  # a cautious deployment, so a proactive setting that does not count shows
    turn = said(trip, **arguments)
    [entry] = turn["recorded"]["not_in_effect"]
    assert why in entry["why"] and how in entry["how"]
    assert turn["save_mode"]["mode"] == "cautious"
    assert turn["next"].startswith("刚记的设置没生效")


def test_a_save_mode_that_counts_says_nothing_and_switches(trip):
    turn = said(trip)
    assert "not_in_effect" not in turn["recorded"] and turn["save_mode"] == dict(turn["save_mode"], mode="proactive", by="user")


@pytest.mark.parametrize("key, value, why", [
    ("stability:food", "sometimes", "stable or changing"),
    ("ask_languages", "de", "language codes"),
    ("note", "x", "不是服务会读的设置"),
    ("languages", ["de"], "像是「ask_languages」"),
])
def test_the_same_check_holds_for_every_setting_key(trip, key, value, why):
    turn = said(trip, key=key, value=value, text="设置")
    [entry] = turn["recorded"]["not_in_effect"]
    assert why in entry["why"], entry


def test_valid_settings_of_other_keys_say_nothing(trip):
    for n, (key, value) in enumerate((("ask_languages", ["de"]), ("note:cash", "带现金"), ("stability:food", "changing"))):
        assert "not_in_effect" not in said(trip, key=key, value=value, text="设置", request=f"ok{n}")["recorded"]


def test_a_setting_recorded_through_propose_is_said_in_the_commit_receipt(trip):
    turn = trip.turn("alice", WORDS, "p", scenario="replan", speaker="p1")
    draft = trip.propose("alice", ctx_id=turn["ctx_id"], changes=dict(items=[dict(
        kind="setting", key="setting:save_mode", subject=["*"], strength="prefer", quote=WORDS, text="主动",
        source={"event": "this", "basis": "user_explicit"})]))
    receipt = trip.commit("alice", draft["draft_id"])
    assert "save_mode" in receipt["settings_not_in_effect"][0]["how"]


def test_english_receipts_say_it_in_english(trip):
    turn = trip.turn("alice", "From now on just change the plan, no need to ask me.", "en", scenario="replan", speaker="p1",
                     record=dict(items=[dict(kind="setting", key="setting:save_mode", subject=["*"], strength="prefer",
                                             quote="From now on just change the plan", text="proactive",
                                             source={"event": "this", "basis": "user_explicit"})]))
    [entry] = turn["recorded"]["not_in_effect"]
    assert "looks like 'save_mode'" in entry["why"] and "record it again" in entry["how"]
    assert turn["next"].startswith("The setting just recorded does not take effect")


def test_a_setting_said_again_with_a_new_value_takes_effect_and_says_nothing(trip):
    assert "not_in_effect" not in said(trip, key="ask_languages", value=["de"], text="问人用德语", request="l1")["recorded"]
    again = said(trip, key="ask_languages", value=["no"], text="问人改用挪威语", request="l2")
    assert "not_in_effect" not in again["recorded"], again["recorded"]
    assert "not_in_effect" not in said(trip, request="m1")["recorded"]
    assert "not_in_effect" not in said(trip, value="cautious", text="以后先问我", request="m2")["recorded"]
