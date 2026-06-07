"""
Hacker News scraper via the Algolia search API.

Free, no key, generous rate limits. HN is English+tech-skewed, so it's a
strong signal for finance/tech niches and a weak signal for fitness/cooking/travel.
Per-niche config controls whether HN runs.
"""
from __future__ import annotations

import logging
from typing import Iterable

import requests

from .base import RawTrend, Scraper

log = logging.getLogger(__name__)

_BASE_URL = "https://hn.algolia.com/api/v1/search"


class HackerNewsScraper(Scraper):
    name = "hn"
    supports_regions = False  # HN is global
    request_delay_seconds = 0.3

    def fetch(self, niche: str, region: str) -> Iterable[RawTrend]:
        # HN doesn't localize; we only emit it once into "global" niche feeds.
        # normalize.py will then optionally fold global trends into each region.
        if region != "global":
            return

        niche_cfg = self.config["niches"][niche]
        query = niche_cfg.get("hn_query", "").strip()
        if not query:
            return  # niche explicitly opts out (e.g., fitness)

        # `search_by_date` would give recency; `search` ranks by HN popularity.
        # We want popular-recent, so use search with numericFilters time window (7 days).
        import time
        seven_days_ago = int(time.time()) - 7 * 86400

        params = {
            "query": query,
            "tags": "story",
            "numericFilters": f"created_at_i>{seven_days_ago},points>20",
            "hitsPerPage": 20,
        }

        resp = requests.get(_BASE_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        self.sleep()

        hits = data.get("hits", [])
        # Use HN points as the score signal, normalized.
        max_points = max((h.get("points") or 0 for h in hits), default=1) or 1

        for hit in hits:
            title = (hit.get("title") or "").strip()
            if not title:
                continue
            points = hit.get("points") or 0
            score = (points / max_points) * 100
            yield RawTrend(
                title=title,
                source=self.name,
                niche=niche,
                region="global",
                score=score,
                url=hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                summary=None,
                raw={
                    "points": points,
                    "num_comments": hit.get("num_comments"),
                    "author": hit.get("author"),
                    "created_at": hit.get("created_at"),
                },
            )


if __name__ == "__main__":
    import argparse
    import json
    from pathlib import Path

    import yaml

    parser = argparse.ArgumentParser()
    parser.add_argument("--niche", required=True)
    parser.add_argument("--region", default="global")
    parser.add_argument("--config", default="niches/config.yaml")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")
    config = yaml.safe_load(Path(args.config).read_text())
    scraper = HackerNewsScraper(config)
    results = scraper.safe_fetch(args.niche, args.region)
    print(json.dumps([r.to_dict() for r in results], indent=2))
