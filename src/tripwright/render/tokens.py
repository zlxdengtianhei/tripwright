"""Token estimate for pack budgets.

No tokenizer ships with the package, and hosts use different ones, so the estimate is a fixed rule: each CJK or
full-width character counts as one token, every other character as a third of one. The rule leans high for Chinese
and for ASCII-heavy JSON, which keeps a pack under a host's real limit. It is not calibrated against a real tokenizer;
replay reports with the host's usage figures should replace the two weights, not the callers.
"""
from __future__ import annotations

import math


def wide(character: str) -> bool:
    code = ord(character)
    return (0x2E80 <= code <= 0x9FFF or 0xAC00 <= code <= 0xD7AF or 0xF900 <= code <= 0xFAFF
            or 0xFE30 <= code <= 0xFE4F or 0xFF00 <= code <= 0xFFEF or 0x20000 <= code <= 0x2FFFF)


def estimate(text: str) -> int:
    count = sum(1 for character in text if wide(character))
    return count + math.ceil((len(text) - count) / 3)
