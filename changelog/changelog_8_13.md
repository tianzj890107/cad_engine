# Changelog · 2026-08-13

本文件记录 2026-08-13 当天完成的修改、验证与部署情况。

## 1. 工艺明细空路线自动补齐

- 修复 AI 只返回毛坯和工艺概览、`steps` 为空时页面显示 0 工序的问题；后端按零件分类和几何特征自动补齐通用可执行工序骨架。
- 材料和公差缺失只限制精确切削参数，不再阻止下料、主体加工/成形、特征加工、去毛刺及最终检验等工序输出。
- 已有 0 工序记录在读取时自动修复，不重复调用 AI；同时修复工序类型枚举导致“已有最终检验仍被误报缺失”的校验问题。
- 主要文件：`backend/services/process.py`、`backend/main.py`、`tests/test_ai_sop.py`。

## 2. 需求接收与完整工艺评估链路贯通

- 将需求创建、需求确认、图纸解析、图纸校核、零件拆解、几何生成、2D 工程图、工艺路线、成本估算、报价、议价和审批串成可追踪的项目流程；每个阶段均保留异步任务状态和结果。
- 需求创建支持多份图纸/模型资料、文件大小与数量限制、文本提取以及需求单 PDF 预览/下载；需求字段推荐按行业和必填字段生成，确认后才进入后续工程阶段。
- 增加项目级 AI 问答，回答基于当前项目的需求、IR、工艺、成本和汇总结果，并保存对话记录，避免脱离项目上下文的泛化回答。
- 主要文件：`backend/main.py`、`backend/services/requirement_extract.py`、`backend/services/requirement_pdf.py`、`backend/services/tasks.py`、`backend/services/summary.py`、`frontend/requirement-create.js`、`frontend/project-chat.js`。

## 3. 图纸解析、证据校核与几何安全门

- 图纸解析拆分为输入清单、文本提取、视图/候选识别、关键尺寸绑定、IR 组装和规则校验等阶段；关键尺寸、数量、材料等字段带有来源、证据强度和冲突信息。
- AI 校核结果改为字段级补丁：强证据可应用，弱证据进入待确认区；人工确认只修改对应字段，并记录操作者和变更来源，不再用整份模型结果覆盖工程数据。
- 对 IR、输入文件和上下游结果增加摘要/版本校验；需求、材料、制造和其他前置数据变化后，旧的审批、几何或工程图结果会被标记为过期，避免继续使用陈旧结果。
- CAD、2D 工程图和单零件重生成前执行确定性几何预检，缺少基体、必要尺寸无值、尺寸非法或数量非法时阻断生成；材料缺失或弱证据只作为提醒，不再误阻断可生成的几何。
- 主要文件：`backend/services/vision.py`、`backend/models/ir.py`、`backend/storage/store.py`、`backend/main.py`、`frontend/inline-analysis.js`、`frontend/index.html`。

## 4. 工艺路线、工程结果与行业规则

- 工艺生成按机加工、钣金、焊接结构和标准外购件分类加载模板；增加特征—工序、材料—工艺兼容性、模板允许工序和最终检验规则校验。
- 没有企业规则库时仅使用可审计的通用设备、工具和规则，不再把模型常识或示例供应商、产能和价格伪装成企业数据；电子陶瓷规则只在项目证据匹配时启用。
- 工艺、材料、制造、清洗、组装、生产、成本和定价结果统一接入 SOP、证据、假设、待澄清项、校验结果、实际模型和版本留痕，支持 READY/PARTIAL/BLOCKED 状态。
- 主要文件：`agent_knowledge/`、`backend/models/ai.py`、`backend/models/process.py`、`backend/services/sop.py`、`backend/services/ai_governance.py`、`backend/services/process.py`、`backend/services/material.py`、`backend/services/manufacturing.py`。

## 5. 成本、报价、议价与审批状态治理

- 成本估算、定价、议价和审批结果增加业务内容比较与依赖摘要；只有业务内容真实变化时才更新版本、重置下游审批或重新计算，避免重复保存产生假变更。
- 报价审批按本地保守矩阵计算所需角色和审批节点，审批前检查 IR、成本和报价依赖是否仍为当前版本；结果中保留审签人、时间和审计信息。
- 前端补齐成本、报价、报告审核/发布和汇总结果页面的状态展示、过期提示、下载与操作反馈。
- 主要文件：`backend/models/cost.py`、`backend/models/workflow.py`、`backend/services/costest.py`、`backend/services/pricing.py`、`backend/services/pricenego.py`、`backend/services/approval.py`、`frontend/cost.js`、`frontend/report.js`、`frontend/summary-result.js`。

## 6. 2.1 工作台与演示页面同步业务状态

- 2.1 工作台内嵌工艺拆解和成本分析，保留零件摘要、工程图、专家模式和零件选择状态；解析、校核、待确认项、版本审签和 AI 问答可在同一项目上下文中操作。
- 新增并整理需求、解析、工艺评估、报告审核和报告发布等 HTML 演示页面，统一导航、按钮、图标、加载态、折叠区和结果页交互。
- 增加需求推荐、PDF、内嵌分析、项目问答和解析加载等前端样式与脚本，并同步处理内网自动管理员模式下的账户页和流程页登录状态。
- 主要文件：`frontend/index.html`、`frontend/app.js`、`frontend/home.js`、`frontend/workbench.css`、`frontend/requirement-*.js`、`frontend/report-*.js`、`AI工艺页面集/`。

## 7. 模型能力、配置与数据边界

- Qwen 客户端支持视觉/文本/联网模型池、重试、结构化 JSON 修复、输出预算和实际模型留痕；视觉模型关闭思考以兼容部分 OpenAI 兼容网关只返回 reasoning 内容的问题。
- 模型设置页面改为供应商联动下拉，联网搜索模型读取服务端能力；Team/Qwen 的普通工艺和成本分析不再误显示为已联网。
- 完善 `.env.example`、配置项和 Docker 镜像规则，确保 AI SOP、行业知识和流程规则在完整构建与内网源码挂载两种部署方式中都能加载。
- 主要文件：`backend/config.py`、`backend/services/qwen_client.py`、`backend/services/openai_client.py`、`frontend/home.js`、`.env.example`、`Dockerfile`、`.dockerignore`、`requirements.txt`。

## 8. 内网发布链路与端口约定

- 内网发布固定使用 `20260722` 分支：本地提交推送 GitHub 后，由 SSH 通知服务器快进拉取；依赖或 Dockerfile 变化时重建镜像，否则重启现有 `cad-engine` 容器。
- 服务器对外端口固定为 `8002`，容器内部监听 `8000`；宿主机 `8000` 保留给其他服务。内网模式保留服务器 `data/` 和 `.env`，源码目录以只读方式挂载。
- 发布脚本新增容器状态和 `/api/health` 校验；健康接口返回 `status=ok` 且 CadQuery、模型池等运行信息正常后，才报告部署完成。
- 部署文档统一命名为 `DEPLOYMENT.md`，并同步 README、周 changelog 和发布脚本说明。
- 主要文件：`DEPLOYMENT.md`、`docker-compose.intranet.yml`、`docker-compose.yml`、`scripts/release_intranet.sh`、`scripts/deploy_intranet_git.sh`、`scripts/sync_intranet_source.sh`、`README.md`。

## 9. 测试、离线验证与数据维护

- 新增 AI SOP、工作流完整性、需求 AI 检查模型、需求确认页脚本和后端加固测试；更新 Qwen 离线测试和几何烟测，覆盖模型留痕、状态流转、过期结果和关键安全门。
- 清理不应进入源码发布的 Python 缓存文件，并保留内网运行数据与独立资料文件不被日常源码同步覆盖。
- 当前发布版本：`bfb85f5`（分支 `20260722`）；线上 `172.16.10.34:8002` 的 `/api/health` 已验证返回 `status: ok`。
- 主要文件：`tests/test_ai_sop.py`、`tests/test_workflow_integrity.py`、`tests/test_requirement_ai_check_model.py`、`tests/test_requirement_confirm_page.js`、`tests/test_backend_hardening.py`、`scripts/smoke_geometry.py`。

## 10. LLM 异步任务显示真实处理阶段

- 所有主要 LLM 异步任务不再统一显示“处理中”，而是显示准备输入、调用模型、模型返回后校验、保存结果等实际阶段；覆盖图纸解析/校核、型号核验、零件拆解、工艺、成本、材料、制造、洁净、组装、产能、汇总、定价、议价和审批。
- 需求文档 AI 提取、成本页、工艺页和 2.1 内嵌分析页会直接展示任务进度文案，用户可以判断当前是在排队、等待模型，还是已经进入结果校验。
- 任务队列增加当前任务上下文和阶段上报能力；任务失败、重启中断和完成状态仍由统一任务状态管理，前端继续使用同一轮询接口。
- 主要文件：`backend/services/tasks.py`、`backend/main.py`、`frontend/requirement-create.js`、`frontend/cost.js`、`frontend/process.js`、`frontend/inline-analysis.js`。

## 11. 进度进一步对齐具体 SOP

- 异步任务返回并持久化 `sop_name`、`sop_step` 和 `sop_total` 供后台追踪；前端只展示简洁的自然语言阶段文案，不显示 SOP 名称和“2/3”编号前缀。
- 进度文案仍采用真实执行边界：准备输入、调用模型、模型返回后的校验/保存；不会伪造模型内部 token 或供应商不可见的中间进度。
- 主要文件：`backend/services/tasks.py`、`frontend/app.js`、`frontend/cost.js`、`frontend/process.js`、`frontend/inline-analysis.js`、`frontend/requirement-create.js`。
