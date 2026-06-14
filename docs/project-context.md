# World Cup Probability Dashboard — 项目上下文

> 📌 **这份文档的作用**：新对话开始时读这个文件，就能无缝接上之前所有讨论。
> 最后更新：2026-06-13

---

## 一句话定位

**实时更新的 2026 FIFA 世界杯概率仪表盘**：比赛中实时展示胜/平/负概率曲线，并随赛果更新各队晋级/夺冠概率。

---

## 项目愿景与差异化

### 核心功能（两者都要）
1. **单场实时胜率** — 比赛进行中的胜/平/负概率，随事件（进球/红牌）动态变化
2. **锦标赛晋级概率** — 各队小组出线、淘汰赛晋级、夺冠概率，随赛果实时更新

### 六大差异化策略（来自竞品调研）
| # | 机会 | 说明 |
|---|------|------|
| 1 | **实时性** | 最大机会 — 五个标杆产品全是赛前静态，实时更新的 WC 仪表盘几乎空白 |
| 2 | **"为什么"解释** | 曲线转折点自动标注"此刻发生了什么"，别人只给数字 |
| 3 | **方法论透明** | FiveThirtyEight 已死、Opta 黑箱 — "透明的活模型"是空白 |
| 4 | **不确定性可视化** | 没有产品把置信区间/阴影带可视化 |
| 5 | **地理/旅行/气候因子** | 2026 跨美加墨特殊性，针对性差异化 |
| 6 | **单场景极致** | 移动优先、秒级更新、世界杯聚焦 |

### 定位声明
- **是分析工具，不是博彩站点**（规避合规风险）
- 卖点不是"最准"，而是"实时 + 透明 + 可解释"

---

## 关键技术决策（已定）

### 技术栈
| 层 | 技术 | 理由 |
|----|------|------|
| 后端 | **FastAPI** + WebSocket | 用户 Python 最熟，FastAPI 支持 async + WS |
| 概率计算 | **Python** (scipy + numpy + pandas) | Poisson/Dixon-Coles/MC 都有现成库 |
| 前端 | **React** + **Recharts** | 生态最大，Recharts 简单（用户 JS 1.5/5，需我辅助） |
| 存储 | **SQLite** | 零配置、单文件、够用（个人项目） |
| 数据格式 | Parquet（历史数据）+ SQLite（热数据）+ 内存（实时） | 分层缓存 |

### 数据范围决策
- ✅ **只用 4 年国际比赛数据**（2022-2026），不是 10 年
  - 理由：国家队一年才 ~15 场，4 年 = 60 场够用；老数据有干扰（球员换代、战术演变）
  - Dixon-Coles 时间衰减函数会自然降权老数据
- ❌ **不用俱乐部比赛数据**（MVP 阶段）
  - 理由：国家队 ≠ 俱乐部球员加总；复杂度 ×10，准确率只 +2-5%；伤病/阵容已用 API 覆盖

### 硬件需求
- **用户的 Mac 完全够用**（统计建模 ≠ 深度学习）
- Poisson/Dixon-Coles 训练 < 5 秒，Monte Carlo 10000 次 ~5-10 秒
- 不需要 GPU

---

## 算法选型

### 核心算法链
```
Elo Rating (实力分数) 
    ↓
Poisson / Dixon-Coles (赛前胜/平/负概率)
    ↓
实时 game state 修正 (比分 + 时间 + 红牌 + xG)
    ↓
Monte Carlo 模拟 (10000 次整个锦标赛)
    ↓
晋级/夺冠概率聚合
```

### 算法说明（通俗版见各算法文档）

| 算法 | 作用 | 参考 |
|------|------|------|
| **Elo Rating** | 给每队一个实力分数，分差 → 胜率 | Wikipedia World Football Elo |
| **Poisson 模型** | 用平均进球数算比分分布 → 胜率 | hackerearth 博客 |
| **Dixon-Coles** | Poisson 改进版：修正低分偏差 + 时间衰减 | opisthokonta.net |
| **Monte Carlo** | 模拟锦标赛 10000 次，统计晋级频率 | zvizdo/fifa-wc-2026-simulation |
| **实时胜率** | Poisson 剩余时间模拟 + game state 调整 | Win Probability Model (sharmaabhishekk) |

### 准确性目标
- **基准**：国际 Elo 模型 ~60% 准确率
- **目标**：Dixon-Coles + 实时修正 → 60-62%
- **验证**：Brier Score 回测 + 博彩赔率对标 + Calibration Plot

---

## 数据方案

### 数据源
| 源 | 用途 | 免费额度 |
|----|------|---------|
| **API-Football** (RapidAPI) | 主力：赛程/比分/事件/阵容/伤病 | 100 次/天 |
| **football-data.org** | 备用比分源 | 10 次/分钟 |
| **OpenWeatherMap** | 天气数据（比赛地） | 1000 次/天 |
| **World Cup API** | WC2026 专用数据 | TBD |
| **Odds API** | 赔率对标 | TBD |

### 抓取策略
```
比赛日:
  赛前 3h  ── 拉天气
  赛前 1h  ── 拉首发阵容
  赛中     ── 每 30s 拉比赛事件（180次/场，需控额度）
  赛后     ── 锁定结果，触发锦标赛模拟重算

非比赛日:
  每天 1 次 ── 赛程 + 伤病 + 赔率 + 重跑 Monte Carlo
```

### 三层缓存（省 API 额度）
```
请求 → 内存缓存(30s TTL) → SQLite(今日数据) → API
```

### 数据库 Schema（SQLite）
- `teams` — 球队表（id, name, group, elo, altitude_home）
- `matches` — 比赛表（score, status, weather JSON, altitude, kickoff）
- `events` — 比赛事件（goal/card/sub, minute）
- `predictions` — 预测记录（prob, minute, model_version, confidence）
- `tournament_probs` — 锦标赛概率（team, round, advancement_prob, win_prob）
- `lineups` — 首发阵容
- `injuries` — 伤病名单

详见 [[architecture.md]]

---

## 环境因素建模（非随机因素）

```
进球期望 = f(主队实力, 客队实力, 海拔修正, 天气修正, 伤病修正)
```

| 因素 | 修正方式 | 数据来源 |
|------|---------|---------|
| 海拔 > 2000m | 客队进球期望 × 0.85 | 静态数据集（自建） |
| 大雨 | 双方进球期望 × 0.8 | OpenWeatherMap |
| 高温 | 技术型球队 × 0.9 | OpenWeatherMap |
| 核心前锋缺阵 | 该队进球期望 × 0.85 | API-Football 伤病 |
| 主力门将缺阵 | 该队失球期望 × 1.15 | API-Football 伤病 |
| 赛程密集（3天内第二场） | 进球期望降低 | 赛程时间差计算 |

⚠️ 修正系数需用历史数据回测校准，当前是估算值。

---

## 准确性保障（五层把控）

1. **数据质量** — 双源交叉验证 + 时间衰减 + 赛事等级区分
2. **模型准确** — Dixon-Coles > Poisson；回测验证（Brier Score / Calibration / Log Loss）
3. **实时修正** — 比赛中根据 game state 持续调整
4. **对标层** — 博彩赔率反推概率对标 + ESPN/FiveThirtyEight 对比 + Elo 基准
5. **透明度** — 显示置信区间 + 模型依据 + 历史准确率 + 免责声明

详见 [[accuracy-strategy.md]]

---

## 产品调研结论

### 竞品格局
| 产品 | 参考价值 |
|------|---------|
| **FiveThirtyEight** (SPI) | 方法论标杆（已停更），三轴模型 |
| **The Analyst / Opta** | 锦标赛预测标杆，蒙特卡洛 + 晋级路径树 |
| **SofaScore** | 动量图(momentum graph) — 实时 UX 范例 |
| **TheDatabetics** | 分层建模 + "概率区间 + 语境因子"理念 |

### 设计模式共识
- **实时胜率** → 曲线图（关键事件标在转折点）
- **赛前/锦标赛概率** → 百分比条/概率柱
- **信息架构** → 层级递进：核心数字 → 对比+驱动因素 → 方法论+准确率
- **晋级路径** → bracket 树 + 概率排行榜

详见 [[product-research.md]]

---

## Phase 规划

### Phase 0 — 核心验证（~1-2 天，我来写代码）
纯 Python 命令行，验证模型可行性：
- 下载 4 年国际比赛数据
- 计算各队 Elo
- Poisson 预测下一场比赛概率
- 命令行输出（不碰前端）
- **目标**：验证"我能做出预测"

### Phase 1 — MVP（~3-5 天）
- FastAPI 包一层
- 最简单网页（可先用 Jinja 模板，不一定 React）
- 表格展示赛程 + 概率
- 静态预测 + 手动刷新

### Phase 2 — 实时
- WebSocket 实时推送
- 比赛中胜率曲线
- Monte Carlo 锦标赛模拟
- 自动 polling

### Phase 3 — 完整
- 锦标赛树可视化
- 用户交互（点击球队）
- 移动端适配
- 分享/截图

---

## 工作流约定

用户的工作模式：**Claude 写实现 + 用户验收决策**。
- 时间估算基于"我来写代码"，不是用户从零写
- 但决策/验收/调试时间省不掉
- ⚠️ 曾遇到工具被中断的问题：批准工具时不要同时打字

---

## 待设计清单（下一步重点）

详见 [[design-todo.md]]，核心未定项：
1. **产品范围** — MVP 精确到什么程度
2. **核心页面/线框图** — 用户看到什么
3. **用户旅程** — 谁用、什么场景
4. **视觉风格** — 配色、图表风格
5. **功能优先级** — Must / Should / Nice
6. **成功标准** — 怎么算"做成了"

---

## 相关文件索引

| 文件 | 内容 |
|------|------|
| [[project-context.md]] | 本文件（主上下文） |
| [[architecture.md]] | 技术架构详情 + 数据流图 + DB Schema |
| [[research-index.md]] | NotebookLM 笔记本 + 37 个源分类 |
| [[accuracy-strategy.md]] | 五层准确性把控详解 |
| [[product-research.md]] | 竞品分析 + 设计模式 |
| [[design-todo.md]] | 设计阶段待办清单 |

## NotebookLM 笔记本
- **名称**：Research: World Cup Probability Dashboard
- **ID**：`5dcbda2a-fc3a-45a2-9e65-e0acdc4a53ac`
- **源数量**：~37 个（算法/数据/前端/产品方法论）

---

## 如何在新对话继续

新对话开头说：
> "读一下 `~/Desktop/worldcup-dashboard/docs/project-context.md`，我们继续做产品设计。"

我会读完文档接上，然后可以从 [[design-todo.md]] 开始设计。
