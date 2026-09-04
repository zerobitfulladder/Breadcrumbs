"""Turning a source's title into plain text.

Publishers ship titles with their own typesetting still attached. IEEE sends
LaTeX (`$\\rm K$-SVD`), Crossref sends XML fragments (`<i>k</i>-means`), and
both send HTML entities. A title is a label — it is drawn in a card, a list row
and a browser tab, none of which typeset anything — so the markup is stripped
rather than rendered.

Abstracts are left alone: there the mathematics is the content, and it is
rendered properly instead.
"""
from __future__ import annotations

import html
import re

# \mathrm{x}, \textit{x}, \mathbf{x} … — a font command wrapping its argument.
_WRAPPING = re.compile(
    r"\\(?:math(?:rm|bf|it|cal|bb|sf|tt)|text(?:rm|bf|it|sf|tt|normal)?|"
    r"emph|mbox|hbox|operatorname)\s*\{([^{}]*)\}"
)
# Bare font switches: \rm, \bf, \it — and the same words with the backslash
# already lost somewhere upstream, which is how "$\rm K$" becomes "$rm K$".
_SWITCH = re.compile(r"\\?\b(?:rm|bf|it|sf|tt|scriptstyle|displaystyle)\b\s*")
_TAG = re.compile(r"<[^>]+>")
_MATH = re.compile(r"\$([^$]*)\$")


def _demath(match: re.Match[str]) -> str:
    """Reduce one $…$ span to its readable text, or keep it if it is real maths."""
    inner = match.group(1)
    stripped = _SWITCH.sub("", _WRAPPING.sub(r"\1", inner)).strip()
    # Anything still carrying operators, sub/superscripts or commands is real
    # notation. Leave the delimiters on so it can be rendered rather than shown
    # as mangled text.
    if re.search(r"[\\^_=+<>/]|\\\\", stripped):
        return match.group(0)
    return stripped


def clean_title(text: str | None) -> str | None:
    """Plain text for a title, or None when there is nothing left."""
    if not text:
        return text
    out = html.unescape(text)
    out = _TAG.sub("", out)
    out = _MATH.sub(_demath, out)
    out = _WRAPPING.sub(r"\1", out)
    # Collapse the whitespace XML fragments leave behind.
    out = re.sub(r"\s+", " ", out).strip()
    return out or None
