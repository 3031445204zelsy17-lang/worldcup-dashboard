"""
P1-3 format.py 单测(赛制规则 + bracket 树, 纯逻辑).
跑: .venv/bin/python -m unittest backend.simulation.test_format
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.simulation import format as f  # noqa: E402


class TestParseSlot(unittest.TestCase):
    def test_all_types(self):
        self.assertEqual(f.parse_slot("1A"), ("gw", "A"))
        self.assertEqual(f.parse_slot("2L"), ("gr", "L"))
        self.assertEqual(f.parse_slot("3"), ("third", None))
        self.assertEqual(f.parse_slot("W73"), ("winner", 73))
        self.assertEqual(f.parse_slot("L101"), ("loser", 101))


class TestRankGroup(unittest.TestCase):
    def test_points_dominant(self):
        # A 全胜(9分), B/C/D 互平各 2 分 → B/C/D 同分同 gd(-3)同 gf(0), H2H 全平, 兜底 teams 序
        teams = ["A", "B", "C", "D"]
        res = {("A", "B"): (1, 0), ("A", "C"): (1, 0), ("A", "D"): (1, 0),
               ("B", "C"): (0, 0), ("B", "D"): (0, 0), ("C", "D"): (0, 0)}
        self.assertEqual(f.rank_group(teams, res), ["A", "B", "C", "D"])

    def test_gd_tiebreak(self):
        # A,B 同 5 分; A 净胜球 +2 > B +1 → A 第一
        teams = ["A", "B", "C", "D"]
        res = {("A", "B"): (1, 1), ("A", "C"): (2, 0), ("A", "D"): (0, 0),
               ("B", "C"): (1, 0), ("B", "D"): (0, 0), ("C", "D"): (1, 0)}
        self.assertEqual(f.rank_group(teams, res), ["A", "B", "C", "D"])

    def test_h2h_tiebreak(self):
        # C 5 分第一; A,B 同 4 分同 gd(0)同 gf(1) → H2H: A 胜 B → A 在 B 前; D 2 分最后
        teams = ["A", "B", "C", "D"]
        res = {("A", "B"): (1, 0), ("A", "C"): (0, 1), ("A", "D"): (0, 0),
               ("B", "C"): (0, 0), ("B", "D"): (1, 0), ("C", "D"): (1, 1)}
        self.assertEqual(f.rank_group(teams, res), ["C", "A", "B", "D"])


class TestBestThirds(unittest.TestCase):
    def test_top8_by_tiebreak(self):
        gt = {
            "A": ("tA", 7, 2, 5), "B": ("tB", 6, 1, 4), "C": ("tC", 6, 1, 3),
            "D": ("tD", 5, 0, 2), "E": ("tE", 4, -1, 1), "F": ("tF", 4, -2, 1),
            "G": ("tG", 3, -3, 0), "H": ("tH", 3, -4, 0), "I": ("tI", 2, -5, 0),
            "J": ("tJ", 1, -6, 0), "K": ("tK", 1, -7, 0), "L": ("tL", 0, -8, 0),
        }
        top8 = f.best_thirds(gt)
        self.assertEqual(len(top8), 8)
        self.assertEqual(top8[:2], ["A", "B"])           # pts 降序
        self.assertEqual(top8[2], "C")                   # B,C 同 6 分同 gd, gf B>C
        self.assertNotIn("L", top8)                      # 0 分的 L 不进


class TestAssignThirds(unittest.TestCase):
    def _valid(self, combo):
        a = f.assign_thirds(list(combo))
        if a is None:
            return False
        # 每个第三名位的分配满足候选池 + 8 个不同组
        return (all(a[m] in f.THIRD_POOL[m] for m in f.THIRD_MATCHES)
                and len(set(a.values())) == 8
                and set(a.values()) == set(combo))

    def test_combos_legal(self):
        # 抽样若干 8-组组合(含各种分布), 都应找到合法分配且满足候选池
        combos = [list("ABCDEFGH"), list("EFGHIJKL"), list("ABDEFGHI"),
                  list("ACEGIKMO")[:8] if False else list("ACDFGHIJ"),
                  list("BDEGIJKL")]
        for c in combos:
            self.assertTrue(self._valid(c), f"组合 {c} 无合法分配")

    def test_returns_none_on_impossible(self):
        # 构造无解: 候选池只含 ABCDF(M74), 给 8 个都不在任一池 → 无解
        # 实际 Annex C 保证 8 组合有解, 这里用一个不可能的「全相同」边界(重复)测鲁棒
        # third_groups 应是 8 个不同组; 传重复 → 回溯无法匹配 8 位 → None
        self.assertIsNone(f.assign_thirds(["A", "A", "A", "A", "A", "A", "A", "A"]))

    def test_all_495_combos_legal(self):
        # 生产级: 遍历全部 C(12,8)=495 个第三名组合, 每个都应有合法分配
        # (防 MC 在某个低概率组合崩溃; Annex C 保证有解, 此处验证回溯实现覆盖全部)
        from itertools import combinations
        bad = []
        for combo in combinations("ABCDEFGHIJKL", 8):
            a = f.assign_thirds(list(combo))
            if a is None or not (all(a[m] in f.THIRD_POOL[m] for m in f.THIRD_MATCHES)
                                 and len(set(a.values())) == 8 and set(a.values()) == set(combo)):
                bad.append(combo)
        self.assertEqual(bad, [], f"{len(bad)} 个第三名组合无合法解")


class TestBracketStructure(unittest.TestCase):
    def test_match_counts(self):
        self.assertEqual(len(f.R32), 16)
        self.assertEqual(len(f.R16), 8)
        self.assertEqual(len(f.QF), 4)
        self.assertEqual(len(f.SF), 2)
        self.assertEqual(len(f.THIRD_MATCHES), 8)

    def test_third_slots_are_away(self):
        # 第三名位("3")都在 away_slot, 且其 match_no 恰为 THIRD_MATCHES
        third_mno = [m[0] for m in f.R32 if m[2] == "3"]
        self.assertEqual(sorted(third_mno), sorted(f.THIRD_MATCHES))

    def test_winner_refs_close(self):
        # 所有 Wx / Lx 引用的 match_no 必须在更早轮次定义
        defined = set(m[0] for m in f.R32)
        for m in f.R16:
            self.assertEqual(f.parse_slot(m[1])[0], "winner")
            self.assertIn(f.parse_slot(m[1])[1], defined)
            self.assertIn(f.parse_slot(m[2])[1], defined)
        defined |= set(m[0] for m in f.R16)
        for m in f.QF:
            self.assertIn(f.parse_slot(m[1])[1], defined)
            self.assertIn(f.parse_slot(m[2])[1], defined)
        defined |= set(m[0] for m in f.QF)
        for m in f.SF:
            self.assertIn(f.parse_slot(m[1])[1], defined)
            self.assertIn(f.parse_slot(m[2])[1], defined)
        # Final 引用 SF 胜者
        self.assertIn(f.parse_slot(f.FINAL[1])[1], set(m[0] for m in f.SF))
        self.assertIn(f.parse_slot(f.FINAL[2])[1], set(m[0] for m in f.SF))

    def test_venues_all_mapped(self):
        # 所有 bracket 场地都在 VENUE_COUNTRY(东道主国, 供 neutral 判定)
        all_matches = list(f.R32) + list(f.R16) + list(f.QF) + list(f.SF) + [f.FINAL]
        for m in all_matches:
            self.assertIn(m[3], f.VENUE_COUNTRY, f"场地 {m[3]} 未映射")


class TestVenueNeutral(unittest.TestCase):
    def test_host_home_advantage(self):
        # 美国本土(单方东道主) → 非中立(享 γ)
        self.assertFalse(f.venue_neutral("United States", "Spain", "Inglewood"))
        self.assertFalse(f.venue_neutral("Mexico", "Spain", "Mexico City"))

    def test_both_neutral(self):
        self.assertTrue(f.venue_neutral("Spain", "France", "Houston"))

    def test_two_hosts_venue_decides(self):
        # 两东道主相遇: 一场一地, 场地国那方享 γ(不对消; venue_neutral 按场地判定,
        # 比 match_predictor.wc_neutral_host 按身份判定更准 —— 淘汰赛场地已知)
        self.assertFalse(f.venue_neutral("United States", "Canada", "Inglewood"))   # 美本土 → 美享γ
        self.assertFalse(f.venue_neutral("United States", "Canada", "Toronto"))     # 加本土 → 加享γ


if __name__ == "__main__":
    unittest.main()
