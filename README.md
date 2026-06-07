# directo-trends

Free, scheduled, region-aware trend aggregation pipeline for **Directo**.

## What this is

A GitHub Actions cron job that scrapes free public trend sources every 6 hours, normalizes them into per-region per-niche JSON files, commits the result to this repo, and serves it via Cloudflare Pages as a free CDN.

The Directo mobile app fetches these JSON files directly. No backend server, no paid APIs.

## Architecture

```
GitHub Actions (cron, every 6h)
    │
    ├─ runs scrapers (Python)
    │     ├─ Google Trends   (pytrends)
    │     ├─ YouTube         (yt-dlp)
    │     ├─ Google News     (RSS)
    │     ├─ Reddit          (public JSON)
    │     ├─ Hacker News     (Algolia API)
    │     ├─ TikTok CC       (Playwright)         [coming]
    │     └─ TikTokApi       (davidteather lib)   [coming]
    │
    ├─ normalizes → per-region per-niche JSON
    │     public/v1/US/fitness.json
    │     public/v1/US/finance.json
    │     ...
    │
    └─ commits to main  →  Cloudflare Pages auto-deploys
                                │
                                ▼
                  https://directo-trends.pages.dev/v1/US/fitness.json
```

## Scope

**Niches:** fitness, finance, cooking, travel
**Regions:** US, UK, CA, AU, IN, plus a `global` fallback
**Schedule:** every 6 hours via GitHub Actions cron

## Files

```
.github/workflows/scrape.yml     — cron job
scrapers/
  base.py                        — abstract Scraper base
  news.py                        — Google News RSS
  hackernews.py                  — HN Algolia API
  reddit.py                      — Reddit public JSON
  google_trends.py               — pytrends (coming)
  youtube.py                     — yt-dlp (coming)
  tiktok_cc.py                   — TikTok Creative Center (coming)
  tiktok_api.py                  — TikTokApi (coming)
niches/config.yaml               — per-niche scraper config
normalize.py                     — merges all scraper outputs
scoring.py                       — trend velocity + relevance scoring
public/                          — output, served by Cloudflare Pages
requirements.txt                 — Python deps
```

## Running locally

```bash
pip install -r requirements.txt
python -m scrapers.news --niche fitness --region US
python normalize.py --output public/
```

## Deployment

1. Push to `main` — that's it.
2. GitHub Actions runs the scraper cron on schedule.
3. Cloudflare Pages auto-deploys `public/` on every commit.

## License

MIT (pipeline code). Trend data is public web content cached for redistribution.
