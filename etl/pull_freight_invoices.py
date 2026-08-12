"""
Walks the Kestrel Logistics Partner API's freight_invoices endpoint end to
end and caches the result locally, so the running app never depends on the
mock API being up.

Handles, per 03_External_Sources.md and observed server behaviour:
  - Cursor pagination (200/page), following `next_cursor` until null.
  - ~1 request in 9 returns 429 with `Retry-After` -- honoured exactly.
  - ~1 request in 25 returns 503 -- retried with exponential backoff.
  - The first page of a cursor walk is slow (~1-2s) by design; this is not
    treated as a failure.
  - Timestamps are UTC in the API; converted to Asia/Kolkata on write,
    because the operational DB (and everything joined against it) is IST.
  - `amount`, `detention_charge` etc. are in paise; converted to INR
    (rupees) on write. This is the only source of *actual* billed freight
    cost -- `deliveries.fuel_cost_inr` is driver-entered and unreconciled.

Resumable: cursor progress is checkpointed to disk after every page, so a
killed run picks back up rather than restarting the ~41,500-row walk.

Usage:
    python3 etl/pull_freight_invoices.py [--refresh]
"""
import argparse
import csv
import json
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from etl import config

MAX_RETRIES_PER_PAGE = 8
IST = ZoneInfo(config.OPERATING_TIMEZONE)

PAISE_FIELDS = ["amount", "detention_charge"]


def _get(path: str, params: dict) -> tuple[int, dict, dict]:
    qs = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    url = f"{config.PARTNER_API_BASE_URL}{path}?{qs}" if qs else f"{config.PARTNER_API_BASE_URL}{path}"
    req = urllib.request.Request(url, headers={"X-API-Key": config.PARTNER_API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return resp.status, dict(resp.headers), body
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode("utf-8"))
        return e.code, dict(e.headers), body


def get_with_retry(path: str, params: dict) -> dict:
    backoff = 1.0
    for attempt in range(1, MAX_RETRIES_PER_PAGE + 1):
        status, headers, body = _get(path, params)
        if status == 200:
            return body
        if status == 429:
            wait = float(headers.get("Retry-After", backoff))
            print(f"    429 rate-limited, waiting {wait}s (attempt {attempt})")
            time.sleep(wait)
            continue
        if status == 503:
            print(f"    503 upstream unavailable, backing off {backoff:.1f}s (attempt {attempt})")
            time.sleep(backoff)
            backoff = min(backoff * 2, 20)
            continue
        raise RuntimeError(f"Unexpected status {status} from {path}: {body}")
    raise RuntimeError(f"Gave up on {path} after {MAX_RETRIES_PER_PAGE} retries")


def to_ist_iso(utc_iso: str) -> str:
    dt = datetime.fromisoformat(utc_iso.replace("Z", "+00:00")).astimezone(IST)
    return dt.isoformat()


def normalise_invoice(inv: dict) -> dict:
    out = dict(inv)
    for f in PAISE_FIELDS:
        if out.get(f) is not None:
            out[f + "_inr"] = out.pop(f) / 100.0
    out["created_at_ist"] = to_ist_iso(out.pop("created_at_utc"))
    return out


def pull_all_invoices(checkpoint_path: Path, out_path: Path):
    cursor = None
    rows_written = 0
    fieldnames = None
    mode = "w"

    if checkpoint_path.exists():
        state = json.loads(checkpoint_path.read_text())
        cursor = state.get("cursor")
        rows_written = state.get("rows_written", 0)
        if cursor:
            print(f"Resuming from checkpoint: cursor={cursor}, {rows_written} rows already written")
            mode = "a"

    f = open(out_path, mode, newline="", encoding="utf-8")
    writer = None
    if mode == "a" and out_path.stat().st_size > 0:
        with open(out_path, "r", encoding="utf-8") as rf:
            fieldnames = next(csv.reader(rf))
        writer = csv.DictWriter(f, fieldnames=fieldnames)

    page_num = 0
    start = time.time()
    total_estimate = None
    while True:
        page_num += 1
        body = get_with_retry("/v1/freight_invoices", {"cursor": cursor, "limit": 200})
        total_estimate = body.get("total_estimate", total_estimate)
        rows = [normalise_invoice(r) for r in body["data"]]
        if rows and writer is None:
            fieldnames = list(rows[0].keys())
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
        if writer:
            writer.writerows(rows)
        rows_written += len(rows)
        f.flush()

        cursor = body.get("next_cursor")
        checkpoint_path.write_text(json.dumps({"cursor": cursor, "rows_written": rows_written}))

        if page_num % 20 == 0 or cursor is None:
            elapsed = time.time() - start
            pct = (rows_written / total_estimate * 100) if total_estimate else 0
            print(f"  page {page_num}: {rows_written}/{total_estimate} rows ({pct:.0f}%), {elapsed:.0f}s elapsed")

        if cursor is None:
            break

    f.close()
    print(f"Done: {rows_written} freight invoices written to {out_path}")


def pull_carriers(out_path: Path):
    body = get_with_retry("/v1/carriers", {})
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        rows = body["data"]
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for r in rows:
            r = dict(r)
            r["regions"] = "|".join(r["regions"])
            writer.writerow(r)
    print(f"Wrote {len(rows)} carriers to {out_path}")


def pull_fuel_surcharge(out_path: Path, start_date: str, end_date: str):
    months = []
    y, m = int(start_date[:4]), int(start_date[5:7])
    ey, em = int(end_date[:4]), int(end_date[5:7])
    while (y, m) <= (ey, em):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1

    rows = []
    for month in months:
        body = get_with_retry("/v1/fuel_surcharge", {"month": month})
        rows.append(body)

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} monthly fuel surcharge records to {out_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="Re-pull even if a completed cache exists")
    args = parser.parse_args()

    config.FREIGHT_CACHE.mkdir(parents=True, exist_ok=True)
    invoices_path = config.FREIGHT_CACHE / "freight_invoices.csv"
    checkpoint_path = config.FREIGHT_CACHE / "_checkpoint.json"
    carriers_path = config.FREIGHT_CACHE / "carriers.csv"
    surcharge_path = config.FREIGHT_CACHE / "fuel_surcharge.csv"
    done_marker = config.FREIGHT_CACHE / "_done"

    if done_marker.exists() and not args.refresh:
        print(f"Cache already complete at {invoices_path}, skipping (use --refresh to re-pull).")
        return

    if args.refresh and checkpoint_path.exists():
        checkpoint_path.write_text(json.dumps({"cursor": None, "rows_written": 0}))
        with open(invoices_path, "w") as f:
            pass

    print("Pulling carriers...")
    pull_carriers(carriers_path)

    print("Pulling monthly fuel surcharge index...")
    pull_fuel_surcharge(surcharge_path, config.WEATHER_START_DATE, config.WEATHER_END_DATE)

    print("Pulling freight invoices (this takes a few minutes)...")
    pull_all_invoices(checkpoint_path, invoices_path)

    done_marker.write_text(datetime.now(timezone.utc).isoformat())


if __name__ == "__main__":
    main()
