"""PII detection and redaction for AgentWeave.

Scans text for common PII patterns and either redacts, flags, or passes
through based on the configured mode (``AGENTWEAVE_PII_MODE`` env var).

Supported patterns:
  - Email addresses
  - Phone numbers (US/international)
  - US Social Security Numbers (SSN)
  - Credit card numbers (Visa, MC, Amex, Discover)
  - IPv4 addresses

Modes:
  ``off``     — disabled; no scanning (default)
  ``flag``    — detect only; returns is_detected=True, text unchanged
  ``redact``  — replace detected PII with ``[REDACTED:<type>]``
  ``block``   — raise PIIBlockedError when PII is found

No external dependencies — regex only.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import NamedTuple

__all__ = [
    "PIIMode",
    "PIIMatch",
    "PIIResult",
    "PIIBlockedError",
    "scan_text",
    "get_pii_mode",
]

# ---------------------------------------------------------------------------
# PII regex patterns
# ---------------------------------------------------------------------------

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "EMAIL",
        re.compile(
            r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
            re.IGNORECASE,
        ),
    ),
    (
        "PHONE",
        re.compile(
            # Matches: +1-800-555-1234, (800) 555-1234, 800.555.1234, 8005551234
            r"(?<!\d)(\+?1[-.\s]?)?(\(?\d{3}\)?[-.\s]?)(\d{3}[-.\s]?\d{4})(?!\d)",
        ),
    ),
    (
        "SSN",
        re.compile(
            # US SSN: 123-45-6789 or 123 45 6789
            r"(?<!\d)\d{3}[-\s]\d{2}[-\s]\d{4}(?!\d)",
        ),
    ),
    (
        "CREDIT_CARD",
        re.compile(
            # Visa (4xxx), MC (5xxx/2xxx), Amex (3[47]xx), Discover (6xxx)
            # Must have separators (spaces or dashes) or be 16 consecutive digits
            r"(?<!\d)"
            r"("
            r"4\d{3}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}"  # Visa 16-digit
            r"|5[1-5]\d{2}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}"  # Mastercard
            r"|2[2-7]\d{2}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}"  # Mastercard 2xxx
            r"|3[47]\d{2}[-\s]?\d{6}[-\s]?\d{5}"  # Amex 15-digit
            r"|6(?:011|5\d{2})[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}"  # Discover
            r")"
            r"(?!\d)",
        ),
    ),
    (
        "IPV4",
        re.compile(
            r"(?<!\d)"
            r"(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)"
            r"(?!\d)",
        ),
    ),
]


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

class PIIMode:
    OFF = "off"
    FLAG = "flag"
    REDACT = "redact"
    BLOCK = "block"

    _VALID = {OFF, FLAG, REDACT, BLOCK}

    @classmethod
    def from_env(cls) -> str:
        raw = os.getenv("AGENTWEAVE_PII_MODE", "off").strip().lower()
        if raw not in cls._VALID:
            import warnings
            warnings.warn(
                f"AGENTWEAVE_PII_MODE={raw!r} is not valid; defaulting to 'off'. "
                f"Valid values: {', '.join(sorted(cls._VALID))}",
                stacklevel=3,
            )
            return cls.OFF
        return raw


class PIIMatch(NamedTuple):
    """A single PII hit within a text string."""
    kind: str       # e.g. "EMAIL", "PHONE", "SSN", "CREDIT_CARD", "IPV4"
    start: int      # start index in the *original* text
    end: int        # end index in the *original* text
    value: str      # the matched string


@dataclass
class PIIResult:
    """Result of a PII scan."""
    original: str
    cleaned: str                        # redacted text (same as original when mode != redact)
    matches: list[PIIMatch] = field(default_factory=list)
    is_detected: bool = False           # True when at least one PII pattern matched


class PIIBlockedError(Exception):
    """Raised when PII is detected and mode is 'block'."""

    def __init__(self, matches: list[PIIMatch]):
        kinds = sorted({m.kind for m in matches})
        super().__init__(
            f"Request blocked: PII detected ({', '.join(kinds)}). "
            "Set AGENTWEAVE_PII_MODE=redact or =flag to allow."
        )
        self.matches = matches


# ---------------------------------------------------------------------------
# Core scanning logic
# ---------------------------------------------------------------------------

def _find_matches(text: str) -> list[PIIMatch]:
    """Return all PII matches in *text*, in order of their start position."""
    hits: list[PIIMatch] = []
    for kind, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            hits.append(PIIMatch(kind=kind, start=m.start(), end=m.end(), value=m.group()))
    # Sort by start position so redaction can proceed left-to-right without offset issues
    hits.sort(key=lambda h: h.start)
    return hits


def _redact(text: str, matches: list[PIIMatch]) -> str:
    """Replace each match span with ``[REDACTED:<kind>]``, working right-to-left
    so earlier indices remain valid after each substitution."""
    result = list(text)
    for m in reversed(matches):
        result[m.start : m.end] = list(f"[REDACTED:{m.kind}]")
    return "".join(result)


def scan_text(text: str, mode: str | None = None) -> PIIResult:
    """Scan *text* for PII and return a :class:`PIIResult`.

    Parameters
    ----------
    text:
        The string to scan.
    mode:
        Override the mode for this call.  When ``None``, the mode is read
        from the ``AGENTWEAVE_PII_MODE`` environment variable.

    Raises
    ------
    PIIBlockedError
        When ``mode == "block"`` and PII is detected.
    """
    if mode is None:
        mode = PIIMode.from_env()

    if mode == PIIMode.OFF or not text:
        return PIIResult(original=text, cleaned=text)

    matches = _find_matches(text)

    if not matches:
        return PIIResult(original=text, cleaned=text)

    if mode == PIIMode.BLOCK:
        raise PIIBlockedError(matches)

    if mode == PIIMode.REDACT:
        cleaned = _redact(text, matches)
    else:  # FLAG
        cleaned = text

    return PIIResult(
        original=text,
        cleaned=cleaned,
        matches=matches,
        is_detected=True,
    )


def get_pii_mode() -> str:
    """Return the current PII mode from the environment."""
    return PIIMode.from_env()
