"""
P1-1 schema.py 单测.
跑: .venv/bin/python -m unittest backend.data.test_schema
"""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data.schema import (  # noqa: E402
    ALL_DDL, P1_1_DDL, DEFAULT_GROUPS_CSV, Match, Status, init_db, known_teams,
    load_team_names, match_key, parse_status, seed_teams, team_id_of, validate_team,
)


class TestInitDb(unittest.TestCase):
    def _user_tables(self, conn):
        # 过滤 sqlite_sequence 等 sqlite_% 系统表(AUTOINCREMENT 自动生成)
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}

    def test_p1_1_builds_only_teams_matches(self):
        conn = init_db(":memory:")
        self.assertEqual(self._user_tables(conn), {"teams", "matches"})
        conn.close()

    def test_all_tables_builds_seven(self):
        conn = init_db(":memory:", all_tables=True)
        self.assertEqual(self._user_tables(conn),
                         {"teams", "matches", "events", "predictions",
                          "tournament_probs", "lineups", "injuries"})
        conn.close()

    def test_ddl_single_source_has_seven(self):
        # DDL 真相源: P1_1 两张 + 其余五张 = 7
        self.assertEqual(len(P1_1_DDL), 2)
        self.assertEqual(len(ALL_DDL), 7)


class TestSeedTeams(unittest.TestCase):
    def setUp(self):
        self.conn = init_db(":memory:")

    def tearDown(self):
        self.conn.close()

    def test_seeds_48(self):
        n = seed_teams(self.conn)
        self.assertEqual(n, 48)
        self.assertEqual(len(load_team_names(self.conn)), 48)

    def test_idempotent(self):
        seed_teams(self.conn)
        seed_teams(self.conn)              # 再灌一次
        self.assertEqual(len(load_team_names(self.conn)), 48)   # 不重复

    def test_team_id_roundtrip(self):
        seed_teams(self.conn)
        self.assertIsNotNone(team_id_of(self.conn, "Spain"))
        self.assertIsNone(team_id_of(self.conn, "Mars"))


class TestParseStatus(unittest.TestCase):
    def test_na_is_upcoming(self):
        self.assertIs(parse_status(None, None), Status.UPCOMING)
        self.assertIs(parse_status(None, 0), Status.UPCOMING)
        self.assertIs(parse_status(2, None), Status.UPCOMING)

    def test_scores_is_finished(self):
        self.assertIs(parse_status(2, 0), Status.FINISHED)
        self.assertIs(parse_status(0, 0), Status.FINISHED)


class TestValidateTeam(unittest.TestCase):
    def setUp(self):
        self.known = known_teams()

    def test_known_ok(self):
        ok, w = validate_team("Spain", self.known)
        self.assertTrue(ok)
        self.assertEqual(w, "")

    def test_unknown_warns_not_raise(self):
        # 淘汰赛占位符 / 历史小队 → warning, 但不崩
        ok, w = validate_team("Winner Group A", self.known)
        self.assertFalse(ok)
        self.assertIn("warning", w)


class TestMatchModel(unittest.TestCase):
    def _m(self, **kw):
        base = dict(date="2026-06-11", home="Mexico", away="South Africa",
                    home_score=2, away_score=0, status=Status.FINISHED,
                    neutral=False, source="martj42")
        base.update(kw)
        return Match(**base)

    def test_match_key_stable_unique(self):
        m = self._m()
        self.assertEqual(m.match_key, "2026-06-11|Mexico|South Africa")
        self.assertEqual(m.match_key, match_key(m.date, m.home, m.away))
        # 不同对阵 → 不同 key
        m2 = self._m(away="Brazil")
        self.assertNotEqual(m.match_key, m2.match_key)

    def test_to_row_fields(self):
        row = self._m().to_row()
        for k in ("date", "home", "away", "home_score", "away_score",
                  "status", "neutral", "stage", "kickoff", "source", "match_key"):
            self.assertIn(k, row)
        self.assertEqual(row["status"], "finished")
        self.assertEqual(row["neutral"], 0)      # bool → int
        self.assertEqual(row["match_key"], "2026-06-11|Mexico|South Africa")

    def test_finished_property(self):
        self.assertTrue(self._m(status=Status.FINISHED).finished)
        self.assertFalse(self._m(status=Status.UPCOMING,
                                 home_score=None, away_score=None).finished)


if __name__ == "__main__":
    unittest.main()
