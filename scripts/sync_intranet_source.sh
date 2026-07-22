#!/usr/bin/env bash
# 将当前源码增量同步到公司内网服务器；不会上传或删除服务器 data/ 与 .env。
# 用法：bash scripts/sync_intranet_source.sh
set -euo pipefail

HOST="${CAD_ENGINE_HOST:-zhangzhen@172.16.10.34}"
REMOTE_DIR="${CAD_ENGINE_REMOTE_DIR:-/home/data/cad_engine/releases/20260722}"
SSH_KEY="${CAD_ENGINE_SSH_KEY:-$HOME/.ssh/id_ed25519}"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

command -v rsync >/dev/null || { echo "未找到 rsync" >&2; exit 1; }
[[ -f "$SSH_KEY" ]] || { echo "未找到 SSH 私钥：$SSH_KEY" >&2; exit 1; }

SYNC_LOG="$(mktemp)"
trap 'rm -f "$SYNC_LOG"' EXIT

rsync -azi --delete -e "ssh -i $SSH_KEY" \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude 'data/' \
  --exclude '.env' \
  --exclude '.DS_Store' \
  "$ROOT_DIR/" "$HOST:$REMOTE_DIR/" | tee "$SYNC_LOG"

if grep -qE '(^|[[:space:]])(backend/|docker-compose\.intranet\.yml)' "$SYNC_LOG"; then
  ssh -i "$SSH_KEY" "$HOST" "cd '$REMOTE_DIR' && env DOCKER_CONFIG=/home/data/cad_engine/.docker docker compose -f docker-compose.intranet.yml up -d --no-build && env DOCKER_CONFIG=/home/data/cad_engine/.docker docker compose -f docker-compose.intranet.yml restart app"
  echo "后端或容器配置已变更：cad-engine 已重启。"
elif grep -qE '(^|[[:space:]])(requirements\.txt|Dockerfile)' "$SYNC_LOG"; then
  echo "依赖或镜像定义已同步；需要另行构建新镜像后再部署。"
else
  echo "仅前端/静态资源变更：无需重启容器，刷新浏览器即可生效。"
fi

echo "源码同步完成。"
