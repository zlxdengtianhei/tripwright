"""A17 (the A16 self replays), as A17b left it; each with a bad sample in the replay's shape and a legal control.

1. A first part said the same line twice: two points of one kind to one place (“最晚几点出发机场”, “怎么走”) now share one
   line, in turn and in the command line's first-part, and no line comes twice.
2. Without the tools, the command line refused points given as plain strings with pydantic's words alone: it still
   refuses them (A17b: read as points of kind other, the first part answered none of what was asked), now with a right
   shape to copy.

Offline: the fictional plan and MOTIS of test_a16_fixes (public Vienna stop names, made-up times), fictional places."""
from __future__ import annotations

import json
import re

from test_a12_first_part import FIRST
from test_a14_fixes import GO
from test_a16_fixes import WAYS, make  # noqa: F401 (make is a fixture)
from tripwright import cli

# the replay's two points for “今晚到明天 最晚几点出发机场 怎么走”, both to the airport
TWICE = [dict(kind="go", said="今晚到明天 最晚几点出发机场", place="airport"), dict(kind="go", said="怎么走", place="airport")]
# the host's first try at --points: its own words, no kinds
WORDS = ["今晚到明天", "最晚几点出发机场", "怎么走"]


def first_part(service, points: str, capsys) -> tuple[int, dict | None, str]:
    code = cli.main(["--db", str(service.db_path), "first-part", "--text", FIRST, "--points", points])
    captured = capsys.readouterr()
    return code, json.loads(captured.out) if code == 0 else None, captured.err


# --- 1. one line per kind and place ----------------------------------------------------------------------------------------------

def test_points_of_one_kind_to_one_place_share_one_line(make, capsys):
    service = make(tracks=WAYS)
    turn = service.turn("local", FIRST, "q1", scenario="quick", points=TWICE)["first_part"]
    # bad sample (off_r1): the airport's go line once per point, the first and the fourth line the same
    assert [line for line in turn["lines"] if line.startswith("去维也纳机场：")] == [turn["lines"][1]]
    assert len(turn["lines"]) == len(set(turn["lines"])) == 5
    assert turn["lines"][1] == ("去维也纳机场：计划里是 21:45 从北边的岩馆出门，22:13 到 Wien Hauptbahnhof，坐今晚 22:53 REX 7（存档，没核）；"
                                "还有没有更晚的车，第二段核完给。")
    assert turn["lines"][3].startswith("怎么走（存档，没核）：")
    # both points are still answered, each with that line
    assert [point["line"] for point in turn["points"]] == [turn["lines"][1]] * 2
    # the command line's first part is the same
    code, out, _ = first_part(service, json.dumps(TWICE, ensure_ascii=False), capsys)
    assert code == 0 and out["first_part"]["text"] == turn["text"]
    # controls: two places, two kinds at one place, and two things not of the trip keep a line each
    apart = service.turn("local", FIRST, "q2", scenario="quick",
                         points=[TWICE[0], dict(kind="go", said="先去总站", place="hbf")])["first_part"]["lines"]
    assert [line for line in apart if line.startswith("去")] == [apart[1]]
    assert not any(line.startswith("去总站：") for line in apart)  # no stored answer: no placeholder line
    kinds = service.turn("local", FIRST, "q3", scenario="quick",
                         points=[TWICE[0], dict(kind="do", said="机场里逛逛", place="airport")])["first_part"]["lines"]
    assert kinds[1].startswith("去维也纳机场：") and any(line.startswith("机场里逛逛") for line in kinds)
    other = service.turn("local", FIRST, "q4", scenario="quick",
                         points=[dict(kind="other", said="天气"), dict(kind="other", said="厕所")])["first_part"]
    assert other is None


# --- 2. the command line's points ------------------------------------------------------------------------------------------------

def test_the_command_line_refuses_plain_strings_with_a_right_shape_to_copy(make, capsys):
    service = make(tracks=WAYS)
    # bad sample (off_r1): a list of the host's own words; refused as in A16, now with a right shape to copy
    code, _, error = first_part(service, json.dumps(WORDS, ensure_ascii=False), capsys)
    assert code == 1
    assert error.startswith("tripwright: Invalid points: points.0: Input should be a valid dictionary")
    assert "for example --points '[{\"kind\": \"go\", \"said\": \"最晚几点出发去机场\"" in error
    # never read as points of kind other (A17b: “今晚到明天：第二段给。” answered none of what was asked)
    assert "第二段给" not in error
    # the other wrong shapes carry the same example
    for wrong in ('{"kind": "go"}', '[{"kind": "walk", "said": "怎么走"}]', "[1, 2]", "最晚几点出发机场"):
        code, _, other = first_part(service, wrong, capsys)
        assert code == 1 and "for example --points '[{\"kind\": \"go\"" in other, wrong
    assert "Invalid points: points: Input should be a valid list" in first_part(service, '{"kind": "go"}', capsys)[2]
    assert cli.read_points(None) == (None, "")
    # control: the example, with a place of the trip, is taken and answers with the plan's own train
    example = re.search(r"--points '(.+)'", error).group(1).replace("<place ID or stored name>", "airport")
    code, out, _ = first_part(service, example, capsys)
    assert code == 0 and out["first_part"]["lines"][1].startswith("去维也纳机场：计划里是 21:45")
