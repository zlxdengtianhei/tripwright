"""Standing rules the user states in passing, recorded by the service when the host records none (trip60).

The user often says how the assistant should work from now on in the middle of a travel question (“以后注意终端输出不要用
表格的 然后我们现在好像错过了一个车”, 10-29). Hosts recorded such a sentence in some runs and not in others (replays of
2026-11-22: 7 of 15 turns carried no `record`), and the user then had to say it again or ask why it was not kept. Here the
service finds the clause by its words, with no model (principle 8), and records it with the user's exact words, so the
next turns carry it; the reply says what was recorded, and an undo takes it back like any other record.

Only clauses with a marker of time or habit count (以后, 每次, 下次, from now on, always, never ...), or an explicit “X
应该是/要” about how answers, output or interaction work. A clause runs from its marker to the end of its sentence, or
to a word that opens another topic (另外, 对了, 现在, 然后我们 ...), at most MAX characters.
"""
from __future__ import annotations

import hashlib
import re

MAX = 60
KEY = "interaction:said-"  # the key prefix of the entries recorded here
LOOKBACK = 12
# A marker of a lasting rule: time or habit, or how answers, output and interaction should work.
# “之后” counts only at the start of a sentence part (“。之后这种可以…”); after a verb it means “after X” (“核验之后”).
MARK_ZH = re.compile(r"以后|今后|往后|(?<![^，,。.!！?？\s；;])之后(?:的|这种|每次)?|每次|下次|别再|不要再|记住|"
                     r"(?:交互|输出|回答|回复)(?:方式|模式)?(?:应该|最好|要|都)")
MARK_EN = re.compile(r"\b(?:from now on|going forward|in (?:the )?future|next time|every time|always|never|remember to)\b", re.I)
# Where a clause stops: the end of a sentence, or a word that opens another topic.
STOP_ZH = re.compile(r"[。！？!?\n；;]|另外|对了|还有一个|还有就是|然后我们|然后现在|现在(?:的话|直接|我|你)|所以现在")
STOP_EN = re.compile(r"[.!?\n;]|\b(?:also|by the way|right now|now)\b", re.I)
# Words that make a clause about the trip itself, not about how the assistant works: left to the host.
TRIP_ONLY = re.compile(r"行程|酒店|民宿|车票|机票|航班|景点|门票|hotel|flight|ticket", re.I)
FORBID = re.compile(r"不要|别|不用|不再|never|don't|do not", re.I)


def clauses(text: str) -> list[str]:
    """The standing-rule clauses of one message, in order, each at most MAX characters."""
    found = []
    english = not re.search(r"[一-鿿]", text or "")
    mark, stop = (MARK_EN, STOP_EN) if english else (MARK_ZH, STOP_ZH)
    start = 0
    while True:
        hit = mark.search(text or "", start)
        if hit is None:
            break
        # a clause starts where its sentence part starts, at most LOOKBACK characters before the marker, so a negation
        # before it stays with it (“不是说之后呢，你就只以新的要求去考虑” must not read as the rule itself)
        before = text[max(0, hit.start() - LOOKBACK):hit.start()]
        cut = max(before.rfind(mark_) for mark_ in "，,。.!！?？ \n；;")
        begin = hit.start() - len(before) + cut + 1 if cut >= 0 else hit.start() - len(before)
        end_hit = stop.search(text, hit.end())
        end = end_hit.start() if end_hit else len(text)
        clause = text[begin:end].strip(" ，,、").strip()
        start = max(end, hit.end())
        if len(clause) < 6 or TRIP_ONLY.search(clause):
            continue
        if len(clause) > MAX:
            # cut at the last pause within MAX, so the quote never ends mid-word (“…我要只复制一”)
            cut = max(clause.rfind(mark_, 0, MAX) for mark_ in "，,、 ")
            clause = clause[:cut] if cut >= 12 else clause[:MAX]
        if not any(clause in seen or seen in clause for seen in found):
            found.append(clause)
    return found


def items(text: str, event_id: str, *, recorded_quotes: list[str]) -> list[dict]:
    """Record entries (turn's `record.items` shape) for the clauses the host's own record leaves out: kind rule, the
    clause as quote and text, everyone in the trip, long-term, basis user_explicit (the words are the user's)."""
    entries = []
    for clause in clauses(text):
        if any(clause in quote or quote in clause for quote in recorded_quotes if quote):
            continue
        digest = hashlib.sha256(clause.encode("utf-8")).hexdigest()[:8]
        entries.append(dict(kind="rule", key=f"{KEY}{digest}", quote=clause, text=clause, subject=["*"],
                            strength="forbid" if FORBID.search(clause) else "must", long_term=True,
                            source=dict(event=event_id, basis="user_explicit")))
    return entries
