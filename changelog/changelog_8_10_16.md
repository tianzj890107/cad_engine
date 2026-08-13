# cad_engine 变更记录（2026-08-10 至 2026-08-16）

> 本文档记录本项目本周完成的最终用户可见功能变化。

## 维护规则

- 每周使用一个 changelog 文件，按日期分节追加最终用户可见变化。
- 每次功能修改或一组相关调整合并为一条记录，不记录中间尝试、重启、部署或刷新操作。
- 每条记录说明用户可见变化、涉及页面和主要文件。
- 本周结束后，将本文件作为周记录归档；下周新建对应日期范围的周记录。

## 2026-08-10

### 1. 需求字段推荐与确认页展示完善

- 1.1 的 AI 推荐覆盖所有必填字段；非必填字段保持为空，不再生成低价值的“待人工确认”推荐。
- 下拉枚举推荐严格使用系统已有选项，不再由模型创造新枚举值；推荐值、置信度和低置信度提示在 1.2 确认页同步展示。
- 全新/迭代状态增加基于历史数据的蓝色来源提示，标明是否找到既有记录。
- 主要文件：`frontend/requirement-create.js`、`frontend/requirement-ai-recommendation.css`、`frontend/requirement-confirm-page.js`、`backend/services/requirement_extract.py`。

### 2. 需求确认、AI 检查与模型留痕

- 1.2 增加 AI 推荐区域的默认折叠交互，修复检查结果区域加载失败和推荐默认值显示问题。
- AI 检查结果读取实际使用的模型，不再写死模型名称；检查按钮统一显示“AI检查中”。
- 解析和 AI 检查过程保留处理中状态提示，已有人工填写内容不会被推荐值覆盖。
- 主要文件：`frontend/requirement-confirm-page.js`、`frontend/requirement-confirm.html`、`backend/main.py`、`backend/services/qwen_client.py`。

### 3. 2.1 图纸解析工作台与分析区域调整

- 解析资料、设计意图、待澄清问题、版本审签等区域支持统一折叠；解析完成后按阶段自动折叠，保留手动展开能力。
- 左右主卡片、底部按钮、工程图区域和专家模式的高度与滚动关系重新对齐，减少底部空白并保证卡片内部可滚动。
- 工艺拆解和成本分析改为直接内嵌在 2.1 页面中，保留零件摘要并覆盖下方工程图与专家模式区域。
- 主要文件：`frontend/index.html`、`frontend/app.js`、`frontend/workbench.css`、`frontend/project-chat.js`、`frontend/project-chat.css`。

### 4. 2.1 按钮、图标与文件操作视觉统一

- 返回、上一步、首页、解析报告、下载和零件参数操作统一为蓝色静态按钮风格，降低按钮高度并取消不必要的上跳效果。
- 工艺拆解和成本分析使用统一的扳手、金额图标；下载 STEP、STL、DXF 按钮在工程图区域同一行展示。
- 专家模式零件参数改为双列布局，保存零件参数和重新生成零件拆分为两个按钮。
- 主要文件：`frontend/index.html`、`frontend/workbench.css`、`frontend/app.js`。

### 5. 首页导航入口初始统一

- 首页左侧导航的八个 SVG 图标统一尺寸、颜色和展开后的文字入口，模型设置与用户设置固定在底部连续排列。
- 历史会话列表隐藏流程状态文字，保留项目名称和透明背景图标。
- 主要文件：`frontend/home.js`、`frontend/home-layout.css`。

## 2026-08-11

### 1. 2.1 内嵌分析与零件选择状态

- 工艺拆解和成本分析直接内嵌到 2.1 页面，保留包围盒、体积、质量等零件摘要，并覆盖工程图和专家模式区域。
- 内嵌入口和展开面板使用统一图标尺寸；零件清单选中项持续显示与悬浮相同的蓝色边框。
- 主要文件：`frontend/index.html`、`frontend/app.js`、`frontend/workbench.css`。

### 2. 内网运行目录与历史数据补齐

- 内网 `cad-engine` 运行目录切换到 `/home/zhangzhen/cad_engine`，并重新创建服务器 Linux 虚拟环境。
- 仅补齐旧运行目录中存在而新目录缺失的数据文件，保留新目录现有数据和当前代码状态。
- 主要文件：`scripts/sync_intranet_source.sh`、`DEPLOYMENT.md`、`README.md`。

## 2026-08-12

### 1. 用户设置页面登录流程修复

- 用户设置页兼容内网自动管理员模式，没有本地 token 时也能直接读取当前管理员身份。
- 只有接口确认未登录或会话失效时才跳转登录页，避免用户设置页和登录页反复切换。
- 主要文件：`frontend/account.js`、`frontend/account.html`。

### 2. 首页导航顺序与折叠展开对应

- 折叠与展开导航统一为：品牌入口、搜索、新建清单、我的清单、全部清单、分隔线、历史对话。
- 展开导航顶部显示彩色品牌图标和“AI工艺平台”，搜索图标与搜索栏保持对应位置。
- 历史对话在展开状态显示对应的钟表图标；模型设置和用户设置在两种状态下保持相同的上下位置。
- 主要文件：`frontend/home.js`、`frontend/home-layout.css`。

### 3. 首页导航折叠与展开过渡

- 导航展开和收起改为平滑的左右过渡，不再突然出现或消失。
- 收起时折叠图标栏从点击开始保持在左侧，展开面板在其下方退出；到达折叠宽度后不再二次切换或闪动。
- 文字、白色面板和边框作为整体同步移动，避免文字先移动、边框后移动。
- 折叠导航按钮的提示气泡直接显示，不再闪烁；提示改为白底、浅边框、柔和阴影和深色文字。
- 主要文件：`frontend/home.js`、`frontend/home-layout.css`。

### 4. 模型设置改为供应商联动下拉

- LLM 提供商改为 Anthropic、OpenAI、Qwen、DeepSeek 和 Team 的下拉选择。
- 文本模型和视觉模型改为随供应商联动的下拉选择，不再允许自由输入；Team 选项读取当前服务器团队模型池。
- 当前实际服务提供商仍以服务器部署配置为准，页面明确区分可查看的供应商选项与可保存生效的 Team 配置，避免界面选择与实际调用不一致。
- 主要文件：`frontend/home.js`、`frontend/home-layout.css`、`backend/main.py`。

### 5. 联网搜索能力与模型选择真实对齐

- 联网检索模型不再由前端写死，改为读取当前供应商返回的实际联网模型列表。
- Team/Qwen 当前只对“型号联网核验”提供百炼原生搜索能力，普通工艺、成本等分析仍不能使用联网工具；页面会明确显示这一限制。
- 当 Team 没有配置联网搜索模型或 API Key 时，直接显示“没有可用的联网搜索模型”，避免用户误以为普通 Team 模型支持联网。
- 主要文件：`frontend/home.js`、`backend/main.py`。

### 6. AI 工程任务 SOP 与统一结果治理

- 新增图纸解析、图纸校核、通用 CAPP 和行业路由 SOP；AI 异步任务统一留存 READY/PARTIAL/BLOCKED、证据、假设、待澄清项、校验结果、实际模型与 SOP 版本。
- 当前没有企业规则库时使用可审计的通用规则和项目文件证据，不把模型常识伪装成企业标准；电子陶瓷专用提示仅在项目证据明确匹配时加载。
- 主要文件：`agent_knowledge/`、`backend/models/ai.py`、`backend/services/sop.py`、`backend/services/ai_governance.py`、`backend/services/tasks.py`。

### 7. 分阶段图纸解析、字段证据与 CAD 安全门

- 图纸解析增加文件清单、本地文本提取、视图与候选识别、关键尺寸绑定、IR 组装和规则校验阶段记录，并为关键尺寸、数量与材料建立字段级证据台账。
- 新解析结果在生成 CAD、2D 工程图和单零件重生前检查关键字段；材料缺失、证据冲突或只有弱证据时阻止正式生成，人工保存只能确认真实存在的字段值。
- 主要文件：`backend/services/vision.py`、`backend/models/ir.py`、`backend/main.py`、`backend/storage/store.py`。

### 8. 工艺分类模板与通用规则校验

- 工艺生成先由本地规则将零件分为机加工、钣金、焊接结构或标准外购件，再加载对应模板；没有企业资源数据时只使用通用设备和工具类别。
- 增加特征—工序、材料—工艺兼容性、模板允许工序和最终检验规则，生成后由程序检查路线完整性与不适用工序。
- 主要文件：`agent_knowledge/process/`、`agent_knowledge/rules/process_rules.json`、`backend/services/process.py`、`backend/models/process.py`。

### 9. AI 结果确认边界与行业偏向修正

- 型号联网核验改为先保存候选，用户确认单个型号后才写入零件清单/BOM；旧结果也不再自动补写。
- 图纸 AI 校核改为字段级补丁，强证据修改可安全应用，弱证据修改在 2.1 的“AI 校核待确认”区域逐项确认或保留原值，不再重写整份 IR。
- 材料、制造、清洗、组装、成本、定价和谈判提示按通用/电子陶瓷行业动态路由；审批级别改由公开的本地保守矩阵确定。
- 主要文件：`backend/services/model_lookup.py`、`backend/services/vision.py`、`backend/services/material.py`、`backend/services/manufacturing.py`、`backend/services/cleaning.py`、`backend/services/assembly.py`、`backend/services/approval.py`、`frontend/app.js`、`frontend/index.html`。

### 10. AI SOP 稳定性与企业数据边界加固

- 修复生产镜像遗漏 AI SOP 文件的问题，完整构建和内网挂载两种部署方式都会加载同一套规则。
- AI 校核补丁改为事务式验证：不合法的新值不再污染当前 IR；人工补齐材料或尺寸后会重新计算安全门，不再被上一轮陈旧错误阻挡。
- 收紧电子陶瓷行业识别和外购件分类，通用“烧结/金属化”或“阀体”等词不再单独触发专用流程；型号候选补充关联零件提示并去除重复候选。
- 没有企业规则库时不再自动注入示例设备、供应商、产能与价格；旧数据文件保留不删除，但示例行不再参与真实供应商匹配和产能评估。
- 成本分析和型号核验文案改为读取当前能力边界，不再让 Team/Qwen 普通分析误显示为已联网。
- 主要文件：`Dockerfile`、`.dockerignore`、`backend/services/vision.py`、`backend/services/sop.py`、`backend/services/process.py`、`backend/services/model_lookup.py`、`backend/storage/store.py`、`frontend/app.js`、`frontend/cost.js`、`README.md`、`tests/test_ai_sop.py`、`tests/test_backend_hardening.py`。

### 11. CAD/2D 生成门禁纠偏

- CAD、2D 工程图和单零件重新生成只由确定性几何预检拦截：缺少基体、必要尺寸无值、尺寸非法或数量非法仍会阻断。
- 材料缺失、字段证据较弱或证据冲突不再阻断几何生成，改为少量汇总提醒；解析提示也不再把每个估算字段逐条转成人工确认事项。
- 主要文件：`backend/services/vision.py`、`backend/main.py`、`tests/test_ai_sop.py`。
