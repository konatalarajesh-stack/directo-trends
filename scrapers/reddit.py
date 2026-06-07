"""
Reddit scraper using the public .json endpoint (no OAuth) for v1.

Phase 2 will switch to PRAW with OAuth — public endpoint is rate-limited
(~60 req/min/IP), which is fine at our scale but not at scale.

Per-niche `subreddits` list drives the query. Region support is weak:
we filter subs that are clearly country-tagged (e.g. r/india, r/unitedkingdom)
into region-specific output; everything else lands in `global`.
"""
from __future__ import annotations

import logging
from typing import Iterable

import requests

from .base import RawTrend, Scraper

log = logging.getLogger(__name__)

_BASE = "https://www.reddit.com"
_USER_AGENT = "directo-trends/0.1 (+https://github.com/konatalarajesh-stack/directo-trends)"

# Map country-tagged subreddits → region. Everything else → global.
_REGIONAL_SUBS = {
    "india": "IN",
    "unitedkingdom": "UK",
    "casualuk": "UK",
    "australia": "AU",
    "ausfinance": "AU",
    "ukpersonalfinance": "UK",
    "personalfinancecanada": "CA",
    "canada": "CA",
    "ireland": "UK",  # close-enough English bucket
}


class RedditScraper(Scraper):
    name = "reddit"
    supports_regions = True
    request_delay_seconds = 1.5  # polite — public endpoint is rate-limited

    def fetch(self, niche: str, region: str) -> Iterable[RawTrend]:
        niche_cfg = self.config["niches"][niche]
        subreddits = niche_cfg.get("subreddits", [])
        if not subreddits:
            return

        for sub in subreddits:
            sub_region = _REGIONAL_SUBS.get(sub.lower(), "global")
            # Only emit into the requested region (or global into every region via normalize.py).
            if region == "global":
                if sub_region != "global":
                    continue
            else:
                if sub_region != region:
                    continue

            url = f"{_BASE}/r/{sub}/top.json?t=week&limit=15"
            try:
                resp = requests.get(
                    url,
                    headers={"User-Agent": _USER_AGENT},
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as exc:
                log.warning("reddit fetch failed for r/%s: %s", sub, exc)
                self.sleep()
                continue

            posts = data.get("data", {}).get("children", [])
            self.sleep()

            for post in posts:
                p = post.get("data", {})
                title = (p.get("title") or "").strip()
                if not title:
                    continue
                ups = p.get("ups") or 0
                num_comments = p.get("num_comments") or 0
                # Heuristic: combine upvotes (popularity) and comments (engagement).
                score = min(100.0, (ups / 100) + (num_comments / 10))
                permalink = p.get("permalink")
                yield RawTrend(
                    title=title,
                    source=self.name,
                    niche=niche,
                    region=sub_region,
                    score=score,
                    url=f"https://reddit.com{permalink}" if permalink else None,
                    summary=(p.get("selftext") or "")[:280] or None,
                    keywords=[sub],
                    raw={
                        "ups": ups,
                        "num_comments": num_comments,
                        "subreddit": sub,
                        "created_utc": p.get("created_utc"),
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
    scraper = RedditScraper(config)
    results = scraper.safe_fetch(args.niche, args.region)
    print(json.dumps([r.to_dict() for r in results], indent=2))
