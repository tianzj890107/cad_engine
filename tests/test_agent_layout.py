"""2.1 图纸解析页新布局的静态回归测试。

这些断言守的是这次改版的几条硬约束：
  - 右下角浮窗 Agent 已从所有页面移除；
  - 页面结构是「对话设置工具栏 + Agent 对话框 + 3D 工作区」三段；
  - 设计意图与开始解析按钮就在对话内容里；
  - 四组补充能力收进 ＋ 菜单，零件清单与待澄清问题以按钮形式留在结果里；
  - 搬动 DOM 没有弄丢 app.js 依赖的任何 id —— 这是本次改版最大的回归风险。
"""
from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
INDEX = (FRONTEND / "index.html").read_text(encoding="utf-8")
AGENT_JS = (FRONTEND / "agent-chat.js").read_text(encoding="utf-8")
AGENT_CSS = (FRONTEND / "agent-chat.css").read_text(encoding="utf-8")
HOME_LINK = (FRONTEND / "home-link.js").read_text(encoding="utf-8")
APP_JS = (FRONTEND / "app.js").read_text(encoding="utf-8")


def ids(html: str) -> set[str]:
    return set(re.findall(r'id="([^"]+)"', html))


def used_ids(js: str) -> set[str]:
    return (set(re.findall(r'\$\("([A-Za-z0-9_]+)"\)', js))
            | set(re.findall(r'getElementById\("([A-Za-z0-9_]+)"\)', js)))


class FloatingWidgetRemovalTests(unittest.TestCase):
    def test_floating_chat_files_are_gone(self):
        self.assertFalse((FRONTEND / "project-chat.js").exists())
        self.assertFalse((FRONTEND / "project-chat.css").exists())

    def test_no_page_loads_the_floating_widget_anymore(self):
        for directory in (FRONTEND, ROOT / "apps"):
            if not directory.is_dir():
                continue
            for path in list(directory.rglob("*.js")) + list(directory.rglob("*.html")):
                with self.subTest(file=path.relative_to(ROOT)):
                    self.assertNotIn("project-chat", path.read_text(encoding="utf-8"))

    def test_home_link_no_longer_injects_a_launcher(self):
        self.assertNotIn("project-chat", HOME_LINK)
        self.assertNotIn("chat-launcher", HOME_LINK)
        # 返回首页的按钮注入逻辑仍应保留。
        self.assertIn("projectId", HOME_LINK)


class LayoutStructureTests(unittest.TestCase):
    def test_shell_and_two_floating_docks(self):
        self.assertIn('class="oc-app"', INDEX)
        self.assertIn('id="ocAgentDock"', INDEX)         # 左悬浮窗：Agent 设置
        self.assertIn('id="ocFilesDock"', INDEX)         # 右悬浮窗：任务文件
        self.assertIn('class="oc-work"', INDEX)
        self.assertIn('class="oc-agent-pane"', INDEX)    # 工作页左：Agent 对话框
        self.assertIn('class="center-panel"', INDEX)     # 工作页右：基本不变

    def test_the_full_height_sidebar_is_gone(self):
        """整条左侧栏收成悬浮小窗，页面主体全部让给工作区。"""
        self.assertNotIn('class="oc-side"', INDEX)
        self.assertNotIn("oc-sbrand", INDEX)
        self.assertNotIn("oc-seclabel", INDEX)

    def test_agent_dock_carries_the_settings(self):
        dock = INDEX.split('id="ocAgentDock"', 1)[1].split("</aside>", 1)[0]
        for marker in ("ocModelPill", "ocNewChat", "ocSideCwd", "ocSideProfile"):
            with self.subTest(marker=marker):
                self.assertIn(marker, dock)

    def test_composer_has_only_plus_and_send(self):
        composer = INDEX.split('class="oc-composer"', 1)[1].split("</section>", 1)[0]
        self.assertIn("ocPlus", composer)
        self.assertIn("ocInput", composer)
        self.assertIn("ocSend", composer)

    def test_capability_viewer_is_removed_everywhere(self):
        """「查看 Agent 能力」整体下线：页面、弹层、样式都不该再有。"""
        for marker in ("ocCapSide", "ocCapTop", "ocCapComposer", "查看 Agent 能力"):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, INDEX)
        self.assertNotIn("capabilityMenu", AGENT_JS)

    def test_chat_has_no_top_toolbar(self):
        """模型与工具选择都搬进左侧小窗，对话框顶部不再有工具栏。"""
        self.assertNotIn("oc-topbar", INDEX)
        self.assertNotIn("oc-topbar", AGENT_CSS)
        thread_side = INDEX.split('class="oc-agent-pane"', 1)[1].split('id="ocThread"', 1)[0]
        self.assertNotIn("ocModelPill", thread_side)

    def test_no_open_claude_jargon_is_visible_on_the_page(self):
        for marker in ("open-claude", "open_claude", "profile:"):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, INDEX)

    def test_page_body_leaves_room_for_the_docks(self):
        """小窗是 fixed 的，工作区不留出边距就会被压在底下。"""
        block = AGENT_CSS.split(".oc-shell .page-container", 1)[1].split("}", 1)[0]
        self.assertIn("padding-left", block)
        self.assertIn("padding-right", block)

    def test_design_intent_and_parse_button_live_inside_the_thread(self):
        thread = INDEX.split('id="ocTinner"', 1)[1].split('class="oc-composer"', 1)[0]
        self.assertIn('id="intent"', thread)
        self.assertIn('id="btnParse"', thread)
        # 开始解析必须排在设计意图之后。
        self.assertLess(thread.index('id="intent"'), thread.index('id="btnParse"'))
        self.assertIn('id="ocResultActions"', thread)

    def test_result_chips_sit_at_the_bottom_not_in_the_intent_card(self):
        """结果按钮要常驻对话底部；留在第一条消息里，聊几轮就翻不到了。"""
        thread = INDEX.split('id="ocTinner"', 1)[1].split("</div>\n            </div>", 1)[0]
        # 排在设计意图卡与首屏引导语之后。
        for earlier in ('id="intent"', 'id="btnParse"', 'id="ocEmpty"'):
            with self.subTest(after=earlier):
                self.assertLess(thread.index(earlier), thread.index('id="ocResultActions"'))

        # 且必须是 ocTinner 的直接子节点 —— 缩进比 ocEmpty 深就说明它还嵌在某张卡里。
        def indent(marker: str) -> int:
            line = next(l for l in thread.split("\n") if marker in l)
            return len(line) - len(line.lstrip())
        self.assertEqual(indent('id="ocResultActions"'), indent('id="ocEmpty"'))

    def test_chips_are_re_anchored_after_each_turn(self):
        # append 已有节点 = 移动，所以不会出现第二份。
        self.assertIn("tinner.append(resultBox)", AGENT_JS)
        # 一轮对话结束（含出错）后重新置底。
        finally_block = AGENT_JS.split("      busy = false;", 1)[1][:300]
        self.assertIn("refreshResultChips()", finally_block)
        # CSS 兜底：晚到的异步卡片不能把它顶上去。
        block = AGENT_CSS.split(".oc-result-actions {", 1)[1].split("}", 1)[0]
        self.assertIn("order: 1", block)

    def test_left_panel_is_replaced_not_duplicated(self):
        self.assertNotIn('class="left-panel"', INDEX)
        self.assertNotIn("intent-disclosure", INDEX)     # 设计意图不再是折叠面板

    def test_right_panel_kept_its_pieces(self):
        for marker in ("viewerPartName", "viewer", "partDetail", "parameterEditor",
                       "btnMoreActions", "actionSheet"):
            with self.subTest(marker=marker):
                self.assertIn(marker, INDEX)


class DrawerTests(unittest.TestCase):
    def test_plus_menu_offers_the_four_capabilities(self):
        for label in ("补充需求图纸", "技术文档与视图", "导入已有模型", "版本与校核审查"):
            with self.subTest(label=label):
                self.assertIn(label, AGENT_JS)

    def test_every_drawer_group_points_at_sections_that_exist(self):
        block = AGENT_JS.split("const DRAWER_GROUPS = {", 1)[1].split("\n  };", 1)[0]
        referenced = set(re.findall(r'"([A-Za-z0-9_]+)"', block)) - {
            "补充需求图纸", "技术文档与补充视图", "导入已有 3D 模型",
            "版本与校核审查", "零件清单", "待澄清问题",
        }
        page_ids = ids(INDEX)
        for section in referenced:
            if section in {"upload", "evidence", "import3d", "review", "parts", "questions"}:
                continue
            with self.subTest(section=section):
                self.assertIn(section, page_ids)

    def test_all_drawer_sections_are_marked(self):
        body = INDEX.split('id="ocDrawerBody"', 1)[1]
        marked = body.count("data-drawer-section")
        self.assertGreaterEqual(marked, 8)

    def test_parts_and_questions_stay_available_as_result_buttons(self):
        self.assertIn('id="secParts"', INDEX)
        self.assertIn('id="tree"', INDEX)
        self.assertIn('id="secQuestions"', INDEX)
        self.assertIn('id="extras"', INDEX)
        # 两个结果按钮由 chip() 生成，并把抽屉键交给 openDrawer。
        self.assertIn('chip("零件清单", parts, "parts"', AGENT_JS)
        self.assertIn('chip("待澄清问题", questions || "", "questions"', AGENT_JS)
        self.assertIn("openDrawer(drawerKey)", AGENT_JS)
        self.assertIn('$("ocResultActions")', AGENT_JS)


class ParseViewDrawerTests(unittest.TestCase):
    """左侧抽屉改名「解析视图」，只放解析后的标注视图。"""

    def test_entry_and_drawer_are_named_parse_view(self):
        self.assertIn('data-open-drawer="evidence">解析视图<', INDEX)
        self.assertIn("<summary class=\"section-title\">解析视图</summary>", INDEX)
        self.assertIn('evidence: { title: "解析视图"', AGENT_JS)
        # 旧名字不能残留，否则入口叫一个名、抽屉标题叫另一个名。
        self.assertNotIn("技术文档与视图", INDEX)
        self.assertNotIn("技术文档与补充视图", AGENT_JS)

    def test_annotated_view_is_what_remains(self):
        for node in ("imgStage", "sourceImg", "bboxLayer"):
            with self.subTest(id=node):
                self.assertIn(f'id="{node}"', INDEX)

    def test_input_file_list_is_not_duplicated_here(self):
        """输入原图/技术文档只在右侧「任务文件」列一次。"""
        section = INDEX.split('id="secEvidence"', 1)[1].split("</details>", 1)[0]
        self.assertNotIn("evidence-file", section)
        self.assertNotIn("evidence-list", section)


class DomContractTests(unittest.TestCase):
    """搬 DOM 最容易把 app.js 依赖的 id 弄丢，这里直接和 git HEAD 对账。"""

    # 有意删掉的节点。左侧抽屉改成「解析视图」后只保留解析后的标注视图，
    # 输入原图与技术文档的清单统一归右侧「任务文件」小窗，不再两处各渲染一份。
    INTENTIONALLY_REMOVED = {"projectEvidence", "attachmentImageGallery"}

    def test_no_element_id_was_dropped_from_the_page(self):
        previous = subprocess.run(
            ["git", "show", "HEAD:frontend/index.html"],
            capture_output=True, text=True, encoding="utf-8", cwd=ROOT,
        )
        if previous.returncode != 0:
            self.skipTest("无法读取 git HEAD 版本的 index.html")
        dropped = ids(previous.stdout) - ids(INDEX) - self.INTENTIONALLY_REMOVED
        self.assertEqual(dropped, set())

    def test_removed_nodes_have_no_leftover_references(self):
        """删节点必须连着删渲染代码，留着就是一段永远走 early-return 的死逻辑。"""
        for name in self.INTENTIONALLY_REMOVED:
            with self.subTest(id=name):
                self.assertNotIn(name, APP_JS)
                self.assertNotIn(name, AGENT_JS)

    def test_app_js_ids_present_or_created_dynamically(self):
        # 这些是运行时插入的节点或早于本次改版就已缺失的历史遗留，不在页面骨架里。
        dynamic_or_legacy = {
            "btnRegen", "btnSaveParams", "inlineAnalysisHost",
            "chatMessages", "chatForm", "chatInput", "chatContext", "chatReference",
            "chatTyping", "chatModelBadge", "btnChatSend", "btnChatUseDrawing",
        }
        missing = used_ids(APP_JS) - ids(INDEX) - dynamic_or_legacy
        self.assertEqual(missing, set())

    def test_agent_js_only_touches_ids_that_exist(self):
        dynamic = {"ocEmpty"}   # 首屏引导语，发出第一条消息后会被移除
        missing = used_ids(AGENT_JS) - ids(INDEX) - dynamic
        self.assertEqual(missing, set())

    def test_page_loads_the_agent_assets(self):
        self.assertIn("agent-chat.css", INDEX)
        self.assertIn("agent-chat.js", INDEX)
        self.assertIn("app.js", INDEX)


class AgentClientTests(unittest.TestCase):
    def test_client_talks_to_the_agent_endpoints(self):
        # 模型设置不在这里 —— 它走全局的 /api/llm/settings，见 ModelSettingsTests。
        for path in ("/meta", "/send", "/new"):
            with self.subTest(path=path):
                self.assertIn(f'api("{path}")', AGENT_JS)
        self.assertIn("/agent${path}", AGENT_JS)

    def test_client_handles_every_sse_event_type(self):
        for kind in ("text", "tool_use", "tool_result", "error", "done"):
            with self.subTest(kind=kind):
                self.assertIn(f'"{kind}"', AGENT_JS)

    def test_parse_is_triggered_through_the_existing_button_only(self):
        """页面上只能有一个解析入口，否则会出现两套解析实现。"""
        self.assertIn('$("btnParse")', AGENT_JS)
        self.assertIn("button.click()", AGENT_JS)
        self.assertNotIn("/parse", AGENT_JS)

    def test_agent_request_parse_action_is_wired(self):
        self.assertIn('ui_action === "parse"', AGENT_JS)

    def test_app_js_notifies_the_chat_after_parsing(self):
        self.assertIn("agent:parse-done", APP_JS)
        self.assertIn("agent:ir-rendered", APP_JS)
        self.assertIn("agent:parse-done", AGENT_JS)
        self.assertIn("agent:ir-rendered", AGENT_JS)

    def test_styles_are_namespaced_to_avoid_clashing_with_workbench_css(self):
        selectors = re.findall(r"^\.([a-zA-Z][\w-]*)", AGENT_CSS, re.M)
        stray = sorted({name for name in selectors if not name.startswith("oc-")})
        # 只允许复用平台既有的这几个类做局部微调。
        self.assertEqual(stray, [])


if __name__ == "__main__":
    unittest.main()


class PartSubActionTests(unittest.TestCase):
    """工艺推荐 / 成本测算 从右侧零件详情移到左侧零件清单的子按钮。"""

    INLINE_JS = (FRONTEND / "inline-analysis.js").read_text(encoding="utf-8")
    WORKBENCH_CSS = (FRONTEND / "workbench.css").read_text(encoding="utf-8")

    def test_sub_buttons_are_built_under_each_part(self):
        self.assertIn("function buildPartSubActions", APP_JS)
        self.assertIn('container.appendChild(buildPartSubActions(p));', APP_JS)
        self.assertIn('"工艺推荐"', APP_JS)
        self.assertIn('"成本测算"', APP_JS)

    def test_right_panel_no_longer_carries_them(self):
        self.assertNotIn("part-nav", APP_JS.split("function buildPartSubActions")[0])
        self.assertNotIn("data-inline-analysis", APP_JS)
        # 右侧仍保留几何/图纸信息的宿主容器。
        self.assertIn('id="inlineAnalysisHost"', APP_JS)

    def test_analysis_renders_on_the_left_next_to_the_part(self):
        self.assertIn("function openPartAnalysis", APP_JS)
        self.assertIn('data-part-host="${CSS.escape(part.part_id)}"', APP_JS)
        self.assertIn("window.CadInlineAnalysis.open(mode, {", APP_JS)
        # 不再把右侧专家面板藏起来——面板已经不在右侧了。
        block = APP_JS.split("function openPartAnalysis", 1)[1].split("\n}", 1)[0]
        self.assertNotIn("expertPanel", block)

    def test_only_one_part_stays_expanded(self):
        self.assertIn("function togglePartSubActions", APP_JS)
        self.assertIn("togglePartSubActions(part.part_id)", APP_JS)
        self.assertIn("togglePartSubActions(currentSelectedId)", APP_JS)

    def test_sub_buttons_reuse_the_original_styling(self):
        self.assertIn('"part-nav part-subactions-row"', APP_JS)
        self.assertIn(".part-subactions", self.WORKBENCH_CSS)
        self.assertIn(".part-subactions[hidden]", self.WORKBENCH_CSS)

    def test_panel_labels_match_the_button_labels(self):
        """点「工艺推荐」不该弹出标题写「工艺拆解」的面板。"""
        self.assertNotIn("工艺拆解", self.INLINE_JS)
        self.assertNotIn("成本分析", self.INLINE_JS)
        self.assertIn("工艺推荐", self.INLINE_JS)
        self.assertIn("成本测算", self.INLINE_JS)

    def test_api_mode_keys_were_not_renamed(self):
        """接口路径用的是 process/cost，改中文文案不能动它们。"""
        self.assertIn('/${state.mode}', self.INLINE_JS)
        self.assertIn('"process"', self.INLINE_JS)
        self.assertIn('"cost"', self.INLINE_JS)
        self.assertIn('["process", "工艺推荐"', APP_JS)
        self.assertIn('["cost", "成本测算"', APP_JS)

    def test_sub_button_click_does_not_reselect_the_part(self):
        """子按钮在零件行内部，必须阻止冒泡，否则会重复触发选中与右侧跳转。"""
        block = APP_JS.split("function buildPartSubActions", 1)[1]
        self.assertIn("event.stopPropagation()", block)


class ModelSettingsTests(unittest.TestCase):
    """全局模型配置：首页与 2.1 小窗共用一份，任何入口改都全局生效。"""

    MAIN_PY = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    SETTINGS_PY = (ROOT / "backend" / "services" / "llm_settings.py").read_text(encoding="utf-8")
    OC_AGENT = (ROOT / "backend" / "services" / "oc_agent.py").read_text(encoding="utf-8")
    PANEL_JS = (FRONTEND / "llm-settings-panel.js").read_text(encoding="utf-8")
    HOME_JS = (FRONTEND / "home.js").read_text(encoding="utf-8")

    def test_dock_label_says_settings_not_just_model(self):
        dock = INDEX.split('id="ocAgentDock"', 1)[1].split("</aside>", 1)[0]
        self.assertIn("模型参数设置", dock)

    def test_one_panel_implementation_serves_both_places(self):
        """两处各写一套表单，正是之前配置口径对不上的根因。"""
        self.assertIn("window.LlmSettingsPanel", self.PANEL_JS)
        for page in (INDEX, (FRONTEND / "home.html").read_text(encoding="utf-8")):
            with self.subTest():
                self.assertIn("llm-settings-panel.js", page)
        self.assertIn("LlmSettingsPanel.mount", AGENT_JS)
        self.assertIn("LlmSettingsPanel.mount", self.HOME_JS)
        # 首页原来那套表单必须删干净，否则又会分叉。
        for gone in ("homeLlmProvider", "homeLlmVision", "homeLlmText",
                     "saveHomeLlmSettings", "homeApplyProviderCatalog"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, self.HOME_JS)

    def test_both_endpoints_serve_the_same_snapshot(self):
        """/agent/settings 只是全局设置的项目级别名。"""
        block = self.MAIN_PY.split("def agent_settings", 1)[1].split("\n\n\n", 1)[0]
        self.assertIn("get_runtime_llm_settings(user)", block)
        block = self.MAIN_PY.split("def update_agent_settings", 1)[1].split("\n\n\n", 1)[0]
        self.assertIn("update_runtime_llm_settings(body, user)", block)

    def test_agent_reuses_the_language_model(self):
        """不再单列「对话模型」—— 否则又会变成两个模型设置、两处对不上。"""
        block = self.SETTINGS_PY.split("def agent_params", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('params["agent_model"] = selected_model(vision=False)', block)

    def test_multimodal_and_language_models_are_chosen_separately(self):
        """本任务要读图：多模态和纯语言模型必须能分别选。"""
        for field in ("vision_model", "text_model"):
            with self.subTest(field=field):
                self.assertIn(field, self.SETTINGS_PY)
                self.assertIn(field, self.PANEL_JS)
        self.assertIn("多模态模型", self.PANEL_JS)
        self.assertIn("语言模型", self.PANEL_JS)

    def test_model_list_is_the_agreed_whitelist(self):
        from backend.services import llm_settings

        self.assertEqual(
            [item["label"] for item in llm_settings.MODELS],
            ["Opus 5", "GPT-5.6 Sol", "Qwen3.5 Plus", "Qwen3.8 Max",
             "DeepSeek V4 Pro", "DeepSeek V4 Flash"])

    def test_deepseek_is_not_offered_as_a_multimodal_model(self):
        """DeepSeek 没有视觉能力，可选等于允许把图纸解析配崩。"""
        from backend.services import llm_settings

        self.assertNotIn("deepseek-v4-pro", llm_settings.VISION_MODELS)
        self.assertNotIn("deepseek-v4-flash", llm_settings.VISION_MODELS)
        self.assertIn("deepseek-v4-pro", llm_settings.TEXT_MODELS)
        self.assertIn("deepseek-v4-flash", llm_settings.TEXT_MODELS)

    def test_whitelist_is_enforced_server_side(self):
        """只靠前端下拉限制，改一次请求就能绕过。"""
        from backend.services import llm_settings

        with self.assertRaises(ValueError):
            llm_settings.update({"vision_model": "deepseek-v4-pro"}, is_admin=True)
        with self.assertRaises(ValueError):
            llm_settings.update({"text_model": "some-unlisted-model"}, is_admin=True)

    def test_each_model_routes_to_its_own_official_gateway(self):
        """以前所有请求都发往部署配的那一个 MaaS 兼容端点，选了 opus5 也只是
        拿这个 id 去问一个不认识它的网关。"""
        from backend.services import llm_settings

        for model, provider, host in (
            ("claude-opus-5", "anthropic", "api.anthropic.com"),
            ("gpt-5.6-sol", "openai", "api.openai.com"),
            ("qwen3.8-max", "qwen", "dashscope.aliyuncs.com"),
            ("deepseek-v4-pro", "deepseek", "api.deepseek.com"),
        ):
            with self.subTest(model=model):
                self.assertEqual(llm_settings.provider_of(model), provider)
                self.assertIn(host, llm_settings.PROVIDERS[provider]["base_url"])
        # 那个错误的 MaaS 私有网关不能再是任何提供商的 base_url。
        for spec in llm_settings.PROVIDERS.values():
            with self.subTest(base_url=spec["base_url"]):
                self.assertNotIn("maas.aliyuncs.com", spec["base_url"])

    def test_no_model_pool_fallback_remains(self):
        """配的模型报错就静默换下一个，是"配了 opus5 却走老路"的直接原因。"""
        qwen = (ROOT / "backend" / "services" / "qwen_client.py").read_text(encoding="utf-8")
        block = qwen.split("def _model_candidates", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("selected_model(vision=vision)", block)
        self.assertNotIn("_unavailable_models", block)

    def test_panel_shows_exactly_the_six_settings(self):
        """多余项一律不进界面：思考预算/工具轮次有合理默认，
        base_url 与提供商是部署配置，改它们要动 .env 并重启。"""
        for field in ("vision_model", "text_model", "temperature",
                      "max_tokens", "thinking", "api_key"):
            with self.subTest(field=field):
                self.assertIn(field, self.PANEL_JS)
        for gone in ("thinking_budget", "max_iterations", "web_model",
                     "agent_model", "provider_options"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, self.PANEL_JS)

    def test_the_key_is_never_echoed_back(self):
        """回传明文密钥等于把它送进浏览器缓存和日志。"""
        block = self.SETTINGS_PY.split("def snapshot(", 1)[1].split("# ---", 1)[0]
        for marker in ("key_set", "key_hint"):
            with self.subTest(marker=marker):
                self.assertIn(marker, block)
        self.assertNotIn('"api_key":', block)

    def test_keys_are_kept_per_provider_and_exported_to_env(self):
        """选了哪两个模型就只需要哪几把 Key；同时同步到环境变量，
        因为 Agent（open-claude）是从那里取的。"""
        block = self.SETTINGS_PY.split("def _export_env", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("os.environ[env_name] = value", block)
        self.assertIn("api_key_provider", self.SETTINGS_PY)
        self.assertIn("api_key_provider", self.PANEL_JS)

    def test_changing_models_or_keys_needs_admin(self):
        block = self.SETTINGS_PY.split("def update(", 1)[1].split("_update_platform", 1)[0]
        self.assertIn("PermissionError", block)
        self.assertIn("is_admin", block)

    def test_audit_records_the_change_but_not_the_value(self):
        self.assertIn("def changed_fields", self.SETTINGS_PY)
        self.assertIn('key != "api_key"', self.SETTINGS_PY)
        self.assertIn("llm_settings.changed_fields(patch)", self.MAIN_PY)

    def test_params_are_clamped_server_side(self):
        """temperature 传 5 会让整轮请求被上游打回，错误信息还很难懂。"""
        block = self.SETTINGS_PY.split("def update(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("_clamp(", block)
        self.assertIn("PARAM_RANGES", self.SETTINGS_PY)

    def test_clamping_lives_in_exactly_one_place(self):
        """校验放两处就会分叉，所以 oc_agent 只负责赋值。"""
        block = self.OC_AGENT.split("def apply(", 1)[1].split("    def ", 1)[0]
        self.assertNotIn("_clamp", block)

    def test_changing_the_key_rebuilds_the_client(self):
        """open-claude 在构造 client 时就把 key 读进去了，不重建等于没换。"""
        self.assertIn("rebuild_client", self.SETTINGS_PY)
        block = self.OC_AGENT.split("def apply(", 1)[1].split("    def ", 1)[0]
        self.assertIn("create_client()", block)

    def test_changes_reach_sessions_that_already_exist(self):
        """否则改设置只对之后新建的会话生效，用户会看到「我明明改了」。"""
        self.assertIn("def apply_settings", self.OC_AGENT)
        self.assertIn("oc_agent.apply_settings", self.SETTINGS_PY)
        # 新建会话也要立刻套用，不能等重启。
        block = self.OC_AGENT.split("def get_agent", 1)[1].split("def _apply_global", 1)[0]
        self.assertIn("_apply_global(agent)", block)

    def test_clearing_a_field_back_to_default_actually_works(self):
        """用 exclude_none 的话 temperature=null 会被整个丢掉，
        界面上「留空用模型默认值」就永远生效不了。"""
        self.assertIn("exclude_unset=True", self.MAIN_PY)
        self.assertNotIn("model_dump(exclude_none=True)", self.MAIN_PY)
        # 面板确实会把空输入发成 null。
        self.assertIn('field.value === "" ? null : Number(field.value)', self.PANEL_JS)

    def test_agent_side_settings_survive_a_restart(self):
        """原来只存内存，重启就回默认值。"""
        self.assertIn("llm_settings.json", self.SETTINGS_PY)
        self.assertIn("def _persist", self.SETTINGS_PY)
        self.assertIn("0o600", self.SETTINGS_PY)


class RestartTaskTests(unittest.TestCase):
    """「新对话」改成「本次任务从头开始」。"""

    MAIN_PY = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    STORE_PY = (ROOT / "backend" / "storage" / "store.py").read_text(encoding="utf-8")

    def test_button_says_restart_not_new_chat(self):
        dock = INDEX.split('id="ocAgentDock"', 1)[1].split("</aside>", 1)[0]
        self.assertIn("本次任务从头开始", dock)
        self.assertNotIn("新对话", dock)

    def test_it_confirms_before_destroying_results(self):
        block = AGENT_JS.split('$("ocNewChat")', 1)[1].split("\n  });", 1)[0]
        self.assertIn("window.confirm", block)
        self.assertIn("if (!confirmed) return", block)

    def test_it_clears_the_whole_parse_stage(self):
        self.assertIn("def reset_parse_stage", self.STORE_PY)
        self.assertIn("store.reset_parse_stage", self.MAIN_PY)
        block = self.STORE_PY.split("PARSE_STAGE_DOCS = (", 1)[1].split(")", 1)[0]
        for kind in ("ir", "component_match", "geometry", "drawings",
                     "process", "cost", "process_lookup", "cost_lookup"):
            with self.subTest(kind=kind):
                self.assertIn(f'"{kind}"', block)

    def test_inputs_and_history_survive(self):
        """「从头开始」是拿同一份图纸重新解析，不是把图纸也删掉。"""
        block = self.STORE_PY.split("PARSE_STAGE_DOCS = (", 1)[1].split(")", 1)[0]
        for kept in ('"requirement"', '"versions"', '"material"', '"manufacturing"'):
            with self.subTest(kept=kept):
                self.assertNotIn(kept, block)

    def test_downstream_results_are_marked_stale(self):
        block = self.STORE_PY.split("def reset_parse_stage", 1)[1].split("\n\n\n", 1)[0]
        self.assertIn("derived_results_stale", block)

    def test_reset_works_even_when_the_agent_is_down(self):
        """模型连不上不该妨碍用户把任务重来一遍。"""
        block = self.MAIN_PY.split("def agent_restart_task", 1)[1].split("\n\n\n", 1)[0]
        self.assertLess(block.index("store.reset_parse_stage"), block.index("oc_agent.available()"))


class FooterNavigationTests(unittest.TestCase):
    """右下角「下一步」，排在解析报告右边。"""

    WORKBENCH_CSS = (FRONTEND / "workbench.css").read_text(encoding="utf-8")

    def test_next_sits_to_the_right_of_the_report_button(self):
        right = INDEX.split('class="footer-right"', 1)[1].split("</div>", 1)[0]
        self.assertIn('id="btnReport"', right)
        self.assertIn('id="btnNext"', right)
        self.assertLess(right.index('id="btnReport"'), right.index('id="btnNext"'))
        self.assertIn("下一步", right)

    def test_it_reuses_the_workflow_gate_instead_of_routing_itself(self):
        """否则「解析没做完能不能进下一步」会出现两个说法。"""
        block = APP_JS.split('$("btnNext").onclick', 1)[1].split("\n};", 1)[0]
        self.assertIn('CadWorkflowNavigation?.navigate("2.2")', block)
        self.assertNotIn("location.href", block)
        self.assertIn("workflow-navigation.js", INDEX)

    def test_it_refuses_without_a_project(self):
        block = APP_JS.split('$("btnNext").onclick', 1)[1].split("\n};", 1)[0]
        self.assertIn("if (!currentProject)", block)

    def test_it_is_styled_distinctly_from_the_report_button(self):
        self.assertIn(".footer-right .btn-primary", self.WORKBENCH_CSS)
        self.assertIn(".footer-right .btn-primary:disabled", self.WORKBENCH_CSS)


class TaskFilesTests(unittest.TestCase):
    """任务文件收到右侧悬浮小窗，点开是弹窗；过程中产出的文件也要能在这里看到。"""

    MAIN_PY = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")

    def test_files_are_listed_directly_in_the_dock(self):
        """要一眼看见，不能藏在弹窗后面。"""
        dock = INDEX.split('id="ocFilesDock"', 1)[1].split("</aside>", 1)[0]
        for marker in ("ocFilesCount", "ocFilesBody", "ocFilesRefresh"):
            with self.subTest(marker=marker):
                self.assertIn(marker, dock)
        for gone in ("ocFilesModal", "ocFilesBackdrop", "ocFilesOpen", "oc-modal"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, INDEX)
        self.assertNotIn("function openFiles", AGENT_JS)
        self.assertNotIn(".oc-modal", AGENT_CSS)

    def test_both_docks_collapse(self):
        for toggle, dock in (("ocSideToggle", "ocAgentDock"), ("ocFilesToggle", "ocFilesDock")):
            with self.subTest(dock=dock):
                self.assertIn(f'id="{toggle}"', INDEX)
                self.assertIn(f'["{toggle}", "{dock}"]', AGENT_JS)
        self.assertIn(".oc-dock.collapsed .oc-dock-body", AGENT_CSS)

    def test_count_stays_visible_when_collapsed(self):
        """收起之后还得看得出有没有新文件，所以计数在标题栏里、不在 body 里。"""
        head = INDEX.split('id="ocFilesDock"', 1)[1].split('class="oc-dock-body', 1)[0]
        self.assertIn('id="ocFilesCount"', head)

    def test_the_toggle_is_a_sibling_of_the_refresh_button(self):
        """按钮不能嵌套 —— 刷新必须在折叠开关外面。"""
        toggle = INDEX.split('id="ocFilesToggle"', 1)[1].split("</button>", 1)[0]
        self.assertNotIn("ocFilesRefresh", toggle)

    def test_the_dock_scrolls_instead_of_covering_the_footer(self):
        block = AGENT_CSS.split(".oc-dock {", 1)[1].split("}", 1)[0]
        self.assertIn("max-height", block)
        self.assertIn("overflow-y: auto", block)

    def test_the_old_in_thread_assets_card_is_gone(self):
        """资料移到右侧小窗，对话里不再重复放一份。"""
        for marker in ("ocInputAssets", "ocAssetsToggle", "oc-assets-card"):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, INDEX)
        self.assertNotIn("loadInputAssets", AGENT_JS)

    def test_manifest_covers_inputs_and_process_outputs(self):
        """散在各步骤里的产出汇总到一处，才谈得上「过程中所有文件都能查看」。"""
        block = self.MAIN_PY.split("def list_project_files", 1)[1].split("\n\n\n", 1)[0]
        for source in ("source_filename", "attachments", "load_geometry_result",
                       "load_drawings_result", "bom.csv", "costest.csv"):
            with self.subTest(source=source):
                self.assertIn(source, block)

    def test_manifest_only_lists_what_exists(self):
        """列一个点开是 404 的链接比不列更糟。"""
        block = self.MAIN_PY.split("def list_project_files", 1)[1].split("\n\n\n", 1)[0]
        self.assertIn("if store.load_ir(project_id)", block)
        self.assertIn("if store.load_costest(project_id)", block)

    def test_client_reads_the_single_manifest_endpoint(self):
        self.assertIn("}/files`", AGENT_JS)
        # 前端不再各自去猜哪一步生成过什么。
        self.assertNotIn("}/attachments`", AGENT_JS)

    def test_counts_refresh_after_a_task_produces_files(self):
        calls = AGENT_JS.count("loadFiles(") - AGENT_JS.count("function loadFiles(")
        self.assertGreaterEqual(calls, 3)   # 初次加载 + 解析完成 + 任务成功

    def test_counts_and_list_stay_in_sync(self):
        """计数和列表由同一次请求写入，不会出现「显示 5 条但列出 3 条」。"""
        block = AGENT_JS.split("async function loadFiles(", 1)[1].split("\n  }", 1)[0]
        self.assertIn("filesCount.textContent", block)
        self.assertIn("filesBody.replaceChildren()", block)


class ProcessTimelineTests(unittest.TestCase):
    """Agent 的处理过程要显示在对话里，而不是只在右侧任务条上跑。"""

    def test_app_js_broadcasts_task_progress(self):
        self.assertIn("agent:task-progress", APP_JS)
        block = APP_JS.split("agent:task-progress", 1)[1][:500]
        for field in ("label", "status", "progress", "log", "error"):
            with self.subTest(field=field):
                self.assertIn(field, block)

    def test_chat_renders_progress_as_a_timeline(self):
        self.assertIn("agent:task-progress", AGENT_JS)
        for fn in ("ensureProcessCard", "pushProcessStep", "finishProcessCard"):
            with self.subTest(fn=fn):
                self.assertIn(f"function {fn}", AGENT_JS)
        self.assertIn(".oc-process-card", AGENT_CSS)
        self.assertIn(".oc-process-step", AGENT_CSS)

    def test_steps_are_replayed_from_the_log_by_cursor(self):
        """按下标续播，而不是按文本去重。

        文本去重会把"两个零件都没匹配上"折叠成一条，看起来像只查了一件。
        """
        self.assertIn("card.cursor", AGENT_JS)
        self.assertIn("log.slice(card.cursor)", AGENT_JS)
        self.assertNotIn("seen.has(text)", AGENT_JS)

    def test_hits_and_misses_are_visually_distinct(self):
        self.assertIn('"命中"', AGENT_JS)
        self.assertIn('"无同类件"', AGENT_JS)
        self.assertIn(".oc-process-step.hit", AGENT_CSS)
        self.assertIn(".oc-process-step.miss", AGENT_CSS)


class ComponentMatchViewTests(unittest.TestCase):
    """图纸拆解要检索零部件库，并在对话里标出匹配到 / 没匹配到的零件。"""

    MAIN_PY = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")

    def test_parse_job_runs_the_library_lookup(self):
        self.assertIn("component_match.match_project", self.MAIN_PY)
        self.assertIn("component_match.save_report", self.MAIN_PY)
        # 检索失败不能让已经拿到的 IR 作废。
        block = self.MAIN_PY.split("component_match.match_project", 1)[1][:400]
        self.assertIn("except Exception", block)

    def test_report_is_exposed_over_http(self):
        self.assertIn('/api/projects/{project_id}/component-match', self.MAIN_PY)

    def test_chat_renders_the_three_way_split(self):
        self.assertIn("function renderComponentMatch", AGENT_JS)
        for label in ("可复用", "可改制", "未匹配"):
            with self.subTest(label=label):
                self.assertIn(label, AGENT_JS)
        for selector in (".oc-match-stat.reuse", ".oc-match-stat.modify", ".oc-match-row.new"):
            with self.subTest(selector=selector):
                self.assertIn(selector, AGENT_CSS)

    def test_gap_notes_are_shown_for_modifiable_parts(self):
        self.assertIn("item.gap_notes", AGENT_JS)
        self.assertIn(".oc-match-gap", AGENT_CSS)

    def test_previous_card_is_replaced_not_stacked(self):
        """重复解析不该在对话里堆出多张检索结果卡。"""
        self.assertIn('document.querySelector(".oc-match-card")', AGENT_JS)
        self.assertIn(".remove()", AGENT_JS)


class PartEditTests(unittest.TestCase):
    """Agent 通过对话改零件参数：两条会话入口必须共用同一套白名单。"""

    MAIN_PY = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    OC_AGENT = (ROOT / "backend" / "services" / "oc_agent.py").read_text(encoding="utf-8")

    def test_whitelist_has_exactly_one_implementation(self):
        """老的 workbench-chat 与 2.1 的 Agent 都走 services/part_edit。

        两份实现会让白名单漂移：某天其中一条路允许改另一条不允许，而这种差异
        只有出事才会被发现。
        """
        from backend.services import part_edit

        self.assertIn("part_edit.apply_edit", self.MAIN_PY)
        self.assertIn("part_edit.apply_edit", self.OC_AGENT)
        # 白名单只在 part_edit 里定义一次。
        source = (ROOT / "backend").rglob("*.py")
        definers = [p.relative_to(ROOT).as_posix() for p in source
                    if "FEATURE_FIELDS: dict" in p.read_text(encoding="utf-8")]
        self.assertEqual(definers, ["backend/services/part_edit.py"])
        self.assertIn("hole_pattern", part_edit.FEATURE_FIELDS)

    def test_imported_3d_guard_is_shared_too(self):
        self.assertIn("part_edit.blocks_feature_edit", self.MAIN_PY)
        self.assertIn("part_edit.blocks_feature_edit", self.OC_AGENT)

    def test_actor_is_threaded_into_the_agent_stream(self):
        """审计要记真实操作人，不能都记成 system。"""
        self.assertIn("actor=user.get(\"username\", \"system\")", self.MAIN_PY)
        # ContextVar 不跨线程传播，必须在 worker 里再设一次。
        worker = self.OC_AGENT.split("def worker() -> None:", 1)[1][:300]
        self.assertIn("_ACTOR.set(", worker)

    def test_chat_refreshes_the_workbench_after_an_edit(self):
        # 刷新复用 app.js 已有的 refreshAfterChatEdit，不另写一份。
        self.assertIn("cad-engine:workbench-chat-edit", AGENT_JS)
        self.assertIn("cad-engine:workbench-chat-edit", APP_JS)
        self.assertIn("function refreshAfterChatEdit", APP_JS)
        # 刷新必须等本轮结束 —— tool_use 事件早于工具执行，当场刷会读到旧值。
        self.assertIn("pendingEdits", AGENT_JS)
        self.assertIn("flushPartEdits()", AGENT_JS)

    def test_only_applied_edits_trigger_a_refresh(self):
        """工具被拒绝时什么都没写，不该触发刷新。"""
        block = AGENT_JS.split("function parseEditResult", 1)[1][:400]
        self.assertIn("data.applied !== true", block)
        self.assertIn("event.is_error", block)


class ModelRoutingTests(unittest.TestCase):
    """所有 AI 调用点都必须经统一分派层，否则「模型设置」形同虚设。

    这次的现象就是：1.1「接受工艺评估需求」的 AI 解析直接 import 了 qwen_client，
    于是配了 opus5 仍然报「Qwen 调用失败 HTTP 404 … 使用的模型：claude-opus-5」。
    """

    SERVICES = ROOT / "backend" / "services"
    # 允许直连具体提供商的地方：分派层自己，以及百炼原生搜索 API
    # （它返回可核验来源，OpenAI 兼容接口没有等价物）。
    ALLOWED = {"llm_client.py", "qwen_client.py", "openai_client.py", "claude_client.py",
               "model_lookup.py"}

    def test_no_service_imports_a_provider_client_directly(self):
        pattern = re.compile(r"^\s*from \.(?: import (qwen_client|openai_client)|"
                             r"(qwen_client|openai_client) import)", re.M)
        for path in sorted(self.SERVICES.glob("*.py")):
            if path.name in self.ALLOWED:
                continue
            with self.subTest(file=path.name):
                self.assertIsNone(pattern.search(path.read_text(encoding="utf-8")),
                                  f"{path.name} 绕过了 llm_client 分派层")

    def test_main_does_not_call_a_provider_client_for_inference(self):
        main_py = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
        for call in ("qwen_client.run(", "qwen_client.text_block(",
                     "qwen_client.image_block(", "qwen_client.complete_to_model("):
            with self.subTest(call=call):
                self.assertNotIn(call, main_py)

    def test_model_lookup_only_uses_the_native_path_for_qwen(self):
        text = (self.SERVICES / "model_lookup.py").read_text(encoding="utf-8")
        block = text.split("def _lookup_with_search", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('provider == "qwen"', block)
        self.assertIn("llm_client.run(", block)

    def test_route_is_not_cached(self):
        """缓存住就等于把模型冻在进程启动时的那一个，界面上改了不生效。"""
        text = (self.SERVICES / "claude_client.py").read_text(encoding="utf-8")
        block = text.split("def _route(", 1)[0][-200:]
        self.assertNotIn("lru_cache", block)

    def test_audit_never_substitutes_the_configured_model(self):
        """留痕必须记真正跑过的模型；拿配置值顶替会让审计看起来言之凿凿。"""
        text = (self.SERVICES / "llm_client.py").read_text(encoding="utf-8")
        block = text.split("def last_used_model", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("selected_model", block)
        # 两条路径都要有记录点。
        self.assertIn("claude_client.last_used_model()", block)
        self.assertIn("qwen_client.last_used_model()", block)
