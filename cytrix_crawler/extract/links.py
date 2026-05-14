"""Link extraction from a loaded Playwright page (read-only ``page.evaluate``)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin


_EXTRACT_ANCHORS_JS = (
    "() => Array.from(document.querySelectorAll('a[href]'))"
    ".map(a => a.getAttribute('href'))"
    ".filter(h => typeof h === 'string' && h.length > 0)"
)


async def extract_links(page: Any, base_url: str) -> list[str]:
    """Return a deduplicated list of absolute links discovered on ``page``.

    Relative hrefs are resolved against ``base_url``. Hrefs that fail to
    resolve are dropped. Order is preserved by first occurrence so callers
    get stable behavior when enqueueing.
    """
    try:
        hrefs = await page.evaluate(_EXTRACT_ANCHORS_JS)
    except Exception:
        return []

    seen: set[str] = set()
    absolute_links: list[str] = []
    for href in hrefs or []:
        if not isinstance(href, str):
            continue
        candidate = href.strip()
        if not candidate or candidate.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        try:
            absolute = urljoin(base_url, candidate)
        except ValueError:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        absolute_links.append(absolute)
    return absolute_links
