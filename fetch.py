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
    # Also grab the daily forecast for today's detailed text
    r2 = requests.get("https://api.weather.gov/gridpoints/BOX/68,89/forecast",
                      timeout=10, headers={"User-Agent": "RevereMonitor/6.0"})
    periods = r2.json()["properties"]["periods"]
    today = next((x for x in periods if x["isDaytime"]), periods[0])
    return {
        "temp": p["temperature"], "unit": p["temperatureUnit"],
        "wind": p["windSpeed"], "windDir": p.get("windDirection",""),
        "shortForecast": p["shortForecast"],
        "detailedForecast": today.get("detailedForecast",""),
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

    # Build a dict of date -> {day_period, night_period}
    from collections import defaultdict
    by_date = defaultdict(dict)
    for p in periods:
        date = p["startTime"][:10]  # YYYY-MM-DD
        if p["isDaytime"]:
            by_date[date]["day"] = p
        else:
            by_date[date]["night"] = p

    days = []
    for date in sorted(by_date.keys()):
        entry = by_date[date]
        day_p   = entry.get("day")
        night_p = entry.get("night")

        # Need at least a day or night period
        if not day_p and not night_p:
            continue

        # Use daytime period for name/forecast; fall back to night if no daytime
        primary = day_p or night_p
        name = primary["name"]

        # High = daytime temp, Low = nighttime temp
        high = day_p["temperature"] if day_p else None
        low  = night_p["temperature"] if night_p else None

        # If we only have tonight (no daytime), skip — it's a partial day
        if not day_p and low is not None:
            continue

        days.append({
            "name":             name,
            "date":             primary["startTime"],
            "high":             high,
            "low":              low,
            "shortForecast":    (day_p or night_p)["shortForecast"],
            "detailedForecast": (day_p or night_p).get("detailedForecast",""),
            "precip":           (day_p or night_p).get("probabilityOfPrecipitation",{}).get("value",0) or 0,
        })

        if len(days) == 5:
            break

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

# ── IQM2 MEETINGS (Revere's official meeting system) ─
def fetch_iqm2_meetings():
    """Scrape upcoming meetings from Revere's IQM2 system which has real dates."""
    r = requests.get(
        "https://reverema.iqm2.com/Citizens/Calendar.aspx",
        timeout=15, headers=HEADERS
    )
    soup = BeautifulSoup(r.text, "html.parser")
    meetings = []

    # IQM2 uses a table-based layout
    for row in soup.select("tr.MeetingRow, tr[class*='Meeting'], .calendarRow, table tr")[:20]:
        cols = row.find_all("td")
        if len(cols) < 2:
            continue
        text = row.get_text(separator=" ", strip=True)
        if len(text) < 5:
            continue
        link_el = row.find("a", href=True)
        link = ""
        if link_el:
            href = link_el["href"]
            link = href if href.startswith("http") else "https://reverema.iqm2.com" + href

        # Try to extract date from first column
        date_text = cols[0].get_text(strip=True) if cols else ""
        title_text = cols[1].get_text(strip=True) if len(cols) > 1 else text

        if title_text and len(title_text) > 3:
            meetings.append({
                "title": title_text,
                "date":  date_text,
                "time":  "",
                "link":  link or "https://reverema.iqm2.com/Citizens/Calendar.aspx",
            })

    # Fallback: look for any date-like text near links
    if not meetings:
        for a in soup.find_all("a", href=True)[:20]:
            text = a.get_text(strip=True)
            if len(text) > 8 and ("meeting" in text.lower() or "committee" in text.lower() or
                                   "board" in text.lower() or "council" in text.lower()):
                href = a["href"]
                full = href if href.startswith("http") else "https://reverema.iqm2.com" + href
                meetings.append({"title": text, "date": "", "time": "", "link": full})

    print(f"  {'✓' if meetings else '⚠'} IQM2: {len(meetings)} meetings")
    return meetings[:12]

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

    # IQM2 — Revere's official meeting/agenda system, has structured RSS with dates
    data["iqm2_meetings"] = safe(fetch_iqm2_meetings, "IQM2 meetings") or []

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

    # Communities — local RSS feeds + Google News for each town as fallback
    # Each town gets its local paper (if one exists) plus a Google News search
    comm_rss = {
        "Chelsea":      "https://chelsearecord.com/feed/",
        "East Boston":  "https://eastietimes.com/feed/",
        "Lynn":         "https://www.itemlive.com/feed/",
        "Winthrop":     "https://winthroptranscript.com/feed/",
        "Saugus":       "https://saugusadvocate.com/feed/",
        "Everett":      "https://everettindependent.com/feed/",
        "Swampscott":   "https://swampscottreporter.com/feed/",
        "Marblehead":   "https://marbleheadreporter.com/feed/",
        "Peabody":      "https://peabodytimes.com/feed/",
        "Salem":        "https://www.salemnews.com/rss/",
        "Malden":       "https://maldenobserver.com/feed/",
        "Melrose":      "https://melrosefreepress.com/feed/",
    }
    # Google News search per town — reliable fallback when local paper RSS is down
    comm_gnews_towns = [
        "Chelsea MA", "East Boston MA", "Lynn MA", "Winthrop MA",
        "Saugus MA", "Everett MA", "Swampscott MA", "Marblehead MA",
        "Peabody MA", "Salem MA", "Malden MA", "Melrose MA",
    ]

    data["news_communities"] = []
    seen_comm = set()

    # First pass: local RSS feeds
    for name, url in comm_rss.items():
        items = safe(lambda u=url: fetch_feed(u, 4), name) or []
        for item in items:
            link = item.get("link","")
            if link and link not in seen_comm:
                seen_comm.add(link)
                item["source"] = name
                data["news_communities"].append(item)

    # Second pass: Google News per town (catches anything the local RSS missed)
    for town in comm_gnews_towns:
        q = town.replace(" ", "+")
        gnews_url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
        items = safe(lambda u=gnews_url: fetch_feed(u, 3), f"GNews {town}") or []
        town_label = town.replace(" MA", "")
        for item in items:
            link = item.get("link","")
            if link and link not in seen_comm:
                seen_comm.add(link)
                item["source"] = town_label
                data["news_communities"].append(item)

    # Boston — try multiple URLs per source, use Google News as fallback for Globe
    boston_sources = [
        # Boston Globe — try direct RSS first, then Google News search as fallback
        ("https://www.bostonglobe.com/rss/homepage",           "Boston Globe"),
        ("https://news.google.com/rss/search?q=site:bostonglobe.com&hl=en-US&gl=US&ceid=US:en", "Boston Globe"),
        # Google News Boston topic page — broad Boston news aggregation
        ("https://news.google.com/topics/CAAqIQgKIhtDQkFTRGdvSUwyMHZNREZqZUY4U0FtVnVLQUFQAQ?hl=en-US&gl=US&ceid=US%3Aen&output=rss", "Google News Boston"),
        # Boston Herald
        ("https://bostonherald.com/feed/",                     "Boston Herald"),
        # WBUR
        ("https://feeds.wbur.org/wburnews",                    "WBUR"),
        ("https://www.wbur.org/rss/news",                      "WBUR"),
        # WGBH / GBH
        ("https://www.wgbh.org/news/rss",                      "GBH News"),
        # WBZ CBS Boston
        ("https://www.cbsnews.com/boston/rss/",                "WBZ/CBS Boston"),
        # Other Boston TV
        ("https://www.wcvb.com/rss",                           "WCVB"),
        ("https://www.nbcboston.com/feed/",                    "NBC Boston"),
        ("https://whdh.com/feed/",                             "WHDH 7News"),
        # Digital/print
        ("https://www.masslive.com/arc/outboundfeeds/rss/?outputType=xml", "MassLive"),
        ("https://www.bostonmagazine.com/feed/",               "Boston Magazine"),
    ]

    data["news_boston"] = []
    seen_urls = set()
    source_counts = {}
    for url, label in boston_sources:
        if source_counts.get(label, 0) >= 6:
            continue
        items = safe(lambda u=url: fetch_feed(u, 8), label) or []
        for item in items:
            link = item.get("link","")
            if link and link not in seen_urls:
                seen_urls.add(link)
                item["source"] = label
                data["news_boston"].append(item)
                source_counts[label] = source_counts.get(label, 0) + 1

    # Universal Hub — try multiple feed approaches with proper headers
    uhub = []

    # feedparser with explicit Accept header sometimes fixes stale Drupal RSS
    for uh_url in [
        "https://www.universalhub.com/recent/feed",
        "https://universalhub.com/recent/feed",
        "https://www.universalhub.com/node/feed",
        "https://universalhub.com/node/feed",
        "https://www.universalhub.com/atom.xml",
        "https://universalhub.com/atom.xml",
    ]:
        try:
            # Use requests to fetch with proper headers, then parse content
            resp = requests.get(uh_url, timeout=12, headers={
                "User-Agent": "Mozilla/5.0 (compatible; FeedFetcher/1.0)",
                "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
            })
            if resp.status_code == 200 and len(resp.content) > 500:
                feed = feedparser.parse(resp.content)
                if feed.entries:
                    now_ts = __import__('time').time()
                    week_ago = now_ts - (7 * 24 * 3600)
                    for e in feed.entries[:30]:
                        ts = 0
                        if hasattr(e, "published_parsed") and e.published_parsed:
                            try: ts = calendar.timegm(e.published_parsed)
                            except: pass
                        # Only include items from the last 7 days if we have timestamps
                        if ts > 0 and ts < week_ago:
                            continue
                        uhub.append({
                            "title":     getattr(e, "title", ""),
                            "link":      getattr(e, "link", ""),
                            "published": getattr(e, "published", ""),
                            "ts":        ts,
                            "source":    "Universal Hub",
                        })
                    if uhub:
                        print(f"    ✓ Universal Hub: {len(uhub)} items from {uh_url}")
                        break
        except Exception as ex:
            print(f"    ✗ UHub {uh_url}: {ex}")
            continue

    # Fallback: scrape homepage, grab only links from main content area
    if not uhub:
        try:
            resp = requests.get("https://www.universalhub.com", timeout=15, headers=HEADERS)
            soup = BeautifulSoup(resp.text, "html.parser")
            # Target only main content — avoid sidebar, nav, footer
            main = soup.select_one("main, #main, .main-container, #content, .content, .region-content")
            search_area = main if main else soup
            seen_h = set()
            for a in search_area.select("h2 a, h3 a, h4 a, .node-title a")[:20]:
                text = a.get_text(strip=True)
                href = a.get("href", "")
                if len(text) > 10 and href and text not in seen_h:
                    seen_h.add(text)
                    full = href if href.startswith("http") else "https://www.universalhub.com" + href
                    # Only include universalhub.com links, not external
                    if "universalhub.com" in full or full.startswith("/"):
                        uhub.append({"title": text, "link": full,
                                     "published": "", "ts": 0, "source": "Universal Hub"})
            print(f"    {'✓' if uhub else '✗'} UHub homepage scrape: {len(uhub)} items")
        except Exception as e:
            print(f"    ✗ UHub homepage failed: {e}")

    # Final fallback: Google News
    if not uhub:
        uhub = safe(lambda: fetch_feed(
            "https://news.google.com/rss/search?q=site:universalhub.com&hl=en-US&gl=US&ceid=US:en", 20
        ), "UHub via Google News") or []
        for i in uhub: i["source"] = "Universal Hub"

    data["news_universalhub"] = uhub

    # Sports — Boston teams + The Athletic + Google News per team
    sports_sources = [
        # Boston media
        ("https://www.bostonglobe.com/rss/sports",             "Globe Sports"),
        ("https://www.espn.com/espn/rss/boston/news",          "ESPN Boston"),
        ("https://nesn.com/feed/",                             "NESN"),
        ("https://feeds.wbur.org/wburnews",                    "WBUR Sports"),
        ("https://www.masslive.com/sports/arc/outboundfeeds/rss/?outputType=xml","MassLive Sports"),
        ("https://bostonherald.com/sports/feed/",              "Herald Sports"),
        # The Athletic — Boston teams
        ("https://theathletic.com/boston-bruins/feed/",        "The Athletic · Bruins"),
        ("https://theathletic.com/boston-celtics/feed/",       "The Athletic · Celtics"),
        ("https://theathletic.com/boston-red-sox/feed/",       "The Athletic · Red Sox"),
        ("https://theathletic.com/new-england-patriots/feed/", "The Athletic · Patriots"),
        # The Athletic — league-wide
        ("https://theathletic.com/nhl/feed/",                  "The Athletic · NHL"),
        ("https://theathletic.com/college-football/feed/",     "The Athletic · CFB"),
        # Google News per Boston team
        ("https://news.google.com/rss/search?q=Boston+Bruins&hl=en-US&gl=US&ceid=US:en",        "Google News · Bruins"),
        ("https://news.google.com/rss/search?q=Boston+Celtics&hl=en-US&gl=US&ceid=US:en",       "Google News · Celtics"),
        ("https://news.google.com/rss/search?q=Boston+Red+Sox&hl=en-US&gl=US&ceid=US:en",       "Google News · Red Sox"),
        ("https://news.google.com/rss/search?q=New+England+Patriots&hl=en-US&gl=US&ceid=US:en", "Google News · Patriots"),
        ("https://news.google.com/rss/search?q=New+England+Revolution+MLS&hl=en-US&gl=US&ceid=US:en", "Google News · Revolution"),
    ]
    data["news_sports"] = []
    seen_sports = set()
    for url, label in sports_sources:
        items = safe(lambda u=url: fetch_feed(u, 6), label) or []
        for item in items:
            link = item.get("link","")
            if link and link not in seen_sports:
                seen_sports.add(link)
                item["source"] = label
                data["news_sports"].append(item)

    # Boston College — 247Sports and SI block RSS scrapers, use Google News instead
    bc_sources = [
        ("https://bcheights.com/feed/",                                                                      "The Heights"),
        ("https://www.bcinterruption.com/rss/current.xml",                                                   "BC Interruption"),
        ("https://bceagles.com/rss.aspx?path=mhockey",                                                      "BC Hockey"),
        ("https://bceagles.com/rss.aspx",                                                                    "BC Athletics"),
        # Google News searches for 247Sports and SI BC coverage (reliable workaround)
        ("https://news.google.com/rss/search?q=247sports+boston+college&hl=en-US&gl=US&ceid=US:en",         "247Sports BC"),
        ("https://news.google.com/rss/search?q=%22sports+illustrated%22+%22boston+college%22&hl=en-US&gl=US&ceid=US:en", "SI Boston College"),
        ("https://news.google.com/rss/search?q=boston+college+eagles+football+basketball&hl=en-US&gl=US&ceid=US:en",     "Google News BC"),
    ]
    data["news_bc"] = []
    seen_bc = set()
    for url, label in bc_sources:
        items = safe(lambda u=url: fetch_feed(u, 8), label) or []
        for item in items:
            link = item.get("link","")
            if link and link not in seen_bc:
                seen_bc.add(link)
                item["source"] = label
                data["news_bc"].append(item)

    # College Hockey News — confirmed working feed URLs
    college_hockey_sources = [
        ("https://www.uscho.com/feed",                                   "USCHO"),
        ("https://www.collegehockeynews.com/news/xml/newsfeed.xml",      "CHN"),
        ("https://www.collegehockeyinsider.com/feed",                    "CHI"),
        ("https://bchockeyblog.substack.com/feed",                       "BC Hockey Blog"),
        # Google News as supplemental coverage
        ("https://news.google.com/rss/search?q=%22college+hockey%22&hl=en-US&gl=US&ceid=US:en", "Google News · College Hockey"),
        ("https://news.google.com/rss/search?q=%22BC+hockey%22+OR+%22Boston+College+hockey%22&hl=en-US&gl=US&ceid=US:en", "Google News · BC Hockey"),
    ]
    data["news_college_hockey"] = []
    seen_chockey = set()
    for url, label in college_hockey_sources:
        items = safe(lambda u=url: fetch_feed(u, 10), label) or []
        for item in items:
            link = item.get("link", "")
            if link and link not in seen_chockey:
                seen_chockey.add(link)
                item["source"] = label
                data["news_college_hockey"].append(item)

    # National — added CNN
    national_sources = [
        ("https://feeds.npr.org/1001/rss.xml",                                  "NPR"),
        ("https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",           "NY Times"),
        ("https://feeds.bbci.co.uk/news/rss.xml",                               "BBC"),
        ("https://apnews.com/hub/ap-top-news?format=feed&type=rss",             "AP"),
        ("https://rss.cnn.com/rss/edition.rss",                                 "CNN"),
        ("https://thehill.com/feed/",                                            "The Hill"),
        ("https://feeds.washingtonpost.com/rss/national",                       "Washington Post"),
    ]
    data["news_national"] = []
    seen_nat = set()
    for url, label in national_sources:
        items = safe(lambda u=url: fetch_feed(u, 10), label) or []
        for item in items:
            link = item.get("link","")
            if link and link not in seen_nat:
                seen_nat.add(link)
                item["source"] = label
                data["news_national"].append(item)

    # Logan Airport news — Google News search
    logan_news = safe(lambda: fetch_feed(
        'https://news.google.com/rss/search?q=%22logan+airport%22&hl=en-US&gl=US&ceid=US:en', 15
    ), "Logan Airport news") or []
    for i in logan_news: i["source"] = "Google News"
    data["news_logan"] = logan_news

    with open("data.json","w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"\n✅ Done — {data['updated_local']}")

if __name__ == "__main__":
    main()
