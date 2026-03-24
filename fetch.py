#!/usr/bin/env python3
"""
Revere Monitor v3 — Data Fetcher
Runs via GitHub Actions every hour, writes public/data.json
"""

import json, requests, feedparser
from datetime import datetime, timezone
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "RevereMonitor/3.0 (planning@revere.org)"}

def safe(fn, label):
    try:
        r = fn()
        print(f"  ✓ {label}")
        return r
    except Exception as e:
        print(f"  ✗ {label}: {e}")
        return None

# ── WEATHER ─────────────────────────────────────────────────────
def fetch_weather_current():
    r = requests.get("https://api.weather.gov/gridpoints/BOX/68,89/forecast/hourly",
                     timeout=10, headers=HEADERS)
    p = r.json()["properties"]["periods"][0]
    return {
        "temp": p["temperature"], "unit": p["temperatureUnit"],
        "wind": p["windSpeed"], "windDir": p["windDirection"],
        "shortForecast": p["shortForecast"], "icon": p["icon"],
        "humidity": p.get("relativeHumidity", {}).get("value"),
        "precip": p.get("probabilityOfPrecipitation", {}).get("value", 0) or 0,
    }

def fetch_weather_hourly():
    r = requests.get("https://api.weather.gov/gridpoints/BOX/68,89/forecast/hourly",
                     timeout=10, headers=HEADERS)
    return [{
        "time": p["startTime"], "temp": p["temperature"], "unit": p["temperatureUnit"],
        "shortForecast": p["shortForecast"], "wind": p["windSpeed"],
        "precip": p.get("probabilityOfPrecipitation", {}).get("value", 0) or 0,
        "icon": p["icon"],
    } for p in r.json()["properties"]["periods"][:24]]

def fetch_weather_daily():
    r = requests.get("https://api.weather.gov/gridpoints/BOX/68,89/forecast",
                     timeout=10, headers=HEADERS)
    periods = r.json()["properties"]["periods"]
    days = []
    i = 0
    while i < len(periods) and len(days) < 7:
        d = periods[i]
        n = periods[i+1] if i+1 < len(periods) else None
        days.append({
            "name": d["name"], "date": d["startTime"],
            "high": d["temperature"] if d["isDaytime"] else None,
            "low": n["temperature"] if n else None,
            "shortForecast": d["shortForecast"], "icon": d["icon"],
            "precip": d.get("probabilityOfPrecipitation", {}).get("value", 0) or 0,
        })
        i += 2
    return days

# ── SUNRISE / SUNSET ─────────────────────────────────────────────
def fetch_sunrise_sunset():
    r = requests.get(
        "https://api.sunrise-sunset.org/json?lat=42.4082&lng=-71.0120&formatted=0",
        timeout=10)
    d = r.json()["results"]
    return {"sunrise": d["sunrise"], "sunset": d["sunset"],
            "solar_noon": d["solar_noon"], "day_length": d["day_length"],
            "dawn": d.get("civil_twilight_begin"), "dusk": d.get("civil_twilight_end")}

# ── TIDES ────────────────────────────────────────────────────────
def fetch_tides():
    today = datetime.now().strftime("%Y%m%d")
    url = (
        "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
        f"?begin_date={today}&range=48&station=8443970"
        "&product=predictions&datum=MLLW&time_zone=lst_ldt"
        "&interval=hilo&units=english&application=revere_monitor&format=json"
    )
    r = requests.get(url, timeout=10)
    return [{"t": p["t"], "v": float(p["v"]), "type": p["type"]}
            for p in r.json().get("predictions", [])]

# ── LOGAN (FAA) ──────────────────────────────────────────────────
def fetch_logan():
    r = requests.get("https://soa.smext.faa.gov/asws/api/airport/status/BOS",
                     timeout=10, headers={"Accept": "application/json"})
    d = r.json()
    return {
        "name": d.get("Name", "Boston Logan"), "delay": d.get("Delay", False),
        "arriveDeparDelay": [
            {"type": x.get("Type",""), "reason": x.get("Reason",""),
             "avg": x.get("Avg",""), "trend": x.get("Trend","")}
            for x in d.get("ArriveDepartDelay", [])],
        "groundDelay": [
            {"reason": x.get("Reason",""), "avg": x.get("Avg","")}
            for x in d.get("GroundDelay", [])],
        "groundStop": [
            {"reason": x.get("Reason",""), "endTime": x.get("EndTime","")}
            for x in d.get("GroundStop", [])],
    }

# ── MBTA BLUE LINE ───────────────────────────────────────────────
def fetch_mbta():
    r = requests.get(
        "https://api-v3.mbta.com/alerts?filter[route]=Blue&filter[activity]=BOARD,EXIT,RIDE",
        timeout=10)
    return [{"header": a["attributes"]["header"], "effect": a["attributes"]["effect"]}
            for a in r.json().get("data", [])[:6]]

# ── REVERE CITY CALENDAR ─────────────────────────────────────────
def fetch_revere_calendar():
    """Scrape upcoming events from revere.org/calendar"""
    r = requests.get("https://www.revere.org/calendar", timeout=15, headers={
        "User-Agent": "Mozilla/5.0 (compatible; RevereMonitor/3.0)"
    })
    soup = BeautifulSoup(r.text, "html.parser")
    events = []

    # Aptitive CMS event selectors — try several common patterns
    selectors = [
        "article.event", "div.event-item", "li.event",
        ".calendar-event", ".eventItem", "div[class*='event']",
        "article", ".cal-event"
    ]

    for sel in selectors:
        items = soup.select(sel)
        if items:
            for item in items[:8]:
                title_el = item.select_one("h2, h3, h4, .event-title, .title, a")
                date_el  = item.select_one(".date, .event-date, time, [class*='date']")
                link_el  = item.select_one("a")
                if title_el:
                    title = title_el.get_text(strip=True)
                    date  = date_el.get_text(strip=True) if date_el else ""
                    link  = link_el.get("href", "") if link_el else ""
                    if link and not link.startswith("http"):
                        link = "https://www.revere.org" + link
                    if title and len(title) > 3:
                        events.append({"title": title, "date": date, "link": link})
            if events:
                break

    # If structured parsing failed, try plain <a> tags that look like events
    if not events:
        for a in soup.find_all("a", href=True)[:30]:
            href = a["href"]
            text = a.get_text(strip=True)
            if "/calendar/" in href and text and len(text) > 5:
                full = href if href.startswith("http") else "https://www.revere.org" + href
                events.append({"title": text, "date": "", "link": full})

    print(f"  {'✓' if events else '⚠'} Revere calendar: {len(events)} events parsed")
    return events[:8]

# ── YOUTUBE FEED ─────────────────────────────────────────────────
def fetch_youtube(channel_id, max_items=6):
    feed = feedparser.parse(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")
    items = []
    for e in feed.entries[:max_items]:
        vid = getattr(e, "yt_videoid", "") or ""
        if not vid and "v=" in getattr(e, "link", ""):
            vid = e.link.split("v=")[-1].split("&")[0]
        items.append({
            "title": getattr(e, "title", ""), "video_id": vid,
            "link": getattr(e, "link", ""), "published": getattr(e, "published", ""),
            "thumbnail": f"https://img.youtube.com/vi/{vid}/mqdefault.jpg" if vid else "",
        })
    return items

# ── RSS FEED ─────────────────────────────────────────────────────
def fetch_feed(url, max_items=6):
    feed = feedparser.parse(url)
    return [{
        "title":     getattr(e, "title", ""),
        "link":      getattr(e, "link", ""),
        "published": getattr(e, "published", ""),
        "summary":   (getattr(e, "summary", "") or "")[:200],
    } for e in feed.entries[:max_items]]

# ── MAIN ─────────────────────────────────────────────────────────
def main():
    print("🔄 Revere Monitor v3 — fetching...")
    data = {
        "updated":       datetime.now(timezone.utc).isoformat(),
        "updated_local": datetime.now().strftime("%B %-d, %Y at %-I:%M %p"),
    }

    print("\n🌤  Weather")
    data["weather_current"] = safe(fetch_weather_current, "Current")
    data["weather_hourly"]  = safe(fetch_weather_hourly,  "Hourly 24h") or []
    data["weather_daily"]   = safe(fetch_weather_daily,   "7-day") or []

    print("\n☀️  Sky / Tides / Logan")
    data["sunrise_sunset"] = safe(fetch_sunrise_sunset, "Sunrise/Sunset") or {}
    data["tides"]          = safe(fetch_tides,          "NOAA tides") or []
    data["logan"]          = safe(fetch_logan,          "FAA BOS") or {}

    print("\n🚇 MBTA")
    data["mbta_alerts"] = safe(fetch_mbta, "Blue Line") or []

    print("\n🏛️  Revere Calendar")
    data["revere_calendar"] = safe(fetch_revere_calendar, "revere.org/calendar") or []

    print("\n📺 Revere TV")
    REVERE_TV_ID = "UCq-Ej7V3_v7NuGUVRnqv8Aw"
    data["revere_tv"] = safe(lambda: fetch_youtube(REVERE_TV_ID), "Revere TV") or []

    print("\n📰 News")
    # Revere
    data["news_revere"] = safe(
        lambda: fetch_feed("https://www.reverejournal.com/feed/"), "Revere Journal") or []

    # Communities
    comm = {
        "Chelsea":     "https://chelsearecord.com/feed/",
        "East Boston": "https://eastietimes.com/feed/",
        "Lynn":        "https://www.itemlive.com/feed/",
        "Winthrop":    "https://winthroptranscript.com/feed/",
        "Saugus":      "https://saugusadvocate.com/feed/",
        "Everett":     "https://everettindependent.com/feed/",
    }
    data["news_communities"] = []
    for name, url in comm.items():
        items = safe(lambda u=url: fetch_feed(u, 2), name) or []
        for i in items: i["source"] = name
        data["news_communities"].extend(items)

    # Boston
    boston = [
        ("https://www.bostonglobe.com/rss/homepage", "Boston Globe"),
        ("https://bostonherald.com/feed/", "Boston Herald"),
        ("https://www.wgbh.org/news/rss", "GBH News"),
    ]
    data["news_boston"] = []
    for url, label in boston:
        items = safe(lambda u=url: fetch_feed(u, 4), label) or []
        for i in items: i["source"] = label
        data["news_boston"].extend(items)
    data["news_boston"] = data["news_boston"][:12]

    # Sports
    sports = [
        ("https://www.bostonglobe.com/rss/sports", "Globe Sports"),
        ("https://www.espn.com/espn/rss/boston/news", "ESPN Boston"),
        ("https://nesn.com/feed/", "NESN"),
    ]
    data["news_sports"] = []
    for url, label in sports:
        items = safe(lambda u=url: fetch_feed(u, 4), label) or []
        for i in items: i["source"] = label
        data["news_sports"].extend(items)
    data["news_sports"] = data["news_sports"][:10]

    # BC
    bc = [
        ("https://bcheights.com/feed/", "The Heights"),
        ("https://bceagles.com/rss.aspx?path=mhockey", "BC Hockey"),
        ("https://bceagles.com/rss.aspx", "BC Athletics"),
    ]
    data["news_bc"] = []
    for url, label in bc:
        items = safe(lambda u=url: fetch_feed(u, 3), label) or []
        for i in items: i["source"] = label
        data["news_bc"].extend(items)
    data["news_bc"] = data["news_bc"][:8]

    # National
    national = [
        ("https://feeds.npr.org/1001/rss.xml", "NPR"),
        ("https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml", "NY Times"),
        ("https://feeds.bbci.co.uk/news/rss.xml", "BBC"),
        ("https://apnews.com/hub/ap-top-news?format=feed&type=rss", "AP"),
    ]
    data["news_national"] = []
    for url, label in national:
        items = safe(lambda u=url: fetch_feed(u, 4), label) or []
        for i in items: i["source"] = label
        data["news_national"].extend(items)

    with open("public/data.json", "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"\n✅ Done — {data['updated_local']}")

if __name__ == "__main__":
    main()
