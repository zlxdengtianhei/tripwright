"""S4h-b (quality judge 9, premise C and mechanism 4; the off_r1 replay): the command line a host falls back to when
the Tripwright tools are down must neither slow the first part nor pass the wrong question's check.

1. `first-part --points` refused the host's own words for a kind (departure, route, time were tried, then a folded
   form, then no points at all), so the first part came minutes late. Common synonyms are now read as the kind they
   mean, with one line on stderr saying so; a kind nothing maps to is still refused, now with a whole corrected
   command to paste.
2. `query check_answer` without `--request-id` checked an unknown question ("本问原话不明，吃的检查没做") and passed a
   draft that was not checked against this question. It now takes the session's latest turn and says which one on
   stderr; with no turn at all it refuses and shows a command with `--request-id`.

Offline: the fictional plan and MOTIS of test_a16_fixes (public Vienna stop names, made-up times), fictional places."""
from __future__ import annotations

import json
import shlex

import pytest

from test_a12_first_part import FIRST
from test_a16_fixes import WAYS, fixed, local, make  # noqa: F401 (make is a fixture)
from tripwright import cli

# the replay's first try at --points: the host's own words for the kinds, an empty place for the time
BAD_POINTS = [dict(kind="departure", said="最晚几点出发机场", place="机场"), dict(kind="route", said="怎么走", place="机场"),
              dict(kind="time", said="今晚到明天", place="")]
# the kinds the packet names, read as the kind they mean
SYNONYMS = dict(deadline="go", route="go", time="go", latest_departure="go", transit="go", departure="go",
                food="eat", meal="eat", event="do", activity="do", place="at", where="at")
DRAFT = "# 第二段 · 去机场（21:10）\n今晚不用赶，夜里一直有车（存档，没核）。"


def first_part(service, points: str, capsys) -> tuple[int, str, str]:
    code = cli.main(["--db", str(service.db_path), "first-part", "--text", FIRST, "--points", points])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def check_answer(service, capsys, *flags) -> tuple[int, str, str]:
    code = cli.main(["--db", str(service.db_path), "query", "check_answer", "--part", "second", "--text", DRAFT, *flags])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# --- 1. the command line reads the kinds the host wrote -------------------------------------------------------------


def test_the_replays_points_are_read_as_the_kinds_they_mean(make, capsys):
    service = make(tracks=WAYS)
    # bad sample (off_r1): three refusals before a usable form; the first part came late
    code, out, err = first_part(service, json.dumps(BAD_POINTS, ensure_ascii=False), capsys)
    assert code == 0
    assert json.loads(out)["first_part"]["lines"][1].startswith("去维也纳机场：")
    # one line on stderr says what each kind was read as
    assert "read kind 'departure' as 'go'" in err and "'route' as 'go'" in err and "'time' as 'go'" in err
    # control: the kinds turn takes still work and say nothing
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
    # the error ends with a whole corrected command; pasting it works
    command = err.split("try: ", 1)[1].strip()
    assert command.startswith("tripwright ") and '"kind": "other"' in command and "怎么走" in command
    assert cli.main(shlex.split(command)[1:]) == 0
    capsys.readouterr()


# --- 2. check_answer without --request-id ---------------------------------------------------------------------------


def test_check_answer_takes_the_latest_turn_and_names_it(make, capsys):
    service = make(tracks=WAYS)
    service.turn("local", "最晚几点出发机场", "q-air", scenario="quick")
    # bad sample (off_r1): no request_id, so the draft passed a check that never saw this question's words
    code, out, err = check_answer(service, capsys)
    assert code == 0
    assert "using the latest turn's request_id 'q-air'" in err
    assert "本问原话不明" not in json.loads(out)["summary"]
    # the newest turn is the one taken, and an explicit --request-id says nothing
    service.turn("local", "饿了想吃点东西", "q-food", scenario="quick")
    code, _, err = check_answer(service, capsys)
    assert "'q-food'" in err and "'q-air'" not in err
    code, _, err = check_answer(service, capsys, "--request-id", "q-air")
    assert code == 0 and "latest turn" not in err


def test_check_answer_with_no_turn_at_all_refuses_with_an_example(tmp_path, capsys):
    code = cli.main(["--db", str(tmp_path / "s4hb-none.sqlite3"), "query", "check_answer", "--part", "second", "--text", DRAFT])
    captured = capsys.readouterr()
    assert code == 1 and captured.out == ""
    assert "no turn of the last minutes" in captured.err
    assert "tripwright" in captured.err and "--request-id" in captured.err and "check_answer" in captured.err

def test_check_answer_never_takes_a_turn_of_another_hour(make, capsys, monkeypatch):
    # S4h2 replay (off_r1): this question had no turn (the host used the command line's first-part), so the CLI took the
    # trip's import turn of another day and held the draft to a question nobody had asked; the food check never ran
    service = make(tracks=WAYS)  # its only turn is the seed one, at 21:01
    fixed(monkeypatch, local("23:30"))
    code, out, err = check_answer(service, capsys)
    assert code == 1 and out == ""
    assert "no turn of the last minutes" in err and "--request-id" in err and "--question" in err
