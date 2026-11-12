"""A change that brings in unverified facts is saved only after the user has answered (2026-11-12).

Claude and ChatGPT on the web both moved a dinner to 19:00, committed it with accept_unverified in the same turn and
told the user afterwards that the opening hours were not checked. The service could not tell "told first, then
committed" from "committed, then told". Now a host agent's commit (ask_first, set by the MCP adapter) counts
accept_unverified only when a message of the user arrived after a draft returned those facts. Only what the change
brings in is asked: the fictional trip carries ten unverified facts from the start, and a change that leaves them alone
saves as before."""
import asyncio
import time
from copy import deepcopy

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from tripwright.adapters.mcp import create_server
from tripwright.agents import server_instructions
from tripwright.demo import example

DINNER = "evidence:2030-06-10:d1-dinner:opening_hours"
MOVE = "把 6 月 10 日的晚饭从 17:00 挪到 19:00。"
YES = "可以，照这样存"


@pytest.fixture
def trip(service, monkeypatch):
    """The fictional trip as the acceptance instance held it: one version, every opening hour unverified."""
    monkeypatch.setenv("TRIPWRIGHT_NOW", "2030-06-10T16:00:00+02:00")
    monkeypatch.setenv("TRIPWRIGHT_NOW_SET_AT", str(time.time()))
    turn = service.turn("alice", "Create this fictional example trip.", "seed", scenario="status")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=0, plan=deepcopy(example()["trip"]), idempotency_key="seed")
    # code that seeds a trip vouches for the user itself: no ask_first
    service.commit("alice", draft["draft_id"], "seed", accept_unverified=True)
    return service


def day_with(service, **times):
    """Day one with blocks moved: block id → (start, end)."""
    day = deepcopy(service.state("alice", "day", "2030-06-10")["json"])
    for candidate in day["plans"]:
        for track in candidate["tracks"]:
            for block in track["blocks"]:
                if block["id"].replace("-", "_") in times:
                    block["start"], block["end"] = times[block["id"].replace("-", "_")]
    return day


def moved(service, key="move", text=MOVE, dinner=("19:00", "20:00")):
    turn = service.turn("alice", text, key, scenario="replan")
    draft = service.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"],
                            days=[day_with(service, d1_dinner=dinner, d1_coffee=("20:30", "21:30"))], idempotency_key=key)
    assert draft["status"] == "proposed", draft
    return turn, draft


def host_commit(service, draft, key="commit", **arguments):
    return service.commit("alice", draft["draft_id"], key, ask_first=True, **arguments)


def dinner_line(service):
    return next(line for line in service.state("alice", "day", "2030-06-10")["text"].splitlines() if "Havnekaf" in line)


def test_the_saved_plan_already_carries_unverified_facts_and_propose_names_only_the_new_one(trip):
    carried = [check["id"] for check in trip.versions.current("alice")["checks"]["unverified"] if check["critical"]]
    assert len(carried) == 10 and DINNER in carried
    _, draft = moved(trip)
    asked = draft["ask_user"]
    assert [check["id"] for check in asked["unverified"]] == [DINNER]
    assert asked["unverified"][0]["details"] == dict(date="2030-06-10", place="oslo-cafe", start="19:00", end="20:00")
    assert "commit 要等用户回话" in asked["how"] and "1 条" in asked["how"]


@pytest.mark.parametrize("flag", [False, True])
def test_in_the_turn_that_proposed_it_the_change_is_not_saved_with_or_without_the_flag(trip, flag):
    _, draft = moved(trip)
    with pytest.raises(ValueError, match="Not saved yet: this change brings in 1 fact") as refusal:
        host_commit(trip, draft, accept_unverified=flag)
    text = str(refusal.value)
    assert DINNER in text and "oslo-cafe 19:00–20:00" in text and "ask whether to save it as it is" in text
    assert trip.versions.current("alice")["number"] == 1 and dinner_line(trip).startswith("17:00")


def test_the_same_words_sent_again_or_a_screenshot_turn_are_not_the_users_answer(trip):
    _, draft = moved(trip)
    again = trip.turn("alice", MOVE, "move-again", scenario="replan")
    shots = trip.turn("alice", "截图一：港边咖啡馆菜单", "shot", nature="screenshot_extraction")
    assert "waiting_draft" not in again and "waiting_draft" not in shots
    with pytest.raises(ValueError, match="no message of the user has arrived since they were returned"):
        host_commit(trip, draft, accept_unverified=True)
    # the answer settles what came before it, these two turns included
    trip.turn("alice", YES, "yes", scenario="plan")
    assert host_commit(trip, draft, accept_unverified=True)["number"] == 2


def test_after_the_users_answer_the_same_draft_is_saved_and_the_version_names_the_answer(trip):
    _, draft = moved(trip)
    answer = trip.turn("alice", YES, "yes", scenario="plan")
    waiting = answer["waiting_draft"]
    assert waiting["draft_id"] == draft["draft_id"] and waiting["unverified"] == [DINNER] and waiting["stale"] is False
    assert answer["next"].startswith(f"草稿 {draft['draft_id']} 在等用户这句回话")
    # the answer is no new requirement: planning alone names nobody
    assert answer["flow"]["dispatch"] == []
    # the agent still has to state that the user agreed
    with pytest.raises(ValueError, match="A message has arrived since they were returned: if it agrees to save the change as it is"):
        host_commit(trip, draft)
    version = host_commit(trip, draft, accept_unverified=True)
    assert version["number"] == 2 and version["accept_unverified"] is True
    assert version["answered"] == dict(event=answer["receipt"]["event_id"], unverified=[DINNER])
    assert dinner_line(trip).startswith("19:00")
    # the fact stays unverified in the saved version
    assert DINNER in [check["id"] for check in version["checks"]["unverified"]]


def test_a_message_after_the_answer_is_not_covered_by_it(trip):
    """2026-11-13, the judge's probe: "yes, save it", then "and cancel the coffee", saved the old draft with the coffee
    still in it. The answer settles the messages up to it; the one after it has to be recorded or acknowledged."""
    _, draft = moved(trip)
    trip.turn("alice", YES, "yes", scenario="plan")
    more = trip.turn("alice", "第一天的咖啡取消吧。", "more", scenario="replan")
    assert "waiting_draft" not in more  # only the first message after the draft is taken as its answer
    with pytest.raises(ValueError, match="Unsettled messages after the draft: " + more["receipt"]["event_id"]):
        host_commit(trip, draft, accept_unverified=True)
    assert trip.versions.current("alice")["number"] == 1
    # proposed again for the new message, the same fact is not asked a second time
    day = day_with(trip, d1_dinner=("19:00", "20:00"))
    day["plans"][0]["tracks"][0]["blocks"] = [block for block in day["plans"][0]["tracks"][0]["blocks"] if block["id"] != "d1-coffee"]
    again = trip.propose("alice", ctx_id=more["ctx_id"], base_version=more["base_version"], days=[day], idempotency_key="more")
    assert again["status"] == "proposed" and "ask_user" not in again
    version = host_commit(trip, again, key="more", accept_unverified=True)
    assert version["number"] == 2 and "Agreed café" not in trip.state("alice", "day", "2030-06-10")["text"]


def test_an_unrelated_message_first_leaves_the_real_answer_to_be_settled(trip):
    """The service cannot tell which message agrees: the first one after the draft is taken as the answer, so a
    question in between makes the "yes" a later message, settled like any other (here: declared with ack_events)."""
    _, draft = moved(trip)
    trip.turn("alice", "那家店在哪？", "where", scenario="status")
    yes = trip.turn("alice", YES, "yes", scenario="plan")
    with pytest.raises(ValueError, match="Unsettled messages after the draft: " + yes["receipt"]["event_id"]):
        host_commit(trip, draft, accept_unverified=True)
    trip.jobs.run_once("alice")
    trip.result("alice", trip.store.get("drafts", "alice", draft["draft_id"])["job_id"], ack_events=[yes["receipt"]["event_id"]])
    assert host_commit(trip, draft, accept_unverified=True)["number"] == 2


def test_a_draft_proposed_again_after_the_answer_does_not_ask_a_second_time(trip):
    moved(trip)
    answer = trip.turn("alice", YES, "yes", scenario="replan")
    again = trip.propose("alice", ctx_id=answer["ctx_id"], base_version=answer["base_version"],
                         days=[day_with(trip, d1_dinner=("19:00", "20:00"), d1_coffee=("20:30", "21:30"))], idempotency_key="again")
    assert "ask_user" not in again
    version = host_commit(trip, again, accept_unverified=True)
    assert version["number"] == 2 and version["answered"]["event"] == answer["receipt"]["event_id"]


def test_the_answer_covers_only_the_facts_that_were_returned_before_it(trip):
    moved(trip)
    answer = trip.turn("alice", "可以，不过改成 19:30 吧", "later", scenario="replan")
    other = trip.propose("alice", ctx_id=answer["ctx_id"], base_version=answer["base_version"],
                         days=[day_with(trip, d1_dinner=("19:30", "20:30"), d1_coffee=("20:30", "21:30"))], idempotency_key="other")
    assert other["ask_user"]["unverified"][0]["details"]["start"] == "19:30"
    with pytest.raises(ValueError, match="oslo-cafe 19:30–20:30"):
        host_commit(trip, other, accept_unverified=True)


def test_a_refused_draft_also_names_the_unverified_facts_so_the_corrected_one_needs_no_extra_round(trip):
    turn = trip.turn("alice", MOVE, "clash", scenario="replan")
    # the coffee stays at 18:00 in the same place: dinner at 19:00 leaves it no time, and the draft is refused
    clash = trip.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"],
                         days=[day_with(trip, d1_dinner=("19:00", "20:00"), d1_coffee=("18:30", "19:30"))], idempotency_key="clash")
    assert clash["status"] == "rejected" and clash["checks"]["conflicts"]
    asked = [check["id"] for check in clash["ask_user"]["unverified"]]
    # the dinner hour, and for each person the unknown way from the gallery to a coffee that now comes before dinner
    assert asked[0] == DINNER and len(asked) == 4 and all(name.startswith("chain:") and name.endswith(":d1-coffee") for name in asked[1:])
    assert "草稿没被接受" in clash["ask_user"]["how"]
    answer = trip.turn("alice", "那把咖啡顺延到 20:30，照这样存", "fix", scenario="replan")
    assert "waiting_draft" not in answer  # a refused draft is not what waits; the corrected one is proposed now
    fixed = trip.propose("alice", ctx_id=answer["ctx_id"], base_version=answer["base_version"],
                         days=[day_with(trip, d1_dinner=("19:00", "20:00"), d1_coffee=("20:30", "21:30"))], idempotency_key="fix")
    assert fixed["status"] == "proposed" and "ask_user" not in fixed
    assert host_commit(trip, fixed, accept_unverified=True)["answered"]["unverified"] == [DINNER]


def test_an_english_conversation_gets_the_same_steps_in_english(trip):
    _, draft = moved(trip, text="Move the dinner on June 10 from 17:00 to 19:00.")
    assert draft["ask_user"]["how"].startswith("This change brings in 1 unverified fact(s); commit waits for the user's answer.")
    answer = trip.turn("alice", "Yes, save it like that.", "yes", scenario="plan")
    assert answer["next"].startswith(f"Draft {draft['draft_id']} was waiting for this reply (1 unverified fact(s)).")
    assert host_commit(trip, draft, accept_unverified=True)["number"] == 2
    turn = trip.turn("alice", "Move the coffee before dinner, to 18:30.", "clash", scenario="replan")
    clash = trip.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"],
                         days=[day_with(trip, d1_coffee=("18:30", "19:30"))], idempotency_key="clash")
    assert clash["status"] == "rejected" and clash["ask_user"]["how"].startswith("The draft was not accepted")
    fact = dict(key="luggage", value=False, text="Mira has no suitcase", subject=["p1"], true_from="2030-06-10T16:00:00+02:00",
                invalidated_by="user says otherwise", source=dict(event="this", basis="user_explicit"))
    _, again = moved(trip, key="again", text="Move the dinner on June 10 to 19:30.", dinner=("19:30", "20:30"))
    stale = trip.turn("alice", "Yes. And I have no suitcase.", "yes-and", scenario="plan", record=dict(facts=[fact]))
    assert stale["waiting_draft"]["stale"] is True and "propose the same change again" in stale["waiting_draft"]["say"]


def test_a_change_that_brings_in_nothing_new_is_saved_at_once(trip):
    turn = trip.turn("alice", "把第一天的标题改成 Oslo day。", "title", scenario="replan")
    day = day_with(trip)
    day["title"] = "Oslo day"
    draft = trip.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], days=[day], idempotency_key="title")
    assert "ask_user" not in draft and len([check for check in draft["checks"]["unverified"] if check["critical"]]) == 10
    version = host_commit(trip, draft)
    assert version["number"] == 2 and "answered" not in version


def test_a_message_that_changes_the_ledger_leaves_the_waiting_draft_stale_and_says_to_propose_again(trip):
    _, draft = moved(trip)
    fact = dict(key="luggage", value=False, text="Mira 没有箱子", subject=["p1"], true_from="2030-06-10T16:00:00+02:00",
                invalidated_by="user says otherwise", source=dict(event="this", basis="user_explicit"))
    answer = trip.turn("alice", "可以，照这样存。另外我没带箱子。", "yes-and", scenario="plan", record=dict(facts=[fact]))
    assert answer["waiting_draft"]["stale"] is True and "重新 propose" in answer["waiting_draft"]["say"]
    with pytest.raises(ValueError, match="Ledger changed; propose again"):
        host_commit(trip, draft, accept_unverified=True)


def test_what_the_message_states_is_recorded_first_and_only_the_plan_change_waits(trip):
    turn = trip.turn("alice", MOVE + "另外记一下：我对花生过敏。", "both", scenario="replan", speaker="p1")
    item = dict(kind="constraint", key="peanut", quote="我对花生过敏", text="Mira 花生过敏", subject=["p1"], strength="forbid",
                source=dict(event=turn["receipt"]["event_id"], basis="user_explicit"))
    draft = trip.propose("alice", ctx_id=turn["ctx_id"], base_version=turn["base_version"], changes=dict(items=[item]),
                         days=[day_with(trip, d1_dinner=("19:00", "20:00"), d1_coffee=("20:30", "21:30"))], idempotency_key="both")
    assert draft["recorded_first"]["committed"] is True
    # the allergy came in with its own version; the plan draft on top of it asks about the dinner hour only
    assert [check["id"] for check in draft["ask_user"]["unverified"]] == [DINNER]
    with pytest.raises(ValueError, match="Not saved yet"):
        host_commit(trip, draft, accept_unverified=True)
    assert trip.versions.current("alice")["number"] == 2 and dinner_line(trip).startswith("17:00")
    assert [entry["key"] for entry in trip.state("alice", "ledger")["items"]] == ["peanut"]


def call(server, tool, **arguments):
    return asyncio.run(server.call_tool(tool, arguments)).structured_content


def test_the_commit_tool_asks_first_for_every_host(trip):
    server = create_server(trip, local_user="alice", manage_jobs=False)
    turn = call(server, "turn", text=MOVE, scenario="replan")
    draft = call(server, "propose", ctx_id=turn["ctx_id"], base_version=turn["base_version"], idempotency_key="tool",
                 days=[day_with(trip, d1_dinner=("19:00", "20:00"), d1_coffee=("20:30", "21:30"))])
    with pytest.raises(ToolError, match="Not saved yet: this change brings in 1 fact"):
        call(server, "commit", draft_id=draft["draft_id"], idempotency_key="tool", accept_unverified=True)
    answer = call(server, "turn", text=YES, scenario="plan")
    assert answer["waiting_draft"]["draft_id"] == draft["draft_id"]
    version = call(server, "commit", draft_id=draft["draft_id"], idempotency_key="tool", accept_unverified=True)
    assert version["accepted"] is True and version["number"] == 2 and version["answered"]["unverified"] == [DINNER]


def test_the_operator_can_let_the_agent_accept_by_itself(trip, monkeypatch):
    """TRIPWRIGHT_SAVE_UNVERIFIED=agent: nothing waits for the user, and no result says that it does. It is not the
    behaviour before 2026-11-12 in one respect, which holds in both modes: unverified facts the saved plan already
    carries need no flag."""
    monkeypatch.setenv("TRIPWRIGHT_SAVE_UNVERIFIED", "agent")
    server = create_server(trip, local_user="alice", manage_jobs=False)
    turn = call(server, "turn", text=MOVE, scenario="replan")
    draft = call(server, "propose", ctx_id=turn["ctx_id"], base_version=turn["base_version"], idempotency_key="tool",
                 days=[day_with(trip, d1_dinner=("19:00", "20:00"), d1_coffee=("20:30", "21:30"))])
    assert "ask_user" not in draft and [check["id"] for check in draft["tell_user"]["unverified"]] == [DINNER]
    assert "等用户回话" not in draft["tell_user"]["how"] and "accept_unverified=true" in draft["tell_user"]["how"]
    with pytest.raises(ToolError, match="Commit with accept_unverified=true to save it with them") as refusal:
        call(server, "commit", draft_id=draft["draft_id"], idempotency_key="tool")  # the facts are still named
    assert DINNER in str(refusal.value) and "ask whether" not in str(refusal.value)
    version = call(server, "commit", draft_id=draft["draft_id"], idempotency_key="tool", accept_unverified=True)
    assert version["number"] == 2 and "answered" not in version
    # the next message is no answer to anything
    later = call(server, "turn", text="把第一天的标题改成 Oslo day。", scenario="replan")
    assert "waiting_draft" not in later
    day = day_with(trip)
    day["title"] = "Oslo day"
    title = call(server, "propose", ctx_id=later["ctx_id"], base_version=later["base_version"], idempotency_key="title", days=[day])
    assert "tell_user" not in title and "ask_user" not in title
    assert call(server, "commit", draft_id=title["draft_id"], idempotency_key="title")["number"] == 3


def test_tool_descriptions_describe_and_do_not_repeat_the_chat_rules(service):
    """ChatGPT's web app marked the earlier descriptions as suspicious instructions (2026-11-12): turn's repeated the
    six chat rules and commit's said when the model may set accept_unverified."""
    tools = {tool.name: tool.description for tool in asyncio.run(create_server(service, manage_jobs=False).list_tools())}
    rules = [line.split(". ", 1)[1] for line in server_instructions().splitlines()[1:]]
    assert len(rules) == 6 and not any(rule in tools["turn"] for rule in rules)
    opening = dict(turn="Receives", query="Looks up", propose="Creates", commit="Saves", result="Returns", state="Reads",
                   explain="Explains")
    assert {name: text.split(" ")[0] for name, text in tools.items()} == {name: verb.split(" ")[0] for name, verb in opening.items()}
    for text in tools.values():
        assert not any(order in text for order in ("Call `turn`", "only after telling", "Set accept_unverified", "Do not ", "Never "))
