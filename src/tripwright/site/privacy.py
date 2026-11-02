from __future__ import annotations

import html
import re
from collections.abc import Iterable
from urllib.parse import unquote


EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
ORDER = (
    re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", re.I),
    re.compile(r"\bHM[A-Z0-9]{8,}\b"),
    re.compile(r"(?<![\d.,])\d{9,}(?![\d.,])"),
    re.compile(r"(?:订单号|预订号|确认号|确认码|预订码|订座号|护照号|Booking\s*(?:number|no\.?)|PNR)\s*[:：]?\s*[A-Za-z0-9][A-Za-z0-9-]{4,}", re.I),
)


class PrivacyError(ValueError):
    pass


class Policy:
    def __init__(self, lines: Iterable[str] = ()):
        self.replacements = []
        self.forbidden = []
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            word, separator, replacement = (part.strip() for part in line.partition("=>"))
            pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(word)}(?![A-Za-z0-9])", re.I)
            self.forbidden.append(pattern)
            if separator and replacement:
                self.replacements.append((pattern, replacement))


def redact(text: str, policy: Policy) -> str:
    for pattern, replacement in policy.replacements:
        text = pattern.sub(lambda match: replacement, text)
    return text


def redact_values(value, policy: Policy):
    if type(value) is str:
        return redact(value, policy)
    if isinstance(value, dict):
        return {key: redact_values(item, policy) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_values(item, policy) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_values(item, policy) for item in value)
    return value


def scan_text(text: str, policy: Policy | None = None) -> list[tuple[int, str]]:
    policy = policy or Policy()
    findings = []
    for number, line in enumerate(unquote(html.unescape(text)).splitlines(), 1):
        if EMAIL.search(line):
            findings.append((number, "email"))
        if any(pattern.search(line) for pattern in ORDER):
            findings.append((number, "order-like identifier"))
        if any(pattern.search(line) for pattern in policy.forbidden):
            findings.append((number, "private policy word"))
    return findings


def publication_text(text: str, policy: Policy) -> str:
    findings = scan_text(text, policy)
    if findings:
        raise PrivacyError(f"Publication blocked: {len(findings)} privacy finding(s); sensitive values omitted")
    return text
