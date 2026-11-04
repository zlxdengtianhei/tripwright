"""S5b (Claude Code replays s5_ns_on, s5_mcponly; judge verdict-s5-claude-code): the host reads what to do at the moment it
does it. Every turn's result opens with `do_first`: send first_part as the reply's first and only message, ending with the
later line, then end the reply; after “继续” (second_part_due), write nothing before the second part. The Stop hook's
push after “继续” opens with the same sentence, and its reply to a sent second part that differs from the passed draft
tells the host to end the reply without a word once the recheck passes.

Offline: the fictional plan and MOTIS of test_a16_fixes, fictional places."""
from __future__ import annotations

import asyncio
import json

from test_a12_first_part import FIRST
from test_a14_fixes import GO
from test_a16_fixes import WAYS, make  # noqa: F401 (make is a fixture)
from test_s4a2_later import continued
from tripwright.render.check_answer import stop_decision

GO_ON = "继续"


def test_the_first_thing_turn_says_is_to_send_the_first_part_alone_and_end_the_reply(make):
    service = make(tracks=WAYS)
    asked = service.turn("local", FIRST, "q1", scenario="quick", points=GO)
    # bad sample (s5_mcponly_r1: a zero-width character sent, lookups, the second part 93 s later): what to do with
    # first_part came only in `next`, after a dozen other fields
    assert list(asked)[0] == "do_first", list(asked)[:3]
    say = asked["do_first"]
    assert say.startswith("先把 first_part.text 原样发出") and "第一条、也是唯一一条消息" in say and "first_part.later" in say
    assert "结束这一轮" in say and "发之前不调别的工具、不写别的字" in say
    # a host that dispatches its researcher in the background (step 3) keeps its own order
    assert "在后台派 researcher 的" in say


def test_after_go_on_the_first_thing_turn_says_is_to_write_nothing_before_the_second_part(make):
    service = make(tracks=WAYS)
    service.turn("local", FIRST, "q1", scenario="quick", points=GO)
    going_on = service.turn("local", GO_ON, "q2", scenario="quick", points=[])
    assert going_on["second_part_due"]["request_id"] == "q1"
    # bad sample (s5_ns_on_r1, s5_mcponly_r2: “Now running the lookups: …”, “先说一下：……马上发。” before the second part)
    assert list(going_on)[0] == "do_first"
    assert going_on["do_first"] == "这一轮在第二段之前不写任何字：查、核、过 check_answer 之后只发第二段这一条，发完结束这一轮；不提检查和工具回执。"


def test_a_turn_without_points_says_first_to_call_turn_again(make):
    service = make(tracks=WAYS)
    plain = service.turn("local", "最晚几点走？", "q1", scenario="quick")
    assert list(plain)[0] == "do_first" and plain["do_first"].startswith("先补 points")
    assert plain["next"].startswith("这一问没给 points")


def test_do_first_is_the_first_field_the_host_reads_over_mcp(make):
    from tripwright.adapters.mcp import create_server, request_user

    service = make(tracks=WAYS)
    server = create_server(service, local_user="local", manage_jobs=False)

    async def run():
        token = request_user.set("local")
        try:
            return await server.call_tool("turn", dict(text=FIRST, request_id="q1", scenario="quick", points=GO))
        finally:
            request_user.reset(token)
    result = asyncio.run(run())
    content = result[0] if isinstance(result, tuple) else result.content
    text = " ".join(getattr(block, "text", "") for block in content)
    assert next(iter(json.loads(text))) == "do_first", text[:120]


def test_the_stop_hook_after_go_on_opens_with_nothing_before_the_second_part(make):
    service = make(tracks=WAYS)
    first = service.turn("local", FIRST, "q1", scenario="quick", points=GO)["first_part"]
    ends = first["text"] + "\n" + first["later"]
    minute = 21 * 60 + 1
    # the host looked something up after “继续” and stopped without a second part
    reason, verdict = stop_decision(dict(last_assistant_message=""), {}, continued(ends), lambda _: minute)
    assert reason.startswith("这一轮在第二段之前不写任何字：查、核、过 check_answer 之后只发第二段这一条，发完结束这一轮；不提检查和工具回执。"), reason
    # bad sample (s5_mcponly_r2): a status line sent after “继续” is still pushed, with the same sentence first
    reason, _ = stop_decision(dict(last_assistant_message="先说一下：都查完了，正在核对第二段，马上发。"), {},
                              continued(ends, "先说一下：都查完了，正在核对第二段，马上发。"), lambda _: minute)
    assert reason.startswith("这一轮在第二段之前不写任何字")


def test_after_a_blocked_second_part_passes_its_recheck_the_host_ends_without_a_word():
    # bad sample (s5_ns_on_r2 question 2): “过了就结束，别再发一遍” was followed by “第二段上面已经发过了，没有要改的地方。”
    second = "# 第二段 · 核过了（21:05）\n今晚不用赶。"
    reason, verdict = stop_decision(dict(last_assistant_message=second), {"q1": dict(sha256="other", wall=0)}, [], lambda _: 0)
    assert verdict == "blocked: differs from the passed draft"
    assert "重核过了就结束这一轮，不写任何字" in reason and "没过只发一条“更正：”" in reason
    assert "别再发一遍" not in reason
