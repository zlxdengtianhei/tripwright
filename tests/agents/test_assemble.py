"""The generator: one set of sources, deterministic outputs, committed outputs equal a fresh build (CONTRACTS 4)."""
import json

import pytest

from tripwright.agents.__main__ import main
from tripwright.agents.assemble import (FRAGMENTS, GENERATED, NOTICE_PREFIX, ROOT, AssembleError, Sources, b_fragments, build,
                                        check, expand, fragments_changed, load_sources, parse_role, parse_step, write)
from tripwright.render.writing import fragment

MAIN = "skills/tripwright-main/SKILL.md"


def test_committed_outputs_equal_a_fresh_build(sources, outputs):
    problems = check(ROOT, outputs)
    moved = fragments_changed(ROOT, sources)
    if problems and moved:
        pytest.xfail(f"等 B 片段：B 的片段在上次装配后变了（{', '.join(moved)}），等 C 重新装配")
    assert problems == []


def test_a_hand_edit_an_orphan_and_a_moved_fragment_are_reported(checkout):
    assert check(checkout, build(load_sources(checkout))) == []
    (checkout / MAIN).write_text((checkout / MAIN).read_text() + "\nhand edit\n")
    (checkout / GENERATED / "claude/old-role.md").write_text("stale\n")
    problems = check(checkout, build(load_sources(checkout)))
    assert f"{MAIN}: differs from a fresh build; run python -m tripwright.agents build" in problems
    assert any(problem.startswith(f"{GENERATED}/claude/old-role.md: not produced by the build") for problem in problems)
    sources = load_sources(checkout)
    moved = Sources(sources.roles, sources.steps, dict(sources.fragments, **{"triggers.zh.md": "触发：新写的。\n"}))
    assert fragments_changed(checkout, moved) == ["triggers.zh.md"]


def test_build_is_deterministic_and_every_output_names_its_sources(sources, outputs):
    assert build(sources) == outputs
    for path, text in outputs.items():
        head = text.split("\n", 2)
        line = json.loads(text)["_generated"] if path.endswith(".json") else head[1] if text.startswith("---\n") else head[0]
        assert NOTICE_PREFIX in line and "源：" in line and "不要手改" in line, path


def test_notice_sits_where_each_format_allows_it(outputs):
    for path, text in outputs.items():
        if path.endswith(".toml"):
            assert text.startswith(f"# {NOTICE_PREFIX}")
        elif path.endswith(".json"):
            assert text.startswith('{\n  "_generated": "' + NOTICE_PREFIX)
        elif path.endswith("SKILL.md") or f"{GENERATED}/claude/" in path:
            # Hosts only read frontmatter on line 1, so the notice is the first line inside it, as a YAML comment.
            assert text.startswith(f"---\n# {NOTICE_PREFIX}"), path
        else:
            assert text.startswith(f"<!-- {NOTICE_PREFIX}"), path


def test_cli_writes_then_checks_clean_and_refuses_a_source_that_breaks_a_promise(checkout, capsys):
    assert main(["build", "--check", "--root", str(checkout)]) == 0
    role = checkout / "src/tripwright/agents/roles/tripwright-main.md"
    role.write_text(role.read_text().replace("The order between preferences comes only from the user's words.", ""))
    before = (checkout / MAIN).read_text()
    assert main(["build", "--root", str(checkout)]) == 1
    assert "order-only-from-user" in capsys.readouterr().err
    assert (checkout / MAIN).read_text() == before
    assert main(["build", "--check", "--root", str(checkout)]) == 1


def test_write_removes_outputs_the_sources_no_longer_produce(checkout):
    (checkout / "src/tripwright/agents/roles/tripwright-researcher.md").unlink()
    changed = write(checkout, build(load_sources(checkout)))
    assert "skills/tripwright-researcher/SKILL.md" in changed
    assert not (checkout / "skills/tripwright-researcher/SKILL.md").exists()
    assert not (checkout / GENERATED / "codex/tripwright_researcher.toml").exists()


def test_only_blocks_keep_lines_per_target_and_references_move_out_of_skills_only(sources):
    body = ("A\n<!-- only: skill -->\nS\n<!-- /only -->\n<!-- only: mcp l0 -->\nM\n<!-- /only -->\n"
            "<!-- reference: extra.md -->\nR\n<!-- /reference -->\nZ\n")
    skill = expand(body, "skill", sources, "test")
    assert skill.body == "A\nS\nZ\n" and skill.references == {"extra.md": "R\n"}
    assert expand(body, "mcp", sources, "test").body == "A\nM\nR\nZ\n"
    assert expand(body, "claude", sources, "test").body == "A\nR\nZ\n"


@pytest.mark.parametrize("body, message", [
    ("<!-- only: skill -->\nA\n", "not closed"),
    ("A\n<!-- /only -->\n", "/only without only"),
    ("<!-- only: phone -->\nA\n<!-- /only -->\n", "unknown targets"),
    ("text <!-- only: skill --> inline\n", "stand alone"),
    ("<!-- include: fragment writing fr -->\n", "unknown fragment"),
    ("<!-- include: steps everything -->\n", "unknown steps mode"),
    ("<!-- include: website -->\n", "unknown include"),
])
def test_malformed_directives_are_refused(sources, body, message):
    with pytest.raises(AssembleError, match=message):
        expand(body, "skill", sources, "test")


ROLE = """---
name: tripwright-sample
title: Sample
description: Use when testing.
metadata: {trigger: t, owns: o, not: n, layer: discovery}
targets: [skill, claude]
claude: {model: inherit}
---
Body
"""


def test_role_frontmatter_is_validated():
    assert parse_role(ROLE, "roles/tripwright-sample.md").name == "tripwright-sample"
    with pytest.raises(AssembleError, match="name must equal"):
        parse_role(ROLE, "roles/other.md")
    with pytest.raises(AssembleError, match="Claude Code ignores"):
        parse_role(ROLE.replace("model: inherit", "disallowed_tools: Write"), "roles/tripwright-sample.md")
    with pytest.raises(AssembleError, match="metadata needs"):
        parse_role(ROLE.replace("not: n, ", ""), "roles/tripwright-sample.md")
    with pytest.raises(AssembleError, match="targets"):
        parse_role(ROLE.replace("[skill, claude]", "[skill, phone]"), "roles/tripwright-sample.md")
    with pytest.raises(AssembleError, match="valid YAML"):
        parse_role(ROLE.replace("title: Sample", "title: Sample: broken: yes"), "roles/tripwright-sample.md")


STEP = "---\nstep: 2\ntitle: T\nsummary: S\n---\n\n## With tools\n\nA\n\n## Without tools\n\nB\n\n写回仓: nothing.\n"


def test_step_sources_need_both_versions_in_order():
    step = parse_step(STEP, "steps/2-sample.md")
    assert (step.tool, step.notool) == ("A", "B\n\n写回仓: nothing.")
    with pytest.raises(AssembleError, match="step: 3"):
        parse_step(STEP, "steps/3-sample.md")
    with pytest.raises(AssembleError, match="followed by"):
        parse_step(STEP.replace("## Without tools", "## Offline"), "steps/2-sample.md")
    swapped = "---\nstep: 2\ntitle: T\nsummary: S\n---\n\n## Without tools\n\nB\n\n## With tools\n\nA\n"
    with pytest.raises(AssembleError, match="followed by"):
        parse_step(swapped, "steps/2-sample.md")


def test_the_six_steps_and_the_new_trip_step_are_numbered_without_gaps(sources, checkout):
    assert [step.number for step in sources.steps] == [0, 1, 2, 3, 4, 5, 6]
    (checkout / "src/tripwright/agents/steps/3-first.md").unlink()
    with pytest.raises(AssembleError, match="without gaps"):
        load_sources(checkout)


def test_fragments_come_from_b_s_reader_and_a_missing_one_is_left_out(sources):
    for name in ("writing", "skeleton", "transit", "triggers", "rules"):
        for language in ("zh", "en"):
            assert sources.fragments[f"{name}.{language}.md"] == fragment(name, language) + "\n"

    def partial(name, language):
        if name == "triggers":
            raise FileNotFoundError(name)
        return fragment(name, language)

    assert "triggers.zh.md" not in b_fragments(partial) and "writing.zh.md" in b_fragments(partial)
    assert "> 等 B 片段：src/tripwright/render/fragments/triggers.zh.md" in expand(
        "<!-- include: fragment triggers zh -->", "skill", load_sources(ROOT, partial), "test").body


def test_explicit_fragment_includes_still_copy_b_verbatim(sources):
    for name in ("writing", "skeleton", "transit", "triggers"):
        for language in ("zh", "en"):
            body = f"<!-- include: fragment {name} {language} -->"
            file = f"{name}.{language}.md"
            expected = sources.fragments[file].strip("\n") if file in sources.fragments else f"> 等 B 片段：{FRAGMENTS}/{file}"
            assert expected in expand(body, "skill", sources, "test").body


def test_main_answer_and_writing_reference_use_the_role_source(outputs):
    # The shared render fragments still express v2f's omitted legs and empty-fallback inference.
    # v2g owns its answer text in the role so rebuilding cannot silently restore those instructions.
    for path in (MAIN, f"{GENERATED}/mcp/tripwright-main.md", f"{GENERATED}/l0/tripwright-main.md"):
        text = outputs[path]
        assert "sentences" in text and "how_to_check.text" in text
        assert "不列逐段换乘和车次" not in text
        assert "为 null 就写“误了没有后备”" not in text
    reference = outputs["skills/tripwright-main/references/writing.md"]
    assert "roles/tripwright-main.md" in reference.splitlines()[0]
    assert "Copy `sentences`" in reference
    assert "examples.md" in outputs[MAIN]


def _waiting_for_b(sources) -> list[str]:
    return sorted({file for file in ("writing.zh.md", "skeleton.zh.md", "transit.zh.md", "triggers.zh.md", "rules.zh.md",
                                      "rules.en.md", "writing.en.md", "skeleton.en.md", "transit.en.md", "triggers.en.md")
                   if file not in sources.fragments} | set(fragments_changed(ROOT, sources)))


def test_main_skill_has_no_placeholder_once_b_has_delivered(sources):
    waiting = _waiting_for_b(sources)
    text = (ROOT / MAIN).read_text(encoding="utf-8") + (ROOT / "skills/tripwright-main/references/writing.md").read_text(encoding="utf-8")
    if waiting:
        pytest.xfail(f"等 B 片段：{', '.join(waiting)}")
    assert "等 B 片段" not in text


def test_main_skill_stays_within_its_pre_latest_byte_budget(outputs):
    """The deferred first answer replaces old instructions without growing the generated skill; A10 (first_part, the
    server's frame, part 2 sent once) replaced old lines without growing it past 12,171 bytes either."""
    text = outputs[MAIN]
    assert len(text.encode()) <= 12171
    assert len((text + "y" * 12172).encode()) > 12171


def test_a9_instructions_and_examples_are_generated_for_every_host(outputs):
    for path in (MAIN, f"{GENERATED}/mcp/tripwright-main.md", f"{GENERATED}/l0/tripwright-main.md",
                 f"{GENERATED}/server/instructions.md"):
        text = outputs[path]
        assert "until" in text and "03:00" in text and "01:00" not in text  # A14: tonight ends where latest_departure's does
        assert "note" in text and "board_at" in text
        assert "存档，没核" in text
    example = outputs["skills/tripwright-main/references/examples.md"]
    assert example.count("按时刻表，不是实时") == 1
    assert "（按时刻表，不是实时）" not in example
    # A10: the example's latest question goes to latest_departure without a deadline; board_at stays for catching a train
    assert "only `origin`" in example and 'board_at="22:53"' in example and "only a real arrival deadline goes in `by`" in example


@pytest.mark.parametrize("bad", [True, False], ids=["archived-time-as-answer", "real-latest-explicitly-deferred"])
def test_build_rejects_a_stored_latest_as_the_first_answer_and_accepts_deferral(checkout, capsys, bad):
    role = checkout / "src/tripwright/agents/roles/tripwright-main.md"
    text = role.read_text()
    marker = "Good first part, before lookups:\n```text\n"
    before, sample = text.split(marker, 1)
    first_line, rest = sample.split("\n", 1)
    replacement = "按计划，最晚 21:50 出门，坐车去机场；存在计划里，还没核。" if bad else first_line
    role.write_text(before + marker + replacement + "\n" + rest)
    generated = checkout / "skills/tripwright-main/references/examples.md"
    previous = generated.read_text()
    assert main(["build", "--root", str(checkout)]) == (1 if bad else 0)
    if bad:
        assert "unchecked-latest-example-defers-answer" in capsys.readouterr().err
        assert generated.read_text() == previous, "a bad source example must not replace the generated example"
    else:
        assert generated.read_text().split(marker, 1)[1].splitlines()[0] == first_line
        second = generated.read_text().split("# 第二段 · 核过了（22:16）\n", 1)[1]
        assert second.splitlines()[0].startswith("今晚最后一班：最晚 00:53 出门")  # A10: latest_departure's first_line
