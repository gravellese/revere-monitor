#!/usr/bin/env python3
"""
Revere Monitor v4 — Data Fetcher
Runs via GitHub Actions every hour, writes data.json
"""

import json, requests, feedparser
from datetime import datetime, timezone
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

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
                     timeout=10, headers={"User-Agent": "RevereMonitor/4.0"})
    p = r.json()["properties"]["periods"][0]
    return {
        "temp": p["temperature"], "unit": p["temperatureUnit"],
        "wind": p["windSpeed"], "windDir": p.get("windDirection", ""),
        "shortForecast": p["shortForecast"],
        "humidity": p.get("relativeHumidity", {}).get("value"),
        "precip": p.get("probabilityOfPrecipitation", {}).get("value", 0) or 0,
    }

def fetch_weather_hourly():
    r = requests.get("https://api.weather.gov/gridpoints/BOX/68,89/forecast/hourly",
                     timeout=10, headers={"User-Agent": "RevereMonitor/4.0"})
    return [{
        "time": p["startTime"], "temp": p["temperature"],
        "unit": p["temperatureUnit"], "shortForecast": p["shortForecast"],
        "wind": p["windSpeed"],
        "precip": p.get("probabilityOfPrecipitation", {}).get("value", 0) or 0,
    } for p in r.json()["properties"]["periods"][:24]]

def fetch_weather_daily():
    r = requests.get("https://api.weather.gov/gridpoints/BOX/68,89/forecast",
                     timeout=10, headers={"User-Agent": "RevereMonitor/4.0"})
    periods = r.json()["properties"]["periods"]
    days = []
    i = 0
    while i < len(periods) and len(days) < 7:
        d = periods[i]
        n = periods[i+1] if i+1 < len(periods) else None
        days.append({
            "name": d["name"], "date": d["startTime"],
            "high": d["temperature"] if d["isDaytime"] else (n["temperature"] if n else None),
            "low": n["temperature"] if n and not d["isDaytime"] else None,
            "shortForecast": d["shortForecast"],
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
    return {
        "sunrise": d["sunrise"], "sunset": d["sunset"],
        "solar_noon": d["solar_noon"], "day_length": d["day_length"],
        "dawn": d.get("civil_twilight_begin"), "dusk": d.get("civil_twilight_end"),
    }

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
    # Try primary FAA endpoint
    try:
        r = requests.get(
            "https://soa.smext.faa.gov/asws/api/airport/status/BOS",
            timeout=10,
            headers={"Accept": "application/json", "User-Agent": "RevereMonitor/4.0"}
        )
        d = r.json()
        return {
            "name": d.get("Name", "Boston Logan"),
            "delay": d.get("Delay", False),
            "status": "ok" if not d.get("Delay") else "delay",
            "arriveDeparDelay": [
                {"type": x.get("Type",""), "reason": x.get("Reason",""),
                 "avg": x.get("Avg",""), "trend": x.get("Trend","")}
                for x in d.get("ArriveDepartDelay", [])
            ],
            "groundDelay": [
                {"reason": x.get("Reason",""), "avg": x.get("Avg","")}
                for x in d.get("GroundDelay", [])
            ],
            "groundStop": [
                {"reason": x.get("Reason",""), "endTime": x.get("EndTime","")}
                for x in d.get("GroundStop", [])
            ],
        }
    except Exception as e:
        print(f"    FAA primary failed: {e}, trying backup...")
        # Backup: use aviationweather.gov METAR for KBOS
        r2 = requests.get(
            "https://aviationweather.gov/api/data/metar?ids=KBOS&format=json",
            timeout=10
        )
        metar = r2.json()
        if metar:
            m = metar[0]
            return {
                "name": "Boston Logan (KBOS)",
                "delay": False,
                "status": "ok",
                "metar": True,
                "raw_metar": m.get("rawOb", ""),
                "wind_dir": m.get("wdir", ""),
                "wind_speed": m.get("wspd", ""),
                "visibility": m.get("visib", ""),
                "sky": m.get("skyCondition", ""),
                "temp_c": m.get("temp", ""),
                "altimeter": m.get("altim", ""),
                "obs_time": m.get("obsTime", ""),
            }
        raise

# ── MBTA — ALL LINES ─────────────────────────────────────────────
def fetch_mbta():
    # All subway + commuter rail alerts (no route filter)
    r = requests.get(
        "https://api-v3.mbta.com/alerts?filter[activity]=BOARD,EXIT,RIDE&filter[route_type]=0,1,2",
        timeout=10
    )
    alerts = []
    for a in r.json().get("data", [])[:12]:
        attrs = a["attributes"]
        # Get affected routes
        routes = []
        for entity in attrs.get("informed_entities", []):
            rt = entity.get("route", "")
            if rt and rt not in routes:
                routes.append(rt)
        alerts.append({
            "header": attrs["header"],
            "effect": attrs["effect"],
            "routes": routes[:3],
        })
    return alerts

# ── REVERE CITY CALENDAR ─────────────────────────────────────────
def fetch_revere_calendar():
    r = requests.get("https://www.revere.org/calendar", timeout=15, headers=HEADERS)
    soup = BeautifulSoup(r.text, "html.parser")
    events = []

    # Try multiple common CMS patterns
    for item in soup.select("article, .event, .event-item, li.views-row, .calendar-item")[:12]:
        title_el = item.select_one("h2, h3, h4, .event-title, .title")
        date_el  = item.select_one("time, .date, .event-date, .field--name-field-date, [class*='date']")
        time_el  = item.select_one(".time, .event-time, [class*='time']")
        link_el  = item.select_one("a[href]")
        if title_el and len(title_el.get_text(strip=True)) > 4:
            link = ""
            if link_el:
                link = link_el.get("href","")
                if link and not link.startswith("http"):
                    link = "https://www.revere.org" + link
            events.append({
                "title": title_el.get_text(strip=True),
                "date":  date_el.get_text(strip=True) if date_el else "",
                "time":  time_el.get_text(strip=True) if time_el else "",
                "link":  link,
            })

    # Fallback: calendar links
    if not events:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            if "/calendar/" in href and text and len(text) > 5 and not any(x in text.lower() for x in ["calendar","event","view all"]):
                full = href if href.startswith("http") else "https://www.revere.org" + href
                events.append({"title": text, "date": "", "time": "", "link": full})
                if len(events) >= 8:
                    break

    print(f"  {'✓' if events else '⚠'} Revere calendar: {len(events)} events")
    return events[:10]

# ── RSS FEED ─────────────────────────────────────────────────────
def fetch_feed(url, max_items=8):
    try:
        feed = feedparser.parse(url)
        items = []
        for e in feed.entries[:max_items]:
            items.append({
                "title":     getattr(e, "title", ""),
                "link":      getattr(e, "link", ""),
                "published": getattr(e, "published", ""),
                "summary":   (getattr(e, "summary", "") or "")[:200],
            })
        return items
    except:
        return []

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

# ── MAIN ─────────────────────────────────────────────────────────
def main():
    print("🔄 Revere Monitor v4 — fetching...")
    data = {
        "updated":       datetime.now(timezone.utc).isoformat(),
        "updated_local": datetime.now().strftime("%B %-d, %Y at %-I:%M %p"),
    }

    print("\n🌤  Weather")
    data["weather_current"] = safe(fetch_weather_current, "Current")
    data["weather_hourly"]  = safe(fetch_weather_hourly,  "Hourly 24h") or []
    data["weather_daily"]   = safe(fetch_weather_daily,   "7-day")      or []

    print("\n☀️  Sky / Tides / Logan / MBTA")
    data["sunrise_sunset"] = safe(fetch_sunrise_sunset, "Sunrise/Sunset") or {}
    data["tides"]          = safe(fetch_tides,          "NOAA tides")     or []
    data["logan"]          = safe(fetch_logan,          "FAA/KBOS")       or {}
    data["mbta_alerts"]    = safe(fetch_mbta,           "MBTA all lines") or []

    print("\n🏛️  Revere Calendar")
    data["revere_calendar"] = safe(fetch_revere_calendar, "revere.org/calendar") or []

    print("\n📺 Revere TV")
    # Try multiple known Revere TV channel IDs
    revere_tv = []
    for cid in ["UCq-Ej7V3_v7NuGUVRnqv8Aw", "UCxxx"]:  # Add correct ID here if known
        revere_tv = safe(lambda c=cid: fetch_youtube(c), f"Revere TV ({cid})") or []
        if revere_tv:
            break
    data["revere_tv"] = revere_tv

    print("\n📰 News feeds")

    # Revere.org official RSS
    data["news_revere_official"] = safe(
        lambda: fetch_feed("https://www.revere.org/news/feed/rss", 8),
        "Revere.org RSS") or []

    # Revere Journal
    revere_journal = safe(lambda: fetch_feed("https://www.reverejournal.com/feed/", 8), "Revere Journal") or []
    for i in revere_journal: i["source"] = "Revere Journal"
    data["news_revere"] = revere_journal

    # Universal Hub
    for uhub_url in ["https://www.universalhub.com/atom.xml", "https://www.universalhub.com/feed"]:
        uhub = safe(lambda u=uhub_url: fetch_feed(u, 8), "Universal Hub") or []
        if uhub:
            break
    for i in uhub: i["source"] = "Universal Hub"
    data["news_universalhub"] = uhub

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
        items = safe(lambda u=url: fetch_feed(u, 3), name) or []
        for i in items: i["source"] = name
        data["news_communities"].extend(items)

    # Boston — many sources for redundancy
    boston_sources = [
        ("https://www.bostonglobe.com/rss/homepage",      "Boston Globe"),
        ("https://bostonherald.com/feed/",                 "Boston Herald"),
        ("https://www.wgbh.org/news/rss",                  "GBH News"),
        ("https://www.masslive.com/arc/outboundfeeds/rss/?outputType=xml", "MassLive"),
        ("https://www.wcvb.com/rss",                       "WCVB"),
        ("https://www.nbcboston.com/feed/",                "NBC Boston"),
        ("https://www.cbsnews.com/boston/rss/",            "CBS Boston"),
        ("https://whdh.com/feed/",                         "WHDH 7News"),
    ]
    data["news_boston"] = []
    for url, label in boston_sources:
        items = safe(lambda u=url: fetch_feed(u, 3), label) or []
        for i in items: i["source"] = label
        data["news_boston"].extend(items)
    data["news_boston"] = data["news_boston"][:16]

    # Sports — Boston teams focus
    sports_sources = [
        ("https://www.bostonglobe.com/rss/sports",         "Globe Sports"),
        ("https://www.espn.com/espn/rss/boston/news",      "ESPN Boston"),
        ("https://nesn.com/feed/",                         "NESN"),
        ("https://www.masslive.com/sports/arc/outboundfeeds/rss/?outputType=xml", "MassLive Sports"),
        ("https://bostonherald.com/sports/feed/",          "Herald Sports"),
    ]
    data["news_sports"] = []
    for url, label in sports_sources:
        items = safe(lambda u=url: fetch_feed(u, 4), label) or []
        for i in items: i["source"] = label
        data["news_sports"].extend(items)
    data["news_sports"] = data["news_sports"][:14]

    # Boston College
    bc_sources = [
        ("https://bcheights.com/feed/",                   "The Heights"),
        ("https://bceagles.com/rss.aspx?path=mhockey",    "BC Hockey"),
        ("https://bceagles.com/rss.aspx",                  "BC Athletics"),
        ("https://247sports.com/college/boston-college/rss/", "247Sports BC"),
        ("https://www.bcinterruption.com/rss/current.xml", "BC Interruption"),
        ("https://www.si.com/college/boston-college/rss",  "SI Boston College"),
    ]
    data["news_bc"] = []
    for url, label in bc_sources:
        items = safe(lambda u=url: fetch_feed(u, 3), label) or []
        for i in items: i["source"] = label
        data["news_bc"].extend(items)
    data["news_bc"] = data["news_bc"][:12]

    # National
    national_sources = [
        ("https://feeds.npr.org/1001/rss.xml",             "NPR"),
        ("https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml", "NY Times"),
        ("https://feeds.bbci.co.uk/news/rss.xml",          "BBC"),
        ("https://apnews.com/hub/ap-top-news?format=feed&type=rss", "AP"),
        ("https://thehill.com/feed/",                       "The Hill"),
    ]
    data["news_national"] = []
    for url, label in national_sources:
        items = safe(lambda u=url: fetch_feed(u, 4), label) or []
        for i in items: i["source"] = label
        data["news_national"].extend(items)

    with open("data.json", "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"\n✅ Done — {data['updated_local']}")

if __name__ == "__main__":
    main()
