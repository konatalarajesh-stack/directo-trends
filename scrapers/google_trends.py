"""
Google Trends scraper via pytrends.

pytrends wraps Google's internal Trends endpoints. It's semi-maintained
(Google breaks it ~2x/year). We use defensive calls + tight timeouts.

Strategy:
  - For each niche, take a SMALL keyword sample (pytrends caps at 5 per payload).
  - Per region, call `related_queries()` to surface RISING queries —
    these are the strongest "actually trending now" signal Google exposes.
  - Score by Google's own ranking (top-of-list = highest score).

Known issues:
  - 429s under aggressive use. We sleep generously.
  - Empty responses on cold IPs. safe_fetch returns [] cleanly in that case.
"""
from __future__ import annotations

import logging
from typing import Iterable

from .base import RawTrend, Scraper

log = logging.getLogger(__name__)

# Cap keywords per niche payload — pytrends rejects more than 5.
KEYWORDS_PER_PAYLOAD = 3
# Timeframe for the trend payload. 'now 7-d' = past 7 days (rolling).
TIMEFRAME = "now 7-d"


class GoogleTrendsScraper(Scraper):
    name = "google_trends"
    supports_regions = True
    request_delay_seconds = 4.0  # Google Trends is sensitive — be generous

    def __init__(self, config: dict):
        super().__init__(config)
        self._pytrends = None

    def _client(self, hl: str):
        # Lazy-init so import failures don't kill the whole pipeline.
        from pytrends.request import TrendReq
        if self._pytrends is None:
            self._pytrends = TrendReq(
                hl=hl,
                tz=0,
                timeout=(5, 15),
                retries=2,
                backoff_factor=1.5,
            )
        return self._pytrends

    def fetch(self, niche: str, region: str) -> Iterable[RawTrend]:
        if region == "global":
            return  # pytrends is region-scoped; global feed comes from other sources

        niche_cfg = self.config["niches"][niche]
        region_cfg = next(r for r in self.config["regions"] if r["code"] == region)

        keywords = niche_cfg.get("keywords", [])[:KEYWORDS_PER_PAYLOAD]
        if not keywords:
            return

        geo = region_cfg["pytrends_geo"]
        hl = region_cfg["google_news_hl"]  # same locale signal works for pytrends

        try:
            client = self._client(hl)
            client.build_payload(
                kw_list=keywords,
                cat=0,
                timeframe=TIMEFRAME,
                geo=geo,
                gprop="",
            )
            related = client.related_queries()
        except Exception as exc:  # noqa: BLE001
            log.warning("pytrends payload failed niche=%s region=%s: %s", niche, region, exc)
            return
        finally:
            self.sleep()

        # related is dict: {keyword: {"top": DataFrame|None, "rising": DataFrame|None}}
        for keyword, dfs in (related or {}).items():
            if not dfs:
                continue
            rising_df = dfs.get("rising")
            if rising_df is None or rising_df.empty:
                continue

            # Top 10 rising queries per keyword.
            for idx, row in rising_df.head(10).iterrows():
                query = str(row.get("query") or "").strip()
                if not query:
                    continue
                value = row.get("value")  # Google's rising % — can be very high
                # Normalize: anything > 200% = strong rising signal.
                try:
                    growth = float(value)
                except (TypeError, ValueError):
                    growth = 0.0
                # Score: rank-decayed base + growth bonus.
                base = max(0.0, 100.0 - idx * 5.0)
                bonus = min(20.0, growth / 50.0)
                score = min(100.0, base + bonus)

                yield RawTrend(
                    title=query,
                    source=self.name,
                    niche=niche,
                    region=region,
                    score=score,
                    url=f"https://trends.google.com/trends/explore?q={query.replace(' ', '+')}&geo={geo}",
                    summary=f"Rising query (+{int(growth)}%) related to '{keyword}'",
                    keywords=[keyword],
                    raw={
                        "seed_keyword": keyword,
                        "growth_value": value,
                        "rank": idx,
                    },
                )


if __name__ == "__main__":
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
    scraper = GoogleTrendsScraper(config)
    results = scraper.safe_fetch(args.niche, args.region)
    print(json.dumps([r.to_dict() for r in results], indent=2))
