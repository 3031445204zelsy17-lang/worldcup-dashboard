"""P2-1 ESPN source 单测 —— 离线喂 fixture, 不联网.

测 parse_scoreboard/parse_summary 纯函数 + map_team + helpers + 优雅降级.
fixture: backend/data/fixtures/espn_{scoreboard,summary}_sample.json (2026-06-19 实测存).
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from backend.data.sources import espn

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class TestParseScoreboard(unittest.TestCase):
    def setUp(self):
        with open(FIXTURES / "espn_scoreboard_sample.json") as f:
            self.d = json.load(f)

    def test_returns_matches(self):
        r = espn.parse_scoreboard(self.d)
        self.assertEqual(len(r), 4)

    def test_match_fields(self):
        r = espn.parse_scoreboard(self.d)
        for m in r:
            self.assertIn("match_id", m)
            self.assertIn("status_state", m)
            self.assertIn("status_detail", m)
            self.assertIn("home", m)
            self.assertIn("away", m)
            self.assertIn("home_score", m)
            self.assertIn("away_score", m)

    def test_status_state_valid(self):
        r = espn.parse_scoreboard(self.d)
        for m in r:
            self.assertIn(m["status_state"], ("pre", "in", "post"))

    def test_home_away_populated(self):
        r = espn.parse_scoreboard(self.d)
        for m in r:
            self.assertIsNotNone(m["home"])
            self.assertIsNotNone(m["away"])


class TestParseSummary(unittest.TestCase):
    def setUp(self):
        with open(FIXTURES / "espn_summary_sample.json") as f:
            self.d = json.load(f)
        self.r = espn.parse_summary(self.d)

    def test_teams(self):
        # Spain(home) vs Cape Verde(away)
        self.assertEqual(self.r["home"], "Spain")
        self.assertEqual(self.r["away"], "Cape Verde")

    def test_status_post_minute_90(self):
        self.assertEqual(self.r["status_state"], "post")
        self.assertEqual(self.r["minute"], 90)

    def test_events_only_goal_card_sub(self):
        # 只保留 goal/card/sub, 忽略 delay/kickoff/halftime/end-regular-time
        types = {e["type"] for e in self.r["events"]}
        self.assertTrue(types <= {"goal", "card", "sub"},
                        f"意外事件类型: {types}")

    def test_events_have_required_fields(self):
        for e in self.r["events"]:
            self.assertIn("type", e)
            self.assertIn("is_red", e)
            self.assertIn("team", e)
            self.assertIn("player", e)
            self.assertIn("minute", e)

    def test_yellow_card_player_extracted(self):
        cards = [e for e in self.r["events"] if e["type"] == "card"]
        self.assertGreaterEqual(len(cards), 1)   # Spain-Cape Verde 实测有黄牌
        self.assertTrue(any(e["player"] for e in cards),
                        "黄牌球员名未提取")

    def test_minute_from_clock_value(self):
        # 所有事件 minute 应为正整数(clock.value//60+1)
        for e in self.r["events"]:
            self.assertIsInstance(e["minute"], int)
            self.assertGreater(e["minute"], 0)


class TestMapTeam(unittest.TestCase):
    def test_dict_mapping(self):
        self.assertEqual(espn.map_team("Türkiye"), "Turkey")
        self.assertEqual(espn.map_team("Czechia"), "Czech Republic")
        self.assertEqual(espn.map_team("Cabo Verde"), "Cape Verde")
        self.assertEqual(espn.map_team("IR Iran"), "Iran")
        self.assertEqual(espn.map_team("Côte d'Ivoire"), "Ivory Coast")

    def test_passthrough_no_known(self):
        # 无 known 集合 → 不在 dict 的原样返回
        self.assertEqual(espn.map_team("Spain"), "Spain")
        self.assertEqual(espn.map_team("Brazil"), "Brazil")

    def test_known_exact(self):
        known = {"Spain", "Cape Verde", "Turkey"}
        self.assertEqual(espn.map_team("Spain", known), "Spain")
        self.assertEqual(espn.map_team("Cape Verde", known), "Cape Verde")

    def test_known_not_present_returns_none(self):
        known = {"Spain", "Turkey"}
        # 不在 dict 且不在 known → None(调用方 skip)
        self.assertIsNone(espn.map_team("Mars United", known))

    def test_none_empty_input(self):
        self.assertIsNone(espn.map_team(None))
        self.assertIsNone(espn.map_team(""))


class TestHelpers(unittest.TestCase):
    def test_clock_to_minute(self):
        self.assertEqual(espn._clock_to_minute(933), 16)    # 933s → 16'
        self.assertEqual(espn._clock_to_minute(0), 1)       # kickoff → 1'
        self.assertEqual(espn._clock_to_minute(2700), 46)   # 半场 2700s → 46'
        self.assertIsNone(espn._clock_to_minute(None))

    def test_extract_player_from_text(self):
        ke = {"text": "Sidny Cabral (Cabo Verde) is shown the yellow card for a bad foul."}
        self.assertEqual(espn._extract_player(ke), "Sidny Cabral")

    def test_extract_player_participants_preferred(self):
        ke = {"participants": [{"athlete": {"displayName": "Lionel Messi"}}], "text": "其他文本 is shown"}
        self.assertEqual(espn._extract_player(ke), "Lionel Messi")

    def test_to_int(self):
        self.assertEqual(espn._to_int("2"), 2)
        self.assertEqual(espn._to_int(2), 2)
        self.assertIsNone(espn._to_int(None))
        self.assertIsNone(espn._to_int(""))


class TestGracefulDegrade(unittest.TestCase):
    """_get 失败(网络/格式/限流) → fetch 返 None, 不抛异常."""

    def test_fetch_returns_none_when_get_fails(self):
        orig = espn._get
        espn._get = lambda url: None
        try:
            self.assertIsNone(espn.fetch_scoreboard("20260619"))
            self.assertIsNone(espn.fetch_summary("760428"))
        finally:
            espn._get = orig

    def test_parse_handles_empty(self):
        # 空/缺字段 JSON 不崩
        self.assertEqual(espn.parse_scoreboard({}), [])
        r = espn.parse_summary({})
        self.assertEqual(r["events"], [])
        self.assertIsNone(r["home"])


if __name__ == "__main__":
    unittest.main()
