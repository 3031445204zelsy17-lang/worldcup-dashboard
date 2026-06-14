# ⚽ World Cup Probability Dashboard

> 实时更新的 2026 FIFA 世界杯概率仪表盘
> 比赛中实时展示胜/平/负概率曲线，并随赛果更新各队晋级/夺冠概率。

---

## 📁 项目状态

**当前阶段：设计阶段（尚未开始编码）**

调研已完成，技术决策已定，正在做产品设计。

---

## 🚀 如何继续（新对话）

在 Claude Code 里说：
> "读一下 `~/Desktop/worldcup-dashboard/docs/project-context.md`，我们继续。"

然后从 `docs/design-todo.md` 开始做产品设计。

---

## 📚 文档导航

| 文档 | 内容 |
|------|------|
| [`docs/project-context.md`](docs/project-context.md) | **主上下文**（先读这个）— 所有决策汇总 |
| [`docs/architecture.md`](docs/architecture.md) | 技术架构 + 数据流 + DB Schema |
| [`docs/research-index.md`](docs/research-index.md) | NotebookLM 笔记本 + 37 个源分类 |
| [`docs/accuracy-strategy.md`](docs/accuracy-strategy.md) | 五层准确性把控 |
| [`docs/product-research.md`](docs/product-research.md) | 竞品分析 + 设计模式 |
| [`docs/design-todo.md`](docs/design-todo.md) | 设计阶段待办（下一步） |

---

## 🔑 核心决策速览

- **技术栈**：FastAPI + React + Recharts + SQLite
- **数据**：4 年国际比赛（2022-2026），不用俱乐部数据
- **算法**：Elo + Dixon-Coles + Monte Carlo
- **卖点**：实时 + 透明 + 可解释（不追求最准）
- **硬件**：Mac 本地即可，无需 GPU

---

## 📓 NotebookLM 笔记本

- **Research: World Cup Probability Dashboard**
- ID: `5dcbda2a-fc3a-45a2-9e65-e0acdc4a53ac`
- ~37 个源（算法 / 数据 / 前端 / 产品方法论）

---

## ⏰ 时间线

- 2026-06-13：调研完成 + 文档建立
- **下一步**：产品设计 → Phase 0（纯 Python 核心）→ Phase 1（MVP）
- 2026-07-19：世界杯决赛（目标在此前有可用版本）
