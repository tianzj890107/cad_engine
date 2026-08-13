#!/usr/bin/env bash
# 兼容旧命令：发布已提交的当前分支，并让服务器从 GitHub 拉取后部署。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT_DIR/scripts/release_intranet.sh" "$@"
