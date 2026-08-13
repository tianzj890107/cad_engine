# Changelog · 2026-08-12

## 1. 用户设置页面登录流程修复

- 用户设置页兼容内网自动管理员模式，没有本地 token 时也能直接读取当前管理员身份。
- 只有接口确认未登录或会话失效时才跳转登录页，避免用户设置页和登录页反复切换。
- 主要文件：`frontend/account.js`、`frontend/account.html`。

## 2. 首页导航顺序与折叠展开对应

- 折叠与展开导航统一为：品牌入口、搜索、新建清单、我的清单、全部清单、分隔线、历史对话。
- 展开导航顶部显示彩色品牌图标和“AI工艺平台”，搜索图标与搜索栏保持对应位置。
- 历史对话在展开状态显示对应的钟表图标；模型设置和用户设置在两种状态下保持相同的上下位置。
- 主要文件：`frontend/home.js`、`frontend/home-layout.css`。

## 3. 首页导航折叠与展开过渡

- 导航展开和收起改为平滑的左右过渡，不再突然出现或消失。
- 收起时折叠图标栏从点击开始保持在左侧，展开面板在其下方退出；到达折叠宽度后不再二次切换或闪动。
- 文字、白色面板和边框作为整体同步移动，避免文字先移动、边框后移动。
- 折叠导航按钮的提示气泡直接显示，不再闪烁；提示改为白底、浅边框、柔和阴影和深色文字。
- 主要文件：`frontend/home.js`、`frontend/home-layout.css`。

## 4. 模型设置改为供应商联动下拉

- LLM 提供商改为 Anthropic、OpenAI、Qwen、DeepSeek 和 Team 的下拉选择。
- 文本模型和视觉模型改为随供应商联动的下拉选择，不再允许自由输入；Team 选项读取当前服务器团队模型池。
- 当前实际服务提供商仍以服务器部署配置为准，页面明确区分可查看的供应商选项与可保存生效的 Team 配置，避免界面选择与实际调用不一致。
- 主要文件：`frontend/home.js`、`frontend/home-layout.css`、`backend/main.py`。

## 5. 联网搜索能力与模型选择真实对齐

- 联网检索模型不再由前端写死，改为读取当前供应商返回的实际联网模型列表。
- Team/Qwen 当前只对“型号联网核验”提供百炼原生搜索能力，普通工艺、成本等分析仍不能使用联网工具；页面会明确显示这一限制。
- 当 Team 没有配置联网搜索模型或 API Key 时，直接显示“没有可用的联网搜索模型”，避免用户误以为普通 Team 模型支持联网。
- 主要文件：`frontend/home.js`、`backend/main.py`。

## 6. AI 工程任务 SOP 与统一结果治理

- 新增图纸解析、图纸校核、通用 CAPP 和行业路由 SOP；AI 异步任务统一留存 READY/PARTIAL/BLOCKED、证据、假设、待澄清项、校验结果、实际模型与 SOP 版本。
- 当前没有企业规则库时使用可审计的通用规则和项目文件证据，不把模型常识伪装成企业标准；电子陶瓷专用提示仅在项目证据明确匹配时加载。
- 主要文件：`agent_knowledge/`、`backend/models/ai.py`、`backend/services/sop.py`、`backend/services/ai_governance.py`、`backend/services/tasks.py`。

## 7. 分阶段图纸解析、字段证据与 CAD 安全门

- 图纸解析增加文件清单、本地文本提取、视图与候选识别、关键尺寸绑定、IR 组装和规则校验阶段记录，并为关键尺寸、数量与材料建立字段级证据台账。
- 初版安全门会因材料缺失、证据冲突或弱证据阻止正式生成；当天后续已在第 11 项纠偏，最终规则以第 11 项为准。
- 主要文件：`backend/services/vision.py`、`backend/models/ir.py`、`backend/main.py`、`backend/storage/store.py`。

## 8. 工艺分类模板与通用规则校验

- 工艺生成先由本地规则将零件分为机加工、钣金、焊接结构或标准外购件，再加载对应模板；没有企业资源数据时只使用通用设备和工具类别。
- 增加特征—工序、材料—工艺兼容性、模板允许工序和最终检验规则，生成后由程序检查路线完整性与不适用工序。
- 主要文件：`agent_knowledge/process/`、`agent_knowledge/rules/process_rules.json`、`backend/services/process.py`、`backend/models/process.py`。

## 9. AI 结果确认边界与行业偏向修正

- 型号联网核验改为先保存候选，用户确认单个型号后才写入零件清单/BOM；旧结果也不再自动补写。
- 图纸 AI 校核改为字段级补丁，强证据修改可安全应用，弱证据修改在 2.1 的“AI 校核待确认”区域逐项确认或保留原值，不再重写整份 IR。
- 材料、制造、清洗、组装、成本、定价和谈判提示按通用/电子陶瓷行业动态路由；审批级别改由公开的本地保守矩阵确定。
- 主要文件：`backend/services/model_lookup.py`、`backend/services/vision.py`、`backend/services/material.py`、`backend/services/manufacturing.py`、`backend/services/cleaning.py`、`backend/services/assembly.py`、`backend/services/approval.py`、`frontend/app.js`、`frontend/index.html`。

## 10. AI SOP 稳定性与企业数据边界加固

- 修复生产镜像遗漏 AI SOP 文件的问题，完整构建和内网挂载两种部署方式都会加载同一套规则。
- AI 校核补丁改为事务式验证：不合法的新值不再污染当前 IR；人工补齐材料或尺寸后会重新计算安全门，不再被上一轮陈旧错误阻挡。
- 收紧电子陶瓷行业识别和外购件分类，通用“烧结/金属化”或“阀体”等词不再单独触发专用流程；型号候选补充关联零件提示并去除重复候选。
- 没有企业规则库时不再自动注入示例设备、供应商、产能与价格；旧数据文件保留不删除，但示例行不再参与真实供应商匹配和产能评估。
- 成本分析和型号核验文案改为读取当前能力边界，不再让 Team/Qwen 普通分析误显示为已联网。
- 主要文件：`Dockerfile`、`.dockerignore`、`backend/services/vision.py`、`backend/services/sop.py`、`backend/services/process.py`、`backend/services/model_lookup.py`、`backend/storage/store.py`、`frontend/app.js`、`frontend/cost.js`、`README.md`、`tests/test_ai_sop.py`、`tests/test_backend_hardening.py`。

## 11. CAD/2D 生成门禁纠偏

- CAD、2D 工程图和单零件重新生成只由确定性几何预检拦截：缺少基体、必要尺寸无值、尺寸非法或数量非法仍会阻断。
- 材料缺失、字段证据较弱或证据冲突不再阻断几何生成，改为少量汇总提醒；解析提示也不再把每个估算字段逐条转成人工确认事项。
- 对原失败项目完成真实回归：6 个零件的 CAD 和 2D 工程图全部成功，STEP、STL、SVG、DXF 均可下载，过程未调用 AI。
- 主要文件：`backend/services/vision.py`、`backend/main.py`、`tests/test_ai_sop.py`。
