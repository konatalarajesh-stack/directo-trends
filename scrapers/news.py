"""
Google News RSS scraper.

Free, no API key, no rate limit in practice. Localized via `hl` + `gl` URL params.
Returns top stories for each niche's `google_news_topic` query.
"""
from __future__ import annotations

import logging
import urllib.parse
from typing import Iterable

import feedparser

from .base import RawTrend, Scraper

log = logging.getLogger(__name__)

_BASE_URL = "https://news.google.com/rss/search"


def _build_url(query: str, hl: str, gl: str) -> str:
    params = {
        "q": query,
        "hl": hl,
        "gl": gl,
        "ceid": f"{gl}:{hl.split('-')[0]}",
    }
    return f"{_BASE_URL}?{urllib.parse.urlencode(params)}"


class GoogleNewsScraper(Scraper):
    name = "google_news"
    supports_regions = True
    request_delay_seconds = 0.5

    def fetch(self, niche: str, region: str) -> Iterable[RawTrend]:
        niche_cfg = self.config["niches"][niche]
        region_cfg = next(r for r in self.config["regions"] if r["code"] == region)

        query = niche_cfg.get("google_news_topic")
        if not query:
            return

        url = _build_url(
            query=query,
            hl=region_cfg["google_news_hl"],
            gl=region_cfg["google_news_gl"],
        )

        log.debug("google_news fetch: %s", url)
        feed = feedparser.parse(url)
        self.sleep()

        # Top 20 stories — Google News orders by recency + relevance.
        for idx, entry in enumerate(feed.entries[:20]):
            title = (entry.get("title") or "").strip()
            if not title:
                continue
            # Score decays linearly with rank.
            score = max(0.0, 100.0 - idx * 4.0)
            yield RawTrend(
                title=title,
                source=self.name,
                niche=niche,
                region=region,
                score=score,
                url=entry.get("link"),
                summary=entry.get("summary", "")[:280] or None,
                raw={
                    "published": entry.get("published"),
                    "source": entry.get("source", {}).get("title") if entry.get("source") else None,
                },
            )


if __name__ == "__main__":
    # CLI for local debugging:
    #   python -m scrapers.news --niche fitness --region US
    import argparse
    import json
    from pathlib import Path

    import yaml

    parser = argparse.ArgumentParser()
    parser.add_argument("--niche", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--config", default="niches/config.yaml")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")
    config = yaml.safe_load(Path(args.config).read_text())
    scraper = GoogleNewsScraper(config)
    results = scraper.safe_fetch(args.niche, args.region)
    print(json.dumps([r.to_dict() for r in results], indent=2))
