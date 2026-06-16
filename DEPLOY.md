# 私有化部署指南

一条命令拉起完整后端栈:**应用 + Postgres(元数据) + MinIO(对象存储)**。
应用本身无状态(元数据进库、二进制进对象存储),可水平扩容多副本共享同库同桶。

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

1. 用 `admin` 登录,在右上角「+用户」按角色建号(工程师/校核审签/只读)。
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

- **多副本**:`docker compose up -d --scale app=3` 并在前面挂个反向代理(Nginx/Traefik)。
  因状态都在 Postgres + MinIO,副本间天然共享。
- **用现成的库/对象存储**:不想跑自带的 db/minio,删掉这两个 service,把 `app` 的
  `DATABASE_URL` / `S3_*` 指向你已有的 Postgres 与 S3/MinIO 即可。
- **纯本地轻量模式**:不想用容器,直接 `pip install -r requirements.txt` 后
  `uvicorn backend.main:app`,默认就是 SQLite + 本地磁盘 + 关闭鉴权(见 README)。
