from typing import Iterable

from .domain import ProbeResult


def filter_and_rank(results: Iterable[ProbeResult]) -> list[ProbeResult]:
    """Return only working results, sorted by speed_kbps descending."""
    return sorted(
        (r for r in results if r.is_working),
        key=lambda r: r.speed_kbps,
        reverse=True,
    )
