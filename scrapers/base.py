"""
Base class for all trend scrapers.

Every scraper produces a list of `RawTrend` dicts. `normalize.py` merges,
dedupes, scores, and emits per-region per-niche JSON files.

Design goals:
  - Each scraper is independently runnable and testable.
  - Each scraper has a hard timeout and a polite request rate.
  - Failures in one scraper never block the others (caught in normalize.py).
"""
from __future__ import annotations

import abc
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Iterable

log = logging.getLogger(__name__)


@dataclass
class RawTrend:
    """A single trend signal from one source. Multiple sources can emit
    overlapping trends — normalize.py fuzzy-dedupes them by title."""

    title: str                  # human-readable trend label
    source: str                 # "google_news" | "reddit" | "hn" | "youtube" | "tiktok_cc" | ...
    niche: str                  # "fitness" | "finance" | "cooking" | "travel"
    region: str                 # ISO-2 code or "global"
    score: float = 0.0          # source-local relevance/popularity (0-100)
    url: str | None = None      # canonical link if any
    summary: str | None = None  # short description
    keywords: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)  # untransformed payload for debugging
    fetched_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    def to_dict(self) -> dict:
        return asdict(self)


class Scraper(abc.ABC):
    """Abstract scraper. Subclasses implement `fetch(niche, region) -> Iterable[RawTrend]`."""

    name: str = "base"
    supports_regions: bool = True
    request_delay_seconds: float = 1.0  # politeness between calls

    def __init__(self, config: dict):
        self.config = config

    @abc.abstractmethod
    def fetch(self, niche: str, region: str) -> Iterable[RawTrend]:
        """Yield RawTrend for the given (niche, region)."""

    def sleep(self):
        if self.request_delay_seconds:
            time.sleep(self.request_delay_seconds)

    def safe_fetch(self, niche: str, region: str) -> list[RawTrend]:
        """Wrap fetch() with error handling so one source failing doesn't kill the run."""
        try:
            results = list(self.fetch(niche, region))
            log.info("[%s] niche=%s region=%s → %d trends", self.name, niche, region, len(results))
            return results
        except Exception as exc:  # noqa: BLE001 — by design
            log.error("[%s] FAILED niche=%s region=%s: %s", self.name, niche, region, exc)
            return []
