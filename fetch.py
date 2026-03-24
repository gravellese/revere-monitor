#!/usr/bin/env python3
"""Revere Monitor v6 — fetch.py"""

import json, re, requests, feedparser, calendar
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

# ── WEATHER ─────────────────────────────────────────
def fetch_weather_current():
    r = requests.get("https://api.weather.gov/gridpoints/BOX/68,89/forecast/hourly",
                     timeout=10, headers={"User-Agent": "RevereMonitor/6.0"})
    p = r.json()["properties"]["periods"][0]
    return {
        "temp": p["temperature"], "unit": p["temperatureUnit"],
        "wind": p["windSpeed"], "windDir": p.get("windDirection",""),
        "shortForecast": p["shortForecast"],
        "humidity": p.get("relativeHumidity",{}).get("value"),
        "precip": p.get("probabilityOfPrecipitation",{}).get("value",0) or 0,
    }

def fetch_weather_hourly():
    r = requests.get("https://api.weather.gov/gridpoints/BOX/68,89/forecast/hourly",
                     timeout=10, headers={"User-Agent": "RevereMonitor/6.0"})
    return [{
        "time": p["startTime"], "temp": p["temperature"],
        "unit": p["temperatureUnit"], "shortForecast": p["shortForecast"],
        "wind": p["windSpeed"],
        "precip": p.get("probabilityOfPrecipitation",{}).get("value",0) or 0,
    } for p in r.json()["properties"]["periods"][:24]]

def fetch_weather_daily():
    r = requests.get("https://api.weather.gov/gridpoints/BOX/68,89/forecast",
                     timeout=10, headers={"User-Agent": "RevereMonitor/6.0"})
    periods = r.json()["properties"]["periods"]
    days, i = [], 0
    while i < len(periods) and len(days) < 7:
        d = periods[i]
        n = periods[i+1] if i+1 < len(periods) else None
        days.append({
            "name": d["name"], "date": d["startTime"],
            "high": d["temperature"] if d["isDaytime"] else None,
            "low": n["temperature"] if n else None,
            "shortForecast": d["shortForecast"],
            "precip": d.get("probabilityOfPrecipitation",{}).get("value",0) or 0,
        })
        i += 2
    return days

# ── SUNRISE / SUNSET ─────────────────────────────────
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

# ── TIDES ─────────────────────────────────────────────
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
            for p in r.json().get("predictions",[])]

# ── LOGAN — use Aviation Weather as primary ───────────
def fetch_logan():
    result = {
        "name": "Boston Logan (KBOS)",
        "delay": False,
        "status": "ok",
        "metar": None,
        "taf": None,
        "faa_delays": [],
    }

    # 1. FAA status (delays/ground stops) — try but don't fail if down
    try:
        r = requests.get(
            "https://soa.smext.faa.gov/asws/api/airport/status/BOS",
            timeout=8, headers={"Accept":"application/json","User-Agent":"RevereMonitor/6.0"}
        )
        if r.status_code == 200:
            d = r.json()
            result["delay"] = d.get("Delay", False)
            for x in d.get("ArriveDepartDelay",[]):
                result["faa_delays"].append({"type":x.get("Type",""),"reason":x.get("Reason",""),"avg":x.get("Avg",""),"trend":x.get("Trend","")})
            for x in d.get("GroundDelay",[]):
                result["faa_delays"].append({"type":"Ground Delay","reason":x.get("Reason",""),"avg":x.get("Avg","")})
            for x in d.get("GroundStop",[]):
                result["faa_delays"].append({"type":"Ground Stop","reason":x.get("Reason",""),"avg":x.get("EndTime","")})
    except Exception as e:
        print(f"    FAA API skipped: {e}")

    # 2. METAR — current conditions (always fetch this)
    try:
        r2 = requests.get(
            "https://aviationweather.gov/api/data/metar?ids=KBOS&format=json&hours=1",
            timeout=10, headers={"User-Agent": "RevereMonitor/6.0"}
        )
        if r2.status_code == 200:
            metar_data = r2.json()
            if metar_data:
                m = metar_data[0]
                # Format wind
                wdir = m.get("wdir","")
                wspd = m.get("wspd","")
                wgst = m.get("wgst","")
                wind_str = f"{wdir}° @ {wspd} kts"
                if wgst:
                    wind_str += f" gusting {wgst} kts"
                result["metar"] = {
                    "raw":        m.get("rawOb",""),
                    "wind":       wind_str,
                    "visibility": f"{m.get('visib','')} SM",
                    "sky":        m.get("skyCondition",""),
                    "temp_c":     m.get("temp",""),
                    "dewpoint":   m.get("dewp",""),
                    "altimeter":  m.get("altim",""),
                    "wx":         m.get("wxString",""),
                    "obs_time":   m.get("obsTime",""),
                    "flight_cat": m.get("flightCategory",""),  # VFR/MVFR/IFR/LIFR
                }
    except Exception as e:
        print(f"    METAR fetch failed: {e}")

    # 3. TAF — forecast
    try:
        r3 = requests.get(
            "https://aviationweather.gov/api/data/taf?ids=KBOS&format=json",
            timeout=10, headers={"User-Agent": "RevereMonitor/6.0"}
        )
        if r3.status_code == 200:
            taf_data = r3.json()
            if taf_data:
                result["taf"] = taf_data[0].get("rawTAF","")
    except Exception as e:
        print(f"    TAF fetch failed: {e}")

    return result

# ── MBTA ALL LINES ────────────────────────────────────
def fetch_mbta():
    r = requests.get(
        "https://api-v3.mbta.com/alerts?filter[activity]=BOARD,EXIT,RIDE&filter[route_type]=0,1,2",
        timeout=10)
    alerts = []
    for a in r.json().get("data",[])[:20]:
        attrs = a["attributes"]
        routes = []
        for entity in attrs.get("informed_entities",[]):
            rt = entity.get("route","")
            if rt and rt not in routes:
                routes.append(rt)
        alerts.append({
            "header": attrs["header"],
            "effect": attrs["effect"],
            "routes": routes[:3],
        })
    return alerts

# ── REVERE CALENDAR ───────────────────────────────────
def fetch_revere_calendar():
    r = requests.get("https://www.revere.org/calendar", timeout=15, headers=HEADERS)
    soup = BeautifulSoup(r.text, "html.parser")
    events = []
    for item in soup.select("article, .event, .event-item, li.views-row, .calendar-item")[:12]:
        title_el = item.select_one("h2,h3,h4,.event-title,.title")
        date_el  = item.select_one("time,.date,.event-date,[class*='date']")
        time_el  = item.select_one(".time,.event-time,[class*='time']")
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
    if not events:
        for a in soup.find_all("a",href=True):
            href, text = a["href"], a.get_text(strip=True)
            if "/calendar/" in href and text and len(text) > 5:
                full = href if href.startswith("http") else "https://www.revere.org" + href
                events.append({"title":text,"date":"","time":"","link":full})
                if len(events) >= 8: break
    print(f"  {'✓' if events else '⚠'} Revere calendar: {len(events)} events")
    return events[:10]

# ── REVERE TV CHANNEL ID ──────────────────────────────
def fetch_revere_tv_channel_id():
    for url in ["https://www.youtube.com/@reveretv","https://www.youtube.com/user/reveretv"]:
        try:
            r = requests.get(url, timeout=10, headers=HEADERS)
            match = re.search(r'"channelId":"(UC[^"]{20,})"', r.text)
            if match:
                return match.group(1)
        except:
            continue
    return "UCq-Ej7V3_v7NuGUVRnqv8Aw"

# ── YOUTUBE FEED ──────────────────────────────────────
def fetch_youtube(channel_id, max_items=9):
    feed = feedparser.parse(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")
    items = []
    for e in feed.entries[:max_items]:
        vid = getattr(e,"yt_videoid","") or ""
        if not vid and "v=" in getattr(e,"link",""):
            vid = e.link.split("v=")[-1].split("&")[0]
        ts = 0
        if hasattr(e,"published_parsed") and e.published_parsed:
            try: ts = calendar.timegm(e.published_parsed)
            except: pass
        items.append({
            "title":getattr(e,"title",""), "video_id":vid,
            "link":getattr(e,"link",""), "published":getattr(e,"published",""),
            "ts": ts,
            "thumbnail":f"https://img.youtube.com/vi/{vid}/mqdefault.jpg" if vid else "",
        })
    return items

# ── RSS FEED — stores unix timestamp for reliable sort ─
def fetch_feed(url, max_items=30):
    try:
        feed = feedparser.parse(url)
        items = []
        for e in feed.entries[:max_items]:
            # Convert parsed time to unix timestamp for reliable JS sorting
            ts = 0
            if hasattr(e,"published_parsed") and e.published_parsed:
                try: ts = calendar.timegm(e.published_parsed)
                except: pass
            elif hasattr(e,"updated_parsed") and e.updated_parsed:
                try: ts = calendar.timegm(e.updated_parsed)
                except: pass
            items.append({
                "title":     getattr(e,"title",""),
                "link":      getattr(e,"link",""),
                "published": getattr(e,"published",""),
                "ts":        ts,   # Unix timestamp — use this for sorting in JS
            })
        return items
    except Exception as e:
        return []

# ── MAIN ─────────────────────────────────────────────
def main():
    print("🔄 Revere Monitor v6 — fetching...")
    data = {
        "updated":       datetime.now(timezone.utc).isoformat(),
        "updated_local": datetime.now().strftime("%B %-d, %Y at %-I:%M %p"),
    }

    print("\n🌤  Weather")
    data["weather_current"] = safe(fetch_weather_current, "Current") or {}
    data["weather_hourly"]  = safe(fetch_weather_hourly,  "Hourly")  or []
    data["weather_daily"]   = safe(fetch_weather_daily,   "7-day")   or []

    print("\n☀️  Sky / Tides / Logan / MBTA")
    data["sunrise_sunset"] = safe(fetch_sunrise_sunset, "Sunrise/Sunset") or {}
    data["tides"]          = safe(fetch_tides,          "NOAA tides")     or []
    data["logan"]          = safe(fetch_logan,          "Logan/KBOS")     or {}
    data["mbta_alerts"]    = safe(fetch_mbta,           "MBTA all lines") or []

    print("\n🏛️  City")
    data["revere_calendar"] = safe(fetch_revere_calendar, "Revere calendar") or []

    print("\n📺 Revere TV")
    channel_id = safe(fetch_revere_tv_channel_id, "Revere TV channel ID") or "UCq-Ej7V3_v7NuGUVRnqv8Aw"
    data["revere_tv_channel_id"] = channel_id
    data["revere_tv"] = safe(lambda: fetch_youtube(channel_id, 9), "Revere TV videos") or []

    print("\n📰 News")

    # Revere sources
    revere_official = safe(lambda: fetch_feed("https://www.revere.org/news/feed/rss", 20), "Revere.org RSS") or []
    for i in revere_official: i["source"] = "Revere.org"
    revere_journal = safe(lambda: fetch_feed("https://www.reverejournal.com/feed/", 20), "Revere Journal") or []
    for i in revere_journal: i["source"] = "Revere Journal"
    revere_gnews = safe(lambda: fetch_feed(
        "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US%3Aen&q=revere+ma", 15), "Google News Revere") or []
    for i in revere_gnews: i["source"] = "Google News"
    revere_fetchrss = safe(lambda: fetch_feed(
        "https://fetchrss.com/feed/1w57f09FJGjS1w57ef59e6GT.rss", 10), "FetchRSS Revere") or []
    for i in revere_fetchrss: i["source"] = "Revere Feed"
    data["news_revere"] = revere_official + revere_journal + revere_gnews + revere_fetchrss

    # Communities
    comm_sources = {
        "Chelsea":      "https://chelsearecord.com/feed/",
        "East Boston":  "https://eastietimes.com/feed/",
        "Lynn":         "https://www.itemlive.com/feed/",
        "Winthrop":     "https://winthroptranscript.com/feed/",
        "Saugus":       "https://saugusadvocate.com/feed/",
        "Everett":      "https://everettindependent.com/feed/",
        "Swampscott":   "https://swampscottreporter.com/feed/",
        "Marblehead":   "https://marbleheadreporter.com/feed/",
        "Peabody":      "https://peabodytimes.com/feed/",
        "Salem":        "https://www.salemnews.com/search/?f=rss&t=article&l=50&s=start_time&sd=desc",
    }
    data["news_communities"] = []
    for name, url in comm_sources.items():
        items = safe(lambda u=url: fetch_feed(u, 5), name) or []
        for i in items: i["source"] = name
        data["news_communities"].extend(items)

    # Boston — comprehensive source list with multiple URL attempts per source
    boston_sources = [
        # Boston Globe
        ("https://www.bostonglobe.com/rss/homepage",          "Boston Globe"),
        # Boston Herald
        ("https://bostonherald.com/feed/",                     "Boston Herald"),
        # WBZ-TV (CBS Boston) — try multiple URL formats
        ("https://www.cbsnews.com/boston/rss/",                "WBZ CBS Boston"),
        ("https://www.wbz.com/rss/",                           "WBZ News"),
        # WBUR — try multiple
        ("https://feeds.wbur.org/wburnews",                    "WBUR"),
        ("https://www.wbur.org/rss",                           "WBUR"),
        # WGBH / GBH
        ("https://www.wgbh.org/news/rss",                      "GBH News"),
        ("https://www.wgbh.org/rss/news",                      "GBH News"),
        # Other Boston TV
        ("https://www.wcvb.com/rss",                           "WCVB"),
        ("https://www.nbcboston.com/feed/",                    "NBC Boston"),
        ("https://whdh.com/feed/",                             "WHDH 7News"),
        # Print/digital
        ("https://www.masslive.com/arc/outboundfeeds/rss/?outputType=xml","MassLive"),
        ("https://www.bostonmagazine.com/feed/",               "Boston Magazine"),
    ]

    data["news_boston"] = []
    seen_urls = set()
    for url, label in boston_sources:
        items = safe(lambda u=url: fetch_feed(u, 6), label) or []
        for item in items:
            if item["link"] not in seen_urls:  # deduplicate by URL
                seen_urls.add(item["link"])
                item["source"] = label
                data["news_boston"].append(item)

    # Universal Hub
    uhub = []
    for uh_url in [
        "https://www.universalhub.com/node/feed",
        "https://www.universalhub.com/atom.xml",
        "https://www.universalhub.com/feed",
    ]:
        uhub = safe(lambda u=uh_url: fetch_feed(u, 30), f"UHub {uh_url}") or []
        if uhub:
            for i in uhub: i["source"] = "Universal Hub"
            break
    data["news_universalhub"] = uhub

    # Sports
    sports_sources = [
        ("https://www.bostonglobe.com/rss/sports",             "Globe Sports"),
        ("https://www.espn.com/espn/rss/boston/news",          "ESPN Boston"),
        ("https://nesn.com/feed/",                             "NESN"),
        ("https://feeds.wbur.org/wburnews",                    "WBUR Sports"),
        ("https://www.masslive.com/sports/arc/outboundfeeds/rss/?outputType=xml","MassLive Sports"),
        ("https://bostonherald.com/sports/feed/",              "Herald Sports"),
    ]
    data["news_sports"] = []
    seen_sports = set()
    for url, label in sports_sources:
        items = safe(lambda u=url: fetch_feed(u, 8), label) or []
        for item in items:
            if item["link"] not in seen_sports:
                seen_sports.add(item["link"])
                item["source"] = label
                data["news_sports"].append(item)

    # Boston College
    bc_sources = [
        ("https://bcheights.com/feed/",                        "The Heights"),
        ("https://bceagles.com/rss.aspx?path=mhockey",         "BC Hockey"),
        ("https://bceagles.com/rss.aspx",                      "BC Athletics"),
        ("https://247sports.com/college/boston-college/rss/",  "247Sports BC"),
        ("https://www.bcinterruption.com/rss/current.xml",     "BC Interruption"),
        ("https://www.si.com/college/boston-college/rss",      "SI Boston College"),
    ]
    data["news_bc"] = []
    seen_bc = set()
    for url, label in bc_sources:
        items = safe(lambda u=url: fetch_feed(u, 8), label) or []
        for item in items:
            if item["link"] not in seen_bc:
                seen_bc.add(item["link"])
                item["source"] = label
                data["news_bc"].append(item)

    # National
    national_sources = [
        ("https://feeds.npr.org/1001/rss.xml",                 "NPR"),
        ("https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml","NY Times"),
        ("https://feeds.bbci.co.uk/news/rss.xml",              "BBC"),
        ("https://apnews.com/hub/ap-top-news?format=feed&type=rss","AP"),
        ("https://thehill.com/feed/",                          "The Hill"),
        ("https://feeds.washingtonpost.com/rss/national",      "Washington Post"),
    ]
    data["news_national"] = []
    seen_nat = set()
    for url, label in national_sources:
        items = safe(lambda u=url: fetch_feed(u, 10), label) or []
        for item in items:
            if item["link"] not in seen_nat:
                seen_nat.add(item["link"])
                item["source"] = label
                data["news_national"].append(item)

    with open("data.json","w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"\n✅ Done — {data['updated_local']}")

if __name__ == "__main__":
    main()
