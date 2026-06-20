"""
P1-2 worker.py 单测(tick 触发逻辑 + pidfile 守卫 + .env 加载 + 循环退出).

分两类:
- TestTickOrchestration: mock MonteCarloSimulator, 验触发/幂等/异常不杀/DB 单一真相源.
  → 不依赖网络/DC artifacts, 任何环境可跑.
- TestTickIntegration: 真实 collect_once + MC, 验端到端(288 行 + 夺冠和≈1 + 单调).
  → skipUnless 本地 results.csv + DC artifacts.

跑: .venv/bin/python -m unittest backend.test_worker
"""
import copy
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import worker as w  # noqa: E402
from backend.data.cache import MatchCache  # noqa: E402
from backend.data.collector import CollectResult, collect_once  # noqa: E402
from backend.data.schema import Match, Status, init_db, seed_teams  # noqa: E402
from backend.data.sources import martj42  # noqa: E402
from backend.simulation.mc import DEFAULT_DC_PARQUET  # noqa: E402

LOCAL = martj42.LOCAL_COPY


def _result(newly=None, fetched=0, source="martj42:offline", warnings=None):
    """造一个 CollectResult(测试注入 collect_fn 用)."""
    return CollectResult(fetched=fetched, source=source,
                         newly_finished=list(newly or []),
                         warnings=list(warnings or []))


def _m(**kw):
    base = dict(date="2026-06-15", home="Spain", away="Cape Verde",
                home_score=None, away_score=None, status=Status.UPCOMING,
                neutral=True, source="martj42")
    base.update(kw)
    return Match(**base)


class TestTickOrchestration(unittest.TestCase):
    """tick 编排逻辑(mock MC, 不碰网络/artifacts)."""

    def setUp(self):
        self.conn = init_db(":memory:", all_tables=True)
        seed_teams(self.conn)

    def tearDown(self):
        self.conn.close()

    @mock.patch("backend.worker.MonteCarloSimulator")
    def test_no_new_no_recompute(self, MockMC):
        # 冷启动/幂等: 无新完赛 → 不重算, MC 不被实例化
        res = w.tick(self.conn,
                     collect_fn=lambda conn, **kw: _result(newly=[]),
                     snapshot_on_recompute=False)
        self.assertFalse(res.recomputed)
        self.assertEqual(res.probs_rows, 0)
        self.assertEqual(res.elapsed_mc_ms, 0.0)
        MockMC.assert_not_called()

    @mock.patch("backend.worker.MonteCarloSimulator")
    def test_newly_triggers_recompute(self, MockMC):
        # 有新完赛 → 重算: 实例化 MC + run + save, probs_rows 透传
        sim_inst = MockMC.return_value
        sim_inst.run.return_value = {"Spain": {"win": 0.1}}
        sim_inst.to_dataframe.return_value = "df"
        sim_inst.save.return_value = 288
        res = w.tick(self.conn,
                     collect_fn=lambda conn, **kw: _result(newly=[_m(status=Status.FINISHED, home_score=1, away_score=0)]),
                     snapshot_on_recompute=False)
        self.assertTrue(res.recomputed)
        self.assertEqual(res.probs_rows, 288)
        self.assertGreater(res.elapsed_mc_ms, 0.0)
        MockMC.assert_called_once()              # new simulator
        sim_inst.run.assert_called_once()
        sim_inst.save.assert_called_once()

    @mock.patch("backend.worker.MonteCarloSimulator")
    def test_collect_fn_receives_snapshot_false(self, MockMC):
        # 密集轮询约定: worker 调采集时 snapshot=False(改重算时落)
        seen = {}
        def spy(conn, **kw):
            seen.update(kw)
            return _result(newly=[])
        w.tick(self.conn, collect_fn=spy, snapshot_on_recompute=False)
        self.assertFalse(seen["snapshot"])       # 不每 tick 落快照
        self.assertFalse(seen["fetch_backup"])   # 备源本轮关闭

    @mock.patch("backend.worker.MonteCarloSimulator")
    def test_mc_failure_doesnt_crash(self, MockMC):
        # 重算抛异常 → 记 warning, recomputed=False, 不抛出
        MockMC.side_effect = RuntimeError("boom")
        res = w.tick(self.conn,
                     collect_fn=lambda conn, **kw: _result(newly=[_m(status=Status.FINISHED)]),
                     snapshot_on_recompute=False)
        self.assertFalse(res.recomputed)
        self.assertTrue(any("MC 重算失败" in x for x in res.warnings))

    @mock.patch("backend.worker.MonteCarloSimulator")
    def test_warnings_propagate(self, MockMC):
        # 采集层的 warnings 透传到 TickResult
        res = w.tick(self.conn,
                     collect_fn=lambda conn, **kw: _result(newly=[], warnings=["未知队名 X"]),
                     snapshot_on_recompute=False)
        self.assertIn("未知队名 X", res.warnings)

    @mock.patch("backend.worker.MonteCarloSimulator")
    def test_fixtures_come_from_db(self, MockMC):
        # 核心正确性: 重算的 fixtures 来自 DB(单一真相源), 含 DB 里的实际比分,
        # 而非 martj42 离线默认副本.
        cache = MatchCache(self.conn)
        m = Match(date="2026-06-11", home="Mexico", away="South Africa",
                  home_score=2, away_score=1, status=Status.FINISHED,
                  neutral=False, source="martj42")
        cache.upsert_matches([m])                # 灌进 DB(比分 2-1)

        captured = {}
        def cap(dc, fixtures, seed):
            captured["fixtures"] = fixtures
            inst = mock.MagicMock()
            inst.run.return_value = {}
            inst.to_dataframe.return_value = []
            inst.save.return_value = 0
            return inst
        MockMC.side_effect = cap

        w.tick(self.conn,
               collect_fn=lambda conn, **kw: _result(newly=[m]),  # 触发重算
               snapshot_on_recompute=False)
        fx = captured["fixtures"]
        mx = [f for f in fx if f.home == "Mexico"]
        self.assertEqual(len(mx), 1)
        self.assertEqual((mx[0].home_score, mx[0].away_score), (2, 1))   # DB 比分透传


@unittest.skipUnless(LOCAL.exists() and DEFAULT_DC_PARQUET.exists(),
                     "无本地 results.csv 或 DC artifacts, 跳过集成测试")
class TestTickIntegration(unittest.TestCase):
    """端到端: 真实 collect_once(offline) → 翻转 → MC 重算 → 持久化."""

    @classmethod
    def setUpClass(cls):
        cls.real, _ = martj42.fetch_wc2026(allow_network=False)

    def setUp(self):
        # 用文件 DB(避免 :memory: 在某些跨函数边界被回收的坑; 此处单连接其实也行)
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._dbpath = path
        self.conn = init_db(path, all_tables=True)
        seed_teams(self.conn)

    def tearDown(self):
        self.conn.close()
        Path(self._dbpath).unlink(missing_ok=True)

    def test_cold_start_no_recompute(self):
        # 冷启动(DB 空)→ collect_once 不报新完赛 → 不重算
        res = w.tick(self.conn, n=200, snapshot_on_recompute=False, allow_network=False)
        self.assertEqual(res.fetched, 72)
        self.assertFalse(res.recomputed)

    def test_recompute_persists_288_rows(self):
        # 预热: 72 场全 upcoming → 采集真实(12 finished)→ 12 场翻转 → 重算
        cache = MatchCache(self.conn, snapshot_dir=tempfile.mkdtemp())
        cold = []
        for m in self.real:
            u = copy.copy(m)
            u.home_score = None; u.away_score = None; u.status = Status.UPCOMING
            cold.append(u)
        cache.upsert_matches(cold)

        res = w.tick(self.conn, n=200, snapshot_on_recompute=False,
                     allow_network=False, seed=42)
        self.assertTrue(res.recomputed)
        self.assertEqual(res.probs_rows, 288)
        self.assertEqual(res.newly_finished, 12)

        # tournament_probs 落表: 288 行(48 队 × 6 轮)
        cnt = self.conn.execute("SELECT COUNT(*) FROM tournament_probs").fetchone()[0]
        self.assertEqual(cnt, 288)
        # 每队恰好 6 行
        per_team = self.conn.execute(
            "SELECT COUNT(*) FROM tournament_probs GROUP BY team_id").fetchall()
        self.assertTrue(all(c[0] == 6 for c in per_team))
        self.assertEqual(len(per_team), 48)
        # 夺冠和≈1(final 行每队一条, win_prob = 夺冠概率)
        win_sum = self.conn.execute(
            "SELECT SUM(win_prob) FROM tournament_probs WHERE round='final'").fetchone()[0]
        self.assertAlmostEqual(win_sum, 1.0, places=2)

    def test_advancement_monotonic(self):
        # 晋级阶梯单调: group(=1) ≥ ro32 ≥ ro16 ≥ qf ≥ sf ≥ final(=win)
        cache = MatchCache(self.conn, snapshot_dir=tempfile.mkdtemp())
        cold = [copy.copy(m) for m in self.real]
        for u in cold:
            u.home_score = None; u.away_score = None; u.status = Status.UPCOMING
        cache.upsert_matches(cold)
        w.tick(self.conn, n=200, snapshot_on_recompute=False, allow_network=False, seed=42)

        # 抽一支强队(Argentina)看单调
        row = self.conn.execute(
            """SELECT round, advancement_prob FROM tournament_probs
               WHERE team_id=(SELECT id FROM teams WHERE name='Argentina')
               ORDER BY CASE round
                   WHEN 'group' THEN 1 WHEN 'ro32' THEN 2 WHEN 'ro16' THEN 3
                   WHEN 'qf' THEN 4 WHEN 'sf' THEN 5 WHEN 'final' THEN 6 END""",
        ).fetchall()
        probs = [r[1] for r in row]              # [group, ro32, ro16, qf, sf, final]
        self.assertAlmostEqual(probs[0], 1.0, places=6)   # group 恒 1
        for i in range(len(probs) - 1):
            self.assertGreaterEqual(probs[i] + 1e-9, probs[i + 1])   # 单调不增


class TestPidfile(unittest.TestCase):
    """单实例 pidfile 守卫."""

    def test_acquire_release(self):
        pf = Path(tempfile.mkdtemp()) / "w.pid"
        w.acquire_pidfile(pf)
        self.assertEqual(pf.read_text(), str(os.getpid()))
        self.assertTrue(pf.exists())
        w.release_pidfile(pf)
        self.assertFalse(pf.exists())

    def test_release_missing_is_noop(self):
        pf = Path(tempfile.mkdtemp()) / "absent.pid"
        w.release_pidfile(pf)                    # 不抛

    def test_same_pid_reacquire_ok(self):
        # 同 PID 再 acquire = 重启覆盖自己 stale pidfile, 放行
        pf = Path(tempfile.mkdtemp()) / "w.pid"
        w.acquire_pidfile(pf)
        w.acquire_pidfile(pf)                    # 不抛
        w.release_pidfile(pf)

    @mock.patch("backend.worker._pid_alive", return_value=True)
    def test_blocks_when_other_live(self, _):
        pf = Path(tempfile.mkdtemp()) / "w.pid"
        pf.write_text("12345")                   # 别的"活着"的 PID
        with self.assertRaises(SystemExit):
            w.acquire_pidfile(pf)

    @mock.patch("backend.worker._pid_alive", return_value=False)
    def test_overwrites_dead_pid(self, _):
        pf = Path(tempfile.mkdtemp()) / "w.pid"
        pf.write_text("999999")                  # 死 PID → stale, 覆盖
        w.acquire_pidfile(pf)
        self.assertEqual(pf.read_text(), str(os.getpid()))
        w.release_pidfile(pf)

    def test_pid_alive_helpers(self):
        self.assertTrue(w._pid_alive(os.getpid()))            # 自己活着
        self.assertFalse(w._pid_alive(999999))                # 死 PID


class TestDotenv(unittest.TestCase):
    """极简 .env 加载(不覆盖已设 env)."""

    def setUp(self):
        self._key = "WC_WORKER_TEST_FOO"
        os.environ.pop(self._key, None)

    def tearDown(self):
        os.environ.pop(self._key, None)

    def test_loads_simple_and_quoted(self):
        envf = Path(tempfile.mkdtemp()) / ".env"
        envf.write_text(f'{self._key}=bar\n# comment\n{self._key}2="z z"\n')
        w.load_dotenv(envf)
        self.assertEqual(os.environ.get(self._key), "bar")

    def test_no_override_existing(self):
        # 已设的 env 优先(部署平台注入不被 .env 覆盖)
        os.environ[self._key] = "fromenv"
        envf = Path(tempfile.mkdtemp()) / ".env"
        envf.write_text(f"{self._key}=fromfile\n")
        w.load_dotenv(envf)
        self.assertEqual(os.environ[self._key], "fromenv")

    def test_missing_file_is_noop(self):
        w.load_dotenv(Path(tempfile.mkdtemp()) / "nope.env")   # 不抛


class TestRunLoop(unittest.TestCase):
    """run_forever 循环退出 + pidfile 释放(用 max_ticks 避免阻塞)."""

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._dbpath = path
        self.conn = init_db(path, all_tables=True)
        seed_teams(self.conn)

    def tearDown(self):
        self.conn.close()
        Path(self._dbpath).unlink(missing_ok=True)

    def test_max_ticks_exits_and_releases_pidfile(self):
        calls = []
        def fake_collect(conn, **kw):
            calls.append(1)
            return _result(newly=[])             # 无新完赛, 不重算 → 不碰 MC/DC
        pf = Path(tempfile.mkdtemp()) / "w.pid"
        # dc=object() 占位: truthy → run_forever 不 from_artifacts; tick 不重算时不碰它
        w.run_forever(self.conn, interval=0, max_ticks=3,
                      collect_fn=fake_collect, pidfile=pf, dc=object(),
                      live_enabled=False)   # 关 live_tick 避免联网 ESPN
        self.assertEqual(len(calls), 3)
        self.assertFalse(pf.exists())            # finally 释放

    def test_max_ticks_recomputes_each_new(self):
        # 每轮都报新完赛 → 每轮重算(mock MC); 验循环内重算路径走通
        calls = []
        def fake_collect(conn, **kw):
            calls.append(1)
            return _result(newly=[_m(status=Status.FINISHED)])
        with mock.patch("backend.worker.MonteCarloSimulator") as MockMC:
            MockMC.return_value.run.return_value = {}
            MockMC.return_value.to_dataframe.return_value = []
            MockMC.return_value.save.return_value = 288
            w.run_forever(self.conn, interval=0, max_ticks=2,
                          collect_fn=fake_collect, dc=object(),
                          live_enabled=False)   # 关 live_tick 避免联网
        self.assertEqual(len(calls), 2)
        self.assertEqual(MockMC.return_value.save.call_count, 2)


def _live_dc(teams=("Spain", "Brazil")):
    """synthetic DC for live_tick tests(队名落 48 队口径, seed_teams 能查到)."""
    return SimpleNamespace(
        mu=0.0, gamma=0.25,
        attack={teams[0]: 1.5, teams[1]: 1.0},
        defense={teams[0]: 1.0, teams[1]: 1.3},
        teams=list(teams))


class TestLiveTick(unittest.TestCase):
    """P2-1 live_tick: ESPN live → LiveMatchSimulator → predictions 写入 + helpers(不联网)."""

    def setUp(self):
        self.conn = init_db(":memory:", all_tables=True)
        seed_teams(self.conn)
        m = Match(date="2026-06-19", home="Spain", away="Brazil",
                  home_score=None, away_score=None, status=Status.LIVE,
                  neutral=True, source="martj42")
        MatchCache(self.conn).upsert_matches([m])
        self.dc = _live_dc()
        self.match_id = w._find_match_id(self.conn, "Spain", "Brazil")

    def tearDown(self):
        self.conn.close()

    def test_find_match_id_by_team_suffix(self):
        self.assertIsNotNone(self.match_id)
        self.assertIsNone(w._find_match_id(self.conn, "Mars", "Venus"))

    def test_count_reds_normalizes_names(self):
        # ESPN competitors 'Cape Verde' vs events 'Cabo Verde' → 归一后算客队红牌
        events = [{"type": "card", "is_red": True, "team": "Cabo Verde", "minute": 60}]
        self.assertEqual(w._count_reds(events, "Spain", "Cape Verde"), (0, 1))

    def test_no_live_returns_empty(self):
        res = w.live_tick(self.conn, dc=self.dc, fetch_scoreboard_fn=lambda: [])
        self.assertEqual(res.live_matches, 0)
        self.assertEqual(res.updated, 0)

    def test_no_network_returns_empty(self):
        res = w.live_tick(self.conn, dc=self.dc, allow_network=False)
        self.assertEqual(res.updated, 0)

    def test_scoreboard_degrade_warns(self):
        res = w.live_tick(self.conn, dc=self.dc, fetch_scoreboard_fn=lambda: None)
        self.assertEqual(res.updated, 0)
        self.assertTrue(any("scoreboard" in x for x in res.warnings))

    def test_live_writes_prediction(self):
        sb = [{"match_id": "1", "status_state": "in", "home": "Spain", "away": "Brazil",
               "home_score": 1, "away_score": 0}]
        summ = {"match_id": "1", "status_state": "in", "home": "Spain", "away": "Brazil",
                "home_score": 1, "away_score": 0, "minute": 70,
                "events": [{"type": "card", "is_red": True, "team": "Brazil",
                            "player": "X", "minute": 60}]}
        res = w.live_tick(self.conn, dc=self.dc, seed=42,
                          fetch_scoreboard_fn=lambda: sb,
                          fetch_summary_fn=lambda mid: summ)
        self.assertEqual(res.live_matches, 1)
        self.assertEqual(res.updated, 1)
        cnt = self.conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
        self.assertEqual(cnt, 1)
        minute, hw, aw = self.conn.execute(
            "SELECT minute, home_win_prob, away_win_prob FROM predictions").fetchone()
        self.assertEqual(minute, 70)
        self.assertGreater(hw, aw)    # Spain 1-0 70' + Brazil 红牌 → 主胜 > 客胜

    def test_upsert_idempotent(self):
        mid = self.match_id
        w._upsert_prediction(self.conn, mid, 70,
                             {"home_win": 0.6, "draw": 0.2, "away_win": 0.2}, "v1")
        w._upsert_prediction(self.conn, mid, 70,
                             {"home_win": 0.7, "draw": 0.2, "away_win": 0.1}, "v1")
        cnt = self.conn.execute(
            "SELECT COUNT(*) FROM predictions WHERE match_id=? AND minute=70", (mid,)).fetchone()[0]
        self.assertEqual(cnt, 1)      # 幂等: 同 minute+version 只 1 行(更新非新增)

    def test_team_not_in_dc_skipped(self):
        sb = [{"match_id": "1", "status_state": "in", "home": "Mars", "away": "Venus"}]
        summ = {"match_id": "1", "status_state": "in", "home": "Mars", "away": "Venus",
                "home_score": 0, "away_score": 0, "minute": 30, "events": []}
        res = w.live_tick(self.conn, dc=self.dc,
                          fetch_scoreboard_fn=lambda: sb,
                          fetch_summary_fn=lambda mid: summ)
        self.assertEqual(res.updated, 0)
        self.assertTrue(any("未落 DC" in x for x in res.warnings))


if __name__ == "__main__":
    unittest.main()
