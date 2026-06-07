"""
Normalize + merge all scraper outputs into per-region per-niche JSON files.

Pipeline:
  1. Run every scraper for every (niche, region) cell.
  2. Fuzzy-dedupe trends across sources (same story shows up in news + reddit + HN).
  3. Score each merged trend by:
        - source diversity (more sources = stronger signal)
        - source-local score weighted by source reliability
        - velocity (rising > flat > falling) — Phase 2
  4. Emit JSON files into `public/v1/{region}/{niche}.json`.
  5. Generate `public/v1/_meta.json` with last_updated + schema_version.

Run:
  python normalize.py --output public/
"""
from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml
from rapidfuzz import fuzz, process

from scrapers.base import RawTrend
from scrapers.google_trends import GoogleTrendsScraper
from scrapers.hackernews import HackerNewsScraper
from scrapers.news import GoogleNewsScraper
from scrapers.reddit import RedditScraper
from scrapers.tiktok_cc import TikTokCreativeCenterScraper
from scrapers.youtube import YouTubeScraper

log = logging.getLogger(__name__)

# Source weights: how much we trust each source as a trend signal.
# Tweak as we learn which sources actually predict what creators post.
SOURCE_WEIGHTS = {
    "tiktok_cc": 1.5,
    "tiktok_api": 1.3,
    "google_trends": 1.2,
    "youtube": 1.1,
    "reddit": 1.0,
    "google_news": 0.9,
    "hn": 0.7,
}

# Fuzzy-dedup threshold (0-100). 82 catches "12-3-30 workout" ≈ "the 12-3-30 treadmill workout".
DEDUP_THRESHOLD = 82


def run_scrapers(config: dict) -> list[RawTrend]:
    """Run all scrapers across all (niche, region) cells. Returns flat list."""
    scrapers = [
        GoogleNewsScraper(config),
        HackerNewsScraper(config),
        RedditScraper(config),
        GoogleTrendsScraper(config),
        YouTubeScraper(config),
        TikTokCreativeCenterScraper(config),
        # TODO next iteration: TikTokApi (needs ms_token secret)
    ]

    niches = list(config["niches"].keys())
    regions = [r["code"] for r in config["regions"]] + ["global"]

    all_trends: list[RawTrend] = []
    for scraper in scrapers:
        for niche in niches:
            for region in regions:
                # Skip if scraper doesn't support regions and this isn't global.
                if not scraper.supports_regions and region != "global":
                    continue
                all_trends.extend(scraper.safe_fetch(niche, region))

    log.info("Total raw trends collected: %d", len(all_trends))
    return all_trends


def dedupe_and_merge(trends: list[RawTrend]) -> list[dict]:
    """Group RawTrend by (niche, region) and fuzzy-dedupe by title within each group."""
    # Bucket by (niche, region)
    buckets: dict[tuple[str, str], list[RawTrend]] = defaultdict(list)
    for t in trends:
        buckets[(t.niche, t.region)].append(t)

    merged: list[dict] = []
    for (niche, region), bucket in buckets.items():
        seen_titles: list[str] = []
        groups: dict[str, list[RawTrend]] = {}

        for trend in bucket:
            # Find best fuzzy match among existing titles.
            if seen_titles:
                match = process.extractOne(
                    trend.title.lower(),
                    seen_titles,
                    scorer=fuzz.token_set_ratio,
                    score_cutoff=DEDUP_THRESHOLD,
                )
            else:
                match = None

            if match:
                canonical = match[0]
                groups[canonical].append(trend)
            else:
                canonical = trend.title.lower()
                seen_titles.append(canonical)
                groups[canonical] = [trend]

        # Merge each group into a single output trend.
        for canonical, group in groups.items():
            # Pick the highest-scored single trend as the representative.
            best = max(group, key=lambda t: t.score * SOURCE_WEIGHTS.get(t.source, 0.5))
            sources = sorted(set(t.source for t in group))
            # Combined score: weighted sum across sources, capped at 100.
            combined_score = min(
                100.0,
                sum(t.score * SOURCE_WEIGHTS.get(t.source, 0.5) for t in group) / len(group)
                + (len(sources) - 1) * 8,  # diversity bonus
            )
            merged.append({
                "id": _trend_id(niche, region, best.title),
                "title": best.title,
                "summary": best.summary,
                "url": best.url,
                "score": round(combined_score, 1),
                "sources": sources,
                "source_count": len(sources),
                "niche": niche,
                "region": region,
                "first_seen": best.fetched_at,
                "raw_samples": [t.raw for t in group][:3],  # keep a few for debugging
            })

    log.info("Merged into %d unique trends", len(merged))
    return merged


def _trend_id(niche: str, region: str, title: str) -> str:
    import hashlib
    h = hashlib.sha1(f"{niche}|{region}|{title.lower()}".encode()).hexdigest()
    return h[:12]


def emit_files(merged: list[dict], output_dir: Path, config: dict) -> None:
    """Write per-region per-niche JSON files + meta."""
    output_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    schema_version = config.get("schema_version", 1)
    ttl_hours = config.get("cache_ttl_hours", 6)

    # Group by (region, niche)
    by_cell: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for t in merged:
        by_cell[(t["region"], t["niche"])].append(t)

    # Fold "global" trends into every region as low-priority fallbacks.
    global_by_niche: dict[str, list[dict]] = defaultdict(list)
    for (region, niche), trends in by_cell.items():
        if region == "global":
            global_by_niche[niche].extend(trends)

    written = 0
    for region_cfg in config["regions"]:
        region = region_cfg["code"]
        region_dir = output_dir / "v1" / region
        region_dir.mkdir(parents=True, exist_ok=True)

        for niche in config["niches"].keys():
            local = by_cell.get((region, niche), [])
            globals_ = global_by_niche.get(niche, [])
            # Merge local first (priority), then globals tagged with lower base score.
            for g in globals_:
                g_copy = dict(g)
                g_copy["score"] = g["score"] * 0.7  # downweight global trends in region feed
                g_copy["is_global"] = True
                local.append(g_copy)

            # Sort by score, cap at top 25.
            local.sort(key=lambda t: t["score"], reverse=True)
            top = local[:25]

            payload = {
                "schema_version": schema_version,
                "niche": niche,
                "region": region,
                "generated_at": now,
                "cache_ttl_hours": ttl_hours,
                "count": len(top),
                "trends": top,
            }
            outfile = region_dir / f"{niche}.json"
            outfile.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
            written += 1

    # Also emit a global feed per niche, no region downweight.
    global_dir = output_dir / "v1" / "global"
    global_dir.mkdir(parents=True, exist_ok=True)
    for niche, trends in global_by_niche.items():
        trends.sort(key=lambda t: t["score"], reverse=True)
        payload = {
            "schema_version": schema_version,
            "niche": niche,
            "region": "global",
            "generated_at": now,
            "cache_ttl_hours": ttl_hours,
            "count": len(trends[:25]),
            "trends": trends[:25],
        }
        (global_dir / f"{niche}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        written += 1

    # Meta
    meta = {
        "schema_version": schema_version,
        "generated_at": now,
        "cache_ttl_hours": ttl_hours,
        "regions": [r["code"] for r in config["regions"]] + ["global"],
        "niches": list(config["niches"].keys()),
        "files_written": written,
    }
    (output_dir / "v1" / "_meta.json").write_text(json.dumps(meta, indent=2))
    log.info("Wrote %d JSON files to %s", written, output_dir)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="niches/config.yaml")
    parser.add_argument("--output", default="public/")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    config = yaml.safe_load(Path(args.config).read_text())
    raw = run_scrapers(config)
    merged = dedupe_and_merge(raw)
    emit_files(merged, Path(args.output), config)


if __name__ == "__main__":
    main()
