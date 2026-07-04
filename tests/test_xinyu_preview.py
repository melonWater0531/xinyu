from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard"
DOCS = ROOT / "docs"


class XinyuPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (DASHBOARD / "xinyu_preview.html").read_text(encoding="utf-8")
        cls.css = (DASHBOARD / "xinyu_preview.css").read_text(encoding="utf-8")
        cls.js = (DASHBOARD / "xinyu_preview.js").read_text(encoding="utf-8")
        cls.all_text = "\n".join((cls.html, cls.css, cls.js))

    def test_preview_files_and_five_pages_exist(self) -> None:
        for name in ("xinyu_preview.html", "xinyu_preview.css", "xinyu_preview.js"):
            self.assertTrue((DASHBOARD / name).is_file())
        self.assertEqual(
            re.findall(r'data-page="(home|companion|meeting|records|mine)"', self.html),
            ["home", "companion", "meeting", "records", "mine"],
        )
        nav = self.html[self.html.index('class="xy-bottom-nav"'):]
        for label in ("首页", "陪伴", "会议", "记录", "我的"):
            self.assertIn(f"<span>{label}</span>", nav)

    def test_home_is_emotion_first_without_engineering_copy(self) -> None:
        home = self.html[self.html.index('data-page="home"'):self.html.index('data-page="companion"')]
        for phrase in ("当前情绪", "有点疲惫", "小屿留意到", "情绪趋势 · 今天", "小屿建议", "喝水", "活动", "冥想"):
            self.assertIn(phrase, home)
        forbidden = ("EAR", "blink_rate", "blink rate", "calibration", "emotion_probability", "识别概率", "让小屿给一句建议")
        for phrase in forbidden:
            self.assertNotIn(phrase.lower(), home.lower())

    def test_design_tokens_are_scoped_and_avoid_black(self) -> None:
        self.assertIn(".xinyu-preview-page", self.css)
        for token in (
            "--xy-bg: #F6EFE5", "--xy-surface: #FFFDF8", "--xy-text: #3E332A",
            "--xy-caramel: #8A6A45", "--xy-sage: #A8CFA0", "--xy-blue: #8FA9D8",
        ):
            self.assertIn(token, self.css)
        for color in ("#000", "#111", "#222"):
            self.assertNotIn(color, self.all_text.lower())
        self.assertNotRegex(self.css, r"(?m)^\s*:root\s*\{")

    def test_icons_are_inline_svg_without_images_or_external_assets(self) -> None:
        self.assertGreaterEqual(self.html.count("<symbol"), 15)
        self.assertGreaterEqual(self.html.count("<use href=\"#xy-"), 20)
        self.assertNotIn("<img", self.html.lower())
        self.assertNotRegex(self.all_text, r"https?://")
        self.assertFalse(list(DASHBOARD.glob("xinyu_preview*.png")))
        self.assertFalse(list(DASHBOARD.glob("xinyu_preview*.webp")))
        badges = re.findall(r'<span class="[^"]*xy-icon-badge[^"]*">(.*?)</span>', self.html, re.S)
        self.assertTrue(badges)
        self.assertTrue(all("<svg" in content and not re.search(r"[\u4e00-\u9fff]", re.sub(r"<[^>]+>", "", content)) for content in badges))

    def test_companion_meeting_records_and_profile_contracts(self) -> None:
        for phrase in ("和小屿聊聊天", "我今天有点累", "下半年活动规划与预算申报周会", "今日情绪趋势", "今日日记", "本周周报", "心屿设备", "数据与隐私"):
            self.assertIn(phrase, self.html)
        for phrase in ("buildAssistantMemoryContext", "buildXiaoyuReply", "openMeetingDetail", "openDiaryModal", "openWeeklyReportModal"):
            self.assertIn(phrase, self.js)
        for network_marker in ("XMLHttpRequest", "WebSocket", "multi_track", "/api/conversation", "/api/gimbal", "/api/control", "/api/reflect"):
            self.assertNotIn(network_marker, self.js)
        self.assertIn("/static/page2_preview/data/xinyu_seed_data.js", self.html)

    def test_current_meeting_title_wraps_without_splitting_status(self) -> None:
        for phrase in (
            "xy-meeting-title-break",
            "xy-nowrap",
            "formatMeetingTitle",
            "预算申报<span class=\"xy-nowrap\">周会</span>",
        ):
            self.assertIn(phrase, self.html + self.css + self.js)
        self.assertIn("white-space: nowrap", self.css)
        self.assertIn("min-width: 58px", self.css)
        self.assertIn("grid-template-columns: minmax(0, 1fr) auto", self.css)

    def test_local_memory_chat_and_records_are_available(self) -> None:
        for phrase in (
            "assistantMemory", "quickReplies", "loadMeetingMarkdown",
            "saveDiary", "renderDiaryHistory", "renderWeeklyHistory",
            "buildLLMPayload", "requestLLMReply", 'fetch("/api/chat"',
        ):
            self.assertIn(phrase, self.js)
        seed = (DASHBOARD / "page2_preview" / "data" / "xinyu_seed_data.js").read_text(encoding="utf-8")
        self.assertIn("meeting_summary_2026-07-04.md", seed)
        self.assertIn("2026-07", self.html + self.js)

    def test_companion_chat_does_not_replace_local_reply_with_generic_llm_fallback(self) -> None:
        for phrase in (
            "shouldUseLLMReply",
            "谢谢你愿意说出来。我在这里听着，也陪你一起整理。",
            "return shouldUseLLMReply(reply, fallback) ? reply : fallback",
        ):
            self.assertIn(phrase, self.js)
        self.assertIn('quick["给我一些放松建议"]', self.js)

    def test_evening_copy_user_name_and_trend_label(self) -> None:
        seed = (DASHBOARD / "page2_preview" / "data" / "xinyu_seed_data.js").read_text(encoding="utf-8")
        for phrase in ("晚上好，蛋挞", "Hi，蛋挞", '<h1 id="xy-mine-title">蛋挞</h1>'):
            self.assertIn(phrase, self.html)
        self.assertIn('user_name: "蛋挞"', self.js)
        self.assertIn("晚上状态平和了一些", self.js)
        self.assertIn('display: "平和"', seed)
        self.assertNotIn("清楚一些", seed + self.html + self.js)
        self.assertNotIn("上午好，Lintong", self.html)

    def test_diary_history_opens_centered_detail_modal(self) -> None:
        for phrase in (
            'aria-label="打开 ${escapeHTML(formatDate(day.date))} 日记"',
            "openDiaryModal(button.dataset.date)",
            '${formatDate(date)} 日记',
            "override.assistantReply || day.assistantReply",
        ):
            self.assertIn(phrase, self.js)
        self.assertIn("margin: auto;", self.css)
        self.assertIn("height: min(80dvh, 720px)", self.css)
        self.assertIn("border-radius: 30px", self.css)
        self.assertNotIn("margin: auto auto 0", self.css)
        self.assertNotIn("border-radius: 28px 28px 0 0", self.css)

    def test_sop_documents_actual_page2_and_xinyu_access(self) -> None:
        sop = (DOCS / "home_demo_sop.md").read_text(encoding="utf-8")
        for phrase in (
            "/static/xinyu_preview.html",
            "本地 memory context",
            "python3 recamera_fastapi.py",
            "http://localhost:8001/static/xinyu_preview.html",
            "手机录屏时如何找到电脑 IP",
            "hostname -I",
            "ip -4 addr",
            "ipconfig",
            "http://<电脑IP>:8001/static/xinyu_preview.html",
            "export DEEPSEEK_API_KEY=sk-xxx",
            "export ZHIPU_API_KEY=sk-xxx",
            "POST /api/chat",
        ):
            self.assertIn(phrase, sop)

    def test_no_public_placeholder_words_or_fake_status_bar(self) -> None:
        visible_text = "\n".join((self.html, self.css))
        forbidden_visible = (
            "产品预览", "演示数据", "前端演示", "不会启动真实录音", "只记录明显转折点",
            "demo", "Demo", "DEMO", "mock", "fake", "sample data", "debug",
            "9:41", "signal", "battery", "status-bar", "逐字稿",
        )
        for phrase in forbidden_visible:
            self.assertNotIn(phrase, visible_text)
        for phrase in ("产品预览", "演示数据", "前端演示", "不会启动真实录音", "只记录明显转折点", "逐字稿"):
            self.assertNotIn(phrase, self.all_text)

    def test_storage_and_seed_contract(self) -> None:
        self.assertIn('"xinyu.preview.v1"', self.js)
        self.assertIn('"xinyu.actual.diary.v1"', self.js)
        seed = (DASHBOARD / "page2_preview" / "data" / "xinyu_seed_data.js").read_text(encoding="utf-8")
        for phrase in ("2026-06-01", "2026-07-04", "下半年活动规划与预算申报周会", "meeting-2026-07-04-activity-budget"):
            self.assertIn(phrase, seed)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for preview syntax validation")
    def test_preview_javascript_syntax(self) -> None:
        subprocess.run(
            ["node", "--check", str(DASHBOARD / "xinyu_preview.js")],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
