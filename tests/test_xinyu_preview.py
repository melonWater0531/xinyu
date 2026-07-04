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
        for phrase in ("和小屿聊聊天", "我最近有点压力大", "产品讨论会", "项目周会", "今日情绪趋势", "今日日记", "本周周报", "心屿设备", "数据与隐私"):
            self.assertIn(phrase, self.html)
        self.assertIn("<svg viewBox", self.js)
        self.assertIn("只记录明显转折点", self.html)
        for network_marker in ("XMLHttpRequest", "WebSocket", "multi_track", "/api/conversation", "/api/gimbal", "/api/control"):
            self.assertNotIn(network_marker, self.js)
        self.assertEqual(re.findall(r'fetch\("([^\"]+)"', self.js), ["/api/chat"])

    def test_llm_chat_prioritizes_user_text_and_has_local_fallback(self) -> None:
        for phrase in (
            "buildLLMPayload", "用户本轮自述是最高优先级", "只跟随用户文字",
            "40至90个中文字符", "AbortController", "companionReply", "已使用本地回应",
        ):
            self.assertIn(phrase, self.js)
        self.assertIn('fetch("/api/chat"', self.js)
        self.assertIn("controller.abort()", self.js)

    def test_demo_sop_documents_preview_startup_ip_and_llm_call(self) -> None:
        sop = (DOCS / "home_demo_sop.md").read_text(encoding="utf-8")
        for phrase in (
            "Preview 启动方式",
            "python3 recamera_fastapi.py",
            "http://localhost:8001/static/xinyu_preview.html",
            "手机录屏时如何找到电脑 IP",
            "hostname -I",
            "ip -4 addr",
            "ipconfig",
            "http://<电脑IP>:8001/static/xinyu_preview.html",
            "Preview 陪伴页 LLM 调用方式",
            "POST /api/chat",
            "DEEPSEEK_API_KEY",
            "ZHIPU_API_KEY",
            "10 秒超时",
            "本地 fallback",
        ):
            self.assertIn(phrase, sop)

    def test_demo_and_storage_are_preview_only(self) -> None:
        self.assertIn('"xinyu.preview.v1"', self.js)
        self.assertEqual(set(re.findall(r'localStorage\.(?:getItem|setItem)\(([^,)]+)', self.js)), {"STORAGE_KEY"})
        for phrase in ("产品预览 · 演示数据", "演示记录", "不包含真实录音"):
            self.assertIn(phrase, self.html + self.js)
        self.assertIn("DEMO", self.js)

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
