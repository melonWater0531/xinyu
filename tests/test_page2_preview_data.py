from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE2 = ROOT / "dashboard" / "page2_preview"


def load_seed_data() -> dict:
    if not shutil.which("node"):
        raise unittest.SkipTest("Node.js is required to evaluate page2 preview seed data")
    script = """
const fs = require("fs");
const vm = require("vm");
const ctx = { window: {} };
vm.runInNewContext(fs.readFileSync("dashboard/page2_preview/data/xinyu_seed_data.js", "utf8"), ctx);
const d = ctx.window.XINYU_PREVIEW_DATA;
console.log(JSON.stringify({
  currentDate: d.currentDate,
  dayKeys: Object.keys(d.dailyRecords).sort(),
  weeklyKeys: Object.keys(d.weeklyReports).sort(),
  meetings: d.meetings,
  dailyRecords: d.dailyRecords,
  weeklyReports: d.weeklyReports,
  assistantMemory: d.assistantMemory,
  recentDiaryCount: d.getRecentDiaryEntries(5).length,
  weeklyHistoryCount: d.renderWeeklyReportHistory().length
}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class Page2PreviewDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (PAGE2 / "index.html").read_text(encoding="utf-8")
        cls.app = (PAGE2 / "app.js").read_text(encoding="utf-8")
        cls.css = (PAGE2 / "styles.css").read_text(encoding="utf-8")
        cls.seed = (PAGE2 / "data" / "xinyu_seed_data.js").read_text(encoding="utf-8")
        cls.minutes = (PAGE2 / "data" / "meeting_summary_2026-07-04.md").read_text(encoding="utf-8")
        cls.data = load_seed_data()

    def test_data_files_are_present_and_loaded_before_app(self) -> None:
        self.assertTrue((PAGE2 / "data").is_dir())
        self.assertTrue((PAGE2 / "data" / "xinyu_seed_data.js").is_file())
        self.assertTrue((PAGE2 / "data" / "meeting_summary_2026-07-04.md").is_file())
        self.assertLess(self.html.index("./data/xinyu_seed_data.js"), self.html.index("./app.js"))

    def test_meeting_summary_is_complete_minutes_only(self) -> None:
        for phrase in ("会议摘要", "核心活动规划与分工", "新增任务与协作", "待办事项"):
            self.assertIn(phrase, self.minutes)
        self.assertGreater(len(self.minutes), 900)
        self.assertNotIn("逐字稿", self.minutes + self.seed + self.html)

    def test_daily_records_cover_june_first_to_july_fourth(self) -> None:
        days = self.data["dayKeys"]
        self.assertEqual(days[0], "2026-06-01")
        self.assertEqual(days[-1], "2026-07-04")
        self.assertEqual(len(days), 34)
        for key in days:
            day = self.data["dailyRecords"][key]
            for field in ("emotionTrend", "focusDisplay", "steps", "waterCups", "waterGoal", "meditation", "hadMeeting", "diary", "assistantReply", "weekKey"):
                self.assertIn(field, day)
            self.assertGreaterEqual(len(day["emotionTrend"]), 3)
            self.assertLessEqual(len(day["emotionTrend"]), 5)
            self.assertGreaterEqual(day["steps"], 2200)
            self.assertLessEqual(day["steps"], 9200)
            self.assertGreaterEqual(day["waterCups"], 3)
            self.assertLessEqual(day["waterCups"], 8)

    def test_july_fourth_links_to_required_meeting(self) -> None:
        day = self.data["dailyRecords"]["2026-07-04"]
        self.assertEqual(day["meetingId"], "meeting-2026-07-04-activity-budget")
        self.assertEqual(day["meetingTitle"], "下半年活动规划与预算申报周会")
        self.assertEqual(day["steps"], 3200)
        self.assertEqual(day["waterCups"], 5)
        self.assertFalse(day["meditation"])
        self.assertIn("预算申报", day["diary"])

    def test_weekly_reports_and_assistant_memory_exist(self) -> None:
        self.assertEqual(self.data["weeklyKeys"], ["2026-W23", "2026-W24", "2026-W25", "2026-W26", "2026-W27"])
        self.assertEqual(self.data["weeklyHistoryCount"], 5)
        self.assertIn("活动规划与预算申报", self.data["weeklyReports"]["2026-W27"]["summary"])
        memory = self.data["assistantMemory"]
        self.assertEqual(memory["currentDate"], "2026-07-04")
        self.assertEqual(memory["currentDay"]["meetingId"], "meeting-2026-07-04-activity-budget")
        self.assertIn("下半年活动规划与预算申报周会", memory["currentMeeting"]["title"])
        self.assertGreaterEqual(self.data["recentDiaryCount"], 5)

    def test_preview_meeting_path_does_not_call_real_recorder_apis(self) -> None:
        combined = "\n".join((self.html, self.app, self.seed))
        for forbidden in ("/api/multi_track/start", "/api/multi_track/stop", "/api/meeting/summarize", "/api/conversation", "/api/chat", "/api/reflect", "/api/gimbal", "sendBeacon"):
            self.assertNotIn(forbidden, combined)

    def test_productized_copy_has_no_public_placeholder_words_or_status_bar(self) -> None:
        visible_sources = "\n".join((self.html, self.app, self.css))
        for forbidden in (
            "产品预览", "演示数据", "前端演示", "不会启动真实录音", "只记录明显转折点",
            "demo", "Demo", "DEMO", "mock", "fake", "sample data", "debug",
            "9:41", "status-bar", "signal", "battery",
        ):
            self.assertNotIn(forbidden, visible_sources)

    def test_home_companion_records_and_meeting_ui_hooks_exist(self) -> None:
        combined = "\n".join((self.html, self.app))
        for phrase in (
            "情绪趋势 · 今天",
            "小屿帮你整理了今天的状态变化",
            "buildAssistantMemoryContext",
            "buildXiaoyuReply",
            "quick-chat",
            "openMeetingDetail",
            "loadMeetingMinutesMarkdown",
            "openDiaryModal",
            "saveDiaryFromDialog",
            "openWeeklyReportModal",
            "renderDiaryHistory",
            "renderWeeklyHistory",
        ):
            self.assertIn(phrase, combined)
        self.assertIn("meeting_summary_2026-07-04.md", self.seed)
        self.assertNotIn("逐字稿", combined)
        self.assertNotIn("focusScore", self.html)

    def test_profile_device_card_is_text_only_and_keeps_status(self) -> None:
        mine = self.html[self.html.index('data-page="mine"'):]
        for phrase in ("设备状态", "心屿设备", "在线 · 电量 85%"):
            self.assertIn(phrase, mine + self.app)
        for forbidden in ("device-blob", "device-visual", "abstract-device", "<svg"):
            self.assertNotIn(forbidden, mine)


if __name__ == "__main__":
    unittest.main()
