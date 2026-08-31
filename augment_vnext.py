#!/usr/bin/env python3
"""Revere Monitor vNext normalization and resilience pass.

Runs after legacy fetch.py. Keeps last-known-good data, repairs fragile feed routes,
adds deterministic Reddit feeds, and emits source-health metadata for the UI.
"""
import json, re, calendar
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import feedparser
import requests
from bs4 import BeautifulSoup

DATA_FILE = "data.json"
PREVIOUS_FILE = "data.previous.json"
UA = {
    "User-Agent": "Mozilla/5.0 (compatible; RevereMonitor/7.1; +https://github.com/gravellese/revere-monitor)",
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, text/html, */*",
}

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


def entry_ts(e):
    for key in ("published_parsed", "updated_parsed"):
        p = getattr(e, key, None)
        if p:
            try:
                return calendar.timegm(p)
            except Exception:
                pass
    raw = getattr(e, "published", "") or getattr(e, "updated", "")
    if raw:
        try:
            return int(parsedate_to_datetime(raw).timestamp())
        except Exception:
            pass
    return 0


def feed_via_requests(url, limit=30, source=None):
    r = requests.get(url, headers=UA, timeout=15)
    r.raise_for_status()
    f = feedparser.parse(r.content)
    out = []
    for e in f.entries[:limit]:
        link = getattr(e, "link", "") or ""
        out.append({
            "title": getattr(e, "title", "") or "",
            "link": link,
            "published": getattr(e, "published", "") or getattr(e, "updated", "") or "",
            "ts": entry_ts(e),
            "author": getattr(e, "author", "") or "",
            "feed_title": getattr(getattr(e, "source", None), "title", "") if getattr(e, "source", None) else "",
            "summary": getattr(e, "summary", "") or getattr(e, "description", "") or "",
            "source": source or getattr(e, "author", "") or "Feed",
        })
    return [x for x in out if x.get("title") and x.get("link")]


def rss4_url_near(anchor):
    """Recover the existing rssrssrssrss permalink from legacy fetch.py."""
    try:
        txt = open("fetch.py", "r", encoding="utf-8").read()
        idx = txt.find(anchor)
        if idx < 0:
            return None
        block = txt[max(0, idx - 1800):idx + 200]
        urls = re.findall(r"https://www\.rssrssrssrss\.com/api/merge\?feeds=[^'\"\s]+", block)
        return urls[-1] if urls else None
    except Exception:
        return None


def fallback_universal_hub(limit=30):
    """Universal Hub is too important to vanish when its RSS route breaks."""
    r = requests.get("https://www.universalhub.com/", headers=UA, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out, seen = [], set()
    for a in soup.select("h1 a[href], h2 a[href], h3 a[href], .views-row a[href]"):
        title = " ".join(a.get_text(" ", strip=True).split())
        href = a.get("href", "")
        if len(title) < 12 or not href:
            continue
        if href.startswith("/"):
            href = "https://www.universalhub.com" + href
        if "universalhub.com" not in href or href in seen:
            continue
        seen.add(href)
        out.append({"title": title, "link": href, "published": "", "ts": 0, "source": "Universal Hub", "summary": ""})
        if len(out) >= limit:
            break
    return out


def fallback_sports(limit=35):
    q = quote('Boston sports OR NFL OR MLB OR NHL OR NBA OR college football OR college basketball')
    return feed_via_requests(
        f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en",
        limit, "Google News · Sports"
    )


def fallback_espn(limit=30):
    try:
        return feed_via_requests("https://www.espn.com/espn/rss/news", limit, "ESPN")
    except Exception:
        q = quote("site:espn.com sports")
        return feed_via_requests(
            f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en",
            limit, "ESPN via Google News"
        )


def fallback_revere_tv(limit=9):
    channel = "UCq-Ej7V3_v7NuGUVRnqv8Aw"
    items = feed_via_requests(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel}", limit, "Revere TV")
    for x in items:
        m = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{8,})", x.get("link", ""))
        x["video_id"] = m.group(1) if m else ""
        x["thumbnail"] = f"https://img.youtube.com/vi/{x['video_id']}/mqdefault.jpg" if x["video_id"] else ""
    return items


def reddit_feed(subreddit, limit=12):
    # Reddit's JSON endpoints routinely reject unattended GitHub runners.
    # Public .rss feeds are a more appropriate deterministic acquisition path.
    urls = [
        f"https://www.reddit.com/r/{quote(subreddit)}/hot/.rss",
        f"https://www.reddit.com/r/{quote(subreddit)}/.rss",
    ]
    last = None
    for url in urls:
        try:
            items = feed_via_requests(url, limit, f"r/{subreddit}")
            if items:
                for x in items:
                    x.update({"subreddit": subreddit, "score": 0, "comments": 0, "is_self": False})
                return items
        except Exception as e:
            last = e
    raise last or RuntimeError("Reddit RSS returned no items")


def repair_empty_families(current, repairs):
    """Second-chance routes for families that legacy acquisition returned empty."""
    if not current.get("news_universalhub"):
        try:
            current["news_universalhub"] = fallback_universal_hub()
            repairs["news_universalhub"] = "Universal Hub homepage fallback"
        except Exception as e:
            repairs["news_universalhub"] = f"fallback failed: {e}"[:180]

    if not current.get("news_substack"):
        u = rss4_url_near("Substack reading list")
        if u:
            try:
                current["news_substack"] = feed_via_requests(u, 50, "Substack")
                repairs["news_substack"] = "rssrssrssrss retry with browser headers"
            except Exception as e:
                repairs["news_substack"] = f"rss4 retry failed: {e}"[:180]

    if not current.get("news_sports"):
        u = rss4_url_near("My Sports News")
        if u:
            try:
                current["news_sports"] = feed_via_requests(u, 50, "My Sports")
                repairs["news_sports"] = "rssrssrssrss retry with browser headers"
            except Exception:
                pass
        if not current.get("news_sports"):
            try:
                current["news_sports"] = fallback_sports()
                repairs["news_sports"] = "Google News sports fallback"
            except Exception as e:
                repairs["news_sports"] = f"fallback failed: {e}"[:180]

    if not current.get("news_espn"):
        try:
            current["news_espn"] = fallback_espn()
            repairs["news_espn"] = "ESPN HTTP/Google News fallback"
        except Exception as e:
            repairs["news_espn"] = f"fallback failed: {e}"[:180]

    if not current.get("revere_tv"):
        try:
            current["revere_tv"] = fallback_revere_tv()
            repairs["revere_tv"] = "YouTube RSS retry with browser headers"
        except Exception as e:
            repairs["revere_tv"] = f"fallback failed: {e}"[:180]


def clean_local_leaks(current):
    """Prevent clearly non-local sports stories from entering Massachusetts via broad Boston bundles."""
    generic_sports = re.compile(r"\b(us open|wimbledon|tennis|pga|golf|formula 1|f1|premier league|champions league)\b", re.I)
    local = re.compile(r"\b(boston|massachusetts|new england|revere|cambridge|somerville|chelsea|everett|lynn|winthrop|bruins|celtics|red sox|patriots|revolution)\b", re.I)
    xs = current.get("news_boston")
    if isinstance(xs, list):
        current["news_boston"] = [x for x in xs if not (generic_sports.search(x.get("title", "")) and not local.search(x.get("title", "")))]


def main():
    now = datetime.now(timezone.utc).isoformat()
    current = load(DATA_FILE)
    previous = load(PREVIOUS_FILE)
    repairs = {}

    repair_empty_families(current, repairs)
    clean_local_leaks(current)

    health = {}
    for key in PRESERVE_KEYS:
        cur, old = current.get(key), previous.get(key)
        if isinstance(cur, list):
            if cur:
                health[key] = {"status": "healthy", "count": len(cur), "last_success": now, "route": repairs.get(key, "legacy collector")}
            elif isinstance(old, list) and old:
                current[key] = old
                health[key] = {"status": "stale", "count": len(old), "last_success": previous.get("updated"), "note": "Latest fetch was empty; showing last-known-good items."}
            else:
                health[key] = {"status": "empty", "count": 0, "last_success": None, "note": repairs.get(key)}

    reddit_all, by_subreddit = [], {}
    reddit_by_category = {k: [] for k in REDDIT}
    reddit_health = {}
    prev_reddit = previous.get("reddit", {}) if isinstance(previous.get("reddit"), dict) else {}
    prev_by_sub = prev_reddit.get("by_subreddit", {}) if isinstance(prev_reddit.get("by_subreddit"), dict) else {}

    for category, subs in REDDIT.items():
        for sub in subs:
            try:
                items = reddit_feed(sub)
                by_subreddit[sub] = items
                reddit_health[sub] = {"status": "healthy", "count": len(items), "last_success": now, "route": "Reddit RSS"}
            except Exception as e:
                cached = prev_by_sub.get(sub, []) if isinstance(prev_by_sub.get(sub), list) else []
                by_subreddit[sub] = cached
                reddit_health[sub] = {"status": "stale" if cached else "failed", "count": len(cached), "last_success": previous.get("updated") if cached else None, "note": str(e)[:180]}
            reddit_by_category[category].extend(by_subreddit[sub])
            reddit_all.extend(by_subreddit[sub])

    reddit_all.sort(key=lambda x: x.get("ts", 0), reverse=True)
    for items in reddit_by_category.values():
        items.sort(key=lambda x: x.get("ts", 0), reverse=True)

    current["reddit"] = {"all": reddit_all, "categories": reddit_by_category, "by_subreddit": by_subreddit}
    current["source_health"] = {**health, "reddit": reddit_health}
    current["vnext"] = {"version": 2, "normalized_at": now, "architecture": "legacy collectors + resilient normalization + source fallbacks", "repairs": repairs}
    current.pop("personal_calendar", None)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2, ensure_ascii=False, default=str)
    print(f"vNext normalization complete: {len(reddit_all)} Reddit items; repairs={repairs}")


if __name__ == "__main__":
    main()
