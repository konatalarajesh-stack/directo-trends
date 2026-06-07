"""
YouTube scraper via channel RSS feeds.

Uses the per-niche `youtube_channels` list in niches/config.yaml. For each
channel, pulls the last 15 videos via https://www.youtube.com/feeds/videos.xml.

Why RSS instead of yt-dlp or YouTube Data API:
  - Free, no key, no quota.
  - Stable URL pattern, very rarely breaks.
  - Per-channel = curated quality. Avoids the noisy "search trending" path.

Phase 2 will add a search-based scraper (yt-dlp) for broader region coverage.
"""
from __future__ import annotations

import logging
import time
from typing import Iterable

import feedparser

from .base import RawTrend, Scraper

log = logging.getLogger(__name__)

_FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={cid}"

# Recency cutoff — only include videos uploaded in the last 14 days.
RECENCY_DAYS = 14


class YouTubeScraper(Scraper):
    name = "youtube"
    # The channel list is global; we emit into "global" niche feed and let
    # normalize.py fold globals into each region.
    supports_regions = False
    request_delay_seconds = 1.0

    def fetch(self, niche: str, region: str) -> Iterable[RawTrend]:
        if region != "global":
            return

        niche_cfg = self.config["niches"][niche]
        channels = niche_cfg.get("youtube_channels", [])
        if not channels:
            return

        cutoff = time.time() - RECENCY_DAYS * 86400

        for cid in channels:
            url = _FEED_URL.format(cid=cid)
            try:
                feed = feedparser.parse(url)
            except Exception as exc:  # noqa: BLE001
                log.warning("yt rss failed for %s: %s", cid, exc)
                continue
            self.sleep()

            if not feed.entries:
                log.debug("yt rss empty for channel %s", cid)
                continue

            channel_title = feed.feed.get("title", "")

            for idx, entry in enumerate(feed.entries):
                # Published time → epoch.
                pub_parsed = entry.get("published_parsed")
                if pub_parsed:
                    pub_epoch = time.mktime(pub_parsed)
                    if pub_epoch < cutoff:
                        continue  # too old
                title = (entry.get("title") or "").strip()
                if not title:
                    continue
                # Score: rank within channel decays, recency boost.
                base = max(0.0, 80.0 - idx * 3.0)
                yield RawTrend(
                    title=title,
                    source=self.name,
                    niche=niche,
                    region="global",
                    score=base,
                    url=entry.get("link"),
                    summary=(entry.get("summary") or "")[:280] or None,
                    keywords=[channel_title] if channel_title else [],
                    raw={
                        "channel_id": cid,
                        "channel_title": channel_title,
                        "video_id": entry.get("yt_videoid"),
                        "published": entry.get("published"),
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
    scraper = YouTubeScraper(config)
    results = scraper.safe_fetch(args.niche, args.region)
    print(json.dumps([r.to_dict() for r in results], indent=2))
