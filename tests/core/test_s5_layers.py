"""S5 (METHOD v0.5 2.9, 3.2, 6.1; R-147—R-149): the user's layer and the agent's layer kept apart, what a quick
question's consider keeps when it cuts, one record by its ID, and the agent's own inferences sent back. Each promise has
a bad sample (red before the change) and a legal one."""
from copy import deepcopy
from itertools import count

import pytest

from tripwright import consider

KEYS = count()


@pytest.fixture
def trip(service, plan):
    """A committed trip, everyone p1—p3, whose seed questions are settled."""
    plan = deepcopy(plan)
    plan["areas"] = [*plan["areas"], "Wien"]
    turn = service.turn("alice", "Create the trip.", "s5-seed", scenario="quick")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=plan, idempotency_key="s5-seed")
    service.commit("alice", draft["draft_id"], "s5-seed", accept_unverified=True)
    for context in service.store.all("contexts", "alice"):
        service.store.put("contexts", "alice", dict(context, asked=[]))
    return service


def said(service, text, *, record=None, scenario="quick", **extra):
    key = f"s5-{next(KEYS)}"
    return service.turn("alice", text, key, nature="user_verbatim", native_ref=f"fixture:{key}", speaker="p1",
                        record=record, scenario=scenario, **extra)


def recorded(kind, key, text, *, basis, quote="", strength="prefer"):
    return dict(kind=kind, key=key, quote=quote, text=text, subject=["*"], strength=strength, source=dict(event="this", basis=basis))


def message(service, text, *, nature="host_restatement", speaker="p1"):
    return service.events.receive("alice", text, f"s5-{next(KEYS)}", nature=nature, speaker=speaker)["event_id"]


def entry(event, item_id, text, *, basis, key="pace", strength="prefer", scope=None, subject=("p1",), quote=""):
    return dict(id=item_id, kind="preference", key=key, quote=quote, text=text, subject=list(subject), strength=strength,
                scope=scope or {}, source=dict(event=event, basis=basis))


def status(service, item_id):
    return service.store.get("items", "alice", item_id)["status"]


def relations(service, item_id):
    return service.store.get("items", "alice", item_id).get("relations", {})


# --- 1. the agent's does not replace the user's ----------------------------------------------------------------------

def test_an_agent_inference_does_not_replace_what_the_user_said_on_the_same_key(service):
    said = message(service, "明天轻松一点")
    service.ledger.apply("alice", {"items": [entry(said, "I-user", "明天轻松一点", basis="user_explicit", quote="明天轻松一点")]})
    later = message(service, "去机场怎么走")
    service.ledger.apply("alice", {"items": [entry(later, "I-agent", "（推断）可以多走路省钱", basis="agent_inference")]})
    # bad sample (gap inventory 1.1): the user's entry became superseded and the agent's carried supersedes
    assert status(service, "I-user") == "active"
    assert status(service, "I-agent") == "active" and "supersedes" not in relations(service, "I-agent")
    assert relations(service, "I-agent")["conflicts_with_user"] == ["I-user"]
    assert not service.store.all("undecided", "alice")  # the agent's own reading is not put to the user as a question


def test_a_recommended_default_does_not_replace_a_direction_the_user_set(service):
    said = message(service, "这趟以看展为主")
    service.ledger.apply("alice", {"items": [entry(said, "I-user", "以看展为主", basis="direction_set", key="focus")]})
    later = message(service, "下午做什么")
    service.ledger.apply("alice", {"items": [entry(later, "I-default", "下午逛公园", basis="recommended_default", key="focus")]})
    assert status(service, "I-user") == "active" and relations(service, "I-default")["conflicts_with_user"] == ["I-user"]


def test_an_agent_inference_in_a_narrower_scope_is_no_exception_to_the_user(service):
    said = message(service, "这趟都轻松一点")
    service.ledger.apply("alice", {"items": [entry(said, "I-user", "轻松一点", basis="user_explicit")]})
    later = message(service, "周三呢")
    day = {"start": "2030-06-12", "end": "2030-06-12"}
    service.ledger.apply("alice", {"items": [entry(later, "I-agent", "（推断）周三赶一点", basis="agent_inference", scope=day)]})
    assert "exception_of" not in relations(service, "I-agent") and relations(service, "I-agent")["conflicts_with_user"] == ["I-user"]
    compiled = service.ledger.compile("alice", subject=["p1"], scope=day)
    assert "I-user" in [item["id"] for item in compiled["items"]]


def test_an_agent_inference_against_a_user_must_stays_beside_it_without_a_question(service):
    said = message(service, "一定要轻松")
    service.ledger.apply("alice", {"items": [entry(said, "I-user", "一定要轻松", basis="user_explicit", strength="must")]})
    later = message(service, "怎么走")
    service.ledger.apply("alice", {"items": [entry(later, "I-agent", "（推断）多走路", basis="agent_inference")]})
    assert status(service, "I-user") == "active" and status(service, "I-agent") == "active"
    assert relations(service, "I-agent")["conflicts_with_user"] == ["I-user"] and not service.store.all("undecided", "alice")


def test_one_message_recorded_with_the_users_words_and_an_agent_reading_keeps_the_users_active(service):
    said = message(service, "轻松一点，便宜点也行")
    service.ledger.apply("alice", {"items": [entry(said, "I-user", "轻松一点", basis="user_explicit"),
                                             entry(said, "I-agent", "（推断）多走路省钱", basis="agent_inference")]})
    # bad sample: both became pending with a question, so the user's own words were not used
    assert status(service, "I-user") == "active" and status(service, "I-agent") == "active"
    assert relations(service, "I-agent")["conflicts_with_user"] == ["I-user"] and not service.store.all("undecided", "alice")
    # the agent's reading recorded first, the user's words of the same message after it: the same
    again = message(service, "快一点，打车也行")
    service.ledger.apply("alice", {"items": [entry(again, "I-agent2", "（推断）打车", basis="agent_inference", key="speed")]})
    service.ledger.apply("alice", {"items": [entry(again, "I-user2", "快一点", basis="user_explicit", key="speed")]})
    assert status(service, "I-user2") == "active" and status(service, "I-agent2") == "active"
    assert relations(service, "I-agent2")["conflicts_with_user"] == ["I-user2"]


def test_the_user_still_replaces_their_own_words_and_the_agents(service):
    said = message(service, "明天轻松一点")
    service.ledger.apply("alice", {"items": [entry(said, "I-user", "明天轻松一点", basis="user_explicit")]})
    changed = message(service, "算了，明天赶一点")
    service.ledger.apply("alice", {"items": [entry(changed, "I-user2", "明天赶一点", basis="user_explicit")]})
    assert status(service, "I-user") == "superseded" and relations(service, "I-user2")["supersedes"] == ["I-user"]
    guessed = message(service, "晚饭吃什么")
    service.ledger.apply("alice", {"items": [entry(guessed, "I-agent", "（推断）便宜的", basis="agent_inference", key="food")]})
    told = message(service, "晚饭吃好一点")
    service.ledger.apply("alice", {"items": [entry(told, "I-user3", "晚饭吃好一点", basis="user_explicit", key="food")]})
    assert status(service, "I-agent") == "superseded" and relations(service, "I-user3")["supersedes"] == ["I-agent"]


# --- 2. consider says whose each line is and keeps the user's when it cuts ---------------------------------------------

def test_a_quick_consider_keeps_the_users_preference_when_the_agents_rules_fill_the_limit(trip):
    quote = "我喜欢慢慢逛。"
    rules = [dict(recorded("constraint", f"guess-{index}", f"（推断）规矩 {index}", basis="agent_inference"),
                  compatible_with=[f"guess-{other}" for other in range(7) if other != index]) for index in range(7)]
    said(trip, quote, record=dict(items=[recorded("preference", "pace", "喜欢慢慢逛", basis="user_explicit", quote=quote), *rules]))
    quick = said(trip, "现在去哪？")
    listed = {entry["text"]: entry for entry in quick["consider"]["entries"]}
    # bad sample (gap inventory 2.4 one): rules came before preferences, so the user's own preference was cut
    assert "喜欢慢慢逛" in listed and listed["喜欢慢慢逛"]["layer"] == "用户说的"
    assert len(quick["consider"]["entries"]) <= consider.table()["scenarios"]["quick"]["limit"]
    # the agent's own rules are what gives way: cut from consider, or left out of the quick pack over budget
    left = {entry["id"] for entry in quick["pack"]["meta"].get("omitted", []) if entry.get("reason") == "over_budget"} if isinstance(
        quick["pack"]["meta"], dict) else set()
    guesses = [item["id"] for item in trip.store.all("items", "alice") if item["key"].startswith("guess-")]
    assert all(listed.get(item["text"], {}).get("layer") == "我的推断" for item in trip.store.all("items", "alice")
               if item["key"].startswith("guess-") and item["text"] in listed)
    assert quick["consider"]["more"] + len(left & set(guesses)) + sum(text.startswith("（推断）") for text in listed) >= len(guesses)
    assert quick["pack"]["tokens"] <= 1800


def test_the_users_entries_and_musts_are_never_cut_and_each_line_says_whose(trip):
    quote = "公园、博物馆、咖啡、甜点、市场、河边、老街都想去。"
    words = ["公园", "博物馆", "咖啡", "甜点", "市场", "河边", "老街"]
    mine = [dict(recorded("preference", f"likes-{index}", f"喜欢{word}", basis="user_explicit", quote=quote),
                 compatible_with=[f"likes-{other}" for other in range(len(words)) if other != index]) for index, word in enumerate(words)]
    said(trip, quote, record=dict(items=[*mine, recorded("constraint", "default-walk", "步行不超过 20 分钟", basis="recommended_default"),
                                         recorded("constraint", "must-guess", "（推断）不能晚于 22 点回", basis="agent_inference", strength="must")]))
    quick = said(trip, "附近干嘛？")
    listed = {entry["text"]: entry for entry in quick["consider"]["entries"]}
    assert {item["text"] for item in mine} <= set(listed) and "（推断）不能晚于 22 点回" in listed
    assert "步行不超过 20 分钟" not in listed and quick["consider"]["more"] >= 1  # the agent's default is what gives way
    assert quick["consider"]["entries"][0]["must"]
    words = {entry["layer"] for entry in quick["consider"]["entries"]}
    assert "用户说的" in words and "我的推断" in words
    memory = {entry["text"]: entry for entry in trip.state("alice", view="memory")["consider"]}
    assert memory["步行不超过 20 分钟"]["layer"] == "我的默认"


def test_an_agent_line_that_meets_the_users_names_it_and_the_user_line_is_listed(trip):
    quote = "明天轻松一点。"
    said(trip, quote, record=dict(items=[recorded("preference", "pace", "明天轻松一点", basis="user_explicit", quote=quote)]))
    guessed = said(trip, "去机场怎么走？", record=dict(items=[recorded("preference", "pace", "（推断）多走路省钱", basis="agent_inference")]))
    listed = {entry["text"]: entry for entry in guessed["consider"]["entries"]}
    user = listed["明天轻松一点"]
    assert listed["（推断）多走路省钱"]["conflicts_with_user"] == [user["id"]]
    assert "照用户说的" in guessed["next"]


# --- 3. one record by its ID (the second level of progressive disclosure) ---------------------------------------------

def committed(service, text, items, key):
    turn = said(service, text)
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], idempotency_key=key,
                            changes=dict(items=[dict(item, source=dict(item["source"], event=turn["receipt"]["event_id"])) for item in items]))
    service.commit("alice", draft["draft_id"], key)
    return turn["receipt"]["event_id"]


def test_explain_one_record_gives_its_words_layer_message_status_replacement_and_only_the_latest_version(trip):
    first = committed(trip, "明天轻松一点。", [dict(recorded("preference", "pace", "明天轻松一点", basis="user_explicit", quote="明天轻松一点。"),
                                                  id="I-easy")], "s5-easy")
    committed(trip, "算了，明天赶一点。", [dict(recorded("preference", "pace", "明天赶一点", basis="user_explicit", quote="明天赶一点。"),
                                              id="I-rush")], "s5-rush")
    committed(trip, "去机场怎么走？", [dict(recorded("preference", "pace", "（推断）可以多走路", basis="agent_inference"), id="I-walk")], "s5-walk")
    versions = len(trip.store.all("versions", "alice"))
    assert versions >= 3
    old = trip.explain("alice", "I-easy")
    # bad sample (gap inventory 2.1): no record (its words, layer, message) and every version with its whole pack
    card = old["record"]
    assert card["text"] == "明天轻松一点" and card["quote"] == "明天轻松一点。" and card["layer"] == "用户说的"
    assert card["said"]["event"] == first and card["said"]["text"] == "明天轻松一点。" and card["said"]["speaker"] == "p1"
    assert card["status"] == "superseded" and card["superseded_by"] == ["I-rush"] and card["current"] == "I-rush"
    assert [line["version"] for line in old["version_usage"]] == [max(version["number"] for version in trip.store.all("versions", "alice"))]
    assert "constraints" not in old["version_usage"][0]
    mine = trip.explain("alice", "I-walk")["record"]
    assert mine["layer"] == "我的推断" and mine["relations"]["conflicts_with_user"] == ["I-rush"] and mine["status"] == "active"
    assert trip.explain("alice", "I-rush")["record"]["conflicted_by"] == ["I-walk"]
    assert len(trip.explain("alice", version=None)["version_usage"]) == versions  # without a record ID, as before


def test_explain_one_record_answers_over_the_mcp_server_without_any_repository_file(trip):
    import asyncio

    from tripwright.adapters.mcp import create_server, request_user

    committed(trip, "晚饭吃好一点。", [dict(recorded("preference", "food", "晚饭吃好一点", basis="user_explicit", quote="晚饭吃好一点。"),
                                              id="I-food")], "s5-food")
    server = create_server(trip, local_user="alice", manage_jobs=False)

    async def run():
        token = request_user.set("alice")
        try:
            return await server.call_tool("explain", {"record_id": "I-food"})
        finally:
            request_user.reset(token)
    result = asyncio.run(run())
    structured = result[1] if isinstance(result, tuple) else result.structured_content
    assert structured["record"]["text"] == "晚饭吃好一点" and structured["record"]["said"]["text"] == "晚饭吃好一点。"
    assert trip.repository is None  # the single-database (remote-like) service: nothing read from a trip repository


# --- 4. the cloud bridge: the agent sends back what it inferred or chose -----------------------------------------------

def test_turn_and_the_instructions_ask_for_the_agents_own_inferences_in_the_same_proposal(trip):
    from tripwright.agents import server_instructions
    from tripwright.agents.assemble import INSTRUCTIONS_LIMIT

    turn = said(trip, "去机场怎么走？")
    step = next(step for step in turn["steps"] if step["step"] == 6)
    assert "agent_inference" in step["do"] and "one `propose`" in step["do"] and "nothing in between" in step["do"]
    # bad sample: turn returned no shape for sending the agent's own inferences back
    hint = turn["write_back"]
    assert "agent_inference" in hint["say"] and "propose" in hint["say"] and "commit" in hint["say"]
    [shape] = hint["record"]["items"]
    assert shape["source"] == {"event": turn["receipt"]["event_id"], "basis": "agent_inference"} and shape["quote"] == ""
    text = server_instructions()
    assert "agent_inference" in text and len(text.encode()) <= INSTRUCTIONS_LIMIT


def test_inferences_sent_with_the_plan_need_no_usage_while_the_users_words_still_do(trip):
    turn = said(trip, "明天轻松一点，帮我排一下。", scenario="plan")
    plan = trip.state("alice")["plan"]
    guess = dict(recorded("preference", "walk", "（推断）可以多走路", basis="agent_inference"), id="I-guess",
                 source=dict(event=turn["receipt"]["event_id"], basis="agent_inference"))
    draft = trip.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=plan, idempotency_key="s5-guess",
                         changes=dict(items=[guess]))
    # bad sample (gap inventory 1.6 iii): the agent's own inference became a required condition without a usage
    assert "usage:I-guess" not in [check["id"] for check in draft["checks"]["conflicts"]], draft["checks"]["conflicts"]
    trip.commit("alice", draft["draft_id"], "s5-guess", accept_unverified=True)
    # once recorded, the next proposal's wide pack carries it without asking for a disposition
    turn = said(trip, "再排一下。", scenario="plan")
    draft = trip.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=plan, idempotency_key="s5-again")
    pack = trip.store.get("drafts", "alice", draft["draft_id"])["verify"]["pack"]
    assert "I-guess" in pack["required"]["items"]["ids"] and pack["records"]["I-guess"]["non_required"]
    assert "I-guess" not in [entry["item_id"] for entry in pack["dispositions"]]
    assert "usage:I-guess" not in [check["id"] for check in draft["checks"]["conflicts"]]
    # control: the user's own words in the same proposal still need a usage
    turn = said(trip, "晚饭吃好一点。", scenario="plan")
    mine = dict(recorded("preference", "food", "晚饭吃好一点", basis="user_explicit", quote="晚饭吃好一点。"), id="I-food",
                source=dict(event=turn["receipt"]["event_id"], basis="user_explicit"))
    draft = trip.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], plan=plan, idempotency_key="s5-food",
                         changes=dict(items=[mine]))
    assert "usage:I-food" in [check["id"] for check in draft["checks"]["conflicts"]]


def test_a_wrong_record_in_turn_still_receives_the_message_and_drafts_the_first_part(trip):
    # bad sample (gap inventory 3.5): a record of the wrong shape refused the whole turn, so the first part came late
    turn = said(trip, "去机场怎么走？", record=dict(items=["（推断）多走路"]))
    assert turn["first_part"]["text"] and turn["receipt"]["event_id"]
    [left] = turn["recorded"]["not_recorded"]
    assert left["part"] == "record" and "record.items.0" in left["reason"] and turn["receipt"]["event_id"] in left["fix"]
    assert "propose" in left["fix"] and "record" in turn["next"]
    # an entry the ledger refuses (a quote that is not in the message) and a part turn does not take: the same
    refused = said(trip, "晚上吃什么？", record=dict(items=[recorded("preference", "food", "便宜的", basis="agent_inference", quote="想吃便宜的")],
                                                    decisions=[dict(question="晚饭", choice="A")]))
    assert refused["first_part"]["text"] and refused["recorded"]["not_recorded"][0]["part"] == "record"
    assert not [item for item in trip.store.all("items", "alice") if item["key"] == "food"]  # nothing half-recorded
    # the same request sent again returns the same receipt; a corrected record goes into propose, as fix says
    first = trip.turn("alice", "怎么走？", "s5-same", record=dict(items=["x"]))
    assert trip.turn("alice", "怎么走？", "s5-same", record=dict(items=["x"]))["receipt"] == first["receipt"]
