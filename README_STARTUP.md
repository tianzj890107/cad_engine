# 启动说明

本项目有两种启动方式：本地开发启动和公司内网 Docker 启动。公司内网正式访问地址是 `http://172.16.10.34:8002`。

## 一、本地开发启动

### 1. 安装依赖

```bash
cd /Users/sher/Desktop/Boulderaitech/cad_engine
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，至少配置当前使用的 LLM Provider 和 API Key。密钥不要提交到 Git。

### 3. 启动应用

```bash
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

浏览器访问：

```text
http://127.0.0.1:8000
```

本地开发模式的宿主机端口是 `8000`；这与公司内网服务器的访问端口不同。

## 二、完整 Docker 启动

适用于需要同时启动应用、Postgres 和 MinIO 的环境：

```bash
cp .env.example .env
# 编辑 .env，填写数据库、对象存储、鉴权和 LLM 配置
docker compose up -d --build
docker compose ps
docker compose logs -f app
```

默认访问地址（完整 Docker compose）：

- 应用：`http://127.0.0.1:8000`
- MinIO 控制台：`http://127.0.0.1:9001`

检查应用：

```bash
curl http://127.0.0.1:8000/api/health
```

返回 JSON 中包含 `"status":"ok"` 才表示应用健康。

## 三、公司内网部署

内网部署使用 `20260722` 分支，服务器登录用户和目录为：

```text
zhangzhen@172.16.10.34
/home/zhangzhen/cad_engine
```

服务器对外使用 `8002`，容器内部监听 `8000`：

```text
浏览器 → 172.16.10.34:8002 → 容器:8000
```

发布前必须保证本地工作区干净，并且修改已经提交：

```bash
git status
git branch --show-current
git log -1 --oneline
```

执行发布：

```bash
bash scripts/sync_intranet_source.sh
```

该脚本会完成：推送当前分支、SSH 通知服务器、服务器快进拉取、重启或重建容器、检查容器状态和 `/api/health`。

发布后验证：

```bash
curl http://172.16.10.34:8002/api/health
```

## 四、常用运维命令

```bash
docker compose ps
docker compose logs --tail=100 app
docker compose restart app
docker compose down              # 停止服务，保留数据卷
docker compose down -v           # 停止并删除数据卷，谨慎使用
```

内网环境的 `data/` 和 `.env` 独立保留，不要用源码同步覆盖；当前 AI/CAD 任务队列是进程内队列，生产环境保持单个 app 实例。

更完整的私有化配置、备份和扩容说明见 [DEPLOYMENT.md](DEPLOYMENT.md)。
