#!/usr/bin/env bash
# 在内网服务器执行：从 GitHub 快进拉取指定分支，再重启/重建应用。
set -euo pipefail

BRANCH="${1:-${CAD_ENGINE_DEPLOY_BRANCH:-20260722}}"
REMOTE="${CAD_ENGINE_GIT_REMOTE:-origin}"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DOCKER_CONFIG_DIR="${CAD_ENGINE_DOCKER_CONFIG:-/home/data/cad_engine/.docker}"
COMPOSE_FILE="${CAD_ENGINE_COMPOSE_FILE:-docker-compose.intranet.yml}"

cd "$ROOT_DIR"

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "服务器源码工作区存在未提交改动，已停止部署：" >&2
  git status --short --untracked-files=no >&2
  exit 1
fi

CURRENT_BRANCH="$(git branch --show-current)"
if [[ "$CURRENT_BRANCH" != "$BRANCH" ]]; then
  echo "服务器当前分支为 $CURRENT_BRANCH，期望 $BRANCH，已停止部署。" >&2
  exit 1
fi

BEFORE="$(git rev-parse HEAD)"
git fetch "$REMOTE" "$BRANCH"
git merge --ff-only "$REMOTE/$BRANCH"
AFTER="$(git rev-parse HEAD)"

if git diff --name-only "$BEFORE" "$AFTER" -- requirements.txt Dockerfile | grep -q .; then
  env DOCKER_CONFIG="$DOCKER_CONFIG_DIR" docker compose -f "$COMPOSE_FILE" up -d --build
  echo "依赖或镜像定义已变化，已重新构建并启动。"
else
  env DOCKER_CONFIG="$DOCKER_CONFIG_DIR" docker compose -f "$COMPOSE_FILE" up -d --no-build
  env DOCKER_CONFIG="$DOCKER_CONFIG_DIR" docker compose -f "$COMPOSE_FILE" restart app
  echo "源码已拉取，应用已重启。"
fi

env DOCKER_CONFIG="$DOCKER_CONFIG_DIR" docker compose -f "$COMPOSE_FILE" ps
HEALTH_URL="${CAD_ENGINE_HEALTH_URL:-http://127.0.0.1:8002/api/health}"
HEALTH_BODY="$(mktemp)"
trap 'rm -f "$HEALTH_BODY"' EXIT
if ! curl --fail --silent --show-error --max-time 10 "$HEALTH_URL" >"$HEALTH_BODY"; then
  echo "健康检查失败：$HEALTH_URL" >&2
  exit 1
fi
grep -q '"status"[[:space:]]*:[[:space:]]*"ok"' "$HEALTH_BODY" || {
  echo "健康检查返回异常：$(head -c 500 "$HEALTH_BODY")" >&2
  exit 1
}
echo "健康检查通过：$HEALTH_URL"
echo "部署完成：$BRANCH ${AFTER:0:12}"
