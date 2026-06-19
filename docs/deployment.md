# 部署指南 — P1-7

> 把 worldcup-dashboard 部署成公开站: **单 Azure Container App(eastasia)** —— 前端 + API + worker 烤进一个镜像, **一个域名**, 内地/香港都能访问(免 Vercel, 免 CORS, 免备案)。
> 节奏: 建仓库(~1min) → provision(~15min) → 配 secrets → push 上线。之后改代码 push 即自动更新。

## 架构 / 决策

- **前端 + API + worker** → 一个 Azure Container App(uvicorn 前台同时 serve 只读 API + 前端 SPA 静态文件; worker 后台采集 + MC 重算)
- **同源单域名**: 前端用相对 `/api`(不设 `VITE_API_BASE`)→ 单域名 `xxx.eastasia.azurecontainerapps.io` 免 CORS
- **SQLite** → Azure Files 持久卷(`/mnt/data/wc.db`, WAL 并发)
- **部署** → GitHub Actions(push main 自动: 生成模型 artifacts → ACR multi-stage build[Stage1 node build 前端 → Stage2 python 后端+dist] → 更新 ACA)
- **临时友好**: 赛期/课程结束 `az group delete` 一键清零, 不再计费(见末尾「临时下线」)
- **不改业务代码**: 路径自适配容器; DB 走 env; `app.py` 同源 SPA mount + `Dockerfile` multi-stage 已就绪

> ⚠️ **为何不用 Vercel**: Vercel 默认域名 `*.vercel.app` 在内地访问常被墙; 单 Azure 域名(`azurecontainerapps.io` 是微软大云域名)被墙概率低一个量级, 两地都稳。代价: 每次 CI 多 build 一次前端(~1min), 可接受。
> ⚠️ **诚实边界**: 海外域名无法 100% 保证内地不被墙。要绝对稳只能用 Azure China(21Vianet 独立环境)+ ICP 备案, 临时项目不划算。`azurecontainerapps.io` 日常访问基本没问题。
> ⚠️ **费用**: min-replicas=1 × 0.5vCPU 常驻约 **$10-15/月**(超 ACA 免费额度)。HKMU 学生订阅有 $100 免费额度可能数月抵扣。临时 1-2 月约 $10-30。降级见末尾。

## 前置(都已就绪 ✅)

- GitHub 账号 + `gh` CLI 已登录(`3031445204zelsy17-lang`)
- Azure 账号(HKMU 学校租户订阅) + 本机 `az` CLI 2.87 + containerapp 扩展(已验证能联网)
- 本机 PyYAML 6.0.3(provision.sh 第 7 步注入 Azure Files 卷用)

---

## 步骤 0: 建 GitHub 仓库 + push(一次性, ~1min)

仓库还没推 GitHub(`git remote` 为空)。一条命令建公开仓库 + 推送:

```bash
gh repo create worldcup-dashboard --public --source=. --remote=origin --push \
  --description "2026 FIFA 世界杯实时概率仪表盘 — 实时 + 透明 + 可解释"
```

> 若想先私有后面再开源, 把 `--public` 换 `--private`(注意 private 仓库 Actions 每月 2000 免费分钟, 够用)。

## 步骤 A: Azure 资源 provision(本机, ~15min, 一次性)

```bash
# 先把区域改成 eastasia(默认 eastus; 你在内地/香港, eastasia 最近最稳)
# 编辑 azure/provision.sh 第 12 行: LOCATION="eastasia"
# 然后:
bash azure/provision.sh
```

provision 会建: 资源组 `wc2026-rg` + ACR + Container Apps 环境 + Storage account/file share + Container App(占位镜像先起, CI 推真镜像后恢复)。**记下输出**:
- `ACA FQDN`(如 `https://wc2026-api.eastasia.azurecontainerapps.io`)
- `ACR 名`(如 `wc2026acr12345`)

> 用占位镜像先起容器(健康检查会 fail, 正常), CI 推真镜像后恢复。

## 步骤 B: 配 GitHub Actions 凭据

CI 要登录 Azure 才能 push 镜像 + 更新 ACA。**推荐 OIDC(无 secret)**。

### B1. 创建 OIDC federated credential(本机跑)

```bash
GH_REPO="3031445204zelsy17-lang/worldcup-dashboard"   # ★你的 repo 全名
APP_ID=$(az ad app create --display-name wc2026-gh-actions --query appId -o tsv)
az ad app federated-credential create --id "$APP_ID" --parameters '{
  "name":"gh-actions-main","issuer":"https://token.actions.githubusercontent.com",
  "subject":"repo:'"$GH_REPO"':ref:refs/heads/main","audiences":["api://AzureADTokenExchange"]
}'
PRINCIPAL_ID=$(az ad app show --id "$APP_ID" --query id -o tsv)
RG_ID=$(az group show -n wc2026-rg --query id -o tsv)
ACR_ID=$(az acr show -n <★改成步骤A的ACR名> --query id -o tsv)
az role assignment create --role Contributor --assignee "$PRINCIPAL_ID" --scope "$RG_ID"
az role assignment create --role AcrPush     --assignee "$PRINCIPAL_ID" --scope "$ACR_ID"
echo "AZURE_CLIENT_ID=$APP_ID"
echo "AZURE_TENANT_ID=$(az account show --query tenantId -o tsv)"
echo "AZURE_SUBSCRIPTION_ID=$(az account show --query id -o tsv)"
```

### B2. GitHub repo 配 Secrets

GitHub 仓库 → Settings → Secrets and variables → Actions → New repository secret, 加 3 个:

| Secret 名 | 值 |
|---|---|
| `AZURE_CLIENT_ID` | B1 输出的 APP_ID |
| `AZURE_TENANT_ID` | B1 输出的 tenantId |
| `AZURE_SUBSCRIPTION_ID` | B1 输出的 subscription id |

### B3. 降级 — 用 client secret(若 OIDC 嫌麻烦)

```bash
RG_ID=$(az group show -n wc2026-rg --query id -o tsv)
ACR_ID=$(az acr show -n <ACR名> --query id -o tsv)
az ad sp create-for-rbac --name wc2026-gh --role Contributor \
  --scopes "$RG_ID" "$ACR_ID" --sdk-auth
```
把输出的整个 JSON 存为 GitHub Secret `AZURE_CREDENTIALS`, 然后改 `.github/workflows/deploy.yml` 的 Azure login 步骤: **注释** client-id/tenant-id/subscription-id 三行, 改用 `creds: ${{ secrets.AZURE_CREDENTIALS }}`。

## 步骤 C: 对齐 deploy.yml 资源名 + push 上线

provision.sh 的 `ACR_NAME` 带 `${RANDOM}`(如 `wc2026acr12345`), 但 `.github/workflows/deploy.yml` 写死 `wc2026acr`。**改 deploy.yml 顶部 env 三行**对齐 provision 实际输出:

```yaml
env:
  RG_NAME: wc2026-rg
  ACR_NAME: wc2026acr12345        # ★改成 provision 输出的实际 ACR 名
  ACA_NAME: wc2026-api
  IMAGE: wc2026acr12345.azurecr.io/wc2026-backend:latest   # ★同上 ACR 前缀
```

改完 commit + push:

```bash
git add -A && git commit -m "deploy: 单 Azure 域名同源(砍 Vercel) + deploy.yml 资源名对齐"
git push origin main
```

GitHub Actions 自动跑(首次 ~15-20min: 生成模型 artifacts + node build 前端 + ACR 构建 + 更新 ACA; 之后 artifacts 命中 cache 秒级)。Actions 全绿 → 公开站上线 🎉

---

## 验证 checklist

1. `curl https://<aca-fqdn>/api/health` → `status:ok, db_readable:true, counts:{teams:48,matches:72,tournament_probs:288}`
2. `/api/health` 的 `last_mc_recomputed_at` 非 null(worker 建库 + 首轮 MC 已跑, 首启冷启动 ~30s)
3. `https://<aca-fqdn>/api/tournament?view=win` → Argentina ~16%
4. **浏览器打开 `https://<aca-fqdn>/`** → Overview 48 队渲染(前端也由同域名 serve, 不再需要 Vercel)
5. 浏览器 devtools Network: `/api/health` 请求 URL 是 `<aca-fqdn>/api/health`(**同源**, 200), JS/CSS 走 `/assets/*`
6. 5-10min 后 `last_mc_recomputed_at` 变化(worker 持续采集)
7. Azure Files 有 wc.db: `az storage file list --share-name wcdata --account-name <storage名>`

## 故障排查

| 现象 | 排查 |
|---|---|
| Actions 失败 | GitHub repo → Actions 看哪步红。常见: artifacts 生成错(看日志)、ACR push 权限(role AcrPush)、az login(OIDC subject 不匹配 repo/分支名)、**前端 build 失败**(看 node stage 日志) |
| `/api/health` db_readable:false | worker 还在建库(冷启动 30s), 等; 仍 false → worker 崩: `az containerapp logs show --name wc2026-api -g wc2026-rg --follow` |
| 浏览器打开白屏 | 前端没烤进镜像? 看 CI 日志 Stage 1 node build 是否成功; `curl https://<aca-fqdn>/` 应返回 index.html |
| `/<客户端路由>` 404 | app.py 的 SPA catch-all 没生效 → 确认镜像里的 `frontend/dist/index.html` 存在 |
| 概率不更新 | `last_mc_recomputed_at` 老于 1h → worker 可能崩: `az containerapp revision restart --name wc2026-api -g wc2026-rg`; 或 martj42 暂无新完赛 |
| 想改资源名/规格 | workflow 的 `RG_NAME/ACR_NAME/ACA_NAME` 与 provision 保持一致; 改 cpu/memory: `az containerapp update --cpu 0.75 --memory 1.5Gi` |

## 费用明细

- **ACA Consumption**: 免费 180,000 vCPU-秒 + 360,000 GiB-秒 + 2M requests/月(subscription 级共享)
- 本项目 min-replicas=1 × 0.5vCPU × 24h × 30d = **648,000 vCPU-秒 → 超 ~468k → 约 $10-15/月**(eastasia 同价)
- ACR Basic ~$5/月; Azure Files 5GB ~$0.5/月; (Vercel 已砍, 省一层)
- **HKMU 学生订阅**: Azure for Students 有 $100 免费额度 + 部分免费层, 可能数月零支出
- **降级省钱**(不推荐): `min-replicas=0` + Azure Logic App 每 5min HTTP GET `/api/health` 唤醒容器跑一轮 worker。省 ~$10/月但比赛日密集重算可能漏采, 冷启动慢

## 临时下线(赛期/课程结束后)

```bash
# 一键删整个资源组, 停止所有计费(数据/镜像/容器全清)
az group delete --name wc2026-rg --yes --no-wait

# 想保留 GitHub repo: 仓库留着不动(代码沉淀, 不产生 Azure 费用)
# 想彻底删 repo: gh repo delete 3031445204zelsy17-lang/worldcup-dashboard --yes
```

## 本地 docker 验证(开发/调试)

```bash
# multi-stage build: 本地 build 前端 + 后端
docker build -t wc-backend .
docker run --rm -p 8000:8000 \
  -v "$(pwd)/data:/mnt/data" \
  -e WORKER_DB=/mnt/data/wc.db -e API_DB=/mnt/data/wc.db \
  wc-backend
# 前端: http://localhost:8000/  (同源, 不走 5173)
# API:   http://localhost:8000/api/health
```

## 升级路径(后续)

- **worker 崩不自动重启** → 装 supervisord 管 worker+uvicorn, 或拆 worker/API 双 Container App(各自重启, 共享同一 Azure Files)
- **SQLite on SMB 高并发不稳** → 换 Azure Database for PostgreSQL Flexible Server(免费 12 月), 改 schema/queries
- **artifacts 更新**(如 Elo 重训) → 模型代码变 → cache key 变 → CI 自动重生成
- **内地访问仍不稳** → 绑自定义域名 + ICP 备案(需长期部署才值得); 或前端上 CDN
