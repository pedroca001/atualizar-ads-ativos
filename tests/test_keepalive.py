import unittest
from pathlib import Path
from subprocess import run
from sys import executable

from scripts.keepalive import keepalive_due

REPO_ROOT = Path(__file__).resolve().parents[1]


class KeepaliveDueTests(unittest.TestCase):
    def test_is_due_at_45_days_without_repository_activity(self):
        day = 24 * 60 * 60

        self.assertTrue(keepalive_due(1_000, 1_000 + 45 * day))

    def test_cli_reports_false_before_45_days(self):
        day = 24 * 60 * 60

        completed = run(
            [
                executable,
                str(REPO_ROOT / "scripts" / "keepalive.py"),
                "1000",
                str(1_000 + 44 * day),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.stdout.strip(), "false")


if __name__ == "__main__":
    unittest.main()
