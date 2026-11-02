"""The thirteen mechanical checks: each fires on a bad answer and stays quiet on the legitimate samples.

The checks are signals for replay scoring; these tests only show that each signal fires and that clean samples pass.
"""
import json

import pytest
from render_samples import SAMPLE, load, text

from tripwright.render.check_answer import check_answer, failed, main

GOOD = ["answer_quick", "answer_first", "answer_second", "answer_en"]
LINK = "[Google 地图](https://www.google.com/maps/dir/?api=1&destination=Kunsthalle%20Grauwald&travelmode=transit)"
PLACE = ["Kunsthalle Grauwald", f"美术馆 · Google 地图搜 Kunsthalle Grauwald · {LINK}"]


def options_for(name: str) -> dict:
    data = load(f"{name}.json")
    return dict(skeleton=data["skeleton"], language=data["language"], places=[p["name_local"] for p in data.get("places", [])],
                notices=len(data["notices"]))


@pytest.mark.parametrize("name", GOOD)
def test_legitimate_samples_have_no_failures(name):
    found = check_answer(text(f"{name}.md"), **options_for(name))
    assert not failed(found), found


@pytest.mark.parametrize("check,lines,extra", [
    ("lines", ["坐 471 路去湖边。", "买往返票。", "下车走 6 分钟。", "看告示牌。", "回车站。", "坐 18:05 的车。"], {}),
    ("sentence_length", ["坐车先到总站然后换乘去城另一边的那个很大的美术馆再一路慢慢走回住的地方休息。"], {}),
    ("vague", ["坐 471 路去湖边。", "附近有一家咖啡馆可以坐坐。"], {}),
    ("process", ["坐 471 路去湖边。", "系统里已经记下了你的偏好。"], {}),
    ("field_names", ["坐 471 路去湖边。", "按 `pace` 排的，用了 I-300713-s4v。"], {}),
    ("table", ["坐 471 路去湖边。", "| 时间 | 做什么 |", "|---|---|"], {}),
    ("repeated_place", ["坐 471 路去美术馆。", "", *PLACE, "", *PLACE], {}),
    ("place_line", ["坐 471 路去美术馆。", "", "Kunsthalle Grauwald：10:00 开门", PLACE[1]], {"places": ["Kunsthalle Grauwald"]}),
    ("place_line", ["坐 471 路去美术馆。", "", PLACE[0], f"格劳瓦尔德美术馆，一个很大的市立当代美术馆 · {LINK}"], {}),
    ("links", ["坐 471 路去湖边。", "坐 471 路 · Altmoor Bahnhof → Seeufer Nord"], {}),
    ("unchecked_line", ["坐 471 路去湖边。", "没核：营业时间。"], {}),
    ("first_line", ["我查了一下路线，现在坐 471 路去湖边。"], {}),
    ("english_length", ["Take bus 471 now and then change at the main station to the regional train that goes all the way to "
                        "the lake and walk from there to the pier."], {"language": "en"}),
    ("search_terms", ["坐 471 路去湖边。", "",
                      "Uferweg Nord", "北湖岸步道口 · [Google 地图](https://www.google.com/maps/dir/?api=1&destination=Uferweg%20Nord%2C%20Seeufer)"], {}),
])
def test_each_check_fires_on_its_bad_answer(check, lines, extra):
    found = check_answer("\n".join(lines) + "\n", skeleton="quick", **extra)
    assert check in {entry["check"] for entry in found}, found


def test_hints_do_not_fail_an_answer():
    found = check_answer("出站往湖的方向走 6 分钟。\n附近有一家咖啡馆。\n", skeleton="quick")
    assert {entry["check"] for entry in found} == {"vague"} and not failed(found)


def test_key_information_only_in_the_link_is_named():
    old = text("answer_second.md").replace("ÖBB App 搜 Altmoor Bahnhof → Seeufer Nord", "Altmoor Bahnhof → Seeufer Nord")
    found = [entry for entry in check_answer(old, skeleton="second") if entry["check"] == "search_terms"]
    assert len(found) == 1 and found[0]["line"] == 9
    hidden = text("answer_first.md").replace(" · ÖBB App 搜 Altmoor Bahnhof → Seeufer Nord", "")
    messages = [entry["message"] for entry in check_answer(hidden, skeleton="first") if entry["check"] == "search_terms"]
    assert "only in the link: Altmoor Bahnhof" in messages


def test_command_line_reads_the_answer_data_and_exits_by_failure(tmp_path, capsys):
    assert main([str(SAMPLE / "answer_quick.md"), "--data", str(SAMPLE / "answer_quick.json")]) == 0
    assert "只是信号" in capsys.readouterr().out
    bad = tmp_path / "bad.md"
    bad.write_text("我先查了一下。\n| a | b |\n", encoding="utf-8")
    assert main([str(bad), "--json"]) == 1
    assert {entry["check"] for entry in json.loads(capsys.readouterr().out)} >= {"process", "table"}
