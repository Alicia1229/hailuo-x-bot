"""Compatibility patches for X frontend changes not yet released by twscrape."""
from __future__ import annotations

import asyncio
import re
from urllib.parse import urljoin

from twscrape import xclid


_JS_REF_RE = re.compile(r"(?:\.{0,2}/)?[A-Za-z0-9_./-]+\.js")
_ORIGINAL_FIND_INDICES_URL = xclid._find_indices_url


async def _find_indices_url(scripts: list[str], client) -> str:
    original_error: Exception | None = None
    try:
        return await _ORIGINAL_FIND_INDICES_URL(scripts, client)
    except Exception as exc:
        original_error = exc

    seen: set[str] = set()
    frontier = list(dict.fromkeys(scripts))
    semaphore = asyncio.Semaphore(24)

    async def fetch(url: str) -> tuple[str, str]:
        async with semaphore:
            try:
                return url, (await client.get(url)).text
            except Exception:
                return url, ""

    def priority(url: str) -> tuple[int, str]:
        hints = ("sign", "transaction", "request", "sentry", "filter")
        return (0 if any(hint in url.lower() for hint in hints) else 1, url)

    # X's Vite entry now references a child chunk which then imports sign.o-*.js.
    for _ in range(3):
        frontier = sorted(
            (url for url in frontier if url not in seen),
            key=priority,
        )
        if not frontier:
            break
        seen.update(frontier)

        next_frontier: list[str] = []
        for start in range(0, len(frontier), 24):
            batch = frontier[start : start + 24]
            for url, body in await asyncio.gather(*(fetch(url) for url in batch)):
                match = xclid.INDICES_FILE_RE.search(body)
                if match:
                    return urljoin(url, match.group(0))
                next_frontier.extend(
                    urljoin(url, ref) for ref in _JS_REF_RE.findall(body)
                )
        frontier = list(dict.fromkeys(next_frontier))

    raise original_error or RuntimeError("Couldn't get XClientTxId indices script")


def install() -> None:
    if xclid._find_indices_url is not _find_indices_url:
        xclid._find_indices_url = _find_indices_url

