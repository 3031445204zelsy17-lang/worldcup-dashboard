"""
P1-4 API 端到端测试(TestClient)
================================
每端点 ≥1 测. 临时 DB + seed + 灌 2 场/2 队概率 + FakePredictor(避免每测 from_artifacts).
match_key 含 ``|``, 用 quote 模拟前端 encodeURIComponent.

跑: .venv/bin/python -m unittest backend.api.test_api
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backend.api.app import create_app  # noqa: E402
from backend.data.cache import MatchCache  # noqa: E402
from backend.data.schema import Match, Status, init_db, seed_teams, team_id_of  # noqa: E402


class FakePredictor:
    """固定输出, 验证序列化逻辑(score_matrix ndarray→list, top_scores tuple→list)."""

    def predict(self, home, away, host_home=None, host_away=None, top_n=5):
        sm = np.zeros((11, 11))
        sm[1, 0] = 0.15
        sm[0, 0] = 0.12
        return {
            "home": home, "away": away,
            "neutral": True, "host_home": False, "host_away": False,
            "home_win": 0.5, "draw": 0.3, "away_win": 0.2,
            "lambda_home": 1.4, "lambda_away": 1.1,
            "expected_home": 1.3, "expected_away": 1.0,
            "top_scores": [(1, 0, 0.15), (0, 0, 0.12)],
            "score_matrix": sm,
        }


def _make_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)                       # init_db 重建
    conn = init_db(path, all_tables=True)
    seed_teams(conn)
    MatchCache(conn).upsert_matches([
        Match(date="2026-06-17", home="Spain", away="Japan",
              home_score=None, away_score=None, status=Status.UPCOMING,
              neutral=True, source="martj42", kickoff="2026-06-17T19:00:00+00:00"),
        Match(date="2026-06-11", home="Mexico", away="South Africa",
              home_score=2, away_score=1, status=Status.FINISHED,
              neutral=False, source="martj42", kickoff="2026-06-11T19:00:00+00:00"),
    ])
    now = "2026-06-17T00:00:00+00:00"
    probs = {
        "Spain":     {"group": 1.0, "ro32": 0.90, "ro16": 0.60, "qf": 0.40, "sf": 0.25, "final": 0.15, "win": 0.14},
        "Argentina": {"group": 1.0, "ro32": 0.92, "ro16": 0.71, "qf": 0.45, "sf": 0.28, "final": 0.18, "win": 0.16},
    }
    for team, pr in probs.items():
        tid = team_id_of(conn, team)
        for rnd in ("group", "ro32", "ro16", "qf", "sf", "final"):
            conn.execute(
                "INSERT INTO tournament_probs (team_id, round, advancement_prob, win_prob, calculated_at) "
                "VALUES (?,?,?,?,?)", (tid, rnd, pr[rnd], pr["win"], now))
    conn.commit()
    conn.close()
    return path


class TestApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dbpath = _make_db()
        cls._orig_db = os.environ.get("API_DB")
        os.environ["API_DB"] = cls.dbpath
        cls.app = create_app()
        cls.app.state.predictor = FakePredictor()   # 绕过 lifespan(不加载真 DC)
        cls.app.state.dc_loaded = True
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        if cls._orig_db is None:
            os.environ.pop("API_DB", None)
        else:
            os.environ["API_DB"] = cls._orig_db
        Path(cls.dbpath).unlink(missing_ok=True)

    # ---- health ----
    def test_health(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        b = r.json()
        self.assertTrue(b["db_readable"])
        self.assertEqual(b["counts"]["teams"], 48)
        self.assertEqual(b["counts"]["matches"], 2)
        self.assertEqual(b["counts"]["tournament_probs"], 12)
        self.assertTrue(b["dc_artifacts_loaded"])

    # ---- teams ----
    def test_teams_sort_elo(self):
        b = self.client.get("/api/teams").json()
        self.assertEqual(b["count"], 48)
        elos = [t["elo"] for t in b["teams"]]
        self.assertEqual(elos, sorted(elos, reverse=True))
        self.assertEqual(b["teams"][0]["rank"], 1)

    def test_teams_group_filter(self):
        b = self.client.get("/api/teams?group=H").json()
        self.assertTrue(b["teams"])
        self.assertTrue(all(t["group"] == "H" for t in b["teams"]))

    def test_teams_sort_name_keeps_elo_rank(self):
        b = self.client.get("/api/teams?sort=name").json()
        # sort=name 改显示顺序, 但 rank 仍是 elo 排名(rank 1 应是 elo 最高队)
        rank1 = [t for t in b["teams"] if t["rank"] == 1][0]
        elos = [t["elo"] for t in b["teams"] if t["elo"] is not None]
        self.assertEqual(rank1["elo"], max(elos))

    # ---- tournament ----
    def test_tournament_view_win(self):
        b = self.client.get("/api/tournament").json()
        names = [t["name"] for t in b["teams"]]
        self.assertIn("Spain", names)
        self.assertIn("Argentina", names)
        self.assertLess(names.index("Argentina"), names.index("Spain"))  # 0.16>0.14
        # P1-6: 每队带当前 view 的 95% Wilson CI
        t0 = b["teams"][0]
        self.assertIsNotNone(t0["ci_low"])
        self.assertLessEqual(t0["ci_low"], t0["sort_value"])
        self.assertGreaterEqual(t0["ci_high"], t0["sort_value"])

    def test_tournament_view_ro16_switch(self):
        b = self.client.get("/api/tournament?view=ro16").json()
        self.assertEqual(b["view"], "ro16")
        names = [t["name"] for t in b["teams"]]
        self.assertLess(names.index("Argentina"), names.index("Spain"))  # 0.71>0.60

    def test_tournament_view_invalid(self):
        self.assertEqual(self.client.get("/api/tournament?view=bad").status_code, 400)

    def test_tournament_team_detail(self):
        b = self.client.get("/api/tournament/Spain").json()
        self.assertEqual(b["name"], "Spain")
        rounds = [s["round"] for s in b["advancement_path"]]
        self.assertEqual(rounds, ["group", "ro32", "ro16", "qf", "sf", "final", "win"])
        self.assertAlmostEqual(b["advancement_path"][-1]["prob"], 0.14)
        # P1-6: 每格带 CI, 区间包含点估计
        for step in b["advancement_path"]:
            self.assertLessEqual(step["ci_low"], step["prob"])
            self.assertGreaterEqual(step["ci_high"], step["prob"])
        self.assertTrue(all(m["home"] == "Spain" or m["away"] == "Spain" for m in b["matches"]))
        self.assertEqual(b["drivers"]["data_status"], "pending")

    def test_tournament_team_404(self):
        self.assertEqual(self.client.get("/api/tournament/NoSuchTeam").status_code, 404)

    # ---- matches ----
    def test_matches_filter_date(self):
        b = self.client.get("/api/matches?date=2026-06-17").json()
        self.assertEqual(b["count"], 1)
        self.assertEqual(b["matches"][0]["home"], "Spain")

    def test_matches_filter_team(self):
        b = self.client.get("/api/matches?team=Mexico").json()
        self.assertEqual(b["count"], 1)
        self.assertEqual(b["matches"][0]["home"], "Mexico")

    def test_matches_filter_status(self):
        b = self.client.get("/api/matches?status=finished").json()
        self.assertEqual(b["count"], 1)
        self.assertEqual(b["matches"][0]["status"], "finished")

    def test_match_detail_upcoming(self):
        key = quote("2026-06-17|Spain|Japan")
        r = self.client.get(f"/api/matches/{key}?predict=true")
        self.assertEqual(r.status_code, 200)
        b = r.json()
        self.assertEqual(b["match"]["status"], "upcoming")
        self.assertIsNone(b["score"])
        pred = b["prediction"]
        self.assertEqual(pred["home_win"], 0.5)
        self.assertEqual(len(pred["score_matrix"]), 11)              # 11×11
        self.assertEqual(len(pred["score_matrix"][0]), 11)
        self.assertEqual(pred["top_scores"][0], [1, 0, 0.15])        # tuple→list
        self.assertEqual(pred["max_goals"], 10)

    def test_match_detail_finished_score(self):
        key = quote("2026-06-11|Mexico|South Africa")
        b = self.client.get(f"/api/matches/{key}").json()
        self.assertEqual(b["score"], {"home": 2, "away": 1})

    def test_match_detail_no_predict(self):
        key = quote("2026-06-17|Spain|Japan")
        b = self.client.get(f"/api/matches/{key}?predict=false").json()
        self.assertIsNone(b["prediction"])

    def test_match_detail_404(self):
        self.assertEqual(self.client.get("/api/matches/" + quote("nope|x|y")).status_code, 404)

    # ---- methodology ----
    def test_methodology(self):
        b = self.client.get("/api/methodology").json()
        self.assertIn("Elo", b["algorithm_chain"])
        self.assertIn("Dixon-Coles", b["algorithm_chain"][1])
        if b["accuracy"] is not None:                                # parquet 存在时
            self.assertIn("dcs", b["accuracy"]["variants"])
            self.assertGreater(b["accuracy"]["n_matches"], 0)
        self.assertIsInstance(b["calibration"], list)


if __name__ == "__main__":
    unittest.main()
