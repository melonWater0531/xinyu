from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard"
PRODUCT = DASHBOARD / "product_home"
DOCS = ROOT / "docs"


class XinyuProductHomeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (DASHBOARD / "home.html").read_text(encoding="utf-8")
        cls.css = (PRODUCT / "home.css").read_text(encoding="utf-8")
        cls.js = (PRODUCT / "home.js").read_text(encoding="utf-8")
        cls.seed = (PRODUCT / "seed_data.js").read_text(encoding="utf-8")
        cls.all_text = "\n".join((cls.html, cls.css, cls.js, cls.seed))

    def test_product_files_and_five_pages_exist(self) -> None:
        for path in (
            DASHBOARD / "home.html",
            PRODUCT / "home.css",
            PRODUCT / "home.js",
            PRODUCT / "seed_data.js",
            PRODUCT / "meeting_summary_2026-07-04.md",
        ):
            self.assertTrue(path.is_file(), path)
        self.assertEqual(
            re.findall(r'data-page="(home|companion|meeting|records|mine)"', self.html),
            ["home", "companion", "meeting", "records", "mine"],
        )
        self.assertIn("/static/product_home/home.css", self.html)
        self.assertIn("/static/product_home/seed_data.js", self.html)
        self.assertIn("/static/product_home/home.js", self.html)

    def test_visual_contract_is_polished_and_scoped(self) -> None:
        self.assertIn(".xinyu-preview-page", self.css)
        for phrase in ("当前情绪", "情绪趋势 · 今天", "小屿建议", "和小屿聊聊天", "心屿设备"):
            self.assertIn(phrase, self.html)
        for phrase in ("我今天有点累", "帮我整理一下今天", "给我一些放松建议", "记录一下我的情绪"):
            self.assertNotIn(f"<button type=\"button\">{phrase}</button>", self.html)
        for token in ("--xy-bg: #F6EFE5", "--xy-surface: #FFFDF8", "--xy-text: #3E332A", "--xy-caramel: #8A6A45"):
            self.assertIn(token, self.css)
        for phrase in ("blink_rate", "calibration", "emotion_probability", "识别概率", "逐字稿"):
            self.assertNotIn(phrase.lower(), self.html.lower())

    def test_product_interfaces_are_wired_with_fallbacks(self) -> None:
        for phrase in (
            'fetch("/api/chat"',
            'apiJSON(`/api/conversations/${encodeURIComponent(conversationId)}`',
            'apiJSON("/api/conversations"',
            'apiJSON("/api/memory"',
            'apiJSON("/api/reflect"',
            'apiJSON("/api/report/weekly"',
            'apiJSON("/api/conversation/start"',
            'apiJSON("/api/meeting/complete"',
            'apiJSON("/api/conversation/state"',
            'apiJSON("/api/meeting/speakers"',
            'fetch(`/api/voice/chat?',
            'apiJSON("/api/voice/stop"',
            'apiJSON("/api/system/health"',
            'apiJSON("/api/voice/state"',
            'apiJSON("/api/voice/announce/settings"',
            'new WebSocket(`${scheme}://${location.host}/ws`)',
            'apiJSON("/api/state"',
        ):
            self.assertIn(phrase, self.js)
        for fallback in ("buildXiaoyuReply", "buildDiaryAssistantReply", "fallback", "showToast"):
            self.assertIn(fallback, self.js)
        for phrase in ("memory_context", "work_context", "day_summary", "data-chat-memory-save", "data-delete-conversation", "buildCompanionPrompts", "renderCompanionPrompts"):
            self.assertIn(phrase, self.js)
        self.assertIn('text.startsWith("接着聊聊我日记里写的")', self.js)
        self.assertNotIn('text.includes("整理") || text.includes("今天")', self.js)

    def test_interactive_controls_exist(self) -> None:
        ids = set(re.findall(r'\bid="([^"]+)"', self.html))
        for expected in (
            "xy-voice-record",
            "xy-voice-stop",
            "xy-voice-status",
            "xy-meeting-start",
            "xy-meeting-complete",
            "xy-meeting-live-status",
            "xy-device-online",
            "xy-device-summary",
            "xy-voice-summary",
            "xy-announce-enabled",
            "xy-chat-history-toggle",
            "xy-chat-history-panel",
            "xy-chat-new",
            "xy-chat-history-list",
            "xy-sedentary-minutes",
            "xy-snooze-minutes",
            "xy-eye-fatigue-enabled",
            "xy-meeting-status-enabled",
        ):
            self.assertIn(expected, ids)
        for phrase in ("MediaRecorder", "getUserMedia", "startMeeting", "completeMeeting", "refreshDeviceState", "saveAnnounceSettings"):
            self.assertIn(phrase, self.js)

    def test_seed_and_meeting_summary_contract(self) -> None:
        for phrase in ("2026-06-01", "2026-07-04", "下半年活动规划与预算申报周会", "meeting-2026-07-04-activity-budget"):
            self.assertIn(phrase, self.seed)
        self.assertIn("meeting_summary_2026-07-04.md", self.seed)
        self.assertIn("meeting_summary_2026-07-04.md", self.all_text)
        self.assertIn("/static/product_home/", self.js)

    def test_routes_serve_product_and_legacy_home(self) -> None:
        import recamera_fastapi as api

        product = asyncio.run(api.serve_home())
        legacy = asyncio.run(api.serve_home_old())
        product_body = product.body.decode("utf-8")
        legacy_body = legacy.body.decode("utf-8")
        self.assertIn("/static/product_home/home.js", product_body)
        self.assertIn("xy-voice-record", product_body)
        self.assertIn("/home-old-static/home_selfcare.js", legacy_body)
        self.assertIn("xinyu-icon-slot", legacy_body)

    def test_sop_documents_product_home_and_backup(self) -> None:
        sop = (DOCS / "home_demo_sop.md").read_text(encoding="utf-8")
        for phrase in (
            "http://localhost:8001/home",
            "http://localhost:8001/home-old",
            "POST /api/chat",
            "POST /api/reflect",
            "POST /api/report/weekly",
            "POST /api/conversation/start",
            "POST /api/voice/chat",
        ):
            self.assertIn(phrase, sop)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for product home syntax validation")
    def test_product_home_javascript_syntax(self) -> None:
        subprocess.run(
            ["node", "--check", str(PRODUCT / "home.js")],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
