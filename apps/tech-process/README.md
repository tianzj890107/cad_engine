# 技术工艺管理 / 商机报价管理(前端子应用)

复刻「智能销售管理(ISMP) → 销售项目管理」的两个页面。**一套页面承载两个业务**,
用 URL 参数 `?biz=` 切换:

- `?biz=tech`(默认)= **技术工艺管理**,列表按钮「新增技术工艺」,任务进度 9 段:
  开始 → 图纸解析与设计工艺转化 → 材料定性与供应链拆解 → 制造工艺路径规划和BOM编制 →
  清洗与洁净度管控方案制定 → 组装与检测方案制定 → 产线匹配与产能评估 → 技术工艺总结 → 结束。
- `?biz=quote` = **商机报价管理**,列表按钮「新增报价单」,任务进度 7 段:
  开始 → 成本测算 → 定价方案制定 → 商务及谈判策略 → 价格协商及谈判 → 报价审批与决策 → 结束。

详情页:顶部步骤 tab + 联动的任务进度条 + 步骤内容,点击步骤同步跳转。
技术栈与 eimos 主站一致(React 17 + antd 4),无需构建。

## 运行(已合并到 process_drawing 单一服务)

本应用不再单独起服务,而是由 **process_drawing(FastAPI)统一托管**,与 `/api/*` 同源:

```powershell
cd C:\shuopan\图纸解析与生成\process_drawing
uvicorn backend.main:app --reload --port 8000   # 详见 process_drawing/README.md
```

启动后访问:

- 技术工艺管理:`http://localhost:8000/apps/tech-process/?biz=tech`
- 商机报价管理:`http://localhost:8000/apps/tech-process/?biz=quote`
- 图纸拆解平台(被下面那步嵌入):`http://localhost:8000/`

> 新增其它前端应用:在 `process_drawing/apps/<name>/` 放 `index.html`,即自动由
> `http://localhost:8000/apps/<name>/` 提供,并与所有应用共用同一套 `/api/*` 接口。

### 「图纸解析与设计工艺转化」步骤 = 嵌入图纸拆解平台

技术工艺详情页的该步骤,直接以 iframe 嵌入图纸拆解平台。合并后两者**同源**,
默认嵌入相对地址 `/`(同一服务的根),无需跨端口。需要指向别处时(二选一):

- `localStorage.setItem('techprocess:drawingUrl','http://<host>:<port>/')`,或
- 访问 `…/apps/tech-process/?biz=tech&drawingUrl=http://<host>:<port>/`

> 依赖(React/antd/moment/icons)走 unpkg CDN,首次打开需联网。

## 接入 eimos 主页(已配置)

eimos 主页已把这两个应用以 iframe 形式挂到「智能销售管理(ISMP) → 商机管理」下,
与「商机信息」平级:

- 技术工艺管理:路由 `/pro/eimos/techprocess`
- 商机报价管理:路由 `/pro/eimos/quotemgmt`

iframe 默认地址(见 `eimos/src/pages/TechProcess/index.tsx`、`QuoteMgmt/index.tsx`):

- `http://127.0.0.1:8000/apps/tech-process/?biz=tech`
- `http://127.0.0.1:8000/apps/tech-process/?biz=quote`

换地址时在 eimos 控制台:
`localStorage.setItem('eimos:techProcessUrl','http://<host>:<port>/apps/tech-process/?biz=tech')` 再刷新。
