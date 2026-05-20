"""
FB Ad Library active ads counter.

Monitoring cadence:
- active offers: checked on every scheduled run (04h, 12h, 20h Sao Paulo)
- offers with 0 ads for 2 days: checked once per day at 12h Sao Paulo
- offers with 0 ads for 7 days: marked inactive and checked every 3 days at 12h Sao Paulo
"""

import json
import os
import re
import sys
import time
from datetime import datetime, time as datetime_time, timedelta, timezone
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
TABLE = "ofertas"
HISTORY_TABLE = "oferta_ads_leituras"
HISTORY_RETENTION_DAYS = 7

BRAZIL_TZ = ZoneInfo("America/Sao_Paulo")
DAILY_CHECK_HOUR = 12
DAILY_AFTER_ZERO_DAYS = 2
INACTIVE_AFTER_ZERO_DAYS = 7
INACTIVE_CHECK_INTERVAL_DAYS = 3
SCHEDULE_LOCAL_HOURS = (4, 12, 20)
SCHEDULE_TO_LOCAL_HOUR = {
    "0 7 * * *": 4,
    "0 15 * * *": 12,
    "0 23 * * *": 20,
}

STATUS_ACTIVE = "active"
STATUS_COOLDOWN = "cooldown"
STATUS_INACTIVE = "inactive"

COUNT_PATTERNS = [
    r"~?\s*([\d.,\u00a0\s]+)\s+r[eé]sultats?",
    r"~?\s*([\d.,\u00a0\s]+)\s+resultados?",
    r"(?:about\s+|~)?\s*([\d.,\u00a0\s]+)\s+results?",
    r"~?\s*([\d.,\u00a0\s]+)\s+ergebnisse",
    r"~?\s*([\d.,\u00a0\s]+)\s+risultati",
]

EMPTY_PATTERNS = re.compile(
    r"(no\s+ads\s+match|aucune?\s+annonce|nenhum\s+anúncio|"
    r"ningún\s+anuncio|nessun\s+annuncio|keine\s+anzeigen)",
    re.IGNORECASE,
)


def parse_count(text: str):
    for pattern in COUNT_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            digits = re.sub(r"\D", "", match.group(1))
            if digits:
                return int(digits)
    return None


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def parse_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def next_local_noon(now_utc, days_ahead=1):
    local_now = now_utc.astimezone(BRAZIL_TZ)
    target_date = local_now.date() + timedelta(days=days_ahead)
    target_local = datetime.combine(target_date, datetime_time(DAILY_CHECK_HOUR, 0), tzinfo=BRAZIL_TZ)
    return target_local.astimezone(timezone.utc)


def current_run_local_hour(now_utc):
    explicit_hour = os.environ.get("ADS_RUN_LOCAL_HOUR")
    if explicit_hour:
        try:
            return int(explicit_hour)
        except ValueError:
            print(f"invalid ADS_RUN_LOCAL_HOUR={explicit_hour!r}; inferring run slot", file=sys.stderr)

    event_schedule = os.environ.get("GITHUB_EVENT_SCHEDULE")
    if event_schedule in SCHEDULE_TO_LOCAL_HOUR:
        return SCHEDULE_TO_LOCAL_HOUR[event_schedule]

    local_now = now_utc.astimezone(BRAZIL_TZ)
    local_decimal_hour = local_now.hour + (local_now.minute / 60)

    return min(
        SCHEDULE_LOCAL_HOURS,
        key=lambda scheduled_hour: (local_decimal_hour - scheduled_hour) % 24,
    )


def is_daily_check_run(now_utc):
    return current_run_local_hour(now_utc) == DAILY_CHECK_HOUR


def should_check_offer(row, now_utc):
    status = row.get("ads_monitoring_status") or STATUS_ACTIVE
    next_check_at = parse_datetime(row.get("ads_next_check_at"))

    if next_check_at and now_utc < next_check_at:
        return False, f"next check at {next_check_at.isoformat()}"

    if status == STATUS_INACTIVE:
        if not is_daily_check_run(now_utc):
            return False, "inactive: only checked at 12h Sao Paulo"
        return True, "inactive: 3-day check"

    if status == STATUS_COOLDOWN:
        if not is_daily_check_run(now_utc):
            return False, "0 ads for 2d+: only checked at 12h Sao Paulo"
        return True, "0 ads for 2d+: daily check"

    zero_since = parse_datetime(row.get("ads_zero_since"))
    if zero_since and now_utc - zero_since >= timedelta(days=DAILY_AFTER_ZERO_DAYS):
        if not is_daily_check_run(now_utc):
            return False, "0 ads for 2d+: waiting for daily 12h check"
        return True, "0 ads for 2d+: daily check"

    return True, "active: regular check"


def monitoring_payload(row, count, read_at):
    read_at_dt = parse_datetime(read_at)

    if count > 0:
        return {
            "ads_monitoring_status": STATUS_ACTIVE,
            "ads_zero_since": None,
            "ads_last_positive_at": read_at,
            "ads_inactivated_at": None,
            "ads_next_check_at": None,
        }

    zero_since_dt = parse_datetime(row.get("ads_zero_since")) or read_at_dt
    zero_age = read_at_dt - zero_since_dt

    if zero_age >= timedelta(days=INACTIVE_AFTER_ZERO_DAYS):
        return {
            "ads_monitoring_status": STATUS_INACTIVE,
            "ads_zero_since": zero_since_dt.isoformat(),
            "ads_inactivated_at": row.get("ads_inactivated_at") or read_at,
            "ads_next_check_at": next_local_noon(read_at_dt, INACTIVE_CHECK_INTERVAL_DAYS).isoformat(),
        }

    if zero_age >= timedelta(days=DAILY_AFTER_ZERO_DAYS):
        return {
            "ads_monitoring_status": STATUS_COOLDOWN,
            "ads_zero_since": zero_since_dt.isoformat(),
            "ads_inactivated_at": None,
            "ads_next_check_at": next_local_noon(read_at_dt, 1).isoformat(),
        }

    return {
        "ads_monitoring_status": STATUS_ACTIVE,
        "ads_zero_since": zero_since_dt.isoformat(),
        "ads_inactivated_at": None,
        "ads_next_check_at": None,
    }


def record_reading(sb, row_id, url, count, read_at, status="success", error=None):
    sb.table(HISTORY_TABLE).insert(
        {
            "oferta_id": row_id,
            "anuncios_ativos": count,
            "lido_em": read_at,
            "fonte": "github_actions",
            "link_biblioteca": url,
            "status": status,
            "erro": error,
        }
    ).execute()


def cleanup_old_history(sb):
    cutoff = datetime.now(timezone.utc) - timedelta(days=HISTORY_RETENTION_DAYS)
    sb.table(HISTORY_TABLE).delete().lt("lido_em", cutoff.isoformat()).execute()


def fetch_offer_rows(sb):
    result = sb.table(TABLE).select(
        "id, oferta_data, link_biblioteca, ads_monitoring_status, ads_zero_since, "
        "ads_last_positive_at, ads_inactivated_at, ads_next_check_at"
    ).execute()
    return result.data


def update_current_count(sb, row, count, read_at):
    row_id = row["id"]
    try:
        sb.table(TABLE).update(
            {
                "anuncios_ativos": count,
                "anuncios_ativos_atualizado_em": read_at,
                **monitoring_payload(row, count, read_at),
            }
        ).eq("id", row_id).execute()
    except Exception:
        sb.table(TABLE).update({"anuncios_ativos": count}).eq("id", row_id).execute()


def scrape_one(page, url: str, retries: int = 2):
    last_error = None
    for attempt in range(retries + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)

            try:
                page.wait_for_selector("body", timeout=10000)
            except Exception:
                pass

            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass

            page.wait_for_timeout(2000)

            text = page.locator("body").inner_text()
            count = parse_count(text)
            if count is not None:
                return count

            page.wait_for_timeout(5000)
            text = page.locator("body").inner_text()
            count = parse_count(text)
            if count is not None:
                return count

            if EMPTY_PATTERNS.search(text):
                return 0

            last_error = "counter not found after networkidle"

        except Exception as error:
            last_error = str(error)[:200]

        if attempt < retries:
            print(f"   attempt {attempt + 1} failed: {last_error}", file=sys.stderr)
            time.sleep(3)

    print(f"   giving up: {last_error}", file=sys.stderr)
    return None


def main():
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    try:
        rows = fetch_offer_rows(sb)
    except Exception:
        rows = sb.table(TABLE).select("id, oferta_data, link_biblioteca").execute().data
    print(f"{len(rows)} offers in Supabase")

    now_utc = datetime.now(timezone.utc)
    run_hour = current_run_local_hour(now_utc)
    print(f"run slot: {run_hour:02d}h Sao Paulo")
    targets = []
    skipped = 0

    for row in rows:
        data = row.get("oferta_data")
        if isinstance(data, str):
            data = json.loads(data)

        link = row.get("link_biblioteca") or (data or {}).get("linkBiblioteca")
        if not link:
            continue

        should_check, reason = should_check_offer(row, now_utc)
        if should_check:
            targets.append((row, link, reason))
        else:
            skipped += 1
            print(f"   skipping {row['id']}: {reason}")

    print(f"{len(targets)} offers to check now ({skipped} skipped)\n")

    ok, fail = 0, 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 800},
            locale="pt-BR",
        )
        page = context.new_page()

        for idx, (row, url, reason) in enumerate(targets, 1):
            row_id = row["id"]
            print(f"[{idx}/{len(targets)}] {row_id}")
            print(f"   {reason}")
            read_at = utc_now_iso()
            count = scrape_one(page, url)

            if count is None:
                try:
                    record_reading(
                        sb,
                        row_id,
                        url,
                        None,
                        read_at,
                        status="failed",
                        error="contador nao encontrado",
                    )
                except Exception as error:
                    print(f"   failed to record reading: {str(error)[:200]}", file=sys.stderr)
                fail += 1
                continue

            update_current_count(sb, row, count, read_at)
            try:
                record_reading(sb, row_id, url, count, read_at)
            except Exception as error:
                print(f"   failed to record reading: {str(error)[:200]}", file=sys.stderr)

            status = monitoring_payload(row, count, read_at)["ads_monitoring_status"]
            print(f"   ok: {count} active ads, status={status}")
            ok += 1

        browser.close()

    try:
        cleanup_old_history(sb)
        print(f"history older than {HISTORY_RETENTION_DAYS} days deleted")
    except Exception as error:
        print(f"failed to clean old history: {str(error)[:200]}", file=sys.stderr)

    print(f"\nDone: {ok} ok, {fail} failures, {skipped} skipped")
    if targets and fail / len(targets) > 0.3:
        sys.exit(1)


if __name__ == "__main__":
    main()
