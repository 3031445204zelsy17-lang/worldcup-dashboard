# 调研索引

> NotebookLM 笔记本 + 37 个源的分类索引。
> 笔记本里可以继续探索这些源，做音频消化、对比等。

---

## NotebookLM 笔记本

- **名称**：Research: World Cup Probability Dashboard
- **ID**：`5dcbda2a-fc3a-45a2-9e65-e0acdc4a53ac`
- **访问**：notebooklm.google.com，或用 `notebooklm` CLI

---

## 源分类（共 ~37 个）

### 🏗️ API & 数据源（~6 个）

| 源 | 内容 |
|----|------|
| World Cup API | 2026 FIFA WC 专用 API |
| football-data.org API Reference | 足球数据 API 文档 |
| api-football-pricing | API-Football 定价方案 |
| api-football-register | API-Football 免费注册信息 |
| Best Odds APIs in 2026 | 6 大赔率 API 对比 |
| Calculating PL Win Probabilities (Python) | Python + football-data.org 胜率教程 |

### 🧮 概率模型（~11 个）

| 源 | 内容 |
|----|------|
| **Win Probability Model** (sharmaabhishekk) | 🔑 实时比赛胜率计算方法 |
| Dixon-Coles model (opisthokonta) | DC 模型理论 |
| Predicting Football Results (Python + DC) | DC 的 Python 实现 |
| Predicting Football Results (Statistical Modelling) | Poisson 回归入门 |
| Predicting Football Results (DC + Time-Weighting) | DC + 时间衰减 |
| How betting odds work (Poisson) | Poisson 在赔率中的原理 |
| World Football Elo Ratings (Wikipedia) | Elo 评分体系参考 |
| CRAN: footBayes | R 贝叶斯足球模型包 |
| **GitHub: worldcup-predictor** | 🔑 Elo 核心，60% 回测准确率 |
| **GitHub: fifa-wc-2026-simulation** | 🔑 完整 WC2026 蒙特卡洛模拟 |
| GitHub: FootballMatchPredictionPoisson | Poisson 预测 Python 实现 |

### 🖥️ 前端 & 实时架构（~5 个）

| 源 | 内容 |
|----|------|
| **Complete guide to WebSockets with React** | 🔑 WS + React 教程 |
| **SSE, WebSockets, or Polling?** | 🔑 三种实时方案对比 |
| **GitHub: sportz-websockets** | 🔑 体育实时比分 WS 项目 |
| Building Real-Time Dashboards with WebSockets | 实时仪表盘实战 |
| Recharts | React 图表库 |

### 📊 综合分析（~2 个）

| 源 | 内容 |
|----|------|
| Football Meets Data | 足球分析 & 模拟平台 |
| GitHub: SoccerPredictor | ML pipeline（集成学习 + 神经网络） |

### 📐 产品/方法论/UX（~10 个，第二轮调研新增）

| 源 | 内容 |
|----|------|
| 538 Soccer Predictions 拆解 (dadmetrics) | SPI 模型分析 |
| Why 538 High on Man City (graceonfootball) | SPI 偏差案例 |
| FiveThirtyEight is Dead (fromthebyline) | 公开预测模型遗产 |
| **Opta Supercomputer WC2026** (theanalyst) | 🔑 锦标赛概率标杆 |
| **Football Prediction Model Explained** (thedatabetics) | 🔑 三层建模 + 语境因子 |
| Statistical association football predictions (Wikipedia) | 方法论综述 |
| ESPN Analytics | 实时胜率可视化参考 |
| Supercomputer Predicts WC2026 (si.com) | WC2026 概率视角 |
| Examining 538 SPI Ratings (joshyazman, R) | SPI 实证分析 |
| Best Football Prediction Websites 2025 (escored) | 产品功能对比 |

---

## 关键源 Top 推荐（开发时优先参考）

| 用途 | 推荐源 |
|------|--------|
| 实时胜率怎么算 | Win Probability Model (sharmaabhishekk) |
| 锦标赛模拟代码 | GitHub: fifa-wc-2026-simulation |
| Elo 预测实现 | GitHub: worldcup-predictor |
| 前端实时架构 | GitHub: sportz-websockets |
| 锦标赛概率展示 | Opta Supercomputer WC2026 |
| 产品设计理念 | TheDatabetics（三层建模） |

---

## 调研过程备忘

- Deep research 被 Google 限流（503/rate limit），改用 fast research + 手动添加源
- 部分源被 Cloudflare 拦截（API-Football 文档、Forebet 官网）已清理
- Forebet 是重要竞品但主站无法抓取，通过二手资料间接获取
- API-Football 文档页（documentation-v3）仍被 Cloudflare 保护，需用户手动复制

## 待补充
- API-Football 实际端点文档（Cloudflare 拦截，待手动获取）
- 天气 API 在比赛场地的精确度验证
- 海拔修正系数的学术研究依据
