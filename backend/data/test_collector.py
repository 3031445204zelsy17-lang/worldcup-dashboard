"""
P1-1 collector.py 单测(detect_newly_finished 纯函数 + collect_once 集成).
跑: .venv/bin/python -m unittest backend.data.test_collector
"""
import copy
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data import collector as c  # noqa: E402
from backend.data.cache import MatchCache  # noqa: E402
from backend.data.schema import Match, Status, init_db, seed_teams  # noqa: E402
from backend.data.sources import martj42  # noqa: E402

LOCAL = martj42.LOCAL_COPY


def _m(**kw):
    base = dict(date="2026-06-15", home="Spain", away="Cape Verde",
                home_score=None, away_score=None, status=Status.UPCOMING,
                neutral=True, source="martj42")
    base.update(kw)
    return Match(**base)


class TestDetectNewlyFinished(unittest.TestCase):
    def test_empty_old_returns_nothing(self):
        newly, w = c.detect_newly_finished({}, {})
        self.assertEqual(newly, [])
        self.assertEqual(w, [])

    def test_upcoming_to_finished(self):
        old = _m()
        new = _m(home_score=3, away_score=0, status=Status.FINISHED)
        newly, w = c.detect_newly_finished({old.match_key: old}, {new.match_key: new})
        self.assertEqual(len(newly), 1)
        self.assertEqual(w, [])

    def test_first_seen_not_reported(self):
        # old 里没有的 key(首轮见到)= 不报, 避免冷启动噪声
        new = _m(home_score=3, away_score=0, status=Status.FINISHED)
        newly, _ = c.detect_newly_finished({}, {new.match_key: new})
        self.assertEqual(newly, [])

    def test_score_correction_warns(self):
        old = _m(home_score=2, away_score=0, status=Status.FINISHED)
        new = _m(home_score=2, away_score=1, status=Status.FINISHED)   # 比分修正
        newly, w = c.detect_newly_finished({old.match_key: old}, {new.match_key: new})
        self.assertEqual(newly, [])          # 不算新完赛
        self.assertEqual(len(w), 1)          # 只记 warning
        self.assertIn("比分修正", w[0])

    def test_still_upcoming_no_change(self):
        old = _m(); new = _m()
        newly, w = c.detect_newly_finished({old.match_key: old}, {new.match_key: new})
        self.assertEqual(newly, [])
        self.assertEqual(w, [])


@unittest.skipUnless(LOCAL.exists(), "无本地 results.csv 副本, 跳过")
class TestCollectOnce(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.real, _ = martj42.fetch_wc2026(allow_network=False)

    def _new_conn(self):
        conn = init_db(":memory:")
        seed_teams(conn)
        return conn

    def test_cold_start_no_new_finished(self):
        conn = self._new_conn()
        r = c.collect_once(conn, allow_network=False, fetch_backup=False, snapshot=False)
        self.assertEqual(r.fetched, 72)
        self.assertEqual(r.newly_finished, [])   # 冷启动(DB 空)→ 不报
        self.assertEqual(r.source, "martj42:offline")
        self.assertEqual(r.skipped, [])
        conn.close()

    def test_idempotent_second_round(self):
        conn = self._new_conn()
        c.collect_once(conn, allow_network=False, fetch_backup=False, snapshot=False)
        r2 = c.collect_once(conn, allow_network=False, fetch_backup=False, snapshot=False)
        self.assertEqual(r2.newly_finished, [])   # 第二轮无新完赛
        conn.close()

    def test_detects_newly_finished_after_warmup(self):
        conn = self._new_conn()
        cache = MatchCache(conn, snapshot_dir=tempfile.mkdtemp())
        # 预热: 把真实 72 场全部灌成 upcoming(模拟赛前快照)
        cold = []
        for m in self.real:
            u = copy.copy(m)
            u.home_score = None; u.away_score = None; u.status = Status.UPCOMING
            cold.append(u)
        cache.upsert_matches(cold)
        # 采集: parse 真实版(12 场已 finished)→ detect 12 场翻转
        r = c.collect_once(conn, allow_network=False, fetch_backup=False, snapshot=False)
        self.assertEqual(r.fetched, 72)
        self.assertEqual(len(r.newly_finished), 12)
        self.assertTrue(all(m.status is Status.FINISHED for m in r.newly_finished))
        conn.close()


if __name__ == "__main__":
    unittest.main()
