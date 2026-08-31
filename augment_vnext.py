#!/usr/bin/env python3
"""Revere Monitor vNext normalization and resilience pass.

Runs after legacy fetch.py. It preserves last-known-good arrays when a source family
comes back empty, adds curated Reddit feeds, and emits lightweight source-health
metadata consumed by the vNext UI.
"""
import json
import os
import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests

DATA_FILE = "data.json"
PREVIOUS_FILE = "data.previous.json"
UA = {"User-Agent": "RevereMonitor/7.0 (+https://github.com/gravellese/revere-monitor)"}

PRESERVE_KEYS = [
    "news_revere", "news_communities", "news_boston", "news_universalhub",
    "news_ma_transit", "news_national", "news_substack", "news_sports",
    "news_espn", "news_bc", "news_bc_hockey", "news_college_hockey",
    "news_hockey_east", "news_logan", "news_ktn", "revere_tv",
    "mbta_alerts", "weather_hourly", "weather_daily", "tides",
    "road_events", "sports_schedule",
]

REDDIT = {
    "massachusetts": ["boston", "massachusetts"],
    "national": ["neoliberal"],
    "sports": ["collegehockey", "NASCAR"],
}


def load(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def reddit_feed(subreddit, limit=12):
    url = f"https://www.reddit.com/r/{quote(subreddit)}/hot.json?limit={limit}&raw_json=1"
    r = requests.get(url, headers=UA, timeout=12)
    r.raise_for_status()
    out = []
    for child in r.json().get("data", {}).get("children", []):
        d = child.get("data", {})
        if d.get("stickied"):
            continue
        out.append({
            "title": d.get("title", ""),
            "link": "https://www.reddit.com" + d.get("permalink", ""),
            "source": f"r/{subreddit}",
            "subreddit": subreddit,
            "ts": int(d.get("created_utc") or 0),
            "score": int(d.get("score") or 0),
            "comments": int(d.get("num_comments") or 0),
            "author": d.get("author", ""),
            "is_self": bool(d.get("is_self")),
        })
    return out


def main():
    now = datetime.now(timezone.utc).isoformat()
    current = load(DATA_FILE)
    previous = load(PREVIOUS_FILE)
    health = {}

    # Preserve last-known-good family payloads instead of converting transient
    # failures into empty UI sections.
    for key in PRESERVE_KEYS:
        cur = current.get(key)
        old = previous.get(key)
        if isinstance(cur, list):
            if cur:
                health[key] = {"status": "healthy", "count": len(cur), "last_success": now}
            elif isinstance(old, list) and old:
                current[key] = old
                health[key] = {
                    "status": "stale", "count": len(old),
                    "last_success": previous.get("updated") or None,
                    "note": "Latest fetch was empty; showing last-known-good items."
                }
            else:
                health[key] = {"status": "empty", "count": 0, "last_success": None}

    # Curated, deterministic Reddit overview. Failure in one subreddit does not
    # affect any other subreddit or category.
    reddit_all = []
    reddit_by_category = {k: [] for k in REDDIT}
    reddit_health = {}
    previous_reddit = previous.get("reddit", {}) if isinstance(previous.get("reddit"), dict) else {}
    prev_by_sub = previous_reddit.get("by_subreddit", {}) if isinstance(previous_reddit.get("by_subreddit"), dict) else {}
    by_subreddit = {}

    for category, subs in REDDIT.items():
        for sub in subs:
            try:
                items = reddit_feed(sub)
                by_subreddit[sub] = items
                reddit_health[sub] = {"status": "healthy", "count": len(items), "last_success": now}
            except Exception as e:
                cached = prev_by_sub.get(sub, []) if isinstance(prev_by_sub.get(sub), list) else []
                by_subreddit[sub] = cached
                reddit_health[sub] = {
                    "status": "stale" if cached else "failed",
                    "count": len(cached),
                    "last_success": previous.get("updated") if cached else None,
                    "note": str(e)[:180],
                }
            reddit_by_category[category].extend(by_subreddit[sub])
            reddit_all.extend(by_subreddit[sub])

    reddit_all.sort(key=lambda x: x.get("ts", 0), reverse=True)
    for items in reddit_by_category.values():
        items.sort(key=lambda x: x.get("ts", 0), reverse=True)

    current["reddit"] = {
        "all": reddit_all,
        "categories": reddit_by_category,
        "by_subreddit": by_subreddit,
    }
    current["source_health"] = {**health, "reddit": reddit_health}
    current["vnext"] = {
        "version": 1,
        "normalized_at": now,
        "architecture": "legacy collectors + resilient normalization",
    }

    # Intentionally omit personal calendar data from vNext payload even while
    # legacy fetch.py remains available as a rollback path.
    current.pop("personal_calendar", None)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2, ensure_ascii=False, default=str)

    print(f"vNext normalization complete: {len(reddit_all)} Reddit items")


if __name__ == "__main__":
    main()
