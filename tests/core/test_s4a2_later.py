"""S4a2: a first part that ends the reply with first_part.later (an app that cannot show text between tool calls, core
step 4) is a legal end for check_answer and for the Stop hook; a status line after it, or in it, is not.

Offline: the fictional plan and MOTIS of test_a16_fixes, fictional places."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from test_a12_first_part import FIRST
from test_a14_fixes import GO
from test_a16_fixes import WAYS, first_checks, make  # noqa: F401 (make is a fixture)
from tripwright.render.check_answer import stop_decision

STATUS = "我先去查一下，马上回来。"


def transcript(*texts: str) -> list[dict]:
    """A Claude Code transcript of one question: the user's message, then each text the main agent showed."""
    stamp = lambda at: datetime.fromtimestamp(at, timezone.utc).isoformat().replace("+00:00", "Z")
    asked = time.time()
    entries = [dict(type="user", timestamp=stamp(asked), message=dict(role="user", content=FIRST))]
    entries += [dict(type="assistant", timestamp=stamp(asked + 5 + index), message=dict(role="assistant", content=[dict(type="text", text=text)]))
                for index, text in enumerate(texts)]
    return entries


def stop(texts: list[str], minute: int) -> tuple:
    return stop_decision(dict(last_assistant_message=texts[-1]), {}, transcript(*texts), lambda _: minute)


def test_a_first_part_may_end_the_reply_with_later_and_nothing_after_it(make):
    service = make(tracks=WAYS)
    first = service.turn("local", FIRST, "q1", scenario="quick", points=GO)["first_part"]
    ends = first["text"] + "\n" + first["later"]
    minute = 21 * 60 + 1
    # control: ending with later is a whole first part, and the Stop hook lets the turn end
    assert first_checks(service, ends, "q1") == {}
    assert stop([ends], minute) == (None, "first part ends with first_part.later: the user's next message brings the second part")
    # control: without later the hook still pushes for the second part (D-48)
    assert stop([first["text"]], minute)[0] is not None
    # bad samples: a status line after later, or between the first part and later, in the same text
    after = first_checks(service, ends + "\n" + STATUS, "q1")
    assert set(after) == {"no_status_line", "ends_with_later"}
    before = first_checks(service, first["text"] + "\n" + STATUS + "\n" + first["later"], "q1")
    assert set(before) == {"no_status_line"}
    for text in (ends + "\n" + STATUS, first["text"] + "\n" + STATUS + "\n" + first["later"]):
        reason, verdict = stop([text], minute)
        assert reason.startswith("第一段以“回我一句“继续””那一行结束这一轮") and verdict == "blocked: text after first_part.later"
    # bad sample: the status line sent as a text of its own after the first part
    reason, _ = stop([ends, STATUS], minute)
    assert reason.startswith("第一段以“回我一句“继续””那一行结束这一轮")
    # the server's own “没核：……第二段核完再发。” is not a status line
    assert "no_status_line" not in first_checks(service, first["text"], "q1")


def test_the_later_line_counts_whatever_quote_marks_the_host_writes(make):
    """S4e: a host copied the line with ASCII quotes (dry_cc_ns_synth, 11-03); the line is the same words, so it still ends
    the first part, while a status line after it is still caught."""
    service = make(tracks=WAYS)
    first = service.turn("local", FIRST, "q1", scenario="quick", points=GO)["first_part"]
    minute = 21 * 60 + 1
    for later in (first["later"].replace("“", '"').replace("”", '"'), first["later"].replace("“", "「").replace("”", "」"),
                  first["later"].rstrip("。")):
        ends = first["text"] + "\n" + later
        assert first_checks(service, ends, "q1") == {}
        assert stop([ends], minute) == (None, "first part ends with first_part.later: the user's next message brings the second part")
        # bad sample: a status line after it is still text after first_part.later
        reason, verdict = stop([ends + "\n" + STATUS], minute)
        assert verdict == "blocked: text after first_part.later"


def continued(first_reply: str, *after: str, lookup: bool = True) -> list[dict]:
    """A transcript of a question answered over two messages: the question, a reply that ends `first_reply`, the user's
    “继续”, then the texts shown after it (with one query before them when `lookup`)."""
    stamp = lambda at: datetime.fromtimestamp(at, timezone.utc).isoformat().replace("+00:00", "Z")
    asked = time.time() - 60
    query = dict(type="tool_use", name="mcp__tripwright__query", input=dict(kind="latest_departure"))
    entries = [dict(type="user", timestamp=stamp(asked), message=dict(role="user", content=FIRST)),
               dict(type="assistant", timestamp=stamp(asked + 5), message=dict(role="assistant", content=[dict(type="text", text=first_reply)])),
               dict(type="user", timestamp=stamp(asked + 30), message=dict(role="user", content="继续"))]
    if lookup:
        entries.append(dict(type="assistant", timestamp=stamp(asked + 32), message=dict(role="assistant", content=[query])))
    entries += [dict(type="assistant", timestamp=stamp(asked + 40 + index), message=dict(role="assistant", content=[dict(type="text", text=text)]))
                for index, text in enumerate(after)]
    return entries


def test_after_continue_the_hooks_take_the_first_part_as_sent(make):
    """S4e (dry_cc_ns_synth_v4): after “继续” the first-part hook denied the lookups (no first part since the user's last
    message) and the host sent its first part again. The message that answers a later line is the same question's second
    half: lookups go through, and the Stop hook pushes for the second part, not for a first part."""
    from tripwright.render.check_answer import FIRST_REASON, PUSH_REASON, first_part_decision

    service = make(tracks=WAYS)
    first = service.turn("local", FIRST, "q1", scenario="quick", points=GO)["first_part"]
    minute = 21 * 60 + 1
    query = dict(tool_name="mcp__tripwright__query", tool_input=dict(kind="latest_departure"))
    ends = first["text"] + "\n" + first["later"].replace("“", '"').replace("”", '"')
    # after “继续”: the lookup goes through, and stopping without the second part is pushed to write it
    assert first_part_decision(query, continued(ends, lookup=False), lambda _: minute) == (
        None, "first part sent in the reply that ended with first_part.later")
    reason, verdict = stop_decision(dict(last_assistant_message=""), {}, continued(ends), lambda _: minute)
    assert reason.startswith(PUSH_REASON) and "no first part" not in verdict
    # control: a reply that did not end with later, then a new message: a lookup still waits for this question's first part
    reason, _ = first_part_decision(query, continued(first["text"], lookup=False), lambda _: minute)
    assert reason is not None and reason.startswith(FIRST_REASON)
