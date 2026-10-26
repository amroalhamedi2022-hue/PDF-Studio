"""User-facing PDF page selection: one-based, inclusive, order-preserving.

pypdf.PageRange is deliberately not used here: its syntax is Python-slice,
zero-based and stop-exclusive. This adapter handles the familiar print-dialog
syntax without silently shifting pages or changing requested order.
"""

from __future__ import annotations

import re

_PAGE_TOKEN = re.compile(r"([0-9]+)(?:-([0-9]*))?\Z")


class PageRangeError(ValueError):
    """Invalid user-facing page selection; no output should be written."""


def parse_page_ranges(spec: str, page_count: int, *, max_selections: int = 10000) -> tuple[int, ...]:
    """Return zero-based page indices for '1-3,5,7-6', '2-', or 'all'.

    A range includes both endpoints; reversed ranges reverse output order.
    Repeated pages are preserved intentionally, allowing duplication. Invalid
    or out-of-bounds selections fail as a whole, without silent clamping.
    """
    if not isinstance(page_count, int) or isinstance(page_count, bool) or page_count < 1:
        raise PageRangeError("page_count must be a positive integer")
    if (not isinstance(max_selections, int) or isinstance(max_selections, bool)
            or max_selections < 1):
        raise PageRangeError("max_selections must be a positive integer")
    if not isinstance(spec, str) or not spec.strip():
        raise PageRangeError("page selection must be nonempty text")
    if spec.strip().lower() == "all":
        if page_count > max_selections:
            raise PageRangeError("page selection exceeds configured item limit")
        return tuple(range(page_count))
    # Bound the comma-separated token buffer before split() allocates a list.
    # 32 characters per allowed selection is generous for normal page numbers
    # and ranges while preventing a huge delimiter-heavy string from ballooning.
    if len(spec) > max_selections * 32:
        raise PageRangeError("page selection text exceeds configured limit")

    selected: list[int] = []
    for raw in spec.split(","):
        token = raw.strip()
        match = _PAGE_TOKEN.fullmatch(token)
        if not match:
            raise PageRangeError(f"invalid page selection token: {token!r}")
        start = int(match.group(1))
        end_text = match.group(2)
        end = (page_count if end_text == "" else int(end_text)) if end_text is not None else start
        if start < 1 or start > page_count or end < 1 or end > page_count:
            raise PageRangeError(f"page outside 1..{page_count}")
        step = 1 if end >= start else -1
        range_size = abs(end - start) + 1
        if len(selected) + range_size > max_selections:
            raise PageRangeError("page selection exceeds configured item limit")
        selected.extend(page - 1 for page in range(start, end + step, step))
    return tuple(selected)
