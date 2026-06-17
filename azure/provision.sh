#!/usr/bin/env bash
# azure/provision.sh — 一次性创建后端全部 Azure 资源
#
# 在哪跑: Azure Portal 的 Cloud Shell(Bash)(portal.azure.com → 顶部 ">_" 按钮)。
#   原因: 你本地 az CLI 走代理 503 连不上 Azure; Cloud Shell 走 Azure 自家网络, 无此问题。
# 怎么跑: 把本文件内容粘进 Cloud Shell, 先改下面占位变量(★全局唯一项必须改), 再 bash 运行。
# 详见 docs/deployment.md。
set -euo pipefail

# ============ 占位变量(改这里! ★项必须全局唯一) ============
RG_NAME="wc2026-rg"
LOCATION="eastus"                 # 选离你近的; eastus 配额充足
ACR_NAME="wc2026acr${RANDOM}"     # ★全局唯一, 小写字母数字(加 RANDOM 防撞)
ACA_ENV="wc2026-env"
ACA_NAME="wc2026-api"
STORAGE_ACCOUNT="wc2026st${RANDOM}"  # ★全局唯一, 小写字母数字, 3-24 字符
SHARE_NAME="wcdata"
STORAGE_MOUNT_NAME="wcmount"
VOLUME_NAME="azurefile-vol"
VERCEL_URL="${VERCEL_URL:-https://wc2026-dashboard.vercel.app}"  # 你的 Vercel 域名(部署后回来改)
# ==============================================================

echo "==> 0. containerapp 扩展 + provider 注册"
az extension add --name containerapp --upgrade || true
az provider register --namespace Microsoft.App || true
az provider register --namespace Microsoft.OperationalInsights || true
az provider register --namespace Microsoft.Storage || true

echo "==> 1. 资源组"
az group create --name "$RG_NAME" --location "$LOCATION" -o table

echo "==> 2. ACR (Basic, 便宜; --admin-enabled 方便首次调试)"
az acr create --resource-group "$RG_NAME" --name "$ACR_NAME" --sku Basic --admin-enabled true -o table

echo "==> 3. Container Apps 环境"
az containerapp env create --name "$ACA_ENV" --resource-group "$RG_NAME" --location "$LOCATION" -o table

echo "==> 4. Storage account + file share(5GB, 存 wc.db)"
az storage account create --resource-group "$RG_NAME" --name "$STORAGE_ACCOUNT" \
  --location "$LOCATION" --kind StorageV2 --sku Standard_LRS -o table
az storage share-rm create --resource-group "$RG_NAME" --storage-account "$STORAGE_ACCOUNT" \
  --name "$SHARE_NAME" --quota 5 --enabled-protocols SMB -o table
STORAGE_KEY=$(az storage account keys list -n "$STORAGE_ACCOUNT" --query "[0].value" -o tsv)

echo "==> 5. file share 链接到 Container Apps 环境"
# ACA 不支持 identity 访问 Azure Files, 必须用 storage account key
az containerapp env storage set --name "$ACA_ENV" --resource-group "$RG_NAME" \
  --storage-name "$STORAGE_MOUNT_NAME" \
  --azure-file-account-name "$STORAGE_ACCOUNT" --azure-file-account-key "$STORAGE_KEY" \
  --azure-file-share-name "$SHARE_NAME" --access-mode ReadWrite -o table

echo "==> 6. 创建 Container App(占位镜像先起, CI 推真镜像后自动 update)"
az containerapp create --name "$ACA_NAME" --resource-group "$RG_NAME" \
  --environment "$ACA_ENV" \
  --image mcr.microsoft.com/azuredocs/containerapps-helloworld:latest \
  --cpu 0.5 --memory 1.0Gi --min-replicas 1 --max-replicas 1 \
  --target-port 8000 --ingress external \
  --env-vars WORKER_DB=/mnt/data/wc.db API_DB=/mnt/data/wc.db \
             API_HOST=0.0.0.0 PORT=8000 \
             WORKER_ALLOW_NETWORK=1 WORKER_POLL_INTERVAL=300 WORKER_MC_N=10000 \
             WORKER_PIDFILE=/mnt/data/worker.pid \
             "CORS_ORIGINS=$VERCEL_URL" \
  -o table

echo "==> 7. 注入 Azure Files volume(必须 YAML, 无 CLI 参数) + 回写"
az containerapp show --name "$ACA_NAME" --resource-group "$RG_NAME" --output yaml > /tmp/app.yaml
python3 - <<'PY'
import yaml, pathlib
p = pathlib.Path("/tmp/app.yaml")
doc = yaml.safe_load(p.read_text())
tmpl = doc["properties"]["template"]
vols = tmpl.setdefault("volumes", []) or []
if not any(v.get("name") == "azurefile-vol" for v in vols):
    vols.append({"name": "azurefile-vol", "storageName": "wcmount", "storageType": "AzureFile"})
tmpl["volumes"] = vols
cont = tmpl["containers"][0]
vm = cont.setdefault("volumeMounts", []) or []
if not any(v.get("volumeName") == "azurefile-vol" for v in vm):
    vm.append({"volumeName": "azurefile-vol", "mountPath": "/mnt/data"})
cont["volumeMounts"] = vm
p.write_text(yaml.safe_dump(doc, sort_keys=False))
print("YAML 已注入: volume azurefile-vol → /mnt/data")
PY
az containerapp update --name "$ACA_NAME" --resource-group "$RG_NAME" --yaml /tmp/app.yaml -o table

FQDN=$(az containerapp show --name "$ACA_NAME" --resource-group "$RG_NAME" \
  --query properties.configuration.ingress.fqdn -o tsv)

echo ""
echo "======================================================"
echo "✅ Provision 完成"
echo ""
echo "ACA FQDN:  https://$FQDN"
echo "ACR:       $ACR_NAME.azurecr.io"
echo "RG:        $RG_NAME"
echo ""
echo "记下这些! 下一步(见 docs/deployment.md):"
echo "  1. Vercel 项目 Environment Variables 配 AZURE_API_URL=https://$FQDN"
echo "  2. GitHub 仓库改 .github/workflows/deploy.yml 的 RG_NAME/ACR_NAME/ACA_NAME"
echo "     为本脚本用的值(RG=$RG_NAME ACR=$ACR_NAME ACA=$ACA_NAME)"
echo "  3. 配 GitHub Secrets(OIDC 三件套) — 见 deployment.md「配 GitHub Actions 凭据」"
echo "  4. push main → CI 推真镜像 → 公开站上线"
echo "======================================================"
echo ""
echo "⚠️ 记费提醒: min-replicas=1 × 0.5vCPU 常驻约 \$10-15/月(超 ACA 免费额度)。"
echo "   学生账号免费额度可能抵扣。详见 deployment.md「费用」。"
