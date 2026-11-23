"""Cut Chinese, Japanese and Korean text into words (Q19, 2026-11-23: find the right entries in the user's repository).

`words` cuts a run of CJK text into the words a dictionary recognises (`圣沃尔夫冈 民宿 车票` → 圣, 沃尔夫冈, 民宿,
车票). `recall` matches these words instead of every character pair: a long message yields a few dozen words with a
meaning, not hundreds of pairs, most of which mean nothing and match by accident.

The dictionary is jieba's (MIT, pure Python, bundled, no download). jieba's default cut also guesses unknown words
with a hidden Markov model; `words` turns that off (`HMM=False`), so the service still makes no model decision: the
cut is a longest-path rule over a word table. A place name the table does not hold (圣沃尔夫冈) comes out as its
head word and the rest (圣, 沃尔夫冈); the plan's own names are matched on the raw text by `recall.places_in`, so a
place of the trip is still found whole.

The alternative was a pure rule over a word list of our own: it would have to carry enough ordinary Chinese to cut
everyday sentences (tens of thousands of words) or fall back to character pairs for exactly the words it lacks —
the fault this module removes.
"""
from __future__ import annotations

import warnings

with warnings.catch_warnings():
    # jieba 0.42.1 writes two regexes with unescaped backslashes; Python 3.12 warns on import only (the patterns work)
    warnings.simplefilter("ignore", SyntaxWarning)
    import jieba

jieba.setLogLevel(60)  # no "Building prefix dict" line on stderr


def words(run: str) -> list[str]:
    """The words of one CJK run, in order; one-character words included (the caller drops what it does not want)."""
    return [word.strip() for word in jieba.lcut(run, HMM=False) if word.strip()]
