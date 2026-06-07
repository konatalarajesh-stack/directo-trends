"""
TikTok Creative Center scraper via Playwright.

Source: https://ads.tiktok.com/business/creativecenter/inspiration/popular/hashtag/pc/en
        https://ads.tiktok.com/business/creativecenter/inspiration/popular/song/pc/en

Public, no login required for the trending lists. Heavily protected by
DataDome bot defense, so we use a real Chromium browser via Playwright.

Strategy:
  - Navigate to the page with country + period params.
  - Wait for the hashtag cards to render (DOM-based, robust to API changes).
  - Extract title + rank + post count from each card.
  - Filter hashtags fuzzy-matching the niche's `aliases`/`keywords`.

Failure modes (handled by safe_fetch):
  - Playwright not installed → ImportError → swallowed
  - DataDome captcha → page never renders cards → empty result
  - URL structure change → empty result
"""
from __future__ import annotations

import logging
from typing import Iterable

from rapidfuzz import fuzz

from .base import RawTrend, Scraper

log = logging.getLogger(__name__)

_BASE = "https://ads.tiktok.com/business/creativecenter/inspiration/popular/hashtag/pc/en"

# TikTok CC uses ISO-2 country codes mostly compatible with ours.
# Map any differences (none currently, but stub for future).
_COUNTRY_MAP = {
    "US": "US",
    "UK": "GB",
    "CA": "CA",
    "AU": "AU",
    "IN": "IN",
}

# Period options: 7 (week), 30 (month), 120 (4 months)
PERIOD_DAYS = 7

# Browser timeout for cold-cache page render.
NAV_TIMEOUT_MS = 30_000


class TikTokCreativeCenterScraper(Scraper):
    name = "tiktok_cc"
    supports_regions = True
    # CC pages are heavy — don't hammer.
    request_delay_seconds = 3.0

    def fetch(self, niche: str, region: str) -> Iterable[RawTrend]:
        if region == "global":
            return  # CC is per-country

        country = _COUNTRY_MAP.get(region)
        if not country:
            return

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log.warning("playwright not installed; skipping tiktok_cc")
            return

        niche_cfg = self.config["niches"][niche]
        aliases = [a.lower() for a in niche_cfg.get("aliases", [])]
        keywords = [k.lower() for k in niche_cfg.get("keywords", [])]
        match_terms = aliases + keywords

        url = f"{_BASE}?period={PERIOD_DAYS}&countryCode={country}"
        log.info("tiktok_cc navigate: %s", url)

        cards: list[dict] = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                    ],
                )
                context = browser.new_context(
                    viewport={"width": 1366, "height": 900},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                    locale="en-US",
                )
                page = context.new_page()
                page.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
                # Cards render after JS hydration. Wait for a stable selector.
                # CC currently uses [data-testid="hashtag-card"] or class="CardPc__*"; we try both.
                try:
                    page.wait_for_selector(
                        '[class*="CardPc_card"], [class*="hashtag-card"]',
                        timeout=15_000,
                    )
                except Exception:  # noqa: BLE001
                    log.warning("tiktok_cc cards did not render in time")
                    browser.close()
                    return

                # Extract via evaluated JS for resilience.
                cards = page.evaluate(
                    """
                    () => {
                        const out = [];
                        // Try multiple selector patterns since TikTok rotates class names.
                        const nodes = document.querySelectorAll(
                            '[class*="CardPc_card"], [class*="hashtag-card"], [class*="ItemCard"]'
                        );
                        nodes.forEach((node, idx) => {
                            const txt = node.innerText || '';
                            const lines = txt.split('\\n').map(s => s.trim()).filter(Boolean);
                            if (lines.length === 0) return;
                            // First line is usually the hashtag (with or without #).
                            let title = lines[0];
                            if (title.startsWith('#')) title = title.slice(1);
                            // Look for "X.XM posts" or "XXXk posts" style lines.
                            const postsLine = lines.find(l => /post/i.test(l));
                            const linkEl = node.querySelector('a[href]');
                            const href = linkEl ? linkEl.getAttribute('href') : null;
                            out.push({
                                rank: idx,
                                title,
                                meta: postsLine || null,
                                href,
                            });
                        });
                        return out.slice(0, 30);
                    }
                    """
                )
                browser.close()
        except Exception as exc:  # noqa: BLE001
            log.error("tiktok_cc playwright run failed: %s", exc)
            return
        finally:
            self.sleep()

        if not cards:
            log.warning("tiktok_cc returned 0 cards for region=%s", region)
            return

        log.info("tiktok_cc extracted %d cards (pre-filter)", len(cards))

        # Filter to niche-relevant hashtags by fuzzy match against config keywords.
        emitted = 0
        for card in cards:
            title = (card.get("title") or "").strip()
            if not title:
                continue

            best = max(
                (fuzz.partial_ratio(title.lower(), term) for term in match_terms),
                default=0,
            )
            if best < 70:
                continue  # not niche-relevant

            base = max(0.0, 100.0 - card["rank"] * 3.0)
            relevance_bonus = (best - 70) / 3  # 0–10 bonus
            score = min(100.0, base + relevance_bonus)

            href = card.get("href")
            url_full = (
                f"https://ads.tiktok.com{href}" if href and href.startswith("/") else href
            )

            yield RawTrend(
                title=f"#{title}",
                source=self.name,
                niche=niche,
                region=region,
                score=score,
                url=url_full,
                summary=card.get("meta"),
                keywords=[title],
                raw={
                    "rank": card["rank"],
                    "match_score": best,
                    "country": country,
                    "period_days": PERIOD_DAYS,
                },
            )
            emitted += 1

        log.info("tiktok_cc emitted %d niche-matched trends for %s/%s", emitted, niche, region)


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
    scraper = TikTokCreativeCenterScraper(config)
    results = scraper.safe_fetch(args.niche, args.region)
    print(json.dumps([r.to_dict() for r in results], indent=2))
