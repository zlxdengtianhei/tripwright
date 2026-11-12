"""An English stored-claim sentence is not glued to the node text before it (2026-11-12: “…再坐 541 回 Bad IschlThe plan
stored "不是末班"” in an English first part; the stored text was Chinese and ended in a Latin name)."""
from tripwright.packs import archived_claims

NODE = "16:52 Zwieselalmbahn 站坐 542 → Gosaumühle 17:17，再坐 541 回 Bad Ischl（不是末班）"


def rewritten(language):
    return archived_claims(None, "alice", None, "2026-10-29", dict(text=NODE), {}, "2026-10-29T10:24:00+01:00", language=language)["text"]


def test_the_english_claim_is_a_separate_word():
    assert "Bad Ischl The plan stored \"不是末班\" (archived, not checked)" in rewritten("en")


def test_the_chinese_claim_is_as_before():
    assert rewritten("zh") == "16:52 Zwieselalmbahn 站坐 542 → Gosaumühle 17:17，再坐 541 回 Bad Ischl计划里写过“不是末班”（存档，没核）"
