#!/usr/bin/env bash
# 部署脚本：把构建产物铺到目标目录，注册 systemd 服务，开一次冒烟。
#
#   ./scripts/deploy.sh <目标目录> [端口]
#
# 默认目标目录 /opt/asset-suggestion，端口 8760。
#
# 注意：目标机需要 Python >= 3.10 与 SQLite（标准库自带）。
# 政务专网环境请按方案 §11 的部署拓扑调整（本脚本是单实例参考实现）。
set -euo pipefail

TARGET="${1:-/opt/asset-suggestion}"
PORT="${2:-8760}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="asset-suggestion"

echo "==> 1/5 构建"
"$ROOT/scripts/build.sh"

if [[ ! -f "$ROOT/dist/asset_suggestion-"*.tar.gz ]]; then
  echo "构建产物缺失" >&2
  exit 1
fi
TARBALL="$(ls -1 "$ROOT"/dist/asset_suggestion-*.tar.gz | head -n1)"

echo "==> 2/5 铺设到 $TARGET"
mkdir -p "$TARGET"
tar -xzf "$TARBALL" -C "$TARGET" --strip-components=0
mkdir -p "$TARGET/data"

echo "==> 3/5 生成配置"
if [[ ! -f "$TARGET/config/default.yaml" ]]; then
  echo "配置文件缺失，请检查构建产物" >&2
  exit 1
fi

echo "==> 4/5 注册 systemd 服务"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Asset Suggestion Service (公共设施报修与资产协同)
After=network.target

[Service]
Type=simple
User=asset-suggestion
WorkingDirectory=$TARGET
Environment=PYTHONPATH=$TARGET/src
Environment=PYTHONIOENCODING=utf-8
ExecStart=/usr/bin/env python3 -m asset_suggestion --db $TARGET/data/suggestion.db serve --host 127.0.0.1 --port $PORT
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

echo "==> 5/5 冒烟"
sleep 2
curl -fsS "http://127.0.0.1:${PORT}/api/health"
echo
curl -fsS "http://127.0.0.1:${PORT}/api/status" | head -c 400
echo
echo "==> 部署完成：${SERVICE_NAME} 已启动，监听 127.0.0.1:${PORT}"
echo "    查看日志：journalctl -u ${SERVICE_NAME} -f"
echo "    停止：systemctl stop ${SERVICE_NAME}"
