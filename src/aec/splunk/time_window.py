"""Helpers for Splunk time-window arguments."""
from __future__ import annotations

import re

_RELATIVE_WINDOW_RE = re.compile(
    r"^\d+(?:s|m|h|d|w|mon|q|y)(?:@[a-z]+)?$",
    re.IGNORECASE,
)


def normalize_earliest(value: str) -> str:
    """Preserve absolute Splunk times, but turn ``30d`` into ``-30d``."""
    stripped = value.strip()
    if not stripped or stripped.startswith("-"):
        return stripped
    if _RELATIVE_WINDOW_RE.fullmatch(stripped):
        return f"-{stripped}"
    return stripped
