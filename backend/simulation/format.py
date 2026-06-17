"""
P1-3 赛制规则 + bracket 树(纯逻辑, 无 IO/无随机, 易测)
==========================================================
2026 FIFA World Cup「首次 48 队」新赛制.
权威核实: Wikipedia「2026 FIFA World Cup knockout stage」+ FIFA 2026 Regulations Annex C.

赛制
----
- 小组赛: 12 组(A-L)× 4 队 = 72 场. 每组前 2 + 8 最佳第三 = 32 强.
- 小组排名 tiebreaker: 积分 → 净胜球 → 进球 → 直接交锋(H2H) → 公平/世界排名(留确定性兜底).
- R32 固定 bracket(M73-M88, 16 场, 含场地); R16/QF/SF/Final/第三名固定树.
- 8 个组冠军位(M74/77/79/80/81/82/85/87)各对一个第三名, 候选组池见 THIRD_POOL;
  第三名分配用回溯匹配(合法一对一), 不硬编码 FIFA 495 行表(精度影响极小, 见 mc.py/P1-3 note).

设计: 纯函数 + 常量, 不导入 worldcup_2026_groups(48 队口径), 不做采样/IO.
供 mc.py 调用 + test_format 单测.
"""
from __future__ import annotations

# 12 组标签(48 队口径在 worldcup_2026_groups.GROUPS, 本模块只用 label)
GROUP_LABELS = list("ABCDEFGHIJKL")

# 东道主国(队名口径, 对齐 match_predictor.WC2026_HOSTS)
HOST_COUNTRIES = {"United States", "Canada", "Mexico"}

# R32 场地城市 → 东道主国(淘汰赛 neutral 判定用; 淘汰赛场地全在美/加/墨)
VENUE_COUNTRY = {
    "Inglewood": "United States", "Foxborough": "United States",
    "Houston": "United States", "East Rutherford": "United States",
    "Arlington": "United States", "Santa Clara": "United States",
    "Seattle": "United States", "Miami Gardens": "United States",
    "Kansas City": "United States", "Philadelphia": "United States",
    "Atlanta": "United States",
    "Toronto": "Canada", "Vancouver": "Canada",
    "Mexico City": "Mexico", "Guadalupe": "Mexico",
}

# ============================================================
# bracket 树 —— match_no → (round, home_slot, away_slot, venue)
# slot: "1X"=组X冠军 · "2X"=组X亚军 · "3"=第三名(待 assign_thirds 分配)
#       "W73"=73场胜者 · "L101"=101场败者
# ============================================================
# R32: 16 场(M73-M88). 第三名位(away="3")在 M74/77/79/80/81/82/85/87.
R32 = [
    (73, "2A", "2B", "Inglewood"),
    (74, "1E", "3", "Foxborough"),
    (75, "1F", "2C", "Guadalupe"),
    (76, "1C", "2F", "Houston"),
    (77, "1I", "3", "East Rutherford"),
    (78, "2E", "2I", "Arlington"),
    (79, "1A", "3", "Mexico City"),
    (80, "1L", "3", "Atlanta"),
    (81, "1D", "3", "Santa Clara"),
    (82, "1G", "3", "Seattle"),
    (83, "2K", "2L", "Toronto"),
    (84, "1H", "2J", "Inglewood"),
    (85, "1B", "3", "Vancouver"),
    (86, "1J", "2H", "Miami Gardens"),
    (87, "1K", "3", "Kansas City"),
    (88, "2D", "2G", "Arlington"),
]
# 第三名候选组池(Annex C 抽取的固定候选): match_no → 该位第三名可来自哪些组
THIRD_POOL = {
    74: list("ABCDF"), 77: list("CDFGH"), 79: list("CEFHI"), 80: list("EHIJK"),
    81: list("BEFIJ"), 82: list("AEHIJ"), 85: list("EFGIJ"), 87: list("DEIJL"),
}
THIRD_MATCHES = [74, 77, 79, 80, 81, 82, 85, 87]   # 8 个对第三名的 R32 位

R16 = [
    (89, "W74", "W77", "Philadelphia"),
    (90, "W73", "W75", "Houston"),
    (91, "W76", "W78", "East Rutherford"),
    (92, "W79", "W80", "Mexico City"),
    (93, "W83", "W84", "Arlington"),
    (94, "W81", "W82", "Seattle"),
    (95, "W86", "W88", "Atlanta"),
    (96, "W85", "W87", "Vancouver"),
]
QF = [
    (97, "W89", "W90", "Foxborough"),
    (98, "W93", "W94", "Inglewood"),
    (99, "W91", "W92", "Miami Gardens"),
    (100, "W95", "W96", "Kansas City"),
]
SF = [
    (101, "W97", "W98", "Arlington"),
    (102, "W99", "W100", "Atlanta"),
]
FINAL = (104, "W101", "W102", "East Rutherford")
THIRD_PLACE = (103, "L101", "L102", "Miami Gardens")

# 轮次序列(mc.py 遍历): (round_name, matches_list). final 单独.
ROUNDS = [("ro32", R32), ("ro16", R16), ("qf", QF), ("sf", SF)]


# ============================================================
# slot 解析
# ============================================================
def parse_slot(slot: str) -> tuple[str, object]:
    """解析 bracket slot 字符串.

    '1A' → ('gw', 'A')    组A冠军
    '2B' → ('gr', 'B')    组B亚军
    '3'  → ('third', None) 第三名(由 assign_thirds 定具体组)
    'W73'→ ('winner', 73)  73场胜者
    'L101'→('loser', 101)  101场败者
    """
    if slot.startswith("W"):
        return ("winner", int(slot[1:]))
    if slot.startswith("L"):
        return ("loser", int(slot[1:]))
    if slot == "3":
        return ("third", None)
    if slot[0] == "1":
        return ("gw", slot[1:])
    if slot[0] == "2":
        return ("gr", slot[1:])
    raise ValueError(f"无法解析 slot: {slot!r}")


# ============================================================
# 小组排名(纯函数, 供 mc.py 调用 + 单测)
# ============================================================
def _group_stats(teams: list[str], results: dict) -> tuple[dict, dict, dict]:
    """算每队 (pts, gd, gf). results: {(home, away): (hg, ag)}."""
    pts = {t: 0 for t in teams}
    gf = {t: 0 for t in teams}
    ga = {t: 0 for t in teams}
    for (h, a), (hg, ag) in results.items():
        if hg > ag:
            pts[h] += 3
        elif ag > hg:
            pts[a] += 3
        else:
            pts[h] += 1
            pts[a] += 1
        gf[h] += hg
        ga[h] += ag
        gf[a] += ag
        ga[a] += hg
    gd = {t: gf[t] - ga[t] for t in teams}
    return pts, gd, gf


def rank_group(teams: list[str], results: dict) -> list[str]:
    """小组排名 → [第1名, 第2名, 第3名, 第4名] 队名(降序).

    tiebreaker: 积分 → 净胜球 → 进球 → H2H(同前三者并列的队, 用其直接交锋小联赛重排)
                → teams 列表原始顺序(Python sorted 稳定性兜底, 确定性).

    teams: 4 队名(顺序作确定性兜底). results: {(home, away): (hg, ag)} 该组 6 场.
    """
    pts, gd, gf = _group_stats(teams, results)
    key = lambda t: (pts[t], gd[t], gf[t])
    ranked = sorted(teams, key=key, reverse=True)   # 稳定: 同 key 保持 teams 序

    out: list[str] = []
    i, n = 0, len(ranked)
    while i < n:
        j = i
        while j < n and key(ranked[j]) == key(ranked[i]):
            j += 1
        block = ranked[i:j]
        if len(block) > 1:
            # H2H: block 内直接交锋小联赛重排(仍并列则保稳定序兜底)
            sub = {(h, a): r for (h, a), r in results.items()
                   if h in block and a in block}
            h_pts, h_gd, h_gf = _group_stats(block, sub)
            block.sort(key=lambda t: (h_pts[t], h_gd[t], h_gf[t]), reverse=True)
        out.extend(block)
        i = j
    return out


def best_thirds(group_thirds: dict) -> list[str]:
    """12 组的第 3 名, 按同 tiebreaker 取前 8.

    group_thirds: {group_label: (team, pts, gd, gf)}.
    返回 [group_label, ...] 前 8(晋级); 后 4 淘汰.
    """
    items = [(lbl, t, pts, gd, gf) for lbl, (t, pts, gd, gf) in group_thirds.items()]
    items.sort(key=lambda x: (x[2], x[3], x[4]), reverse=True)
    return [x[0] for x in items[:8]]


# ============================================================
# 第三名分配(回溯匹配候选池)
# ============================================================
def assign_thirds(third_groups: list[str]) -> dict | None:
    """给定「哪 8 个组出了第三名」, 回溯找一个合法一对一分配到 8 个组冠军位.

    third_groups: 8 个 group label(出第三名的组).
    返回 {match_no: group_label} 或 None(无解 —— 理论不会, Annex C 保证有解).

    诚实标注: 回溯取「字典序首个合法解」, 个别组合可能与 FIFA 495 表不同(均合法),
    只影响第三名队 R32 对阵, 对夺冠概率榜/强队晋级阶梯无实质影响.
    """
    available = list(third_groups)
    assignment: dict[int, str] = {}
    used: set[str] = set()

    def backtrack(idx: int) -> bool:
        if idx == len(THIRD_MATCHES):
            return True
        m = THIRD_MATCHES[idx]
        for g in THIRD_POOL[m]:
            if g in available and g not in used:
                assignment[m] = g
                used.add(g)
                if backtrack(idx + 1):
                    return True
                del assignment[m]
                used.discard(g)
        return False

    return assignment if backtrack(0) else None


# ============================================================
# 淘汰赛 neutral 判定
# ============================================================
def venue_neutral(home: str, away: str, venue: str) -> bool:
    """淘汰赛某场 neutral: 恰一方为东道主且在本土作战 → False(享 γ); 否则 True.

    规则对齐 match_predictor.wc_neutral_host 的 XOR, 场地信息从 bracket 取.
    """
    country = VENUE_COUNTRY.get(venue)
    if country is None or country not in HOST_COUNTRIES:
        return True                          # 未知/非东道主国场地 → 中立
    home_home = (home == country)
    away_home = (away == country)
    return not (home_home ^ away_home)       # 恰一方本土 → 非中立
