# 图纸解析与生成平台

设备需求原图 → **结构化设计意图(IR)** → **确定性 CAD 几何**(STEP/STL/3D)。

核心思路:所选大模型提供商只负责把原图**理解并结构化**成「特征 + 参数 + 装配关系」的设计意图(IR);
真正的几何由确定性 CAD 内核(CadQuery / OpenCASCADE)生成 —— 从而输出**可制造、可校验、可追溯**。

```
上传原图 ──▶ 大模型视觉解析(IR) ──▶ 大模型拆解推荐 ──▶ CAD 内核生成几何+校验 ──▶ 前端展示
            structured outputs        DFM/复用建议        STEP/STL/质量属性     拆解树/3D/告警
```

## 目录结构

```
backend/
  config.py              全局配置(.env)
  models/ir.py           ★ 设计意图 IR 数据契约(Pydantic) —— 全平台地基
  services/
    claude_client.py     Anthropic / Claude 原有封装
    openai_client.py     OpenAI 可选封装(Responses API + 结构化输出 + 视觉)
    llm_client.py        提供商选择层(由 LLM_PROVIDER 决定)
    vision.py            图解析: 原图 -> IR
    decompose.py         拆解推荐: IR -> 增强 IR(生成/复用/DFM 建议)
    geometry.py          ★ 几何内核: IR 特征 -> CadQuery B-rep -> STEP/STL + 校验
    drawing2d.py         2D 工程图: OCCT HLR 投影三视图 SVG + 下料 DXF
    step_import.py       3D STEP 导入解析: OCCT 读实体 -> 包围盒/体积/孔检测 -> IR
    tree.py              层级结构树: 设备-总成-零件 + 层级编号(供 BOM/前端)
    bom.py               层级 BOM 生成 + CSV 导出
  storage/store.py       项目存储(公开 API 稳定) + 审计/可追溯
  storage/meta_backend.py 可插拔元数据后端: JSON 文件(默认) / SQL(SQLite·Postgres)
  main.py                FastAPI 应用(REST + 静态前端)
frontend/
  index.html / app.js / style.css   工作台(原图/拆解树/three.js 3D 查看器)
scripts/
  smoke_geometry.py      离线烟雾测试(无需 API Key，验证几何内核)
data/                    运行时数据(每个项目一个目录)
```

## 快速开始

### 1. 安装依赖

```powershell
cd C:\shuopan\图纸解析与生成
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

> CadQuery 依赖较重(含 OpenCASCADE),首次安装较慢。若暂时装不上,平台仍可运行
> 解析/拆解,只是「生成几何」按钮会返回 503。

### 2. 配置 API Key

```powershell
copy .env.example .env
# 编辑 .env, 填入 ANTHROPIC_API_KEY (默认) 或 OPENAI_API_KEY (选择 OpenAI 时)
```

### 3. 启动

```powershell
uvicorn backend.main:app --reload --port 8000
```

浏览器打开 http://localhost:8000

> **一键私有化部署**(应用 + Postgres + MinIO):见 [DEPLOY.md](DEPLOY.md) ——
> 配好 `.env` 后 `docker compose up -d --build` 即可,元数据进库、二进制进对象存储、
> 默认开启鉴权。当前 AI/CAD 任务队列为进程内实现，生产环境请保持单个 app 实例。

### 4. 使用

1. 选择设备需求原图(工程图/草图/照片均可);**可选**填写「补充文字说明」、上传「佐证文件」
   (其它视图图片 / 规格文本) → **上传**。补充资料能显著提升解析置信度。
2. **解析为IR**:所选大模型综合原图+补充资料,视觉解析成结构化设计意图(状态栏显示平均置信度)。
3. **校验修正**(自校验第二遍):所选大模型对照原图逐条核对尺寸/特征、补漏并重估置信度
   (状态栏显示 置信度 前→后 的变化)。
4. **拆解推荐**:补全工艺/复用/DFM 建议。
5. **生成几何**:CAD 内核生成 STEP/STL,点击零件查看 3D、质量属性与校验告警。
6. **生成2D工程图**:CAD 内核(OCCT HLR)投影 主视/俯视/侧视/等轴测 SVG + 下料 DXF,
   点击零件即可查看;**导出 BOM**:一键导出物料清单 CSV(Excel 友好)。

**双入口** —— 除"图→IR",也支持 **3D 模型导入**:用「导入 3D 并解析」上传 STEP/STP,
OCCT 反解出零件/包围盒/体积/孔特征,自动生成结构树 + 3D + 2D + BOM(几何为客户原始实体)。

**工艺拆解(零件 → 加工工序)** —— 在零件详情点「🔧 工艺拆解 →」跳到工艺页
(`process.html?project=&part=`)。延续平台思路:大模型只产出**结构化工艺路线**
(ProcessPlan: 工序号/类型/设备/工装/刀具/参数/质量/工时/依赖),平台做确定性归一与校验
(按工序号排序、合计工时、依赖合法性告警)。支持「生成 / 重新生成 / 编辑改参后保存」,
缺尺寸/公差/材料会进 open_questions 并降低相应工序置信度。接口
`POST/GET/PUT /api/projects/{id}/parts/{part_id}/process`(POST 为异步任务)。

**成本分析(零件 → 成本拆解,联网检索行情)** —— 在零件详情点「💰 成本分析 →」跳到成本页
(`cost.html?project=&part=`)。同样产出**结构化成本明细**(CostAnalysis:分项类别/计算依据/
数量/单价/金额/价格来源/置信度)。当当前提供商开放联网工具时，可检索材料、外购件、加工费率等
公开行情作为依据并把出处写进 price_references；Team/Qwen 当前仅“型号联网核验”支持原生搜索，
普通成本分析会明确按离线知识执行，不伪装成已联网。平台做
确定性的金额/合计**重算与勾稽校验**(数量×单价 与 金额 不符则告警)、分类汇总。**价格依据带
可点击网页链接**,并自动收集 web_search 检索到的来源(标题+链接)成「检索来源」可逐条核查。
支持设定核算批量、生成/重新生成、行内改价后保存。接口
`POST/GET/PUT /api/projects/{id}/parts/{part_id}/cost`(POST 异步,`?quantity=` 指定批量)。

> **附加信息对话框**:工艺拆解页与成本分析页都提供「附加信息」输入(文字说明 + 文件:其它
> 视图图片 / 规格文本 / 已知报价),生成与重新生成时一并提交(multipart),当前模型优先采用 ——
> 让结果更贴合你的实际约束。

**交互式解析工作台** —— 原图上叠加各零件 bbox(低置信度红框),点框/点树联动选中;
右侧详情可**行内改尺寸/材料/数量**,点「保存并重生该零件」即回存 IR 并单件重生几何与 2D
(`PUT /ir` + `POST /parts/{id}/regenerate`)。

**版本与校核审签(可追溯,PRD 6.5)** —— 每次解析/校验/拆解/编辑/恢复都**自动留一个 IR
版本快照**;中栏「版本与校核审签」面板可:
- **任选两版对比**:零件级 diff(增/删/改),精确到「`features[0].thickness` 12 → 20」;
- **校核审签流**:草稿 → 送审 → 通过/驳回(记审签人/意见/时间),状态徽标实时显示;
- **一键恢复**:把任一历史版本恢复为当前 IR(另存为新版本,**不覆盖历史**)。

接口:`GET /versions`、`GET /versions/{v}`、`GET /versions/{a}/diff/{b}`、
`POST /versions/{v}/{submit|approve|reject|restore}`。审签/恢复均写审计轨迹。

> 提升置信度的设计:① 系统提示内置置信度评分标尺,纠正"虚低";② 上传可附文字说明/佐证文件
> 形成多模态上下文;③ 自校验第二遍对照原图核对。三者叠加。

## 离线验证几何内核(无需 API Key)

```powershell
python scripts\smoke_geometry.py
```

会用一个内置示例 IR(底板 + 4 个安装孔)生成 STEP/STL 到 `data/_smoke/`。

## 存储后端(工程化底座)

元数据(项目/IR/几何·图纸结果/审计)走可插拔后端,二进制文件(原图/附件/STEP/STL/SVG/DXF)
始终落在 `data/<项目ID>/` —— 即"结构化元数据进 DB,二进制进 blob"。

- **默认 `file`**:JSON 文件,零依赖,行为与之前完全一致。
- **`sql`**(私有化推荐):`STORAGE_BACKEND=sql`。默认 SQLite(无需外部服务),
  生产把 `DATABASE_URL` 指向 Postgres 即可。表:`projects` / `docs` / `audit_log`。
- 每个关键动作(创建/解析/校验/生成/导出)都写 **审计轨迹**,见 `GET /api/projects/{id}/audit`。

`store.py` 的公开函数签名保持稳定,切换后端不影响上层与现有功能。

## 鉴权与 RBAC(私有化部署)

默认**关闭**(`AUTH_ENABLED=false`),行为与之前完全一致(隐式 `system/admin`),本地开发零改动。
私有化部署置 `AUTH_ENABLED=true` 即开启登录与角色权限(零外部依赖:pbkdf2 加盐散列口令 +
HMAC 签名自包含令牌):

- **角色**:`viewer`(只读)、`engineer`(仅可编辑自己的项目)、`sales_manager`(录入客户信用等级)、`process_manager`(创建需求、
  汇总结果)、`process_director`(审核/发布)、`admin`(全权 + 用户管理)。
- 首次启动自动创建管理员(默认 `admin/admin123`,见 `.env`,**请尽快改密**)。
- 写操作会按项目所有者及角色校验；审核/发布需工艺技术总监或管理员。
- **审签实名**:通过/驳回的「审签人」即当前登录账号(不再是前端自填字符串),与时间/意见一起
  写入版本审签记录与审计轨迹。
- 前端未登录时弹登录页；部分浏览器原生媒体/下载资源需临时以 `?token=` 透传，服务端
  已设置严格 Referrer-Policy，后续可升级为一次性下载票据。

接口:`POST /api/login`、`GET /api/me`、`GET/POST /api/users`(管理员)。开启前务必把
`AUTH_SECRET` 改成随机长串。

临时演示可设置 `AUTH_AUTO_ADMIN=true`：浏览器直接以默认管理员身份进入，跳过登录页；
该开关只适合受控演示环境，结束后应改回 `false` 并重启服务。

## 异步任务(耗时操作不再阻塞)

解析/校验/拆解(模型调用,数十秒)与几何/2D/3D 导入(CAD,数秒至数分钟)都改为
**异步任务**:接口立即返回 `{task_id}`,前端轮询进度,完成后取结果渲染。

- 进程内队列(标准库 `ThreadPoolExecutor`,**零外部中间件**,契合私有化/单机优先);
  CAD 任务经全局锁串行化(OCCT 非线程安全),模型任务可并发。
- 任务状态/进度/结果落盘(可追溯、可跨请求轮询):
  `GET /api/projects/{id}/tasks/{task_id}`(轮询单个)、`GET .../tasks`(列表)。
  状态机:`queued → running → succeeded/failed`。
- 提交后端点:`POST .../parse|verify|decompose|generate|drawings` 与 `POST /projects/3d`
  现在都返回 `{task_id}`(3D 另带 `project_id`)。并发度 `TASK_WORKERS`(默认 4)。
- 架构上把"提交 fn→拿 task_id→轮询"做成与执行器无关,日后要上多机只需把执行器换成
  分布式 broker(Celery/RQ),上层与前端不动。

## 对象存储(二进制可插拔)

元数据已可进 SQL,二进制(原图/附件/STEP/STL/SVG/DXF)也做成**可插拔 blob 后端**:

- **默认 `local`**:落本地磁盘 `DATA_DIR/<project>/...`,零依赖,行为与之前完全一致。
- **`s3` / `minio`**(私有化集群/多节点共享):`BLOB_BACKEND=s3` + `S3_*` 配置,`pip install boto3`。
- CAD 内核只能写本地路径,故 S3 模式下采用「**本地缓存 + 远端对象库**」:几何先写本地工作
  目录、生成后 `sync_dir` 上传;读取/对外服务时本地缺失再回源拉取(materialize)。
- `S3_PREFIX` 可做多租户/多环境的 key 前缀隔离。`store.py` 二进制读写全部走统一 blob 接口
  (`backend/storage/blob_backend.py`),切后端不影响上层与现有功能。

## 设计要点

- **IR 是契约**:`models/ir.py` 用「特征」语义(plate/box/cylinder/hole/hole_pattern/fillet/chamfer)
  描述几何,而非裸坐标 —— 可参数化重建、可改参。
- **数值不脑补**:图上有标注的尺寸必须采用;无标注的估值会进 `open_questions` 并降低 `confidence`。
- **确定性几何**:LLM 不写几何代码;`geometry.py` 把 schema 约束的特征翻译成 CadQuery 调用。
- **可追溯**:原图 → IR(含 provenance/bbox) → 几何 → 校验,逐级落盘形成证据链。

## 后续可扩展(路线图)

- DFM 规则引擎独立化、干涉检查、FEA(CalculiX)静力校核
- FreeCAD TechDraw 自动投影 2D 工程图(DXF/PDF)
- 零件库 RAG:相似件检索做「复用 vs 新增」推荐
- 人在环审校:前端改 IR 参数 → 一键重生几何(参数化威力)
- 版本/分支:多拆解方案对比
