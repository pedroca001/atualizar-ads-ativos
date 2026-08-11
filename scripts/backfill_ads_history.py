"""Recover deleted ads-history readings from GitHub Actions logs.

The command is dry-run by default. It never updates the current offer count and
only inserts successful history readings for offer/date/run-slot combinations
that are not already present in Supabase.
"""

import argparse
import json
import os
import re
import subprocess
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


REPOSITORY = "pedroca001/atualizar-ads-ativos"
WORKFLOW = "Daily FB Ads Counter"
HISTORY_TABLE = "oferta_ads_leituras"
BACKFILL_SOURCE = "github_actions_log_backfill"
BRAZIL_TZ = ZoneInfo("America/Sao_Paulo")
READING_HOURS = (4, 12, 20)

TIMESTAMP_PATTERN = re.compile(r"(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)")
OFFER_PATTERN = re.compile(r"\[\d+/\d+\]\s+(?P<offer_id>\d+)\s*$")
SUCCESS_PATTERN = re.compile(r"\bok:\s+(?P<count>\d+)\s+active ads\b")
RUN_SLOT_PATTERN = re.compile(r"run slot:\s+(?P<hour>\d{2})h Sao Paulo")


def parse_timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def nearest_reading_hour(date):
    local = date.astimezone(BRAZIL_TZ)
    decimal_hour = local.hour + (local.minute / 60)
    return min(READING_HOURS, key=lambda hour: abs(hour - decimal_hour))


def reading_slot(offer_id, read_at, run_hour=None):
    local = read_at.astimezone(BRAZIL_TZ)
    hour = run_hour if run_hour in READING_HOURS else nearest_reading_hour(read_at)
    return int(offer_id), local.date().isoformat(), hour


def parse_run_log(log_text):
    """Return the run slot and successful readings found in one Actions log."""
    run_hour = None
    pending = None
    readings = []

    for line in log_text.splitlines():
        slot_match = RUN_SLOT_PATTERN.search(line)
        if slot_match:
            run_hour = int(slot_match.group("hour"))

        timestamp_match = TIMESTAMP_PATTERN.search(line)
        offer_match = OFFER_PATTERN.search(line)
        if timestamp_match and offer_match:
            pending = {
                "oferta_id": int(offer_match.group("offer_id")),
                "lido_em": parse_timestamp(timestamp_match.group("timestamp")),
            }
            continue

        success_match = SUCCESS_PATTERN.search(line)
        if pending and success_match:
            readings.append(
                {
                    **pending,
                    "anuncios_ativos": int(success_match.group("count")),
                }
            )
            pending = None

    return run_hour, readings


def run_gh(arguments, allow_failure=False):
    completed = subprocess.run(
        ["gh", *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode and not allow_failure:
        raise subprocess.CalledProcessError(
            completed.returncode,
            completed.args,
            output=completed.stdout,
            stderr=completed.stderr,
        )
    return completed.stdout


def list_runs(limit):
    output = run_gh(
        [
            "run",
            "list",
            "--repo",
            REPOSITORY,
            "--workflow",
            WORKFLOW,
            "--limit",
            str(limit),
            "--json",
            "databaseId,createdAt,status,conclusion",
        ]
    )
    return json.loads(output)


def fetch_existing_readings(client, start_utc):
    rows = []
    page_size = 1000
    offset = 0

    while True:
        result = (
            client.table(HISTORY_TABLE)
            .select("oferta_id,lido_em,status")
            .eq("status", "success")
            .gte("lido_em", start_utc.isoformat())
            .range(offset, offset + page_size - 1)
            .execute()
        )
        page = result.data or []
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += page_size


def collect_log_readings(runs, start_utc, end_utc):
    readings = []
    recovered_run_slots = set()

    for index, run in enumerate(runs, 1):
        run_id = run["databaseId"]
        print(f"[{index}/{len(runs)}] reading Actions log {run_id}")
        log_text = run_gh(
            ["run", "view", str(run_id), "--repo", REPOSITORY, "--log"],
            allow_failure=True,
        )
        if not log_text.strip():
            print(f"   log unavailable for run {run_id}")
            continue
        run_hour, parsed = parse_run_log(log_text)

        for reading in parsed:
            if start_utc <= reading["lido_em"] < end_utc:
                reading["run_hour"] = run_hour
                readings.append(reading)
                recovered_run_slots.add(reading_slot(reading["oferta_id"], reading["lido_em"], run_hour)[1:])

    return readings, recovered_run_slots


def select_missing_readings(candidates, existing_rows, start_utc, end_utc):
    existing_slots = {
        reading_slot(row["oferta_id"], parse_timestamp(row["lido_em"]))
        for row in existing_rows
        if row.get("lido_em")
    }
    selected_by_slot = {}

    for reading in candidates:
        if not start_utc <= reading["lido_em"] < end_utc:
            continue
        slot = reading_slot(reading["oferta_id"], reading["lido_em"], reading.get("run_hour"))
        if slot in existing_slots:
            continue
        previous = selected_by_slot.get(slot)
        if previous is None or reading["lido_em"] > previous["lido_em"]:
            selected_by_slot[slot] = reading

    return sorted(selected_by_slot.values(), key=lambda row: (row["lido_em"], row["oferta_id"]))


def insert_readings(client, readings):
    payload = [
        {
            "oferta_id": reading["oferta_id"],
            "anuncios_ativos": reading["anuncios_ativos"],
            "lido_em": reading["lido_em"].isoformat(),
            "fonte": BACKFILL_SOURCE,
            "status": "success",
        }
        for reading in readings
    ]

    for offset in range(0, len(payload), 250):
        client.table(HISTORY_TABLE).upsert(
            payload[offset : offset + 250],
            on_conflict="oferta_id,lido_em,fonte",
        ).execute()


def local_window(days):
    today = datetime.now(BRAZIL_TZ).date()
    start_date = today - timedelta(days=days - 1)
    start_local = datetime.combine(start_date, time.min, tzinfo=BRAZIL_TZ)
    end_local = datetime.combine(today + timedelta(days=1), time.min, tzinfo=BRAZIL_TZ)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=14, help="Inclusive chart window in local days")
    parser.add_argument("--apply", action="store_true", help="Insert missing readings after the dry-run summary")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.days < 1:
        raise SystemExit("--days must be at least 1")

    from supabase import create_client

    start_utc, end_utc = local_window(args.days)
    runs = [
        run
        for run in list_runs(max(60, args.days * 4 + 10))
        if run.get("status") == "completed"
        and start_utc - timedelta(hours=12) <= parse_timestamp(run["createdAt"]) < end_utc
    ]

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    existing = fetch_existing_readings(client, start_utc)
    candidates, recovered_run_slots = collect_log_readings(runs, start_utc, end_utc)
    missing = select_missing_readings(candidates, existing, start_utc, end_utc)

    expected_run_slots = {
        ((start_utc.astimezone(BRAZIL_TZ).date() + timedelta(days=day)).isoformat(), hour)
        for day in range(args.days)
        for hour in READING_HOURS
    }
    unavailable_run_slots = sorted(expected_run_slots - recovered_run_slots)

    print(f"existing successful readings: {len(existing)}")
    print(f"log readings parsed: {len(candidates)}")
    print(f"missing readings recoverable: {len(missing)}")
    print(f"run slots with no recoverable log: {len(unavailable_run_slots)}")
    for date_key, hour in unavailable_run_slots:
        print(f"   unavailable: {date_key} {hour:02d}h")

    if not args.apply:
        print("dry-run only; rerun with --apply to insert recoverable readings")
        return

    insert_readings(client, missing)
    print(f"inserted or confirmed: {len(missing)} readings")


if __name__ == "__main__":
    main()
