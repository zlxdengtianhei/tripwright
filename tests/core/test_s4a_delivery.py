"""S4a: the second part as a separate reply where an app cannot show text between tool calls, and the source of the status
lines before the first part without MCP.

1. turn returns the steps of every message; the first part carries `later`, the line that ends a reply with it (core
   step 4). The user's next message, sent with points [], gets `second_part_due` for the earlier question and no new
   first part, until a second part for that question passed check_answer.
2. Without MCP the command line printed the first part twice: in the JSON and as plain lines on stderr. In the self
   replays (a17b, a16) every turn saw those plain lines in a tool result before sending the first part, and the status
   lines came after them; `2>&1 | json.load` broke on the mix. It now prints the JSON only.
3. The PreToolUse hook took `tripwright … query --help` for a lookup and denied it, right before the background dispatch
   and the status line (a17b_off_r1); reading the help looks nothing up.

Offline: the fictional plan and MOTIS of test_a16_fixes (public Vienna stop names, made-up times), fictional places."""
from __future__ import annotations

import json

from test_a12_first_part import FIRST
from test_a14_fixes import GO
from test_a16_fixes import WAYS, make, query  # noqa: F401 (make is a fixture)
from tripwright import cli
from tripwright.agents import core_steps
from tripwright.render.check_answer import draft_digest, lookup_call
from tripwright.storage import remember_answer

GO_ON = "继续"


# --- 1. the second part in the next reply ---------------------------------------------------------------------------------------

def test_a_reply_that_ends_with_the_first_part_gets_the_second_part_on_the_next_message(make):
    service = make(tracks=WAYS)
    asked = service.turn("local", FIRST, "q1", scenario="quick", points=GO)
    # every turn carries the steps and the line that ends a reply with the first part
    assert asked["steps"] == core_steps()["steps"] and len(asked["steps"]) == 8 and asked["always"] == core_steps()["always"]
    assert asked["first_part"]["later"] == "要核过的第二段，回我一句“继续”。"
    assert "第二段" in asked["first_part"]["text"] and "second_part_due" not in asked
    # bad sample: “继续” used to get a first part of its own, drafted from the plan again, and nothing pointed back at q1
    going_on = service.turn("local", GO_ON, "q2", scenario="quick", points=[])
    assert going_on["first_part"] is None
    due = going_on["second_part_due"]
    assert {key: due[key] for key in ("request_id", "said", "first_part")} == dict(request_id="q1", said=FIRST, first_part=asked["first_part"]["text"])
    # what step 5 looks up from: the point's place and where the user is
    assert due["points"] == [dict(kind="go", said="最晚几点出发去机场", place="airport")] and due["at"]["place"] == "gym-north"
    assert going_on["next"].startswith("这一句是要上一问（request_id q1，“今晚到明天 最晚几点出发机场 怎么走”）的第二段：不再发第一段")
    assert "先把 first_part.text 原样发给用户" not in going_on["next"]
    # control: once a second part for q1 passed check_answer (it records the passed draft, as the Stop hook reads it), the
    # same message is a message of its own
    text = "# 第二段 · 核过了（21:05）\n…"
    remember_answer(service.answers_path(), "q1", text, draft_digest(text), "2031-10-31T21:05:00+01:00")
    again = service.turn("local", GO_ON, "q3", scenario="quick", points=[])
    assert "second_part_due" not in again and again["first_part"]["text"]


def test_only_a_message_that_names_nothing_asks_for_the_latest_second_part(make):
    service = make(tracks=WAYS)
    service.turn("local", FIRST, "q1", scenario="quick", points=GO)
    # control: a new question is answered as one; the earlier second part is not pushed into it
    new = service.turn("local", "还是问一下，最晚几点去机场", "q2", scenario="quick", points=GO)
    assert "second_part_due" not in new and new["first_part"]["text"]
    # “继续” asks for the latest question's second part, and asks again while that one has not passed
    assert service.turn("local", GO_ON, "q3", scenario="quick", points=[])["second_part_due"]["request_id"] == "q2"
    assert service.turn("local", GO_ON, "q4", scenario="quick", points=[])["second_part_due"]["request_id"] == "q2"
    # control: points not given at all are not “go on”; turn asks for them as before
    plain = service.turn("local", GO_ON, "q5", scenario="quick")
    assert "second_part_due" not in plain and plain["next"].startswith("这一问没给 points")


def test_the_command_line_first_part_carries_the_steps_and_the_line_that_ends_a_reply(make, capsys):
    service = make(tracks=WAYS)
    assert cli.main(["--db", str(service.db_path), "first-part", "--text", FIRST, "--points", json.dumps(GO, ensure_ascii=False)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["steps"] == core_steps()["steps"] and out["first_part"]["later"] == "要核过的第二段，回我一句“继续”。"


def test_one_question_goes_through_from_the_instructions_and_the_tool_results_alone(make):
    """The walk of an entry with the MCP server and nothing else, in an app that cannot show text between tool calls:
    every value the host passes on comes from a tool result, none from a file."""
    service = make(tracks=WAYS)
    first = service.turn("local", FIRST, "q1", scenario="quick", points=GO)            # step 1
    reply_one = first["first_part"]["text"] + "\n" + first["first_part"]["later"]     # steps 2-4: no subagent, end the reply
    assert reply_one.endswith("回我一句“继续”。")
    due = service.turn("local", GO_ON, "q2", scenario="quick", points=[])["second_part_due"]  # the next message, step 1-2
    found = query(service, "latest_departure", origin=due["at"]["place"], destination=due["points"][0]["place"])  # step 5
    draft = "\n".join(["# 第二段 · 核过了（21:01）", found["first_line"], "和第一段不同：最晚已查到。", "以上交通时刻按时刻表，不是实时。",
                       "## 完整安排", "21:45 从北边的岩馆出门"])
    assert query(service, "check_answer", text=draft, part="second", request_id=due["request_id"])["ok"]  # step 7
    assert "second_part_due" not in service.turn("local", GO_ON, "q3", scenario="quick", points=[])     # step 8 sent it


# --- 2. the command line prints the JSON only -----------------------------------------------------------------------------------

def test_the_command_line_prints_the_first_part_only_inside_its_json(make, capsys):
    service = make(tracks=WAYS)
    code = cli.main(["--db", str(service.db_path), "first-part", "--text", FIRST, "--points", json.dumps(GO, ensure_ascii=False)])
    captured = capsys.readouterr()
    assert code == 0
    # bad sample (a17b_off_r1, a17b_off_r2, a16_off_r1): the first part's lines on stderr, read as if shown to the user
    assert captured.err == ""
    lines = json.loads(captured.out)["first_part"]["lines"]
    assert not set(lines) & set(captured.out.splitlines())  # never a plain line of the output
    # stdout and stderr together, as `2>&1 | json.load` reads them, are one JSON document
    assert json.loads(captured.out + captured.err)["first_part"]["lines"] == lines


# --- 3. the help is not a lookup ------------------------------------------------------------------------------------------------

def bash(command: str) -> dict:
    return dict(tool_name="Bash", tool_input=dict(command=command))


def test_reading_the_command_line_help_is_not_a_lookup():
    wheel = "uvx --from ./tools/tripwright-0.0.1-py3-none-any.whl tripwright --repo ."
    # bad sample (a17b_off_r1, a17_off_r2): denied before the first part, and the deny sent the host to dispatch first
    assert not lookup_call(bash(f"cd ws; {wheel} query --help 2>&1 | head -60"))
    assert not lookup_call(bash(f"{wheel} query -h"))
    # controls: a lookup is still one, in any part of the command and through an alias (a17_off_r1)
    assert lookup_call(bash(f"{wheel} query latest_departure --arguments '{{}}'"))
    assert lookup_call(bash(f"{wheel} query --help; {wheel} query transit --arguments '{{}}'"))
    assert lookup_call(bash(f'W="{wheel}"; $W query latest_departure --arguments x'))
    assert not lookup_call(bash(f"{wheel} query check_answer --part second --text-file -"))
    assert not lookup_call(bash(f"{wheel} first-part --text x --points '[]'"))
