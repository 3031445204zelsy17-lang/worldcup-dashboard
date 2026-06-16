"""
P1-1 martj42 源单测(离线: 用本地 results.csv 副本, 不打网络).
跑: .venv/bin/python -m unittest backend.data.test_martj42
"""
import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data.schema import Status, known_teams  # noqa: E402
from backend.data.sources import martj42  # noqa: E402

LOCAL = martj42.LOCAL_COPY


@unittest.skipUnless(LOCAL.exists(), "无本地 results.csv 副本(P0-2 产出), 跳过")
class TestParseWC2026(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.df = pd.read_csv(LOCAL, parse_dates=["date"])
        cls.matches = martj42.parse_wc2026(cls.df)
        cls.known = known_teams()

    def test_72_matches(self):
        self.assertEqual(len(self.matches), 72)

    def test_12_finished_60_upcoming(self):
        fin = [m for m in self.matches if m.status is Status.FINISHED]
        upc = [m for m in self.matches if m.status is Status.UPCOMING]
        self.assertEqual(len(fin), 12)
        self.assertEqual(len(upc), 60)

    def test_finished_have_scores_upcoming_none(self):
        for m in self.matches:
            if m.status is Status.FINISHED:
                self.assertIsNotNone(m.home_score)
                self.assertIsNotNone(m.away_score)
            else:
                self.assertIsNone(m.home_score)
                self.assertIsNone(m.away_score)

    def test_neutral_passthrough(self):
        # Mexico v South Africa 在墨西哥城 → neutral=False(东道主本土)
        m = next(m for m in self.matches if m.home == "Mexico" and m.away == "South Africa"
                 and m.date == "2026-06-11")
        self.assertFalse(m.neutral)
        # Brazil v Morocco 在美国但都非本土 → neutral=True
        b = next(m for m in self.matches if m.home == "Brazil" and m.away == "Morocco")
        self.assertTrue(b.neutral)

    def test_team_names_in_known_48(self):
        # 72 场的队名必须全落 48 队口径(martj42 全称 = 模型层口径)
        for m in self.matches:
            self.assertIn(m.home, self.known, f"未知主队: {m.home}")
            self.assertIn(m.away, self.known, f"未知客队: {m.away}")

    def test_source_tag_propagates(self):
        tagged = martj42.parse_wc2026(self.df, source="martj42:offline")
        self.assertEqual(tagged[0].source, "martj42:offline")


@unittest.skipUnless(LOCAL.exists(), "无本地 results.csv 副本, 跳过")
class TestFetchOffline(unittest.TestCase):
    def test_fetch_offline_returns_matches(self):
        matches, src = martj42.fetch_wc2026(allow_network=False)
        self.assertEqual(src, "martj42:offline")
        self.assertEqual(len(matches), 72)


if __name__ == "__main__":
    unittest.main()
