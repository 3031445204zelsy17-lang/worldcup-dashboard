# 技术架构

> 配合 [[project-context.md]] 阅读。这里放技术细节、数据流、DB Schema。

---

## 整体架构图

```
┌─────────────────────────────────────────────────┐
│                  Frontend (React)                │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐ │
│  │ 实时胜率  │ │ 晋级概率  │ │ 赛程/赛果表格    │ │
│  │ 曲线图    │ │ 排行榜    │ │                  │ │
│  └──────────┘ └──────────┘ └──────────────────┘ │
│         ↕ WebSocket (实时推送)                    │
├─────────────────────────────────────────────────┤
│                Backend (Python)                   │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐ │
│  │ 数据采集  │ │ 概率引擎  │ │ 锦标赛模拟器     │ │
│  │ (Polling)│ │          │ │ (Monte Carlo)    │ │
│  └──────────┘ └──────────┘ └──────────────────┘ │
│         ↕                                        │
├─────────────────────────────────────────────────┤
│              外部数据源                            │
│  API-Football / football-data.org / World Cup API│
│  OpenWeatherMap (天气)                            │
└─────────────────────────────────────────────────┘
```

---

## 部署架构与核心原则（2026-06-14 补充）

### 一份代码，两种部署（靠 .env 配置区分）

```
          开源仓库（一份代码）
           ┌────────┴────────┐
      clone 自部署          你部署公开站
      （别人的个人版）       （小规模上线）
      用他们自己的 key       用你的 key
      本地 / 便宜机器        真实服务器（Vercel + Fly/VPS）
```

- **BYO key（Bring Your Own Key）**：公开站用你的 key；别人自部署填他们自己的 key（README 给 `.env.example`）
- **🔑 key 绝不进仓库**：`.env` 加进 `.gitignore`，仓库只放 `.env.example`。最高优先级——key 一旦 push 到公开仓库会被脚本秒扫盗用

### 核心设计原则：后台采集 + 用户只读 → 额度与用户数解耦

> 数据从外部 API 单向流入存储，用户访问**只读存储**，不反向打外部 API。
> 1 个人和 1000 个人访问，消耗的外部 API 一模一样（都是后台那次定时采集）。
> 这是"开源 + 小成本公开站"能成立的根基。

### 阶段差异：A 阶段不需要实时推送

| | A 阶段（托管锦标赛） | B 阶段（实时增量）|
|---|---|---|
| 用户怎么拿数据 | 访问时读 SQLite（只读）| WebSocket / SSE 实时推送 |
| 后台 | 定时采集 + 赛后 MC 重算 | + 赛中持续采集事件 |
| 外部 API 消耗 | 每天 ~10-20 次（免费够）| 一场 ~180 次（需付费）|

⚠️ 下方"数据流"图里的 **WebSocket 推送服务**是 B 阶段才启用；A 阶段前端直接读只读端点即可。

---

## 数据流（完整）

```
         外部 API 层
    ┌─────┼──────┼──────┐
    │     │      │      │
 API-   foot-  Open-  静态
Football  ball  Weather 数据集
         .org   Map
    │     │      │      │
    └─────┼──────┼──────┘
          ↓
    ┌──────────────┐
    │ 数据采集服务   │ ← FastAPI 后台任务
    │ (定时拉取)     │    根据比赛状态调整频率
    └──────┬───────┘
           ↓
    ┌──────────────┐
    │ SQLite 数据库 │ ← 所有数据落地，重启不丢
    └──────┬───────┘
           ↓
    ┌──────────────┐
    │ 概率计算引擎   │ ← 从 DB 读数据，算概率
    │ Poisson/DC   │    结果写回 DB
    │ + 环境修正    │
    └──────┬───────┘
           ↓
    ┌──────────────┐
    │ WebSocket    │ ← 前端订阅，实时推送
    │ 推送服务      │
    └──────────────┘
```

---

## 三层存储

```
┌──────────────────────────────────────────────┐
│              内存层 (最快)                     │
│  - 当前比赛实时数据 (TTL 30s)                  │
│  - 当前 Monte Carlo 结果 (一场比赛期间)        │
└────────────────┬─────────────────────────────┘
                 ↓ 写入
┌──────────────────────────────────────────────┐
│            SQLite (热数据)                     │
│  - 今天的比赛、事件、概率历史                   │
│  - 用户会反复查的数据                          │
└────────────────┬─────────────────────────────┘
                 ↓ 定期归档
┌──────────────────────────────────────────────┐
│          Parquet 文件 (冷数据)                 │
│  - 历史比赛 (训练用)                           │
│  - 训练好的模型参数                            │
│  - 过往赛季的完整数据                          │
└──────────────────────────────────────────────┘
```

---

## 数据库 Schema (SQLite)

### teams（球队表）
```sql
id              INTEGER PRIMARY KEY
name            TEXT
group_name      TEXT          -- A/B/C...
elo_rating      REAL          -- 当前 Elo 分
altitude_home   REAL          -- 主场海拔（米）
updated_at      TIMESTAMP
```

### matches（比赛表）
```sql
id              INTEGER PRIMARY KEY
home_team_id    INTEGER REFERENCES teams(id)
away_team_id    INTEGER REFERENCES teams(id)
score_home      INTEGER
score_away      INTEGER
status          TEXT          -- upcoming/live/finished
weather_json    TEXT          -- {temp, rain, wind}
altitude        REAL          -- 比赛场地海拔
kickoff_time    TIMESTAMP
stage           TEXT          -- group/ro16/qf/sf/final
```

### events（比赛事件表）
```sql
id              INTEGER PRIMARY KEY
match_id        INTEGER REFERENCES matches(id)
type            TEXT          -- goal/card/sub
team_id         INTEGER
player_name     TEXT
minute          INTEGER
created_at      TIMESTAMP
```

### predictions（预测记录表）
```sql
id              INTEGER PRIMARY KEY
match_id        INTEGER REFERENCES matches(id)
minute          INTEGER       -- 比赛第几分钟（0=赛前）
home_win_prob   REAL
draw_prob       REAL
away_win_prob   REAL
model_version   TEXT          -- poisson/dixoncoles/ensemble
confidence      REAL          -- 置信区间宽度
calculated_at   TIMESTAMP
```

### tournament_probs（锦标赛概率表）
```sql
id                  INTEGER PRIMARY KEY
team_id             INTEGER REFERENCES teams(id)
round               TEXT      -- group/ro16/qf/sf/final
advancement_prob    REAL      -- 晋级到此轮的概率
win_prob            REAL      -- 夺冠概率
calculated_at       TIMESTAMP
```

### lineups（阵容表）
```sql
id              INTEGER PRIMARY KEY
match_id        INTEGER REFERENCES matches(id)
team_id         INTEGER
players_json    TEXT          -- JSON 数组
announced_at    TIMESTAMP
```

### injuries（伤病表）
```sql
id              INTEGER PRIMARY KEY
team_id         INTEGER REFERENCES teams(id)
player_name     TEXT
status          TEXT          -- injured/doubtful/fit
updated_at      TIMESTAMP
```

---

## 缓存策略（省 API 额度）

```python
# 伪代码：三层缓存
def get_match_data(match_id):
    key = f"match_{match_id}"
    
    # 1. 内存缓存（30s TTL）
    if key in _cache:
        data, ts = _cache[key]
        if time.time() - ts < 30:
            return data  # ← 命中，不耗 API
    
    # 2. SQLite（今日数据）
    data = db.query("SELECT * FROM matches WHERE id=?", match_id)
    if data and data.recently_updated:
        _cache[key] = (data, time.time())
        return data
    
    # 3. 打 API（最后手段）
    data = api_football.get_match(match_id)
    db.save(data)
    _cache[key] = (data, time.time())
    return data
```

**效果**：100 次/天 API 额度，靠缓存能撑住几百次页面刷新。

---

## Monte Carlo 优化

```python
# 错误：存每次模拟结果 → 630,000 行垃圾
# 正确：只存聚合

import numpy as np

def run_monte_carlo(n=10000):
    # 向量化，不用 Python 循环
    home_goals = np.random.poisson(home_lambda, size=(n, 63))
    away_goals = np.random.poisson(away_lambda, size=(n, 63))
    
    # 模拟整个锦标赛 n 次
    # ...
    
    # 只落地聚合结果
    for team in teams:
        db.save({
            'team': team,
            'win_prob': wins[team] / n,
            'ro16_prob': ro16[team] / n,
        })
    # 48 队 × 6 轮 = 288 行，搞定
```

---

## 抓取频率与额度

| 场景 | 频率 | API 消耗 |
|------|------|---------|
| 比赛直播中 | 每 30s | ~180 次/场 ⚠️ 超免费额度 |
| 非直播比赛 | 每 5-10 min | ~10 次/场 ✅ |
| 非比赛日 | 每天 1 次 | ~5-10 次 ✅ |

**免费额度 100 次/天的应对：**
- 方案 A：升级付费（$9.99/月，3000 次）
- 方案 B：只跟踪重点比赛（每日 1-2 场）
- 方案 C：非关键时刻降频到 5 min（~30 次/场）

---

## 未来代码架构（Phase 1+ 才建）

```
~/Desktop/worldcup-dashboard/
├── docs/                    ← 现在的文档
├── backend/
│   ├── src/
│   ├── models/              ← Poisson/Dixon-Coles/Elo 实现
│   ├── api/                 ← FastAPI 路由
│   ├── data/                ← 数据采集 + 缓存
│   └── simulation/          ← Monte Carlo
├── frontend/
│   ├── src/
│   └── components/          ← React 组件 + Recharts
└── data/
    ├── raw/                 ← 原始数据
    └── processed/           ← Parquet 预处理
```

**现在不建代码结构，等设计完再建。**

---

## 待定的实现细节（2026-06-14，做到那步再定）

这些不影响架构大方向，是实现层选项：

| 待定项 | 选项 | 何时定 |
|---|---|---|
| 后台 worker 形态 | FastAPI 同进程(APScheduler) / 独立进程 / 外部 cron(GitHub Actions) | Phase 1 |
| SQLite 持久化 | 挂持久卷 / 换免费 Postgres(Supabase/Neon) | 部署时 |
| 实时推送方式（B）| WebSocket / SSE / 轮询+缓存 | Phase 2 |
| MC 重算调度 | 赛后触发 / 定时；多场同时结束的并发性能 | Phase 1-2 |
| 历史数据来源（Phase 0 回测）| API-Football 历史端点 / 现成数据集(Kaggle) | Phase 0 |
| 认证 | 纯只读无登录？大概率是 | 设计收尾 |
| 冷门对阵收缩 | 用 Elo 先验做 Dixon-Coles 攻防参数 shrinkage | Phase 0-1 |
