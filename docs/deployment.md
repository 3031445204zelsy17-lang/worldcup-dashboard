# 部署指南 — P1-7

> 把 worldcup-dashboard 部署成公开站: **Vercel**(前端) + **Azure Container Apps**(后端 worker+API) + **GitHub Actions** 自动部署。
> 节奏: 一次性 provision(~15min) → 配 secrets → push 上线。之后改代码 push 即自动更新。

## 架构 / 决策

- **前端** → Vercel(静态 SPA, `vercel.json` 把 `/api/*` proxy 到 Azure → 免 CORS)
- **后端** → Azure Container App 单容器双进程(worker 后台采集+MC 重算 / uvicorn 前台只读 API)
- **SQLite** → Azure Files 持久卷(`/mnt/data/wc.db`, WAL 并发)
- **部署** → GitHub Actions(push main 自动: 生成模型 artifacts → ACR 构建 → 更新 ACA)
- **不改业务代码**: 路径自适配容器; DB 走 env; CORS/api.js 已支持两种模式

> ⚠️ **费用**: min-replicas=1 × 0.5vCPU 常驻约 **$10-15/月**(超 ACA 免费额度)。学生账号免费额度可能抵扣。换 7×24 实时采集, 值。降级见末尾。

## 前置

- GitHub 仓库(本 repo)
- Azure 账号(portal.azure.com)
- Vercel 账号(vercel.com, 用 GitHub 登录)

---

## 步骤 A: Azure 资源 provision(~15min, 一次性)

**在 Azure Portal Cloud Shell 跑**(不走你本地代理——你本地 az CLI 走代理 503 连不上 Azure):

1. 打开 portal.azure.com → 顶部工具栏 `>_`(Cloud Shell) → 选 **Bash**(首次会让你建 storage, 同意即可)
2. 把 `azure/provision.sh` 内容粘进 Cloud Shell 编辑器(`code provision.sh` 打开编辑器, 粘贴 → Ctrl+S → Ctrl+Q)
3. 改占位变量(脚本顶部):
   - `ACR_NAME` / `STORAGE_ACCOUNT` 已加 `${RANDOM}` 防撞, 可不改
   - `VERCEL_URL` 先留默认, 步骤 C 后回来改成你的 Vercel 域名
4. `bash provision.sh` → 记下输出的 **ACA FQDN** + ACR 名 + RG 名

> provision 用占位镜像先起容器(健康检查会 fail, 正常), CI 推真镜像后恢复。

## 步骤 B: 配 GitHub Actions 凭据

CI 要登录 Azure 才能 push 镜像 + 更新 ACA。**推荐 OIDC(无 secret)**。

### B1. 创建 OIDC federated credential(Cloud Shell 跑)

```bash
GH_REPO="你的GitHub用户名/worldcup-dashboard"   # ★改成你的 repo 全名
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

Cloud Shell 跑:
```bash
RG_ID=$(az group show -n wc2026-rg --query id -o tsv)
ACR_ID=$(az acr show -n <ACR名> --query id -o tsv)
az ad sp create-for-rbac --name wc2026-gh --role Contributor \
  --scopes "$RG_ID" "$ACR_ID" --sdk-auth
```
把输出的整个 JSON 存为 GitHub Secret `AZURE_CREDENTIALS`, 然后改 `.github/workflows/deploy.yml` 的 Azure login 步骤: **注释** client-id/tenant-id/subscription-id 三行, 改用 `creds: ${{ secrets.AZURE_CREDENTIALS }}`。

## 步骤 C: Vercel 部署前端

1. vercel.com → Add New → Project → Import 你的 GitHub 仓库
2. Framework Preset: **Other**(`vercel.json` 已配 buildCommand `cd frontend && npm ci && npm run build` + outputDirectory `frontend/dist`)
3. Settings → Environment Variables 加:
   - `AZURE_API_URL` = 步骤 A 的 ACA FQDN(**不带尾斜杠**, 如 `https://wc2026-api.eastus.azurecontainerapps.io`)
4. Deploy → 拿到 Vercel 域名(`xxx.vercel.app`)
5. (可选回填)把 provision 里的 `VERCEL_URL` 改成这个域名后重跑 step 6-7, 或直接 `az containerapp update` 改 `CORS_ORIGINS`(rewrite proxy 同源其实不依赖 CORS, 这步可选)

> 若改用 CORS 降级(非 rewrite proxy): 不配 `AZURE_API_URL`, 改配 `VITE_API_BASE=<ACA FQDN>`, 并把 `CORS_ORIGINS` 改成你的 Vercel 域名。

## 步骤 D: push 上线

```bash
# 确认 .github/workflows/deploy.yml 的 RG_NAME/ACR_NAME/ACA_NAME 与 provision.sh 一致后:
git push origin main
```

GitHub Actions 自动跑(首次 ~15min 生成 artifacts, 之后 cache 秒级)。Actions 全绿 → 公开站上线 🎉

---

## 验证 checklist

1. `curl https://<aca-fqdn>/api/health` → `status:ok, db_readable:true, counts:{teams:48,matches:72,tournament_probs:288}`
2. `/api/health` 的 `last_mc_recomputed_at` 非 null(worker 建库 + 首轮 MC 已跑, 首启冷启动 ~30s)
3. `https://<aca-fqdn>/api/tournament?view=win` → Argentina ~16%
4. Vercel 域名打开 → Overview 48 队渲染
5. 浏览器 devtools Network: `/api/health` 请求 URL 是 `xxx.vercel.app/api/health`(**同源 proxy**, 非 azure URL), 200
6. 5-10min 后 `last_mc_recomputed_at` 变化(worker 持续采集)
7. Azure Files 有 wc.db: Cloud Shell `az storage file list --share-name wcdata --account-name <storage名>`

## 故障排查

| 现象 | 排查 |
|---|---|
| Actions 失败 | GitHub repo → Actions 看哪步红。常见: artifacts 生成错(看日志)、ACR push 权限(role AcrPush)、az login(OIDC subject 不匹配 repo/分支名) |
| `/api/health` db_readable:false | worker 还在建库(冷启动 30s), 等; 仍 false → worker 崩: `az containerapp logs show --name wc2026-api -g wc2026-rg --follow` |
| Vercel 页白 / 接口 504 | `AZURE_API_URL` 没配 / 带尾斜杠; 或 ACA 还在占位镜像(push main 触发 CI 推真镜像了吗?) |
| CORS 报错 | 走 rewrite proxy 不该有 CORS; 若直连 Azure 才需 `CORS_ORIGINS` 加域名 |
| 概率不更新 | `last_mc_recomputed_at` 老于 1h → worker 可能崩: `az containerapp revision restart --name wc2026-api -g wc2026-rg`; 或 martj42 暂无新完赛 |
| 想改资源名/规格 | workflow 的 `RG_NAME/ACR_NAME/ACA_NAME` 与 provision 保持一致; 改 cpu/memory: `az containerapp update --cpu 0.75 --memory 1.5Gi` |

## 费用明细

- **ACA Consumption**: 免费 180,000 vCPU-秒 + 360,000 GiB-秒 + 2M requests/月(subscription 级共享)
- 本项目 min-replicas=1 × 0.5vCPU × 24h × 30d = **648,000 vCPU-秒 → 超 ~468k → 约 $10-15/月**(EastUS)
- ACR Basic ~$5/月; Azure Files 5GB ~$0.5/月; Vercel Hobby 免费
- **学生账号**: Azure for Students 有 $100 免费额度 + 部分免费层, 可能数月零支出
- **降级省钱**(不推荐): `min-replicas=0` + Azure Logic App 每 5min HTTP GET `/api/health` 唤醒容器跑一轮 worker。省 ~$10/月但比赛日密集重算可能漏采, 冷启动慢

## 本地 docker 验证(开发/调试)

```bash
# 用本地已有 artifacts(data/processed/ 有 parquet) bake 进镜像
docker build -t wc-backend .
docker run --rm -p 8000:8000 \
  -v "$(pwd)/data:/mnt/data" \
  -e WORKER_DB=/mnt/data/wc.db -e API_DB=/mnt/data/wc.db \
  wc-backend
curl http://localhost:8000/api/health
```

## 升级路径(后续)

- **worker 崩不自动重启** → 装 supervisord 管 worker+uvicorn, 或拆 worker/API 双 Container App(各自重启, 共享同一 Azure Files)
- **SQLite on SMB 高并发不稳** → 换 Azure Database for PostgreSQL Flexible Server(免费 12 月), 改 schema/queries
- **artifacts 更新**(如 Elo 重训) → 模型代码变 → cache key 变 → CI 自动重生成
