# 私有化部署指南

## 公司内网当前部署：增量演示模式

服务器 `172.16.10.34:8000` 的 `cad-engine` 已将 `backend/`、`frontend/`、`apps/` 以只读方式挂载到容器中，服务器 `data/` 与 `.env` 独立保留，不会被日常源码同步覆盖。

日常修改后，在本机项目根目录执行：

```bash
bash scripts/sync_intranet_source.sh
```

- 前端 HTML/CSS/JS：同步后刷新浏览器即可生效；
- 后端 Python：脚本仅重启 `cad-engine` 容器，不重建 Docker 镜像；
- 仅在修改 `requirements.txt` 或 `Dockerfile` 时，才需要本机构建新镜像并导入服务器。

一条命令拉起完整后端栈:**应用 + Postgres(元数据) + MinIO(对象存储)**。
元数据进入 Postgres、二进制进入 MinIO；但当前 AI/CAD 任务队列在 app 进程内，
因此部署时应保持单个 app 实例。接入共享任务队列后再做水平扩容。

```
        ┌─────────┐      ┌──────────────┐
浏览器 ─▶│  app    │─────▶│ Postgres(db) │  项目/IR/版本/审计/用户(SQL)
        │ :8000   │      └──────────────┘
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

- 平台:        http://localhost:8000  (默认管理员 `admin` / 你设的 `DEFAULT_ADMIN_PASSWORD`)
- MinIO 控制台: http://localhost:9001  (`S3_ACCESS_KEY` / `S3_SECRET_KEY`)

启动顺序由 compose 编排:`db` 健康 + `createbucket` 建好桶后,`app` 才启动。

## 四、首次使用

1. 用 `admin` 登录，在右上角账户菜单中创建或审核用户，并按角色授予工艺工程师、工艺技术经理、工艺技术总监或只读权限。
2. 上传设备需求原图 → 解析 → 校验 → 拆解 → 生成几何/2D → 审签。
3. 所有耗时步骤为异步任务,前端自动轮询进度。

## 五、运维

```bash
docker compose down               # 停服(保留数据卷)
docker compose down -v            # 停服并删数据(谨慎!清空 Postgres+MinIO)
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
