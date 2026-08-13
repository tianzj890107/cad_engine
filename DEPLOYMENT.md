# 私有化部署指南

## 公司内网当前部署：GitHub 分支发布

服务器 `172.16.10.34:8002` 的 `cad-engine` 运行目录为 `/home/zhangzhen/cad_engine`，并将其中的 `backend/`、`frontend/`、`apps/` 以只读方式挂载到容器中，容器内部仍监听 8000，服务器 8000 端口保留给其他服务；该目录中的 `data/` 与 `.env` 独立保留，不会被日常源码同步覆盖。

日常修改必须先提交到当前发布分支，再执行：

```bash
bash scripts/sync_intranet_source.sh
```

如果服务器暂时未配置公钥，可临时使用密码模式（密码只由 SSH 交互式读取，不会写入脚本）：

```bash
CAD_ENGINE_USE_PASSWORD=true bash scripts/sync_intranet_source.sh
```

该命令会依次完成：推送当前提交到 GitHub 同名分支、通过 SSH 通知服务器、服务器执行
`git fetch` 和 `git merge --ff-only`、重启或重建应用、检查容器状态并访问 `/api/health`。
旧命令名保留为兼容入口；实际逻辑位于 `scripts/release_intranet.sh`（开发机）和
`scripts/deploy_intranet_git.sh`（服务器）。

部署完成后建议恢复公钥模式，避免每次发布重复输入密码。

- 前端 HTML/CSS/JS：拉取后会重启 `cad-engine` 容器，刷新浏览器即可生效；
- 后端 Python：脚本重启 `cad-engine` 容器，不重建 Docker 镜像；
- 修改 `requirements.txt` 或 `Dockerfile` 时，服务器会自动重新构建镜像。

`data/`、`.env`、`__pycache__/` 和 `*.pyc` 均不属于源码发布内容，不进入 Git；服务器上的
运行数据和密钥会原地保留。服务器源码工作区若存在未提交修改，部署脚本会直接停止，避免
静默覆盖线上手工改动。

当前公司内网演示 compose 已固定 `AUTH_AUTO_ADMIN=true`，应用会自动使用默认管理员身份；完整用户、角色和登录接口仍保留。若切换正式登录环境，将 `docker-compose.intranet.yml` 中该值改回 `false` 后重启容器即可。

团队 Qwen API 建议在服务器 `.env` 保持以下高质量配置（密钥和内网 Base URL 不写入 Git）：

```dotenv
LLM_PROVIDER=qwen
QWEN_MODEL=qwen3-vl-plus
QWEN_VISION_MODELS=qwen3-vl-plus,qwen3-vl-flash,qwen-vl-plus,glm-5v-turbo
QWEN_MAX_RETRIES=3
QWEN_SCHEMA_REPAIR_RETRIES=2
QWEN_MAX_OUTPUT_TOKENS=32768
QWEN_VISION_MAX_OUTPUT_TOKENS=32768
QWEN_TEXT_MAX_OUTPUT_TOKENS=12000
QWEN_VISION_ENABLE_THINKING=false
QWEN_TEXT_ENABLE_THINKING=true
LLM_MAX_ATTACHMENTS=20
LLM_MAX_ATTACHMENT_TEXT_CHARS=100000
LLM_MAX_ATTACHMENT_IMAGE_BYTES=20971520
LLM_MAX_DOCUMENTS=32
LLM_MAX_DOCUMENT_CHARS=100000
LLM_MAX_TOTAL_DOCUMENT_CHARS=500000
```

视觉模型必须使用视觉能力池；文本模型不应被放进视觉池。视觉 JSON 关闭思考是兼容性设置，
因为部分 OpenAI 兼容网关在开启思考时只返回 `reasoning_content` 而没有 JSON 正文。
Pydantic/CAD 结构化校验仍然保留，用于阻止错误尺寸进入工程数据，不是费用限制。

一条命令拉起完整后端栈:**应用 + Postgres(元数据) + MinIO(对象存储)**。
元数据进入 Postgres、二进制进入 MinIO；但当前 AI/CAD 任务队列在 app 进程内，
因此部署时应保持单个 app 实例。接入共享任务队列后再做水平扩容。

```
        ┌─────────┐      ┌──────────────┐
浏览器 ─▶│  app    │─────▶│ Postgres(db) │  项目/IR/版本/审计/用户(SQL)
        │ :8002   │      └──────────────┘
        │ FastAPI │      ┌──────────────┐
        │ +CAD    │─────▶│ MinIO        │  原图/STEP/STL/SVG/DXF(对象存储)
        └─────────┘      └──────────────┘
```

## 一、前置

- 安装 Docker + Docker Compose(`docker compose version` 可用即可)。
- 服务器建议 ≥ 4C/8G(CadQuery/OCCT 几何运算吃 CPU/内存)。

## 二、配置

在仓库根目录(与 `docker-compose.yml` 同级)新建 `.env`:

```dotenv
ANTHROPIC_API_KEY=sk-ant-xxxxxxxx        # 必填: 图像解析/拆解要用
AUTH_SECRET=用 openssl rand -hex 32 生成   # 必填: 令牌签名密钥
POSTGRES_PASSWORD=改成强密码
S3_ACCESS_KEY=改成强账号
S3_SECRET_KEY=改成强密码
DEFAULT_ADMIN_PASSWORD=改成强密码          # 首次启动建的 admin 初始密码
```

> 不填的项会用 `docker-compose.yml` 里的默认值(仅供试跑,**生产务必全部改掉**)。

## 三、启动

```bash
docker compose up -d --build      # 首次会构建应用镜像(含 CadQuery,稍久)
docker compose ps                 # 看各服务状态
docker compose logs -f app        # 跟踪应用日志
```

- 平台:        http://localhost:8002  (本机默认端口仍可按 compose 映射调整；默认管理员 `admin` / 你设的 `DEFAULT_ADMIN_PASSWORD`)
- MinIO 控制台: http://localhost:9001  (`S3_ACCESS_KEY` / `S3_SECRET_KEY`)

启动顺序由 compose 编排:`db` 健康 + `createbucket` 建好桶后,`app` 才启动。

## 四、首次使用

1. 用 `admin` 登录，在右上角账户菜单中创建或审核用户，并按角色授予销售经理、工艺工程师、工艺技术经理、工艺技术总监或只读权限。
2. 上传设备需求原图 → 解析 → 校验 → 拆解 → 生成几何/2D → 审签。
3. 所有耗时步骤为异步任务,前端自动轮询进度。

## 五、运维

```bash
docker compose down               # 停服(保留数据卷)
docker compose down -v             # 停服并删数据(谨慎!清空 Postgres+MinIO)
docker compose pull && docker compose up -d --build   # 升级
```

数据卷:`pgdata`(Postgres)、`miniodata`(MinIO 对象)、`appcache`(S3 本地缓存)。
**备份** = 备份 `pgdata` + `miniodata`(`appcache` 是缓存,可丢弃)。

## 六、扩容 / 接入既有设施

- **当前版本请保持单个 app 实例**。耗时 AI/CAD 任务仍由进程内队列执行；在未接入 Redis/Celery、RQ 或数据库任务租约前，多副本会带来重复执行和任务恢复误判风险。
  需要横向扩容时，应先把 `backend/services/tasks.py` 替换为共享任务队列，再在前面挂 Nginx/Traefik。
- **用现成的库/对象存储**:不想跑自带的 db/minio,删掉这两个 service,把 `app` 的
  `DATABASE_URL` / `S3_*` 指向你已有的 Postgres 与 S3/MinIO 即可。
- **纯本地轻量模式**:不想用容器,直接 `pip install -r requirements.txt` 后
  `uvicorn backend.main:app`,默认就是 SQLite + 本地磁盘 + 关闭鉴权(见 README)。
