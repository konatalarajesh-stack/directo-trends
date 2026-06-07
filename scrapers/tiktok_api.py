"""
TikTok scraper via the unofficial TikTokApi (davidteather) library.

Acts as the SECONDARY TikTok signal — fills gaps when tiktok_cc.py's
Creative Center scrape goes dark (TikTok rotates its anti-bot fingerprint
every few months).

Requires a fresh ms_token cookie pulled from a logged-in TikTok web session.
Store as a GitHub Actions secret named `TIKTOK_MS_TOKEN`. The scraper
no-ops cleanly if the secret is missing — never breaks the pipeline.

How to obtain ms_token (do this once a month):
  1. Open https://www.tiktok.com in Chrome, log in.
  2. DevTools → Application → Cookies → tiktok.com → copy the `msToken` value.
  3. GitHub → repo Settings → Secrets and variables → Actions → New secret:
       Name: TIKTOK_MS_TOKEN
       Value: <paste>
"""
from __future__ import annotations

import logging
import os
from typing import Iterable

from rapidfuzz import fuzz

from .base import RawTrend, Scraper

log = logging.getLogger(__name__)

_COUNTRY_MAP = {
    "US": "US",
    "UK": "GB",
    "CA": "CA",
    "AU": "AU",
    "IN": "IN",
}

# Match threshold to filter trending tags down to a niche.
NICHE_MATCH_THRESHOLD = 70


class TikTokApiScraper(Scraper):
    name = "tiktok_api"
    supports_regions = True
    # davidteather/TikTokApi spawns Playwright internally — same politeness budget.
    request_delay_seconds = 4.0

    def fetch(self, niche: str, region: str) -> Iterable[RawTrend]:
        if region == "global":
            return
        country = _COUNTRY_MAP.get(region)
        if not country:
            return

        ms_token = os.environ.get("TIKTOK_MS_TOKEN", "").strip()
        if not ms_token:
            log.info("tiktok_api: TIKTOK_MS_TOKEN not set, skipping")
            return

        try:
            # Lazy import — keep the pipeline running even if the package fails to install.
            from TikTokApi import TikTokApi  # type: ignore
        except ImportError:
            log.warning("tiktok_api: TikTokApi not installed, skipping")
            return

        niche_cfg = self.config["niches"][niche]
        match_terms = [a.lower() for a in niche_cfg.get("aliases", [])] + \
                      [k.lower() for k in niche_cfg.get("keywords", [])]

        # TikTokApi is async; we wrap it in asyncio.run via a thin helper.
        import asyncio

        async def _run() -> list[dict]:
            results: list[dict] = []
            async with TikTokApi() as api:
                await api.create_sessions(
                    ms_tokens=[ms_token],
                    num_sessions=1,
                    sleep_after=3,
                    headless=True,
                )
                try:
                    # Pull from the FYP-like "trending" videos endpoint.
                    async for video in api.trending.videos(count=40):
                        info = getattr(video, "as_dict", None) or {}
                        # Best-effort title from description.
                        desc = info.get("desc") or ""
                        if not desc:
                            continue
                        results.append({
                            "desc": desc,
                            "play": info.get("stats", {}).get("playCount", 0),
                            "id": info.get("id"),
                            "author": (info.get("author") or {}).get("uniqueId"),
                        })
                except Exception as exc:  # noqa: BLE001
                    log.warning("tiktok_api trending.videos failed: %s", exc)
            return results

        try:
            videos = asyncio.run(_run())
        except Exception as exc:  # noqa: BLE001
            log.warning("tiktok_api run failed: %s", exc)
            return
        finally:
            self.sleep()

        if not videos:
            log.info("tiktok_api: no videos returned for region=%s", region)
            return

        max_play = max((v.get("play") or 0 for v in videos), default=1) or 1

        for v in videos:
            desc = v["desc"]
            # Filter to niche-relevant only.
            best = max(
                (fuzz.partial_ratio(desc.lower(), term) for term in match_terms),
                default=0,
            )
            if best < NICHE_MATCH_THRESHOLD:
                continue

            play = v.get("play") or 0
            popularity = (play / max_play) * 100
            score = min(100.0, popularity * 0.75 + (best - NICHE_MATCH_THRESHOLD) * 0.4)

            yield RawTrend(
                title=desc[:140],
                source=self.name,
                niche=niche,
                region=region,
                score=score,
                url=v.get("id") and f"https://www.tiktok.com/@{v.get('author')}/video/{v['id']}",
                summary=None,
                keywords=[],
                raw={
                    "play_count": play,
                    "author": v.get("author"),
                    "tiktok_id": v.get("id"),
                    "match_score": best,
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
    scraper = TikTokApiScraper(config)
    results = scraper.safe_fetch(args.niche, args.region)
    print(json.dumps([r.to_dict() for r in results], indent=2))
