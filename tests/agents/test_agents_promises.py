"""Each promise in the role texts is present, and removing it (or adding the forbidden wording) is caught."""
import re

import pytest

from tripwright.agents.promises import PROMISES, promise_problems


def test_generated_texts_keep_every_promise(outputs):
    assert promise_problems(outputs) == []


@pytest.mark.parametrize("promise", [promise for promise in PROMISES if promise.must], ids=lambda promise: promise.id)
def test_dropping_a_required_statement_is_caught(outputs, promise):
    path = next(path for path in outputs if any(re.fullmatch(pattern.replace("*", ".*"), path) for pattern in promise.paths))
    broken = dict(outputs, **{path: re.sub(promise.must[0], "", outputs[path])})
    assert any(problem.startswith(f"{promise.id}: {path} lacks") for problem in promise_problems(broken, (promise,)))


@pytest.mark.parametrize("promise", [promise for promise in PROMISES if promise.must_not], ids=lambda promise: promise.id)
def test_adding_forbidden_wording_is_caught(outputs, promise):
    path = next(path for path in outputs if any(re.fullmatch(pattern.replace("*", ".*"), path) for pattern in promise.paths))
    broken = dict(outputs, **{path: outputs[path] + "\n" + promise.bad_sample + "\n"})
    assert any(problem.startswith(f"{promise.id}: {path} says") for problem in promise_problems(broken, (promise,)))


def test_naming_a_forbidden_word_in_quotes_to_forbid_it_is_allowed(outputs):
    path = "skills/tripwright-main/SKILL.md"
    allowed = dict(outputs, **{path: outputs[path] + "\nNever write “提交成功”.\n"})
    assert promise_problems(allowed) == []


def test_a_promise_whose_output_disappeared_is_reported(outputs):
    missing = {path: text for path, text in outputs.items() if "tripwright-verifier" not in path}
    assert any("no output matches" in problem for problem in promise_problems(missing))


def test_each_promise_from_the_packet_has_a_check():
    # Packet item 4 lists what the main skill must say; item 1 what the verifier and researcher must do.
    assert {promise.id for promise in PROMISES} >= {
        "turn-first", "order-only-from-user", "competing-preferences-get-options", "baggage-holder",
        "declare-relations-in-propose", "declarations-hold-only-on-overlap", "transport-text-search-term-and-triggers",
        "write-state-words", "never-call-a-write-success", "six-single-agent-steps", "multi-agent-first",
        "no-tool-steps-end-with-write-back", "verifier-independent-input", "verifier-stale-gives-no-conclusion",
        "researcher-saved-first-with-provenance", "subagents-never-commit", "new-trip-carry-over",
        "possible-contradictions-named", "locks-follow-identity", "turn-records-this-message", "new-trip-calls",
        "new-trip-without-tools", "verifier-gets-the-wide-pack"}


def test_a_negated_block_is_not_mistaken_for_blocking(outputs):
    path = "skills/tripwright-main/references/records.md"
    fine = dict(outputs, **{path: outputs[path] + "\nUndeclared overlaps get a notice that never blocks.\n"})
    bad = dict(outputs, **{path: outputs[path] + "\nUndeclared overlaps block the proposal.\n"})
    assert promise_problems(fine) == []
    assert any("declarations-hold-only-on-overlap" in problem for problem in promise_problems(bad))
