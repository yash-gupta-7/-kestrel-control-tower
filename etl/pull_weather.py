"""
Pulls daily historical weather (max temperature, precipitation) for each of
the 8 warehouse cities from Open-Meteo, for cold-chain / demand context.

This is explicitly optional per 03_External_Sources.md ("if a public API is
unreachable from your network, that is a design problem, not a blocker").
Accordingly: failures here are logged and swallowed, not raised -- the app
must work with zero weather data. Coordinates for each warehouse city are
in etl/config.py (the operational DB does not carry warehouse lat/long).

Usage:
    python3 etl/pull_weather.py [--refresh]
"""
import argparse
import csv
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from etl import config


def fetch_city(city: str, lat: float, lon: float) -> list[dict] | None:
    url = (
        f"{config.OPEN_METEO_BASE_URL}?latitude={lat}&longitude={lon}"
        f"&start_date={config.WEATHER_START_DATE}&end_date={config.WEATHER_END_DATE}"
        f"&daily=temperature_2m_max,precipitation_sum&timezone=Asia%2FKolkata"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "kestrel-control-tower/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"  {city}: unreachable ({e}) -- skipping, app will run without weather data for this city")
        return None

    daily = body.get("daily", {})
    dates = daily.get("time", [])
    tmax = daily.get("temperature_2m_max", [])
    precip = daily.get("precipitation_sum", [])
    return [
        {"city": city, "date": d, "temp_max_c": t, "precipitation_mm": p}
        for d, t, p in zip(dates, tmax, precip)
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    config.WEATHER_CACHE.mkdir(parents=True, exist_ok=True)
    out_path = config.WEATHER_CACHE / "daily_weather.csv"

    if out_path.exists() and not args.refresh:
        print(f"Cache exists at {out_path}, skipping (use --refresh to re-pull).")
        return

    all_rows = []
    for city, (lat, lon) in config.WAREHOUSE_CITY_COORDS.items():
        print(f"Fetching weather for {city}...")
        rows = fetch_city(city, lat, lon)
        if rows:
            all_rows += rows

    if not all_rows:
        print("No weather data retrieved from any city. Writing an empty cache; "
              "cold-chain weather correlation will be unavailable in the app, "
              "which is expected to degrade gracefully.")
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["city", "date", "temp_max_c", "precipitation_mm"])
        return

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"Wrote {len(all_rows)} city-days to {out_path}")


if __name__ == "__main__":
    main()
