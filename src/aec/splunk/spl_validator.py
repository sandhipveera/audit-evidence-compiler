"""SPL validator — executes adversary-recommended searches and returns results."""
from __future__ import annotations

import logging
import re
from typing import Any

from aec.splunk.client import SplunkClient, SplunkSearchError
from aec.splunk.time_window import normalize_earliest

log = logging.getLogger(__name__)

FORBIDDEN_COMMANDS = {"delete", "outputlookup", "collect", "sendemail", "runshellscript"}
MAX_QUERY_LENGTH = 4096


def _validate_spl_syntax(query: str) -> str | None:
    """Basic SPL syntax validation. Returns error message or None if OK."""
    if not query.strip():
        return "Empty query"
    if len(query) > MAX_QUERY_LENGTH:
        return f"Query exceeds maximum length ({MAX_QUERY_LENGTH} chars)"

    pipes = re.split(r"\|", query)
    for segment in pipes:
        stripped = segment.strip()
        if not stripped:
            continue
        first_word = stripped.split()[0].lower() if stripped.split() else ""
        if first_word in FORBIDDEN_COMMANDS:
            return f"Forbidden command: {first_word}"

    unclosed_brackets = query.count("[") - query.count("]")
    if unclosed_brackets != 0:
        return f"Unbalanced brackets (off by {unclosed_brackets})"

    unclosed_parens = query.count("(") - query.count(")")
    if unclosed_parens != 0:
        return f"Unbalanced parentheses (off by {unclosed_parens})"

    return None


def run_spl(
    query: str,
    time_window: str = "30d",
    client: SplunkClient | None = None,
) -> dict[str, Any]:
    """Execute a SPL query and return structured results.

    Returns:
        {ok: bool, hit_count: int, sample: list, error: str|None}
    """
    syntax_error = _validate_spl_syntax(query)
    if syntax_error:
        return {"ok": False, "hit_count": 0, "sample": [], "error": syntax_error}

    if client is None:
        try:
            client = SplunkClient()
        except ValueError as e:
            return {"ok": False, "hit_count": 0, "sample": [], "error": str(e)}

    earliest = normalize_earliest(time_window)

    try:
        result = client.search(query=query, earliest=earliest, latest="now", max_results=10)
        return {
            "ok": True,
            "hit_count": result["event_count"],
            "sample": result["results"][:5],
            "error": None,
        }
    except SplunkSearchError as e:
        return {"ok": False, "hit_count": 0, "sample": [], "error": str(e)}
    except Exception as e:
        return {"ok": False, "hit_count": 0, "sample": [], "error": f"Search failed: {e}"}
