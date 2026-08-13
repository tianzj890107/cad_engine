#!/usr/bin/env bash
# 在开发机执行：推送当前提交到 GitHub，然后触发服务器拉取同一分支部署。
set -euo pipefail

HOST="${CAD_ENGINE_HOST:-zhangzhen@172.16.10.34}"
REMOTE_DIR="${CAD_ENGINE_REMOTE_DIR:-/home/zhangzhen/cad_engine}"
SSH_KEY="${CAD_ENGINE_SSH_KEY:-$HOME/.ssh/id_ed25519}"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BRANCH="${CAD_ENGINE_DEPLOY_BRANCH:-$(git -C "$ROOT_DIR" branch --show-current)}"

if [[ -z "$BRANCH" ]]; then
  echo "当前不在分支上，无法发布。" >&2
  exit 1
fi
if [[ -n "$(git -C "$ROOT_DIR" status --porcelain)" ]]; then
  echo "本地仍有未提交内容；请先提交，发布已停止。" >&2
  git -C "$ROOT_DIR" status --short >&2
  exit 1
fi

if [[ "${CAD_ENGINE_USE_PASSWORD:-false}" == "true" ]]; then
  SSH_OPTS=(-o PreferredAuthentications=password -o PubkeyAuthentication=no)
else
  [[ -f "$SSH_KEY" ]] || { echo "未找到 SSH 私钥：$SSH_KEY" >&2; exit 1; }
  SSH_OPTS=(-o IdentitiesOnly=yes -o BatchMode=yes -i "$SSH_KEY")
fi

git -C "$ROOT_DIR" push origin "HEAD:$BRANCH"
ssh "${SSH_OPTS[@]}" "$HOST" "cd '$REMOTE_DIR' && bash scripts/deploy_intranet_git.sh '$BRANCH'"
