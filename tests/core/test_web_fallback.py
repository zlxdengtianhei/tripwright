"""2026-11-25: what the host does with a point the repository does not hold.

Before: `recall.use` said "say the repository does not hold it, then look it up" and nothing more, so a host that found nothing
in the repository mostly answered from memory or never searched. After: `recall.use` ends with four cases (in the repository and
fresh; expired; public fact not stored: web search, at most 3 pages, one `web_evidence` record each; only the user knows it:
ask, no search), and the researcher's order says the same."""
import pytest

from tripwright import service as module
from tripwright.agents.assemble import ROOT

from test_catalog import seeded

W1_KEY = dict(
    zh=("仓里没有怎么办", "③仓里没有，而这是公开能查到的事", "用你自己的网页搜索查", "最多 3 页", "一页一次 query(kind='web_evidence') 记进仓，asks 写用户这句原话",
        "④仓里没有，而这事只有用户自己知道", "问用户，不上网"),
    en=("When the repository lacks it", "(3) Not in the repository, and a public fact", "your own web search", "at most 3 pages",
        "once with query(kind='web_evidence'), with the user's own words as asks", "(4) Not in the repository, and only the user knows it",
        "ask the user; do not search the web"),
)
EXISTING_END = dict(zh="一页一次 query(kind='web_evidence') 记进仓。仓里没有怎么办", en="once per page. When the repository lacks it")


@pytest.mark.parametrize("language", ["zh", "en"])
def test_the_four_cases_are_in_recall_use_after_the_old_text(language):
    use = module.RECALL_USE[language]
    for sentence in W1_KEY[language]:
        assert sentence in use, sentence
    assert EXISTING_END[language] in use  # appended, nothing moved or reworded in front of it
    assert use.index(W1_KEY[language][0]) > use.index("recall.catalog")


@pytest.mark.parametrize("language,text", [("zh", "维也纳中央火车站几点关？"), ("en", "What time does Wien Hauptbahnhof close?")])
def test_the_host_gets_the_four_cases_in_the_turn_receipt(service, plan, monkeypatch, language, text):
    seeded(service, plan, monkeypatch)
    use = service.turn("alice", text, f"w1-{language}", scenario="quick")["recall"]["use"]
    for sentence in W1_KEY[language]:
        assert sentence in use, sentence


def test_the_researcher_does_not_search_the_web_for_what_only_the_user_knows_and_records_the_users_words():
    role = (ROOT / "src/tripwright/agents/roles/tripwright-researcher.md").read_text(encoding="utf-8")
    assert "only the user knows (what was booked, their preferences, what they said before) is not on the web" in role
    assert "search nothing" in role and "the user must be asked" in role
    assert '"type", "asks"})' in role and "the user's own words as the main agent sent them" in role
    for path in [ROOT / "skills/tripwright-researcher/SKILL.md", ROOT / "src/tripwright/agents/generated/claude/tripwright-researcher.md",
                 ROOT / "src/tripwright/agents/generated/codex/tripwright_researcher.toml"]:
        assert "is not on the web" in path.read_text(encoding="utf-8"), path
