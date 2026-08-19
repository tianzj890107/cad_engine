# Changelog · 2026-08-19

本文件记录 2026-08-19 当天完成的最终业务变动、验证与部署情况。

> 今日已启用持续记录：后续每完成一组修改，按最终业务结果及时更新本文件；不记录中间尝试或文件修改流水账。

## 1. 修复 1.2 确认需求页样式丢失

- 现象：1.2 确认需求页打开后 HTML 内容虽已注入，但 PDF 预览区、需求信息、确认意见、弹窗等全部失去卡片化布局，页面看起来“未正式加载”。
- 根因：8-17 调整“确认页不再展示额外完整性检查”时，误把整份 `requirement-confirm.css` 替换为单条隐藏 AI 检查区的规则，1.2 页全部专属样式被一并删除。
- 修复结果：恢复与 1.3 审核页一致的卡片、PDF 预览、意见填写与弹窗样式；页面样式缓存版本号升级为 `reqconfirm5`。
- 验证：本地服务 `http://127.0.0.1:8000/requirement-confirm.html` 正常返回，`requirement-confirm.css` 完整输出全部样式；已提交并推送 `20260722`、`agentic` 两个分支，内网服务按 `20260722` 发布并完成健康检查。
- 主要涉及文件：`frontend/requirement-confirm.css`、`frontend/requirement-confirm.html`。

## 2. 补齐 1.2 确认页设计稿的 V1/V2 版本切换

- 工艺集设计稿 `AI工艺_确认工艺评估需求_页面2.html` 原有版本切换样式与 `selectVersion` 脚本，但正文缺少实际按钮，弹窗标题固定为 `- V2`，属于残缺元素。
- 已在 PDF 表单头部补齐 V1/V2 版本切换按钮，切换时同步更新弹窗标题（`工艺评估需求单 - V1/V2`）。
- 验证：页面结构校验无未闭合标签；JS 回归测试 `test_requirement_confirm_page.js` 与需求流程相关测试全部通过（Python 69 项 + JS 1 项）；已提交并推送 `20260722`、`agentic` 两个分支，仅涉及设计稿与 changelog，未改动其他页面。
- 部署：已随 `20260722` 于 2026-08-19 发布内网，服务器快进至 `0f9d38a`，容器 `cad-engine` 正常，`/api/health` 返回 HTTP 200（设计稿目录不参与静态服务，线上页面行为无变化）。
- 主要涉及文件：`AI工艺页面集/AI工艺_确认工艺评估需求_页面2.html`。

## 3. 恢复 1.2 确认页“AI 检查结果”确认表

- 确认页重新展示“AI 检查结果”区：默认加载本地确定性 precheck（`/requirement/precheck`，不调用模型），生成 Section A–G 及 3.1~3.4 的确认表；若已保存过 Qwen 检查结果则优先展示模型结果；“AI 检查”按钮保留，可按需调用模型并回写留痕。
- 页面 JS/CSS 缓存版本升级为 `reqconfirm14` / `reqconfirm6`。
- 主要涉及文件：`frontend/requirement-confirm-page.js`、`frontend/requirement-confirm.css`、`frontend/requirement-confirm.html`。

## 4. Qwen 结构化输出截断时，修复调用自动放大输出上限

- `qwen_client` 的截断分支（`finish_reason=length/max_tokens`）此前修复时沿用原 `max_tokens`，再次截断会直接失败；现与“未通过本地字段校验”分支一致，在修复调用前把输出上限放大到 24000（视觉）/ 12000（文本），避免因预估不足导致输出失败。
- 主要涉及文件：`backend/services/qwen_client.py`。

## 5. 1.2 确认页“提交意见”图标对齐设计稿

- 确认页“提交意见”标题此前使用 `▢` 方块字符，与工艺集设计稿不一致；已替换为设计稿同款聊天气泡 SVG 图标（紫色，与标题其他图标样式一致）。
- 页面 JS 缓存版本升级为 `reqconfirm15`。
- 验证：JS 回归测试 `test_requirement_confirm_page.js` 通过。
- 主要涉及文件：`frontend/requirement-confirm-page.js`、`frontend/requirement-confirm.html`。
