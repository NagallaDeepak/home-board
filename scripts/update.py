"""Fetch weather (Open-Meteo) and headlines (publisher RSS feeds) into data/board.json.

Runs on GitHub's servers every 30 minutes via .github/workflows/update.yml.
No API keys, no AI: weather numbers and headlines are copied as the sources publish them.
"""
import datetime
import email.utils
import html
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

OUT = sys.argv[1] if len(sys.argv) > 1 else "data/board.json"
UA = {"User-Agent": "Mozilla/5.0 (living-room-board; +https://pages.github.com)"}

CITIES = [
    {"id": "boston", "city": "Boston", "region": "Massachusetts, USA", "lat": 42.3601, "lon": -71.0589,
     "tz": "America/New_York", "unit": "F"},
    {"id": "hyderabad", "city": "Hyderabad", "region": "Telangana, India", "lat": 17.3850, "lon": 78.4867,
     "tz": "Asia/Kolkata", "unit": "C"},
    {"id": "vijayawada", "city": "Vijayawada", "region": "Andhra Pradesh, India", "lat": 16.5062, "lon": 80.6480,
     "tz": "Asia/Kolkata", "unit": "C"},
]

FEEDS = {
    "andhra": [("The Hindu", "https://www.thehindu.com/news/national/andhra-pradesh/feeder/default.rss")],
    "telangana": [("The Hindu", "https://www.thehindu.com/news/national/telangana/feeder/default.rss")],
    "india": [("The Hindu", "https://www.thehindu.com/news/national/feeder/default.rss")],
    "usa": [("NPR", "https://feeds.npr.org/1003/rss.xml")],
    "world": [("BBC News", "https://feeds.bbci.co.uk/news/world/rss.xml")],
}
PER_REGION = 6
MAX_AGE_H = 36


def get(url, timeout=25):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()


def weather(c):
    q = urllib.parse.urlencode({
        "latitude": c["lat"], "longitude": c["lon"], "timezone": c["tz"], "forecast_days": 5,
        "temperature_unit": "fahrenheit" if c["unit"] == "F" else "celsius", "wind_speed_unit": "mph",
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                 "precipitation_sum,wind_gusts_10m_max",
    })
    d = json.loads(get("https://api.open-meteo.com/v1/forecast?" + q))
    cur, dl = d["current"], d["daily"]
    days = [{"date": dl["time"][i], "hi": dl["temperature_2m_max"][i], "lo": dl["temperature_2m_min"][i],
             "code": dl["weather_code"][i], "rain": dl["precipitation_probability_max"][i],
             "rainMm": dl["precipitation_sum"][i], "gustMph": dl["wind_gusts_10m_max"][i]}
            for i in range(len(dl["time"]))]
    # Simple rule-based alerts from the forecast numbers themselves.
    alerts = []
    for day in days[:3]:
        name = datetime.date.fromisoformat(day["date"]).strftime("%a")
        if (day["gustMph"] or 0) >= 40:
            alerts.append("%s: wind gusts up to %d mph" % (name, round(day["gustMph"])))
        mm = day["rainMm"] or 0
        if mm >= 50:
            alerts.append("%s: heavy rain, about %d mm" % (name, round(mm)))
        elif mm >= 20:
            alerts.append("%s: rain expected, about %d mm" % (name, round(mm)))
    return {"id": c["id"], "city": c["city"], "region": c["region"], "unit": c["unit"],
            "now": {"t": cur["temperature_2m"], "feels": cur["apparent_temperature"],
                    "hum": cur["relative_humidity_2m"], "wind": cur["wind_speed_10m"], "code": cur["weather_code"]},
            "days": days, "alert": " · ".join(alerts) or None}


def clean(text, limit):
    text = re.sub(r"<[^>]+>", " ", html.unescape(text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def headlines(source, url, now):
    root = ET.fromstring(get(url))
    items = []
    for it in root.iter("item"):
        try:
            t = email.utils.parsedate_to_datetime(it.findtext("pubDate"))
        except (TypeError, ValueError):
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=datetime.timezone.utc)
        if (now - t).total_seconds() > MAX_AGE_H * 3600:
            continue
        link = (it.findtext("link") or "").strip().split("?")[0]
        if not link.startswith("https://"):
            continue
        items.append({"headline": clean(it.findtext("title"), 160), "summary": clean(it.findtext("description"), 220),
                      "link": link, "time": t.isoformat(), "source": source})
    return items


def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        old = json.load(open(OUT))
    except (OSError, ValueError):
        old = {}
    out = {"updatedAt": now.isoformat(), "sources": "Open-Meteo · The Hindu · NPR · BBC", "weather": [], "news": {}}
    for c in CITIES:
        try:
            out["weather"].append(weather(c))
        except Exception as e:  # keep the last good copy for this city
            print("weather failed", c["id"], e, file=sys.stderr)
            prev = [w for w in old.get("weather", []) if w.get("id") == c["id"]]
            if prev:
                out["weather"].append(prev[0])
    for region, feeds in FEEDS.items():
        items = []
        for source, url in feeds:
            try:
                items += headlines(source, url, now)
            except Exception as e:
                print("feed failed", region, url, e, file=sys.stderr)
        seen, uniq = set(), []
        for i in sorted(items, key=lambda x: x["time"], reverse=True):
            if i["headline"] not in seen:
                seen.add(i["headline"])
                uniq.append(i)
        out["news"][region] = uniq[:PER_REGION] or old.get("news", {}).get(region, [])
    with open(OUT, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("wrote", OUT, sum(len(v) for v in out["news"].values()), "headlines")


if __name__ == "__main__":
    main()
