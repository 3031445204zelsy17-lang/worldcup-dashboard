"""
P1-3 Monte Carlo 锦标赛模拟器
============================
N 次(默认 10000)向量化模拟整个 2026 世界杯 → 聚合各队晋级/夺冠概率 → tournament_probs.

架构(对齐 architecture.md:237-261「不用 Python 循环」)
------------------------------------------------------
- 小组赛: np.random.poisson 一次采样 N×72 场(已完赛场用实际比分锁定)→ 排名.
- 32 强: 12 冠军 + 12 亚军 + 8 最佳第三(format.best_thirds)+ 第三名分配(format.assign_thirds).
- 淘汰赛: 逐轮向量化(R32→R16→QF→SF→Final), 每轮构造 per-sim 对阵 → 批量 Poisson 采样.
          90min 平局 → 30min 加时(λ/3, 实力仍有效)→ 仍平才 50/50 点球(近似随机). 2026-06-18 修正:
          旧版平局直接翻硬币 → 系统性压低顶尖队/抬升防守型队(Argentina 14.6%→17% 修正).
          场地 neutral 用 format.venue_neutral.
- 聚合: 各队达到各轮频次/N = advancement_prob; 夺冠频次/N = win_prob. 48×6=288 行.

关键决策(详见 P1-3 plan / progress note)
------------------------------------------
- 绕过 DixonColes.predict(逐场标量): 直接 λ 公式 + Poisson 独立采样, 不做 τ(对聚合概率影响可忽略).
- 已完赛小组赛场锁定实际比分(P1-1 diff→重算的意义: 每赛完一场后续概率就反映已发生结果).
- 小组排名用 pts→gd→gf→确定性兜底(忽略罕见 H2H, MC 多次平均; 完整 H2H 在 format.rank_group).
- 第三名分配用 format.assign_thirds(回溯匹配, 不硬编码 495 表).

运行: .venv/bin/python -m backend.simulation.mc          # n=10000, 打印夺冠 Top10 + 存表
      .venv/bin/python -m backend.simulation.mc 1000      # 自定义 n
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data.schema import DEFAULT_DB, init_db, team_id_of  # noqa: E402

# DC 产出路径(与 match_predictor 一致)
DEFAULT_DC_PARQUET = ROOT / "data" / "processed" / "dixon_coles_current.parquet"
DEFAULT_DC_JSON = ROOT / "data" / "processed" / "dixon_coles_global.json"
from backend.data.worldcup_2026_groups import GROUPS  # noqa: E402
from backend.models.dixon_coles import DixonColes  # noqa: E402
from backend.simulation import format as fmt  # noqa: E402

DEFAULT_N = 10000
# 加时赛 λ 衰减: 30min 加时 ≈ 90min 的 1/3. 加时赛实力仍有效(强队占优), 加时仍平才点球(≈50% 随机).
EXTRA_TIME_LAMBDA_FRACTION = 1.0 / 3.0
# 过度离散: 真实进球 var/λ≈1.80, Poisson(var=λ)低估大比分尾部. NB=Poisson(Gamma) 混合 var=λ+λ²/r.
# r=0(默认)=Poisson(现状, 不破坏生产); r>0 时 MC 采样用 NB 还原比分分布(见 memory overdispersion-goal-distribution).
DEFAULT_NB_R = 0.0
ROUNDS_ORDER = ["group", "ro32", "ro16", "qf", "sf", "final"]   # advancement 降序


def default_fixtures(allow_network: bool = False) -> list:
    """从 martj42 离线解析 72 场 WC 小组赛赛程(list[Match], 含 finished/upcoming)."""
    from backend.data.sources.martj42 import fetch_wc2026
    matches, _ = fetch_wc2026(allow_network=allow_network)
    return matches


class MonteCarloSimulator:
    """2026 世界杯 Monte Carlo 模拟器.

    典型用法:
        sim = MonteCarloSimulator()                      # 从默认 artifacts + 离线赛程
        probs = sim.run(n=10000)                         # → {team: {round: prob}}
        df = sim.to_dataframe(probs); sim.save(conn, df) # → tournament_probs 表
    """

    def __init__(self, dc: DixonColes | None = None, groups: dict | None = None,
                 fixtures: list | None = None, seed: int | None = None,
                 nb_r: float = DEFAULT_NB_R) -> None:
        self.dc = dc or DixonColes.from_artifacts(DEFAULT_DC_PARQUET, DEFAULT_DC_JSON)
        self.groups = groups if groups is not None else GROUPS
        self.rng = np.random.default_rng(seed)
        self.nb_r = float(nb_r)   # 过度离散参数: 0=Poisson(现状) / >0=NB(尾部厚, 还原大比分)
        # 向量化参数(team → index, attack/defense array)
        self.teams = list(self.dc.teams)
        self.team_idx = {t: i for i, t in enumerate(self.teams)}
        self.att = np.array([self.dc.attack[t] for t in self.teams])
        self.def_ = np.array([self.dc.defense[t] for t in self.teams])
        self.exp_mu = float(np.exp(self.dc.mu))
        self.gamma = float(self.dc.gamma)
        # 小组赛赛程(72 场, 按组分类)
        self._setup_group_stage(fixtures if fixtures is not None else default_fixtures())

    # ---------------- λ 向量化 ----------------
    def _lambda(self, home_idx: np.ndarray, away_idx: np.ndarray,
                neutral: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """向量化算 λ(绕过 predict). neutral=True 中立场(home 无 γ).
        away 队永不享主场 γ(与 DixonColes.predict 一致). γ_host=0(P0-4 证伪, 忽略).
        """
        g = np.where(neutral, 0.0, self.gamma)
        lam_h = self.exp_mu * self.att[home_idx] / self.def_[away_idx] * np.exp(g)
        lam_a = self.exp_mu * self.att[away_idx] / self.def_[home_idx]
        return lam_h, lam_a

    # ---------------- 进球采样(可选过度离散 NB) ----------------
    def _sample_goals(self, lam: np.ndarray, size=None) -> np.ndarray:
        """采样进球数. nb_r=0 → Poisson(现状); nb_r>0 → Negative Binomial(Poisson-Gamma 混合).

        NB 实现: 先采 Gamma(shape=r, scale=λ/r) → mean=λ var=λ²/r, 再叠 Poisson → 总 var=λ+λ²/r.
        还原真实进球过度离散(var/λ≈1.80, 修 Poisson 低估大比分, 见 memory overdispersion-goal-distribution).
        lam 可为标量或 array; size 透传给采样器.
        """
        if self.nb_r > 0:
            mixed = self.rng.gamma(self.nb_r, lam / self.nb_r, size=size)
            return self.rng.poisson(mixed)
        return self.rng.poisson(lam, size=size)

    # ---------------- 淘汰赛分胜负(加时 + 点球) ----------------
    def _decide_knockout_winners(self, home_idx, away_idx, hg, ag, neu):
        """淘汰赛一场或多场分胜负 → 胜者 team_idx array.

        常规时间(hg vs ag)分出胜负 → 该队胜; 平局 → 30min 加时(Poisson, λ×EXTRA_TIME_LAMBDA_FRACTION,
        实力仍有效)→ 加时总进球分胜负; 加时仍平 → 50/50 点球(近似随机).
        加时 λ 在同场地重算(东道主本土仍享 γ). 输入可为标量或 1d array(向量化).
        """
        home_idx = np.atleast_1d(np.asarray(home_idx, dtype=np.int32))
        away_idx = np.atleast_1d(np.asarray(away_idx, dtype=np.int32))
        hg = np.atleast_1d(np.asarray(hg)); ag = np.atleast_1d(np.asarray(ag))
        neu = np.atleast_1d(np.asarray(neu))
        win_idx = np.where(hg > ag, home_idx, away_idx)        # 常规时间分出胜负
        draw = (hg == ag)
        if draw.any():
            dh = home_idx[draw]; da = away_idx[draw]
            dlh, dla = self._lambda(dh, da, neu[draw])
            ehg = self._sample_goals(dlh * EXTRA_TIME_LAMBDA_FRACTION)
            eag = self._sample_goals(dla * EXTRA_TIME_LAMBDA_FRACTION)
            tot_h = hg[draw] + ehg; tot_a = ag[draw] + eag
            still = (tot_h == tot_a)
            et_win = np.where(tot_h > tot_a, dh, da)
            if still.any():                                     # 加时仍平 → 点球 50/50
                coin = self.rng.random(len(dh)) < 0.5
                et_win = np.where(still, np.where(coin, dh, da), et_win)
            win_idx[draw] = et_win
        return win_idx

    # ---------------- 小组赛赛程 ----------------
    def _setup_group_stage(self, fixtures: list) -> None:
        """把 fixtures(list[Match])的 72 场按组分类 → 每组 6 场 (h_idx,a_idx,neutral,hs,as)."""
        # home 队 → 所属组
        team_group = {t: g for g, ts in self.groups.items() for t in ts}
        group_games: dict[str, list] = {g: [] for g in self.groups}
        for m in fixtures:
            g = team_group.get(m.home) or team_group.get(m.away)
            if g is None:
                continue                      # 非小组赛(淘汰赛, 留未来)
            hs = None if m.home_score is None else int(m.home_score)
            as_ = None if m.away_score is None else int(m.away_score)
            group_games[g].append((m.home, m.away, bool(m.neutral), hs, as_))

        # 每组应有 6 场; 转 idx + 预算 λ
        self._group_labels = fmt.GROUP_LABELS
        self._gh = np.empty(72, dtype=np.int32)   # 72 场 home idx
        self._ga = np.empty(72, dtype=np.int32)
        self._gneu = np.empty(72, dtype=bool)
        self._gfin = np.zeros(72, dtype=bool)     # 已完赛 mask
        self._gacth = np.zeros(72, dtype=np.int16)  # 实际 home 比分(finished)
        self._gacta = np.zeros(72, dtype=np.int16)
        self._group_slices: dict[str, tuple[int, int]] = {}   # label → (start,end) in 72
        # 每组的 (home_idx_in_group, away_idx_in_group) pairs(排名用)
        self._group_pairs: dict[str, np.ndarray] = {}
        pos = 0
        for label in self._group_labels:
            teams4 = self.groups[label]
            t4idx = {t: i for i, t in enumerate(teams4)}
            games = group_games[label]
            assert len(games) == 6, f"组 {label} 应有 6 场, 实际 {len(games)}"
            pairs = []
            for k, (h, a, neu, hs, as_) in enumerate(games):
                self._gh[pos] = self.team_idx[h]
                self._ga[pos] = self.team_idx[a]
                self._gneu[pos] = neu
                if hs is not None:
                    self._gfin[pos] = True
                    self._gacth[pos] = hs
                    self._gacta[pos] = as_
                pairs.append((t4idx[h], t4idx[a]))
                pos += 1
            self._group_slices[label] = (pos - 6, pos)
            self._group_pairs[label] = np.array(pairs, dtype=np.int32)
        # 预算 72 场 λ(采样用)
        self._lam_h72, self._lam_a72 = self._lambda(self._gh, self._ga, self._gneu)

    # ---------------- 小组排名(向量化, 无 H2H) ----------------
    @staticmethod
    def _rank4(pairs: np.ndarray, hg: np.ndarray, ag: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """单组 6 场 → (order_idx[4], pts[4], gd[4], gf[4]). order 按 pts→gd→gf 降序(无 H2H).

        pairs: (6,2) home/away idx-in-group. hg/ag: (6,) 比分.
        """
        h = pairs[:, 0]
        a = pairs[:, 1]
        pts = np.zeros(4, dtype=np.int16)
        gf = np.zeros(4, dtype=np.int16)
        ga = np.zeros(4, dtype=np.int16)
        np.add.at(pts, h, np.where(hg > ag, 3, np.where(hg == ag, 1, 0)), )
        np.add.at(pts, a, np.where(ag > hg, 3, np.where(hg == ag, 1, 0)))
        np.add.at(gf, h, hg); np.add.at(ga, h, ag)
        np.add.at(gf, a, ag); np.add.at(ga, a, hg)
        gd = gf - ga
        # lexsort: 最后 key 是主 → (-gf, -gd, -pts) 主=pts 降序. 稳定: 同 key 保原 idx(确定性)
        order = np.lexsort((-gf, -gd, -pts))
        return order, pts, gd, gf

    # ---------------- 小组赛模拟(向量化采样 + per-sim 排名) ----------------
    def _simulate_group_stage(self, n: int, return_scores: bool = False):
        """返回 winners/runners/thirds(thirds_team_idx, thirds_pts, thirds_gd, thirds_gf) shape (n,12).

        return_scores=True 时额外返回 (hg, ag)(N×72 采样比分, 供测试验证已完赛锁定).
        """
        hg = self._sample_goals(self._lam_h72, size=(n, 72)).astype(np.int16)
        ag = self._sample_goals(self._lam_a72, size=(n, 72)).astype(np.int16)
        # 锁定已完赛场实际比分
        if self._gfin.any():
            hg[:, self._gfin] = self._gacth[self._gfin]
            ag[:, self._gfin] = self._gacta[self._gfin]

        ngrp = len(self._group_labels)
        winners = np.empty((n, ngrp), dtype=np.int32)       # team_idx
        runners = np.empty((n, ngrp), dtype=np.int32)
        third = np.empty((n, ngrp), dtype=np.int32)
        third_pts = np.empty((n, ngrp), dtype=np.int16)
        third_gd = np.empty((n, ngrp), dtype=np.int16)
        third_gf = np.empty((n, ngrp), dtype=np.int16)

        # 预取每组的 slice + pairs
        labels = self._group_labels
        slices = [self._group_slices[g] for g in labels]
        pairs = [self._group_pairs[g] for g in labels]
        gh, ga = self._gh, self._ga
        for sim in range(n):
            hgs, ags = hg[sim], ag[sim]
            for gi in range(ngrp):
                s, e = slices[gi]
                pr = pairs[gi]
                # 该组 6 场比分(用全局 team idx 的 home/away; 排名用 in-group idx)
                order, pts, gd, gf = self._rank4(pr, hgs[s:e], ags[s:e])
                # order 是 in-group idx → 映射回全局 team idx(通过该组 6 场的 gh/ga)
                # in-group idx i 对应 teams4[i]; 全局 idx = 该组队 idx
                teams4 = self.groups[labels[gi]]
                tidx = [self.team_idx[t] for t in teams4]
                winners[sim, gi] = tidx[order[0]]
                runners[sim, gi] = tidx[order[1]]
                third[sim, gi] = tidx[order[2]]
                third_pts[sim, gi] = pts[order[2]]
                third_gd[sim, gi] = gd[order[2]]
                third_gf[sim, gi] = gf[order[2]]
        if return_scores:
            return winners, runners, third, third_pts, third_gd, third_gf, hg, ag
        return winners, runners, third, third_pts, third_gd, third_gf

    # ---------------- 淘汰赛逐轮 ----------------
    def _resolve_slot(self, slot: str, sim_winners: dict, group_winners_row: np.ndarray,
                      group_runners_row: np.ndarray, third_team_by_match: dict,
                      current_mno: int) -> int:
        """解析一个 slot → team_idx(标量). 用当前 sim 的组排名/胜者/第三名分配.

        third slot("3")未编码 match_no → 用 current_mno 查第三名分配(第三名位在 R32, mno∈74/77/...).
        """
        kind, val = fmt.parse_slot(slot)
        if kind == "gw":
            return int(group_winners_row[fmt.GROUP_LABELS.index(val)])
        if kind == "gr":
            return int(group_runners_row[fmt.GROUP_LABELS.index(val)])
        if kind == "third":
            return int(third_team_by_match[current_mno])
        if kind == "winner":
            return int(sim_winners[val])
        raise ValueError(f"未预期 slot: {slot!r}")

    def _simulate_knockout(self, n: int, winners, runners, third, third_pts, third_gd, third_gf):
        """逐轮模拟淘汰赛. 返回 reach_counts: {team_idx: {'ro32':N,...,'win':N}}(N=sim 数)."""
        reach = {"ro32": np.zeros(len(self.teams), dtype=np.int32),
                 "ro16": np.zeros(len(self.teams), dtype=np.int32),
                 "qf": np.zeros(len(self.teams), dtype=np.int32),
                 "sf": np.zeros(len(self.teams), dtype=np.int32),
                 "final": np.zeros(len(self.teams), dtype=np.int32),
                 "win": np.zeros(len(self.teams), dtype=np.int32)}
        # ro32: 32 强 = 12 冠军 + 12 亚军 + 8 最佳第三(每 sim 选)
        for sim in range(n):
            gw_row = winners[sim]
            gr_row = runners[sim]
            # 8 最佳第三: 按 (pts,gd,gf) 排序取前 8 的组 label
            tkey = list(zip(third_pts[sim], third_gd[sim], third_gf[sim],
                            fmt.GROUP_LABELS))
            tkey.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
            best8 = [x[3] for x in tkey[:8]]
            assign = fmt.assign_thirds(best8)         # {match_no: group_label}
            # 第三名 team_idx per 第三名位
            third_team = {mno: third[sim][fmt.GROUP_LABELS.index(assign[mno])]
                          for mno in fmt.THIRD_MATCHES}
            sim_winners: dict = {}

            for round_name, matches in fmt.ROUNDS:   # ro32/ro16/qf/sf
                home_idx = np.empty(len(matches), dtype=np.int32)
                away_idx = np.empty(len(matches), dtype=np.int32)
                neu = np.empty(len(matches), dtype=bool)
                for k, (mno, hslot, aslot, venue) in enumerate(matches):
                    h = self._resolve_slot(hslot, sim_winners, gw_row, gr_row, third_team, mno)
                    a = self._resolve_slot(aslot, sim_winners, gw_row, gr_row, third_team, mno)
                    home_idx[k] = h
                    away_idx[k] = a
                    neu[k] = fmt.venue_neutral(self.teams[h], self.teams[a], venue)
                lam_h, lam_a = self._lambda(home_idx, away_idx, neu)
                hg = self._sample_goals(lam_h)
                ag = self._sample_goals(lam_a)
                win_idx = self._decide_knockout_winners(home_idx, away_idx, hg, ag, neu)
                for k, mno in enumerate([m[0] for m in matches]):
                    sim_winners[mno] = int(win_idx[k])

            # Final(单场)
            _, hslot, aslot, venue = fmt.FINAL
            h = self._resolve_slot(hslot, sim_winners, gw_row, gr_row, third_team, fmt.FINAL[0])
            a = self._resolve_slot(aslot, sim_winners, gw_row, gr_row, third_team, fmt.FINAL[0])
            neu = fmt.venue_neutral(self.teams[h], self.teams[a], venue)
            lam_h, lam_a = self._lambda(np.array([h]), np.array([a]), np.array([neu]))
            fhg = self._sample_goals(lam_h)[0]
            fag = self._sample_goals(lam_a)[0]
            champion = int(self._decide_knockout_winners(
                np.array([h]), np.array([a]), np.array([fhg]), np.array([fag]), np.array([neu]))[0])
            finalist = (h, a)

            # 计入 reach(累积: ro32 含出线队; ro16 含 R32 胜者; ...)
            for gi in range(12):
                reach["ro32"][gw_row[gi]] += 1
                reach["ro32"][gr_row[gi]] += 1
            for g in best8:
                reach["ro32"][third[sim][fmt.GROUP_LABELS.index(g)]] += 1
            for mno in [m[0] for m in fmt.R32]:
                reach["ro16"][sim_winners[mno]] += 1
            for mno in [m[0] for m in fmt.R16]:
                reach["qf"][sim_winners[mno]] += 1
            for mno in [m[0] for m in fmt.QF]:
                reach["sf"][sim_winners[mno]] += 1
            for mno in [m[0] for m in fmt.SF]:
                reach["final"][sim_winners[mno]] += 1
            reach["win"][champion] += 1
        return reach

    # ---------------- 主入口 ----------------
    def run(self, n: int = DEFAULT_N) -> dict:
        """跑 n 次模拟 → {team: {round: advancement_prob}}, 含 win_prob."""
        winners, runners, third, tp, tg, tf = self._simulate_group_stage(n)
        reach = self._simulate_knockout(n, winners, runners, third, tp, tg, tf)
        probs = {}
        for t, i in self.team_idx.items():
            if t not in {x for ts in self.groups.values() for x in ts}:
                continue                      # 只输出 48 参赛队
            row = {"group": 1.0}
            for rnd in ("ro32", "ro16", "qf", "sf", "final"):
                row[rnd] = reach[rnd][i] / n
            row["win"] = reach["win"][i] / n
            probs[t] = row
        return probs

    def to_dataframe(self, probs: dict) -> pd.DataFrame:
        """probs → tournament_probs DataFrame(48×6=288 行, 对齐 schema.TOURNAMENT_PROBS_DDL)."""
        rows = []
        for team, row in probs.items():
            win = row["win"]
            for rnd in ROUNDS_ORDER:
                rows.append({"team": team, "round": rnd,
                             "advancement_prob": row[rnd], "win_prob": win})
        df = pd.DataFrame(rows)
        df["round"] = pd.Categorical(df["round"], categories=ROUNDS_ORDER, ordered=True)
        return df.sort_values(["team", "round"]).reset_index(drop=True)

    def save(self, conn: sqlite3.Connection, df: pd.DataFrame) -> int:
        """写 tournament_probs(覆盖=最新) + tournament_probs_history(追加=历史轨迹).

        同一次重算的 48×6 行共用 calculated_at(快照分组键). 返回写入 tournament_probs 行数.
        """
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rows = []
        for r in df.itertuples(index=False):
            tid = team_id_of(conn, r.team)
            if tid is None:
                continue
            rows.append((tid, r.round, float(r.advancement_prob), float(r.win_prob), now))
        # tournament_probs: 覆盖(最新一份, 查询层读它)
        conn.execute("DELETE FROM tournament_probs")
        conn.executemany(
            "INSERT INTO tournament_probs (team_id, round, advancement_prob, win_prob, calculated_at) "
            "VALUES (?,?,?,?,?)", rows)
        # tournament_probs_history: 追加(累积历史轨迹, 每次 MC 重算一份快照, 只 INSERT 不 DELETE)
        conn.executemany(
            "INSERT INTO tournament_probs_history (team_id, round, advancement_prob, win_prob, calculated_at) "
            "VALUES (?,?,?,?,?)", rows)
        conn.commit()
        return len(rows)


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    n = int(argv[0]) if argv else DEFAULT_N
    sim = MonteCarloSimulator(seed=42)
    probs = sim.run(n=n)
    df = sim.to_dataframe(probs)

    # 夺冠 Top10
    win = {t: p["win"] for t, p in probs.items()}
    top = sorted(win.items(), key=lambda x: x[1], reverse=True)[:10]
    total = sum(win.values())
    print("=" * 64)
    print(f"P1-3 Monte Carlo · n={n} · 夺冠概率和={total:.3f}")
    print("=" * 64)
    print("夺冠概率 Top10:")
    for i, (t, p) in enumerate(top, 1):
        print(f"  {i:>2}. {t:<28} {p:>6.1%}")
    # 抽样 3 队晋级阶梯
    print("-" * 64)
    print("晋级阶梯抽样:")
    for t in [top[0][0], top[2][0] if len(top) > 2 else top[0][0], top[5][0] if len(top) > 5 else top[0][0]]:
        r = probs[t]
        print(f"  {t:<28} 出线{r['ro32']:>5.1%}  16强{r['ro16']:>5.1%}  8强{r['qf']:>5.1%}  "
              f"4强{r['sf']:>5.1%}  决赛{r['final']:>5.1%}  夺冠{r['win']:>5.1%}")
    # 存表
    from backend.data.schema import seed_teams
    conn = init_db(DEFAULT_DB, all_tables=True)
    seed_teams(conn)   # 幂等: 确保 teams 表有 48 队(tournament_probs 外键依赖)
    nrow = sim.save(conn, df)
    print("-" * 64)
    print(f"已写 tournament_probs: {nrow} 行(48×6) → {DEFAULT_DB.relative_to(ROOT)}")
    print("=" * 64)


if __name__ == "__main__":
    main()
