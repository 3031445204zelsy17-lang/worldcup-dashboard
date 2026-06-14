# CLAUDE.md — World Cup Probability Dashboard

> 项目入口文档。新对话先读本文件 + `progress.json` 恢复上下文（project-start 工作流）。

## 项目定位
**实时更新的 2026 FIFA 世界杯概率仪表盘**——开源、方法论透明、托管公开站。
卖点：实时 + 透明 + 可解释（不追"最准"，认清足球预测天花板 60-62%）。
完整设计上下文见 `docs/project-context.md`。

## 关键约束（设计/开发时遵守）
- **一份代码两种部署**：公开站（你的 key）+ 别人自部署（他们 key），靠 `.env` 区分
- 🔑 **API key 绝不进仓库**（`.env` 在 `.gitignore`，仓库只放 `.env.example`）
- **后台采集 + 用户只读** → API 额度与用户数解耦（用户访问 0 次外部 API）
- **节奏**：A 先行（托管锦标赛）→ B 实时增量。赶不上峰值时 B 自动退化成 A

## 技术栈
| 层 | 技术 |
|----|------|
| 后端 | FastAPI + WebSocket（async）|
| 概率 | Python (scipy/numpy/pandas)：Elo → Dixon-Coles(+收缩) → Monte Carlo |
| 前端 | React + Recharts |
| 存储 | SQLite(热) + Parquet(历史) + 内存(实时) |
| 数据源 | API-Football(主) / football-data.org(备) / OpenWeatherMap |

## 目录结构
```
worldcup-dashboard/
├── docs/          ← 设计文档（project-context / architecture / wireframes / accuracy-strategy）
├── backend/
│   ├── models/    ← Elo / Dixon-Coles / Monte Carlo
│   ├── api/       ← FastAPI 路由
│   ├── data/      ← 采集 + 缓存
│   └── simulation/
├── frontend/      ← React + Recharts（Phase 1 建）
└── data/
    ├── raw/       ← 原始数据（不进 git）
    └── processed/ ← Parquet 预处理（不进 git）
```

## 开发规范
- 提交信息：`feat/fix/docs` + 中文描述
- 代码注释：中文为主
- 备份优先、测试优先
- 任务进度记录在 `progress.json`（`/project-done` 时更新）

## 验证（project-start 用）
当前任务所属 Phase 的产出就绪检查：
- **Phase 0（模型验证）**：`backend/models/` 有实现 + `data/processed/` 有回测输出（Brier / Calibration）
- **Phase 1（托管）**：后端 API 可 curl 通 + 前端能渲染 + 本地部署跑通
- 通用：`find . -newer progress.json -type f -not -path './.git/*'` 看最近改动

## 用户
Zelsy | HKMU DSAI | 时区 HK | Python 3/5, JS 1.5/5
工作模式：Claude 写实现 + 用户验收决策
