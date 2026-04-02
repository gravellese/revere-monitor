# -*- coding: utf-8 -*-
#!/usr/bin/env python3
"""Revere Monitor v6 -- fetch.py"""

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

# ── WEATHER ──────────────────────────────────────────
def fetch_weather_current():
    r = requests.get("https://api.weather.gov/gridpoints/BOX/68,89/forecast/hourly",
                     timeout=10, headers={"User-Agent": "RevereMonitor/6.0"})
    p = r.json()["properties"]["periods"][0]
    r2 = requests.get("https://api.weather.gov/gridpoints/BOX/68,89/forecast",
                      timeout=10, headers={"User-Agent": "RevereMonitor/6.0"})
    periods = r2.json()["properties"]["periods"]
    today = next((x for x in periods if x["isDaytime"]), periods[0])
    return {
        "temp": p["temperature"], "unit": p["temperatureUnit"],
        "wind": p["windSpeed"], "windDir": p.get("windDirection", ""),
        "shortForecast": p["shortForecast"],
        "detailedForecast": today.get("detailedForecast", ""),
        "humidity": p.get("relativeHumidity", {}).get("value"),
        "precip": p.get("probabilityOfPrecipitation", {}).get("value", 0) or 0,
    }

def fetch_weather_hourly():
    r = requests.get("https://api.weather.gov/gridpoints/BOX/68,89/forecast/hourly",
                     timeout=10, headers={"User-Agent": "RevereMonitor/6.0"})
    return [{
        "time": p["startTime"], "temp": p["temperature"],
        "unit": p["temperatureUnit"], "shortForecast": p["shortForecast"],
        "wind": p["windSpeed"],
        "precip": p.get("probabilityOfPrecipitation", {}).get("value", 0) or 0,
    } for p in r.json()["properties"]["periods"][:24]]

def fetch_weather_daily():
    r = requests.get("https://api.weather.gov/gridpoints/BOX/68,89/forecast",
                     timeout=10, headers={"User-Agent": "RevereMonitor/6.0"})
    periods = r.json()["properties"]["periods"]
    from collections import defaultdict
    by_date = defaultdict(dict)
    for p in periods:
        d = p["startTime"][:10]
        if p["isDaytime"]:
            by_date[d]["day"] = p
        else:
            by_date[d]["night"] = p
    days = []
    for d in sorted(by_date.keys()):
        entry = by_date[d]
        day_p = entry.get("day")
        night_p = entry.get("night")
        if not day_p and not night_p:
            continue
        primary = day_p or night_p
        high = day_p["temperature"] if day_p else None
        low = night_p["temperature"] if night_p else None
        if not day_p and low is not None:
            continue
        days.append({
            "name": primary["name"],
            "date": primary["startTime"],
            "high": high, "low": low,
            "shortForecast": (day_p or night_p)["shortForecast"],
            "detailedForecast": (day_p or night_p).get("detailedForecast", ""),
            "precip": (day_p or night_p).get("probabilityOfPrecipitation", {}).get("value", 0) or 0,
        })
        if len(days) == 7:
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
            for p in r.json().get("predictions", [])]

# ── LOGAN ─────────────────────────────────────────────
def fetch_logan():
    result = {
        "name": "Boston Logan (KBOS)",
        "delay": False, "status": "ok",
        "metar": None, "taf": None, "faa_delays": [],
    }
    try:
        r = requests.get(
            "https://soa.smext.faa.gov/asws/api/airport/status/BOS",
            timeout=8, headers={"Accept": "application/json", "User-Agent": "RevereMonitor/6.0"})
        if r.status_code == 200:
            d = r.json()
            result["delay"] = d.get("Delay", False)
            for x in d.get("ArriveDepartDelay", []):
                result["faa_delays"].append({"type": x.get("Type", ""), "reason": x.get("Reason", ""), "avg": x.get("Avg", ""), "trend": x.get("Trend", "")})
            for x in d.get("GroundDelay", []):
                result["faa_delays"].append({"type": "Ground Delay", "reason": x.get("Reason", ""), "avg": x.get("Avg", "")})
            for x in d.get("GroundStop", []):
                result["faa_delays"].append({"type": "Ground Stop", "reason": x.get("Reason", ""), "avg": x.get("EndTime", "")})
    except Exception as e:
        print(f"    FAA API skipped: {e}")
    try:
        r2 = requests.get(
            "https://aviationweather.gov/api/data/metar?ids=KBOS&format=json&hours=1",
            timeout=10, headers={"User-Agent": "RevereMonitor/6.0"})
        if r2.status_code == 200:
            md = r2.json()
            if md:
                m = md[0]
                wdir = m.get("wdir", "")
                wspd = m.get("wspd", "")
                wgst = m.get("wgst", "")
                wind_str = f"{wdir}° @ {wspd} kts"
                if wgst:
                    wind_str += f" gusting {wgst} kts"
                result["metar"] = {
                    "raw": m.get("rawOb", ""), "wind": wind_str,
                    "visibility": f"{m.get('visib', '')} SM",
                    "sky": m.get("skyCondition", ""),
                    "temp_c": m.get("temp", ""), "dewpoint": m.get("dewp", ""),
                    "altimeter": m.get("altim", ""), "wx": m.get("wxString", ""),
                    "obs_time": m.get("obsTime", ""), "flight_cat": m.get("flightCategory", ""),
                }
    except Exception as e:
        print(f"    METAR fetch failed: {e}")
    try:
        r3 = requests.get(
            "https://aviationweather.gov/api/data/taf?ids=KBOS&format=json",
            timeout=10, headers={"User-Agent": "RevereMonitor/6.0"})
        if r3.status_code == 200:
            td = r3.json()
            if td:
                result["taf"] = td[0].get("rawTAF", "")
    except Exception as e:
        print(f"    TAF fetch failed: {e}")
    return result

# ── MBTA ALL LINES ────────────────────────────────────
def fetch_mbta():
    r = requests.get(
        "https://api-v3.mbta.com/alerts?filter[activity]=BOARD,EXIT,RIDE&filter[route_type]=0,1,2",
        timeout=10)
    alerts = []
    for a in r.json().get("data", [])[:20]:
        attrs = a["attributes"]
        routes = []
        for entity in attrs.get("informed_entities", []):
            rt = entity.get("route", "")
            if rt and rt not in routes:
                routes.append(rt)
        alerts.append({"header": attrs["header"], "effect": attrs["effect"], "routes": routes[:3]})
    return alerts

# ── REVERE CITY CALENDAR ──────────────────────────────
def fetch_revere_calendar():
    from datetime import date as _date, datetime as _dt
    today = _date.today()
    today_ts = int(_dt.combine(today, _dt.min.time()).timestamp())
    events = []
    seen_ids = set()
    months_to_fetch = []
    for delta in range(3):
        m = ((today.month - 1 + delta) % 12) + 1
        y = today.year + ((today.month - 1 + delta) // 12)
        months_to_fetch.append((m, y))
    for month, year in months_to_fetch:
        month_url = f"https://www.revere.org/calendar/view/month/m/{month}/d/1/y/{year}/v/grid"
        try:
            r = requests.get(month_url, headers=HEADERS, timeout=15)
            r.raise_for_status()
        except Exception as e:
            print(f"    [revere_cal] month {month}/{year} failed: {e}")
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        pre_blocks = soup.find_all("pre")
        print(f"    [revere_cal] {month}/{year}: {len(pre_blocks)} event blocks found")
        combined = "\n\n".join(pre.get_text() for pre in pre_blocks)
        events.extend(_parse_civicplus_events(combined, seen_ids))
    events = [e for e in events if e.get("ts", 0) >= today_ts]
    events.sort(key=lambda e: e.get("ts", 0))
    print(f"  {'✓' if events else '⚠'} Revere calendar: {len(events)} upcoming events")
    return events

def _parse_civicplus_events(html, seen_ids):
    from datetime import datetime as _dt
    events = []
    raw = html

    def _all(pattern):
        return {m.start(): m.group(1).strip() for m in re.finditer(pattern, raw)}

    def _nearest(pos_dict, pos, window=700):
        candidates = [(abs(k - pos), v) for k, v in pos_dict.items() if abs(k - pos) < window]
        return min(candidates, key=lambda x: x[0])[1] if candidates else ""

    titles = [(m.start(), m.group(1).strip()) for m in re.finditer(r'\[title\]\s*=>\s*([^\[\n\r]+)', raw)]
    ids = _all(r'\[id\]\s*=>\s*(\d{4,})')
    timestamps = {m.start(): int(m.group(1)) for m in re.finditer(r'\[startDateTimestamp\]\s*=>\s*(\d+)', raw)}
    locations = _all(r'\[location\]\s*=>\s*([^\[\n\r]+)')
    event_urls = _all(r'\[url\]\s*=>\s*(/calendar/event/[^\[\n\r]+)')
    time_starts = _all(r'\[start\]\s*=>\s*(\d+:\d+\s*[AP]M)')
    time_ends = _all(r'\[end\]\s*=>\s*(\d+:\d+\s*[AP]M)')
    categories = _all(r'\[categoryName\]\s*=>\s*([^\[\n\r]+)')
    SKIP_TITLES = {"Public Meeting", "Events", "City Calendar", "Holiday", "Special Event"}

    for pos, title in titles:
        if not title or len(title) < 4 or title in SKIP_TITLES:
            continue
        ts = _nearest(timestamps, pos, 600)
        if not ts:
            continue
        ts_int = int(ts)
        ev_id = _nearest(ids, pos, 600)
        if ev_id and ev_id in seen_ids:
            continue
        if ev_id:
            seen_ids.add(ev_id)
        try:
            dt = _dt.fromtimestamp(ts_int)
        except Exception:
            continue
        loc = _nearest(locations, pos, 800)
        ev_url = _nearest(event_urls, pos, 800)
        t_st = _nearest(time_starts, pos, 800)
        t_en = _nearest(time_ends, pos, 800)
        cat = _nearest(categories, pos, 600)
        loc = re.sub(r'<br\s*/?>', ' · ', loc, flags=re.I).strip()
        time_str = ""
        if t_st:
            time_str = t_st + ("–" + t_en if t_en and t_en != t_st else "")
        events.append({
            "date": dt.strftime("%Y-%m-%d"),
            "date_fmt": dt.strftime("%a, %b %-d"),
            "ts": ts_int,
            "time": time_str or None,
            "all_day": not bool(time_str),
            "summary": title,
            "location": loc[:60] if loc else None,
            "category": cat or "City Event",
            "url": ("https://www.revere.org" + ev_url.strip()) if ev_url else "https://www.revere.org/calendar",
            "calendar": "City",
        })
    return events

# ── REVERE TV ─────────────────────────────────────────
def fetch_revere_tv_channel_id():
    for url in ["https://www.youtube.com/@reveretv", "https://www.youtube.com/user/reveretv"]:
        try:
            r = requests.get(url, timeout=10, headers=HEADERS)
            match = re.search(r'"channelId":"(UC[^"]{20,})"', r.text)
            if match:
                return match.group(1)
        except:
            continue
    return "UCq-Ej7V3_v7NuGUVRnqv8Aw"

def fetch_youtube(channel_id, max_items=9):
    feed = feedparser.parse(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")
    items = []
    for e in feed.entries[:max_items]:
        vid = getattr(e, "yt_videoid", "") or ""
        if not vid and "v=" in getattr(e, "link", ""):
            vid = e.link.split("v=")[-1].split("&")[0]
        ts = 0
        if hasattr(e, "published_parsed") and e.published_parsed:
            try:
                ts = calendar.timegm(e.published_parsed)
            except:
                pass
        items.append({
            "title": getattr(e, "title", ""), "video_id": vid,
            "link": getattr(e, "link", ""), "published": getattr(e, "published", ""),
            "ts": ts,
            "thumbnail": f"https://img.youtube.com/vi/{vid}/mqdefault.jpg" if vid else "",
        })
    return items

# ── RSS FEED ──────────────────────────────────────────
def fetch_feed(url, max_items=30):
    try:
        feed = feedparser.parse(url)
        items = []
        for e in feed.entries[:max_items]:
            ts = 0
            if hasattr(e, "published_parsed") and e.published_parsed:
                try:
                    ts = calendar.timegm(e.published_parsed)
                except:
                    pass
            elif hasattr(e, "updated_parsed") and e.updated_parsed:
                try:
                    ts = calendar.timegm(e.updated_parsed)
                except:
                    pass
            if ts == 0:
                raw = getattr(e, "published", "") or getattr(e, "updated", "")
                if raw:
                    try:
                        from email.utils import parsedate_to_datetime
                        ts = int(parsedate_to_datetime(raw).timestamp())
                    except:
                        try:
                            from datetime import datetime
                            for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z",
                                        "%Y-%m-%dT%H:%M:%SZ", "%a, %d %b %Y %H:%M:%S +0000"):
                                try:
                                    ts = int(datetime.strptime(raw[:len(fmt)], fmt).timestamp())
                                    break
                                except:
                                    pass
                        except:
                            pass
            items.append({
                "title": getattr(e, "title", ""),
                "link": getattr(e, "link", ""),
                "published": getattr(e, "published", ""),
                "ts": ts,
                "author": getattr(e, "author", ""),
                "feed_title": getattr(e.source, "title", "") if hasattr(e, "source") and e.source else "",
                "summary": (
                    (e.content[0].value if hasattr(e, "content") and e.content else "")
                    or getattr(e, "summary", "")
                    or getattr(e, "description", "")
                ),
            })
        return items
    except Exception as e:
        return []

# ── IQM2 MEETINGS ─────────────────────────────────────
def fetch_iqm2_meetings():
    r = requests.get("https://reverema.iqm2.com/Citizens/Calendar.aspx", timeout=15, headers=HEADERS)
    soup = BeautifulSoup(r.text, "html.parser")
    meetings = []
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
        date_text = cols[0].get_text(strip=True) if cols else ""
        title_text = cols[1].get_text(strip=True) if len(cols) > 1 else text
        if title_text and len(title_text) > 3:
            meetings.append({"title": title_text, "date": date_text, "time": "",
                             "link": link or "https://reverema.iqm2.com/Citizens/Calendar.aspx"})
    if not meetings:
        for a in soup.find_all("a", href=True)[:20]:
            text = a.get_text(strip=True)
            if len(text) > 8 and any(w in text.lower() for w in ["meeting", "committee", "board", "council"]):
                href = a["href"]
                full = href if href.startswith("http") else "https://reverema.iqm2.com" + href
                meetings.append({"title": text, "date": "", "time": "", "link": full})
    print(f"  {'✓' if meetings else '⚠'} IQM2: {len(meetings)} meetings")
    return meetings[:12]

# ── PERSONAL CALENDAR ─────────────────────────────────
def fetch_personal_calendar():
    from datetime import date, timedelta, datetime as _dt
    from zoneinfo import ZoneInfo
    import re as _re

    try:
        from icalendar import Calendar as iCal
    except ImportError:
        print("  ✗ icalendar not installed")
        return []

    try:
        import recurring_ical_events
    except ImportError:
        print("  ✗ recurring-ical-events not installed")
        return []

    PERSONAL_CALS = [
        ("Joseph",  "https://calendar.google.com/calendar/ical/gravellese%40gmail.com/private-f7d5ed600f87f0f696c1afd76fb0cb1e/basic.ics"),
        ("Wesley",  "https://calendar.google.com/calendar/ical/49304c3e8f536ae830a6357b1e913aa895693f4565adec7a611dc95eac9961d5%40group.calendar.google.com/private-f5988f3e5bb13da18482519c327d463e/basic.ics"),
        ("Todoist", "https://calendar.google.com/calendar/ical/472e59defe9def3c4f1c8539c4f2ba0db7f21d8d2dd420a73175a82b8c5ed927%40group.calendar.google.com/private-2fc84c690aafe491aede4fa26f43c4bb/basic.ics"),
    ]

    today = date.today()
    future_cutoff = today + timedelta(days=14)
    ET = ZoneInfo("America/New_York")

    # Must be timezone-aware — Google ICS events have TZID set, comparing against
    # naive datetimes throws TypeError and silently kills each calendar fetch.
    range_start = _dt.combine(today, _dt.min.time(), tzinfo=ET)
    range_end   = _dt.combine(future_cutoff, _dt.max.time(), tzinfo=ET)

    events = []
    for cal_name, url in PERSONAL_CALS:
        try:
            r = requests.get(url, timeout=15, headers={"User-Agent": "RevereMonitor/6.0"})
            if r.status_code != 200:
                print(f"    ✗ {cal_name}: HTTP {r.status_code}")
                continue
            cal = iCal.from_ical(r.content)
            occurrences = recurring_ical_events.of(cal).between(range_start, range_end)
            count = 0
            for comp in occurrences:
                dtstart = comp.get('DTSTART')
                if not dtstart:
                    continue
                dt = dtstart.dt
                all_day = not hasattr(dt, 'hour')
                if all_day:
                    event_date = dt
                    time_str = None
                else:
                    try:
                        et = dt.astimezone(ET)
                        event_date = et.date()
                        time_str = et.strftime("%-I:%M %p")
                    except Exception:
                        event_date = dt.date()
                        time_str = dt.strftime("%I:%M %p").lstrip('0')
                summary = str(comp.get('SUMMARY', '') or '').strip()
                location = str(comp.get('LOCATION', '') or '').strip()
                if not summary:
                    continue
                location = _re.sub(r'https?://\S+', '', location).strip()
                location = location[:50] if location else None
                events.append({
                    "date": event_date.isoformat(),
                    "time": time_str,
                    "all_day": all_day,
                    "summary": summary,
                    "location": location,
                    "calendar": cal_name,
                })
                count += 1
            print(f"    ✓ {cal_name}: {count} upcoming events (incl. recurrences)")
        except Exception as e:
            print(f"    ✗ {cal_name}: {e}")

    events.sort(key=lambda x: (x["date"], x["time"] or "00:00"))
    return events

# ── SPORTS SCHEDULE ───────────────────────────────────
def fetch_sports_schedule():
    import urllib.parse
    import re as re_mod
    from datetime import date, timedelta
    try:
        from icalendar import Calendar as iCal
    except ImportError:
        print("  ✗ icalendar not installed")
        return []

    today = date.today()
    past_cutoff = today - timedelta(days=21)
    future_cutoff = today + timedelta(days=60)

    CAL_IDS = {
        "bruins":      "nhl_-m-0j2zj_%42oston+%42ruins#sports@group.v.calendar.google.com",
        "sox":         "mlb_-m-01d5z_%42oston+%52ed+%53ox#sports@group.v.calendar.google.com",
        "lfc":         "dpqj0f4137m5brcrar1jn1vs64@group.calendar.google.com",
        "fleet":       "nudcgn5li9mrusdhdd4h8abbj0@group.calendar.google.com",
        "legacy":      "elgb6vhfucpe38fhv6febj5l3etfbhmm@import.calendar.google.com",
        "bcbase":      "avkc7pucr075rp0ehvqbu1t5rlkku15r@import.calendar.google.com",
        "bclax":       "ne97hfv8h2chq71lb7vgrhc8oeenkqo7@import.calendar.google.com",
        "nascar":      "7e497131cc76c86d4aa976986c8b17eb5c3ecb8555a7dcf00cf1b26138ea3431@group.calendar.google.com",
        "f1":          "4bu98arvuq4clcir4oqq74v8j8udo0dk@import.calendar.google.com",
        "ncaa_hockey": "4591a3673990b40870a5eea863bc4ecb27817dcf8cd1353c10a5fddc682c4659@group.calendar.google.com",
        "worldcup":    "3hq899li0lh09cfs1h4bqdsdjs@group.calendar.google.com",
        "usmnt":       "oib25ejldudu51vruaqvv2f8f8@group.calendar.google.com",
        "uswnt":       "arpnl5b3behv8j9s9lfh32meug@group.calendar.google.com",
        "revolution":  "8tnqel2hvmh8csqlmpira01500@group.calendar.google.com",
        "italy":       "0eejc68fc0shdcks3bk0sh44mg@group.calendar.google.com",
        "navigators":  "e455dca9fc4667821e23c4fc555bb7d631cd26148b8d3269561a8d4f6607f2fa@group.calendar.google.com",
        "watchlist":   "df6346a0c4db83dfa688f28aba800e40ba131190994a61dfbc3de835ec2c3c7f@group.calendar.google.com",
    }

    events = []
    for team, cal_id in CAL_IDS.items():
        encoded = urllib.parse.quote(cal_id, safe='%_+-.')
        url = f"https://calendar.google.com/calendar/ical/{encoded}/public/basic.ics"
        try:
            r = requests.get(url, timeout=15, headers={"User-Agent": "RevereMonitor/6.0"})
            if r.status_code != 200:
                print(f"    ✗ {team}: HTTP {r.status_code}")
                continue
            cal = iCal.from_ical(r.content)
            count = 0
            for comp in cal.walk():
                if comp.name != 'VEVENT':
                    continue
                dtstart = comp.get('DTSTART')
                if not dtstart:
                    continue
                dt = dtstart.dt
                if hasattr(dt, 'date'):
                    try:
                        from zoneinfo import ZoneInfo
                        et = dt.astimezone(ZoneInfo("America/New_York"))
                        event_date = et.date()
                        time_str = et.strftime("%-I:%M %p")
                    except Exception:
                        event_date = dt.date()
                        time_str = dt.strftime("%I:%M %p").lstrip('0')
                else:
                    event_date = dt
                    time_str = ""
                if event_date < past_cutoff or event_date > future_cutoff:
                    continue
                summary = str(comp.get('SUMMARY', '') or '').strip()
                location = str(comp.get('LOCATION', '') or '').strip()
                description = str(comp.get('DESCRIPTION', '') or '').strip()
                location = re_mod.sub(r'\n.*', '', location)
                location = re_mod.sub(r'https?://\S+', '', location).strip()
                location = location[:45] if location else None
                result = None
                score = None
                special = None
                if team in ('bruins', 'sox'):
                    home = "Bruins" if team == 'bruins' else "Red Sox"
                    if event_date < today and home in summary:
                        parts = summary.split('@')
                        if len(parts) == 2:
                            ls = re_mod.search(r'\((\d+)\)', parts[0])
                            rs = re_mod.search(r'\((\d+)\)', parts[1])
                            if ls and rs:
                                if home in parts[0]:
                                    our, their = int(ls.group(1)), int(rs.group(1))
                                else:
                                    our, their = int(rs.group(1)), int(ls.group(1))
                                score = f"{our}-{their}"
                                result = "W" if our > their else ("L" if our < their else None)
                if team in ('bcbase', 'bclax'):
                    m = re_mod.match(r'^\[([WL])\]', summary)
                    if m:
                        result = m.group(1)
                        sm = re_mod.search(r'[WL]\s+(\d+-\d+)', description)
                        if sm:
                            score = sm.group(1)
                    summary = re_mod.sub(r'^\[.\]\s*', '', summary)
                actual_team = team
                if team == 'nascar':
                    blob = (summary + description).lower()
                    if 'cup' in blob:
                        actual_team = 'nascar_cup'
                    elif "o'reilly" in blob or 'xfinity' in blob:
                        actual_team = 'nascar_ore'
                    elif 'truck' in blob or 'craftsman' in blob:
                        actual_team = 'nascar_tck'
                    else:
                        actual_team = 'nascar_cup'
                blob = (summary + description).lower()
                if 'fa cup' in blob:
                    special = "FA Cup"
                elif 'champions league' in blob or ' ucl' in blob:
                    special = "UCL"
                elif 'derby' in blob:
                    special = "Derby"
                elif 'fenway' in blob:
                    special = "Fenway"
                elif 'beanpot' in blob:
                    special = "Beanpot"
                elif 'playoff' in blob or 'postseason' in blob:
                    special = "Playoff"
                events.append({
                    "date": event_date.isoformat(),
                    "team": actual_team,
                    "title": summary[:80],
                    "time": time_str,
                    "result": result,
                    "score": score,
                    "venue": location,
                    "special": special,
                })
                count += 1
            print(f"    ✓ {team}: {count} events in range")
        except Exception as e:
            print(f"    ✗ {team}: {e}")

    events.sort(key=lambda x: (x['date'], x['time'] or ''))
    print(f"  → Total: {len(events)} sports events")
    return events

# ── MAIN ──────────────────────────────────────────────
def main():
    print("🔄 Revere Monitor v6 — fetching...")
    data = {
        "updated": datetime.now(timezone.utc).isoformat(),
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
    data["iqm2_meetings"]   = safe(fetch_iqm2_meetings,   "IQM2 meetings")   or []

    print("\n📺 Revere TV")
    channel_id = safe(fetch_revere_tv_channel_id, "Revere TV channel ID") or "UCq-Ej7V3_v7NuGUVRnqv8Aw"
    data["revere_tv_channel_id"] = channel_id
    data["revere_tv"] = safe(lambda: fetch_youtube(channel_id, 9), "Revere TV videos") or []

    print("\n📰 News")

    # ── REVERE ──────────────────────────────────────────────────────────────
    revere_official = safe(lambda: fetch_feed("https://www.revere.org/news/feed/rss", 20), "Revere.org RSS") or []
    for i in revere_official: i["source"] = "Revere.org"

    revere_journal = safe(lambda: fetch_feed("https://www.reverejournal.com/feed/", 20), "Revere Journal") or []
    for i in revere_journal: i["source"] = "Revere Journal"

    revere_advocate_raw = safe(lambda: fetch_feed("https://advocatenews.net/feed/", 40), "Advocate News") or []
    revere_advocate = [
        i for i in revere_advocate_raw
        if "revere" in (i.get("title","") + i.get("link","") + i.get("summary","") + i.get("description","")).lower()
    ]
    for i in revere_advocate: i["source"] = "Advocate News"

    revere_nbc = safe(lambda: fetch_feed("https://www.nbcboston.com/tag/revere/feed/", 10), "NBC Boston/Revere") or []
    for i in revere_nbc: i["source"] = "NBC Boston"

    revere_gnews1 = safe(lambda: fetch_feed(
        "https://news.google.com/rss/search?q=%22Revere%2C+MA%22&hl=en-US&gl=US&ceid=US:en", 15), "Google News: Revere, MA") or []
    for i in revere_gnews1: i["source"] = "Google News"

    revere_gnews2 = safe(lambda: fetch_feed(
        "https://news.google.com/rss/search?q=%22Revere+Massachusetts%22&hl=en-US&gl=US&ceid=US:en", 15), "Google News: Revere Massachusetts") or []
    for i in revere_gnews2: i["source"] = "Google News"

    revere_gnews3 = safe(lambda: fetch_feed(
        "https://news.google.com/rss/search?q=%22City+of+Revere%22&hl=en-US&gl=US&ceid=US:en", 10), "Google News: City of Revere") or []
    for i in revere_gnews3: i["source"] = "Google News"

    seen_rev = set()
    revere_all = []
    for item in (revere_official + revere_journal + revere_advocate +
                 revere_nbc + revere_gnews1 + revere_gnews2 + revere_gnews3):
        link = item.get("link", "")
        if link and link not in seen_rev:
            seen_rev.add(link)
            revere_all.append(item)
    data["news_revere"] = revere_all
    print(f"  → Revere total: {len(revere_all)} items "
          f"(official:{len(revere_official)} journal:{len(revere_journal)} "
          f"advocate:{len(revere_advocate)} nbc:{len(revere_nbc)} "
          f"gnews:{len(revere_gnews1)+len(revere_gnews2)+len(revere_gnews3)})")

    # ── COMMUNITIES ─────────────────────────────────────────────────────────
    comm_rss = [
        ("Chelsea",      "https://chelsearecord.com/feed/",                                                                          8),
        ("Chelsea",      "https://www.nbcboston.com/tag/chelsea/feed/",                                                              8),
        ("Chelsea",      "https://news.google.com/rss/search?q=%22Chelsea+MA%22+city+news&hl=en-US&gl=US&ceid=US:en",               8),
        ("Chelsea City", "https://news.google.com/rss/search?q=%22City+of+Chelsea%22+Massachusetts&hl=en-US&gl=US&ceid=US:en",      6),
        ("East Boston",  "https://eastietimes.com/feed/",                                                                            8),
        ("East Boston",  "https://eastboston.com/feed/",                                                                             8),
        ("East Boston",  "https://www.nbcboston.com/tag/east-boston/feed/",                                                          8),
        ("East Boston",  "https://news.google.com/rss/search?q=%22East+Boston%22+Massachusetts&hl=en-US&gl=US&ceid=US:en",          8),
        ("Everett",      "https://everettindependent.com/feed/",                                                                     8),
        ("Everett",      "https://www.nbcboston.com/tag/everett/feed/",                                                              8),
        ("Everett",      "https://news.google.com/rss/search?q=%22Everett+MA%22+Massachusetts&hl=en-US&gl=US&ceid=US:en",           8),
        ("Everett",      "https://news.google.com/rss/search?q=%22City+of+Everett%22+Massachusetts&hl=en-US&gl=US&ceid=US:en",      6),
        ("Malden",       "https://www.nbcboston.com/tag/malden/feed/",                                                               8),
        ("Malden",       "https://news.google.com/rss/search?q=%22Malden+MA%22+Massachusetts&hl=en-US&gl=US&ceid=US:en",            8),
        ("Malden City",  "https://news.google.com/rss/search?q=%22City+of+Malden%22+Massachusetts&hl=en-US&gl=US&ceid=US:en",       8),
        ("Malden",       "https://news.google.com/rss/search?q=Malden+Massachusetts+news+2026&hl=en-US&gl=US&ceid=US:en",           6),
        ("Medford",      "https://www.nbcboston.com/tag/medford/feed/",                                                              6),
        ("Medford",      "https://news.google.com/rss/search?q=%22Medford+MA%22+Massachusetts&hl=en-US&gl=US&ceid=US:en",           6),
        ("Lynn",         "https://www.itemlive.com/feed/",                                                                           10),
        ("Lynn",         "https://www.nbcboston.com/tag/lynn/feed/",                                                                 8),
        ("Lynn City",    "https://news.google.com/rss/search?q=%22City+of+Lynn%22+Massachusetts&hl=en-US&gl=US&ceid=US:en",         6),
        ("Lynn",         "https://news.google.com/rss/search?q=%22Lynn+MA%22+Massachusetts&hl=en-US&gl=US&ceid=US:en",              8),
        ("Winthrop",     "https://winthroptranscript.com/feed/",                                                                     10),
        ("Winthrop",     "https://news.google.com/rss/search?q=%22Winthrop+MA%22+Massachusetts&hl=en-US&gl=US&ceid=US:en",          6),
        ("Saugus",       "https://www.nbcboston.com/tag/saugus/feed/",                                                               8),
        ("Saugus",       "https://news.google.com/rss/search?q=%22Saugus+MA%22+Massachusetts&hl=en-US&gl=US&ceid=US:en",            8),
        ("Saugus",       "https://news.google.com/rss/search?q=Saugus+Massachusetts+news+2026&hl=en-US&gl=US&ceid=US:en",           6),
        ("Swampscott",   "https://www.nbcboston.com/tag/swampscott/feed/",                                                           6),
        ("Swampscott",   "https://news.google.com/rss/search?q=%22Swampscott%22+Massachusetts&hl=en-US&gl=US&ceid=US:en",           8),
        ("Marblehead",   "https://www.nbcboston.com/tag/marblehead/feed/",                                                           6),
        ("Marblehead",   "https://news.google.com/rss/search?q=%22Marblehead%22+Massachusetts&hl=en-US&gl=US&ceid=US:en",           8),
        ("Peabody",      "https://www.nbcboston.com/tag/peabody/feed/",                                                              8),
        ("Peabody",      "https://news.google.com/rss/search?q=%22Peabody+MA%22+Massachusetts&hl=en-US&gl=US&ceid=US:en",           8),
        ("Peabody",      "https://news.google.com/rss/search?q=Peabody+Massachusetts+news+2026&hl=en-US&gl=US&ceid=US:en",          6),
        ("Salem",        "https://www.nbcboston.com/tag/salem/feed/",                                                                8),
        ("Salem",        "https://news.google.com/rss/search?q=%22Salem+MA%22+Massachusetts+city&hl=en-US&gl=US&ceid=US:en",        8),
        ("Salem",        "https://news.google.com/rss/search?q=Salem+Massachusetts+news+2026&hl=en-US&gl=US&ceid=US:en",            6),
        ("Melrose",      "https://www.nbcboston.com/tag/melrose/feed/",                                                              6),
        ("Melrose",      "https://news.google.com/rss/search?q=%22Melrose+MA%22+Massachusetts&hl=en-US&gl=US&ceid=US:en",           8),
        ("Advocate",     "https://advocatenews.net/feed/",                                                                           15),
        ("North Shore",  "https://www.boston.com/tag/north-shore/feed/",                                                             6),
        ("North Shore",  "https://news.google.com/rss/search?q=%22North+Shore%22+Massachusetts+news&hl=en-US&gl=US&ceid=US:en",     8),
    ]

    data["news_communities"] = []
    seen_comm = set()
    comm_counts = {}
    for name, url, n in comm_rss:
        items = safe(lambda u=url, c=n: fetch_feed(u, c), f"Comm/{name}") or []
        comm_counts[f"{name}|{url[:50]}"] = len(items)
        for item in items:
            link = item.get("link", "")
            if not link or link in seen_comm:
                continue
            seen_comm.add(link)
            item["source"] = name
            data["news_communities"].append(item)
    data["news_communities"].sort(key=lambda x: x.get("ts", 0), reverse=True)
    print(f"  → Communities total: {len(data['news_communities'])} unique items")
    for key, fetched in comm_counts.items():
        label, url_snip = key.split("|")
        if fetched == 0:
            print(f"    ✗ {label}: 0 fetched — {url_snip}")

    # ── BOSTON ──────────────────────────────────────────────────────────────
    boston_items = safe(lambda: fetch_feed(
        'https://www.rssrssrssrss.com/api/merge?feeds=NoIgFgLhAODOBcB6RB3NA6FBjAbgI3SwHsBbRCIuCgJwEsBTWAWmtlhABpwo4lUwAJmEKlEAM3r0BiTtxgJkaFOgB2eLHiKwKKkWQD8rWAF4AnrMjy+S9Ju1FdxMhKkyul3snoBDbXZ16bnKeqBgoeACu1OhE1ADmiCr0KOzuPAqhyihxeMKxCUkpIAC6QA',
        50), "Boston News combined") or []
    for i in boston_items: i["source"] = i.get("feed_title") or i.get("author") or "Boston News"
    data["news_boston"] = boston_items
    print(f"  → Boston News: {len(boston_items)} items")

    # ── UNIVERSAL HUB ────────────────────────────────────────────────────────
    uhub = []
    for uh_url in [
        "https://www.universalhub.com/recent/feed",
        "https://universalhub.com/recent/feed",
        "https://www.universalhub.com/node/feed",
        "https://universalhub.com/node/feed",
        "https://www.universalhub.com/atom.xml",
        "https://universalhub.com/atom.xml",
    ]:
        try:
            resp = requests.get(uh_url, timeout=12, headers={
                "User-Agent": "Mozilla/5.0 (compatible; FeedFetcher/1.0)",
                "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
            })
            if resp.status_code == 200 and len(resp.content) > 500:
                feed = feedparser.parse(resp.content)
                if feed.entries:
                    import time as _time
                    now_ts = _time.time()
                    week_ago = now_ts - (7 * 24 * 3600)
                    for e in feed.entries[:30]:
                        ts = 0
                        if hasattr(e, "published_parsed") and e.published_parsed:
                            try:
                                ts = calendar.timegm(e.published_parsed)
                            except:
                                pass
                        if ts > 0 and ts < week_ago:
                            continue
                        uhub.append({
                            "title": getattr(e, "title", ""),
                            "link": getattr(e, "link", ""),
                            "published": getattr(e, "published", ""),
                            "ts": ts,
                            "source": "Universal Hub",
                        })
                    if uhub:
                        print(f"    ✓ Universal Hub: {len(uhub)} items from {uh_url}")
                        break
        except Exception as ex:
            print(f"    ✗ UHub {uh_url}: {ex}")
            continue

    if not uhub:
        try:
            resp = requests.get("https://www.universalhub.com", timeout=15, headers=HEADERS)
            soup = BeautifulSoup(resp.text, "html.parser")
            main_el = soup.select_one("main, #main, .main-container, #content, .content, .region-content")
            search_area = main_el if main_el else soup
            seen_h = set()
            for a in search_area.select("h2 a, h3 a, h4 a, .node-title a")[:20]:
                text = a.get_text(strip=True)
                href = a.get("href", "")
                if len(text) > 10 and href and text not in seen_h:
                    seen_h.add(text)
                    full = href if href.startswith("http") else "https://www.universalhub.com" + href
                    if "universalhub.com" in full or full.startswith("/"):
                        uhub.append({"title": text, "link": full, "published": "", "ts": 0, "source": "Universal Hub"})
            print(f"    {'✓' if uhub else '✗'} UHub homepage scrape: {len(uhub)} items")
        except Exception as e:
            print(f"    ✗ UHub homepage failed: {e}")

    if not uhub:
        uhub = safe(lambda: fetch_feed(
            "https://news.google.com/rss/search?q=site:universalhub.com&hl=en-US&gl=US&ceid=US:en", 20
        ), "UHub via Google News") or []
        for i in uhub: i["source"] = "Universal Hub"

    data["news_universalhub"] = uhub

    # ── SPORTS NEWS ──────────────────────────────────────────────────────────
    sports_items = safe(lambda: fetch_feed(
        'https://www.rssrssrssrss.com/api/merge?feeds=NoIgFgLhAODOBcB6RB3NA6AdgTwgSwFsBTWdAYwHsDEBDCMAGyPzMU0cQiJuoCMAnAK55MsRCAA04KHCSoMOfMVKVqdRszysCDXp27V+RACawKAD3FTIMBMjQosuQiXJVa9JizYAzBvp5EaDp+PAoIMUlpWzkHJyVXVQ8Nbx8KcN4aBn8uQKMANwoGQXwKTCto2XsFZ2U3NU9NVkxMgOoyIgYWSOsZO3lHRRcVd3UvLURKbKIAcyIAWjSMrJyDRF4KWAgy+ammOfnuGaZYXaL9haWITOyKmyqB+OH65PHWPdmFzNgAa2Yb1aBDZbHYfA5HE5naYHb5-a4rO59WI1BIjBopCZXAGoCj8BjGXaCaCImLVQa1RKjRreTA0WBkGj8EkPOJDOpJMZNRBYhGYFCwfxRe79VkUtGvLkoKhEUTzMAUMh-bDMkUo54c6mY3EEQQMGjzACMKrksEE-HyeAtmBmM34NGM2BePiIJiFSOQACsKNgKD4zOZ0LwGBQZrBoOEnS7TEFNhFEMYiD4aLqIG7SY8KPkiPx6EQCGUttmXiAALpAA',
        50), "My Sports News") or []
    for i in sports_items: i["source"] = i.get("feed_title") or i.get("author") or "Sports"
    sports_items = [i for i in sports_items if 'espn.com/espn/rss/news' not in i.get("link", "")]
    sports_items.sort(key=lambda x: x.get("ts", 0), reverse=True)
    data["news_sports"] = sports_items[:40]
    print(f"  → My Sports News: {len(data['news_sports'])} items")

    # ── BOSTON COLLEGE ───────────────────────────────────────────────────────
    bc_sources = [
        ("https://bcheights.com/feed/", "The Heights"),
        ("https://www.bcinterruption.com/rss/current.xml", "BC Interruption"),
        ("https://bceagles.com/rss.aspx?path=mhockey", "BC Hockey"),
        ("https://bceagles.com/rss.aspx", "BC Athletics"),
        ("https://news.google.com/rss/search?q=247sports+boston+college&hl=en-US&gl=US&ceid=US:en", "247Sports BC"),
        ("https://news.google.com/rss/search?q=%22sports+illustrated%22+%22boston+college%22&hl=en-US&gl=US&ceid=US:en", "SI Boston College"),
        ("https://news.google.com/rss/search?q=boston+college+eagles+football+basketball&hl=en-US&gl=US&ceid=US:en", "Google News BC"),
    ]
    data["news_bc"] = []
    seen_bc = set()
    for url, label in bc_sources:
        items = safe(lambda u=url: fetch_feed(u, 8), label) or []
        for item in items:
            link = item.get("link", "")
            if link and link not in seen_bc:
                seen_bc.add(link)
                item["source"] = label
                data["news_bc"].append(item)

    # ── COLLEGE HOCKEY ───────────────────────────────────────────────────────
    college_hockey_sources = [
        ("https://www.uscho.com/feed", "USCHO"),
        ("https://www.collegehockeynews.com/news/xml/newsfeed.xml", "CHN"),
        ("https://www.collegehockeyinsider.com/feed", "CHI"),
        ("https://bchockeyblog.substack.com/feed", "BC Hockey Blog"),
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

    bc_hockey = safe(lambda: fetch_feed(
        'https://news.google.com/rss/search?q=%22boston%20college%22%20hockey%20when%3A7d&hl=en-US&gl=US&ceid=US%3Aen', 20
    ), "BC Hockey news") or []
    for i in bc_hockey: i["source"] = "Google News · BC Hockey"
    bc_hockey.sort(key=lambda x: x.get("ts", 0), reverse=True)
    data["news_bc_hockey"] = bc_hockey

    hockey_east = safe(lambda: fetch_feed(
        'https://news.google.com/rss/search?q=%22Hockey+East%22&hl=en-US&gl=US&ceid=US:en', 15
    ), "Hockey East news") or []
    for i in hockey_east: i["source"] = "Google News · Hockey East"
    data["news_hockey_east"] = hockey_east

    # ── NATIONAL ─────────────────────────────────────────────────────────────
    national_sources = [
        ("https://feeds.npr.org/1001/rss.xml", "NPR"),
        ("https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml", "NY Times"),
        ("https://feeds.bbci.co.uk/news/rss.xml", "BBC"),
        ("https://apnews.com/hub/ap-top-news?format=feed&type=rss", "AP"),
        ("https://rss.cnn.com/rss/edition.rss", "CNN"),
        ("https://thehill.com/feed/", "The Hill"),
        ("https://feeds.washingtonpost.com/rss/national", "Washington Post"),
    ]
    data["news_national"] = []
    seen_nat = set()
    for url, label in national_sources:
        items = safe(lambda u=url: fetch_feed(u, 10), label) or []
        for item in items:
            link = item.get("link", "")
            if link and link not in seen_nat:
                seen_nat.add(link)
                item["source"] = label
                data["news_national"].append(item)

    # ── LOGAN AIRPORT NEWS ───────────────────────────────────────────────────
    logan_news = safe(lambda: fetch_feed(
        'https://news.google.com/rss/search?q=%22logan+airport%22&hl=en-US&gl=US&ceid=US:en', 15
    ), "Logan Airport news") or []
    for i in logan_news: i["source"] = "Google News"
    data["news_logan"] = logan_news

    # ── SUBSTACK ─────────────────────────────────────────────────────────────
    substack_items = safe(lambda: fetch_feed(
        'https://www.rssrssrssrss.com/api/merge?feeds=NoIgFgLhAODOBcB6RB3NA6WAbA9igRjgE4CWAdgOboDGOAtogGYCmzAJiADThRxKL5qYHNQDWzAJ75cVWAFd8sCAEMxNek1YdukGAmRoU6rFmYVmwsZPKkWQD8rWAF4AnrMjy+S9Ju1FdxMhKkyul3snoBDbXZ16bnKeqBgoeACu1OhE1ADmiCr0KOzuPAqhyihxeMKxCUkpIAC6QA',
        50), "Substack reading list") or []
    for i in substack_items: i["source"] = i.get("feed_title") or i.get("author") or "Substack"
    data["news_substack"] = substack_items
    has_summary = sum(1 for i in substack_items if i.get("summary", "").strip())
    print(f"  → Substack: {len(substack_items)} items, {has_summary} with summaries")

    # ── MA TRANSIT & HOUSING ─────────────────────────────────────────────────
    ma_transit_queries = [
        ("MBTA",        "https://news.google.com/rss/search?q=MBTA&hl=en-US&gl=US&ceid=US:en"),
        ("MassDOT",     "https://news.google.com/rss/search?q=MassDOT&hl=en-US&gl=US&ceid=US:en"),
        ("DCR",         "https://news.google.com/rss/search?q=DCR+Massachusetts&hl=en-US&gl=US&ceid=US:en"),
        ("EOHLC",       "https://news.google.com/rss/search?q=EOHLC&hl=en-US&gl=US&ceid=US:en"),
        ("MassHousing", "https://news.google.com/rss/search?q=MassHousing&hl=en-US&gl=US&ceid=US:en"),
    ]
    ma_transit_seen = set()
    ma_transit_all = []
    for label, url in ma_transit_queries:
        items = safe(lambda u=url: fetch_feed(u, 10), f"MA Transit/{label}") or []
        for i in items:
            link = i.get("link", "")
            if link and link not in ma_transit_seen:
                ma_transit_seen.add(link)
                i["source"] = label
                ma_transit_all.append(i)
    ma_transit_all.sort(key=lambda x: x.get("ts", 0), reverse=True)
    data["news_ma_transit"] = ma_transit_all[:20]
    print(f"  → MA Transit & Housing: {len(data['news_ma_transit'])} items")

    # ── ESPN ─────────────────────────────────────────────────────────────────
    espn_items = safe(lambda: fetch_feed('https://www.espn.com/espn/rss/news', 30), "ESPN feed") or []
    for i in espn_items: i["source"] = "ESPN"
    data["news_espn"] = espn_items
    print(f"  → ESPN feed: {len(espn_items)} items")

    print("\n📅 Personal Calendar")
    data["personal_calendar"] = safe(fetch_personal_calendar, "Personal calendars") or []

    print("\n⚽🏒 Sports Schedule")
    data["sports_schedule"] = safe(fetch_sports_schedule, "Sports calendars") or []

    # ── MASSDOT ROAD EVENTS ──────────────────────────────────────────────────
    try:
        import xml.etree.ElementTree as ET
        resp = requests.get("http://events.massdot.evbg.net/", headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        root = ET.fromstring(resp.content)
        road_events = []
        for ev in root.findall(".//Event"):
            def t(tag): return (ev.findtext(tag) or "").strip()
            road_events.append({
                "id": t("EventId"),
                "title": f"{t('EventSubType') or t('EventType')} — {t('RoadwayName')} {t('Direction')}".strip(" —"),
                "type": t("EventType"), "subtype": t("EventSubType"),
                "road": t("RoadwayName"), "direction": t("Direction"),
                "start": t("EventStartDate"), "end": t("EventEndDate"),
                "status": t("EventStatus"), "location": t("LocationDescription"),
                "lanes": t("LaneBlockageDescription"),
                "lat": float(t("PrimaryLatitude")) if t("PrimaryLatitude") else None,
                "lng": float(t("PrimaryLongitude")) if t("PrimaryLongitude") else None,
                "updated": t("LastUpdate"),
            })
        road_events.sort(key=lambda x: x.get("start", ""), reverse=True)
        data["road_events"] = road_events
        print(f"  → MassDOT road events: {len(road_events)} events")
    except Exception as e:
        print(f"  ✗ MassDOT road events: {e}")
        data["road_events"] = []

    # ── KTN BREAKING NEWS ────────────────────────────────────────────────────
    import time as _time
    ktn_raw = safe(lambda: fetch_feed(
        'https://kill-the-newsletter.com/feeds/g3cj2vs42hupn2f904lv.xml', 20
    ), "KTN breaking news") or []
    _now = _time.time()
    ktn_items = [i for i in ktn_raw if i.get("ts", 0) == 0 or (_now - i["ts"]) <= 43200][:10]
    data["news_ktn"] = ktn_items
    print(f"  → KTN breaking news: {len(ktn_items)} items (last 12h, raw:{len(ktn_raw)})")

    with open("data.json", "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"\n✅ Done — {data['updated_local']}")

if __name__ == "__main__":
    main()
