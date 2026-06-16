"""
P1-1 cache.py 单测(三层缓存: 内存 TTL / SQLite upsert / Parquet 快照).
跑: .venv/bin/python -m unittest backend.data.test_cache
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data.cache import MatchCache  # noqa: E402
from backend.data.schema import Match, Status, init_db, seed_teams  # noqa: E402
from backend.data.sources import martj42  # noqa: E402

LOCAL = martj42.LOCAL_COPY


def _match(**kw):
    base = dict(date="2026-06-15", home="Spain", away="Cape Verde",
                home_score=None, away_score=None, status=Status.UPCOMING,
                neutral=True, source="martj42")
    base.update(kw)
    return Match(**base)


@unittest.skipUnless(LOCAL.exists(), "无本地 results.csv 副本, 跳过")
class TestUpsertLoad(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.real, _ = martj42.fetch_wc2026(allow_network=False)

    def setUp(self):
        self.conn = init_db(":memory:")
        seed_teams(self.conn)
        self.tmp = tempfile.mkdtemp()
        self.cache = MatchCache(self.conn, snapshot_dir=self.tmp)

    def tearDown(self):
        self.conn.close()

    def test_upsert_then_load_72(self):
        written, skipped = self.cache.upsert_matches(self.real)
        self.assertEqual(written, 72)
        self.assertEqual(skipped, [])
        loaded = self.cache.get_all()
        self.assertEqual(len(loaded), 72)
        fin = [m for m in loaded if m.status is Status.FINISHED]
        self.assertEqual(len(fin), 12)

    def test_upsert_idempotent(self):
        self.cache.upsert_matches(self.real)
        written2, _ = self.cache.upsert_matches(self.real)   # 再灌
        self.assertEqual(written2, 72)                       # 仍 72(ON CONFLICT 更新)
        self.assertEqual(len(self.cache.get_all()), 72)       # 不重复

    def test_status_change_upcoming_to_finished(self):
        # 先灌 upcoming 版 → 再灌 finished 版 → 读回应 finished
        upcoming = _match(home="Spain", away="Cape Verde", date="2026-06-15")
        self.cache.upsert_matches([upcoming])
        self.assertIs(self.cache.get(upcoming.match_key).status, Status.UPCOMING)
        finished = _match(home="Spain", away="Cape Verde", date="2026-06-15",
                          home_score=3, away_score=0, status=Status.FINISHED)
        self.cache.upsert_matches([finished])
        got = self.cache.get(finished.match_key)
        self.assertIs(got.status, Status.FINISHED)
        self.assertEqual((got.home_score, got.away_score), (3, 0))


class TestMemoryTTL(unittest.TestCase):
    def setUp(self):
        self.conn = init_db(":memory:")
        seed_teams(self.conn)
        self.m = _match()

    def tearDown(self):
        self.conn.close()

    def test_negative_ttl_expires_immediately(self):
        cache = MatchCache(self.conn, snapshot_dir=tempfile.mkdtemp(), ttl=-1)
        cache._mem_put(self.m)
        self.assertIsNone(cache._mem_get(self.m.match_key))   # 负 ttl → 立即过期

    def test_long_ttl_hits(self):
        cache = MatchCache(self.conn, snapshot_dir=tempfile.mkdtemp(), ttl=9999)
        cache._mem_put(self.m)
        self.assertIsNotNone(cache._mem_get(self.m.match_key))  # 长 ttl → 命中


@unittest.skipUnless(LOCAL.exists(), "无本地 results.csv 副本, 跳过")
class TestSnapshotRoundtrip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.real, _ = martj42.fetch_wc2026(allow_network=False)

    def test_roundtrip_preserves_fields(self):
        conn = init_db(":memory:"); seed_teams(conn)
        cache = MatchCache(conn, snapshot_dir=tempfile.mkdtemp())
        snap = cache.snapshot(self.real, tag="test")
        restored = cache.load_snapshot(snap)
        self.assertEqual(len(restored), len(self.real))
        by_key = {m.match_key: m for m in self.real}
        for r in restored:
            o = by_key[r.match_key]
            self.assertEqual(r.status, o.status)
            self.assertEqual(r.neutral, o.neutral)
            self.assertEqual((r.home_score, r.away_score),
                             (o.home_score, o.away_score))
        conn.close()


class TestSkippedUnknown(unittest.TestCase):
    def test_unknown_team_skipped(self):
        conn = init_db(":memory:"); seed_teams(conn)
        cache = MatchCache(conn, snapshot_dir=tempfile.mkdtemp())
        m = Match(date="2026-07-01", home="Winner Group A", away="Runner Group B",
                  home_score=None, away_score=None, status=Status.UPCOMING,
                  neutral=True, source="martj42")
        written, skipped = cache.upsert_matches([m])
        self.assertEqual(written, 0)
        self.assertEqual(len(skipped), 1)
        conn.close()


if __name__ == "__main__":
    unittest.main()
