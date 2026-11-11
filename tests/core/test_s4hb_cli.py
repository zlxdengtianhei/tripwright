"""S4h-b: `first-part --points` reads common synonyms as the kind they mean, says so on stderr,
and refuses an unknown kind with a whole corrected command to paste.

Offline: the fictional plan and MOTIS of test_a16_fixes (public Vienna stop names, made-up times), fictional places."""
from __future__ import annotations

import json
import shlex

import pytest

from test_a12_first_part import FIRST
from test_a16_fixes import WAYS, make  # noqa: F401 (make is a fixture)
from tripwright import cli

BAD_POINTS = [dict(kind="departure", said="最晚几点出发机场", place="机场"), dict(kind="route", said="怎么走", place="机场"),
              dict(kind="time", said="今晚到明天", place="")]
SYNONYMS = dict(deadline="go", route="go", time="go", latest_departure="go", transit="go", departure="go",
                food="eat", meal="eat", event="do", activity="do", place="at", where="at")


def first_part(service, points: str, capsys) -> tuple[int, str, str]:
    code = cli.main(["--db", str(service.db_path), "first-part", "--text", FIRST, "--points", points])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_the_replays_points_are_read_as_the_kinds_they_mean(make, capsys):
    service = make(tracks=WAYS)
    code, out, err = first_part(service, json.dumps(BAD_POINTS, ensure_ascii=False), capsys)
    assert code == 0
    assert json.loads(out)["first_part"]["lines"][1].startswith("去维也纳机场：")
    assert "read kind 'departure' as 'go'" in err and "'route' as 'go'" in err and "'time' as 'go'" in err
    code, out, err = first_part(service, json.dumps([dict(kind="go", said="最晚几点出发机场", place="机场")], ensure_ascii=False), capsys)
    assert code == 0 and err == ""


@pytest.mark.parametrize("kind", sorted(SYNONYMS))
def test_each_synonym_of_the_packet_is_taken(make, capsys, kind):
    service = make(tracks=WAYS)
    code, out, err = first_part(service, json.dumps([dict(kind=kind, said="这一个问题")], ensure_ascii=False), capsys)
    assert code == 0, err
    assert f"read kind '{kind}' as '{SYNONYMS[kind]}'" in err


def test_a_kind_nothing_maps_to_is_refused_with_a_command_to_paste(make, capsys):
    service = make(tracks=WAYS)
    points = json.dumps([dict(kind="walk", said="怎么走", place="机场")], ensure_ascii=False)
    code, out, err = first_part(service, points, capsys)
    assert code == 1 and out == ""
    assert "'walk' is not a kind" in err and "for example --points" in err
    command = err.split("try: ", 1)[1].strip()
    assert command.startswith("tripwright ") and '"kind": "other"' in command and "怎么走" in command
    assert cli.main(shlex.split(command)[1:]) == 0
    capsys.readouterr()
