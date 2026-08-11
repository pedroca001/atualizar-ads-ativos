import unittest
from datetime import datetime, timezone

from scripts.backfill_ads_history import parse_run_log, reading_slot, select_missing_readings


SAMPLE_LOG = """
scrape UNKNOWN STEP 2026-08-04T23:52:01.8850015Z run slot: 20h Sao Paulo
scrape UNKNOWN STEP 2026-08-04T23:52:02.7766751Z [1/2] 1784743004475
scrape UNKNOWN STEP 2026-08-04T23:52:10.8522623Z    ok: 270 active ads, status=active
scrape UNKNOWN STEP 2026-08-04T23:52:10.8523042Z [2/2] 1784665252866
scrape UNKNOWN STEP 2026-08-04T23:52:15.9728197Z    ok: 3 active ads, status=active
"""


class BackfillAdsHistoryTests(unittest.TestCase):
    def test_parse_run_log_pairs_offer_with_count_and_original_timestamp(self):
        run_hour, readings = parse_run_log(SAMPLE_LOG)

        self.assertEqual(run_hour, 20)
        self.assertEqual([row["oferta_id"] for row in readings], [1784743004475, 1784665252866])
        self.assertEqual([row["anuncios_ativos"] for row in readings], [270, 3])
        self.assertEqual(readings[0]["lido_em"].isoformat(), "2026-08-04T23:52:02.776675+00:00")

    def test_select_missing_readings_does_not_duplicate_existing_offer_slot(self):
        start = datetime(2026, 8, 4, tzinfo=timezone.utc)
        end = datetime(2026, 8, 6, tzinfo=timezone.utc)
        _, candidates = parse_run_log(SAMPLE_LOG)
        for reading in candidates:
            reading["run_hour"] = 20

        existing = [
            {
                "oferta_id": 1784743004475,
                "lido_em": "2026-08-04T23:52:03Z",
                "status": "success",
            }
        ]

        selected = select_missing_readings(candidates, existing, start, end)

        self.assertEqual([row["oferta_id"] for row in selected], [1784665252866])

    def test_reading_slot_uses_brazil_date_and_explicit_run_hour(self):
        read_at = datetime(2026, 8, 7, 1, 39, tzinfo=timezone.utc)

        self.assertEqual(reading_slot(123, read_at, 20), (123, "2026-08-06", 20))


if __name__ == "__main__":
    unittest.main()
