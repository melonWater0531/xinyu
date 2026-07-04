from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

from services.emotion_prompt import build_chat_system_prompt, build_weekly_report_prompt


ROOT = Path(__file__).resolve().parents[1]


class HomeUpgradeTests(unittest.TestCase):
    def test_home_selfcare_dom_and_assets_are_complete(self) -> None:
        home = (ROOT / "dashboard" / "home.html").read_text(encoding="utf-8")
        ids = set(re.findall(r'\bid="([^"]+)"', home))
        for expected in (
            "selfcare-overview", "selfcare-mood", "selfcare-progress-fill", "selfcare-steps",
            "selfcare-water-add", "selfcare-walk-add", "selfcare-breathing-add", "rec-week-source",
        ):
            self.assertIn(expected, ids)
        for asset in ("home_selfcare.js", "home_selfcare_ui.js", "home_selfcare.css", "home_emotion_chat.js", "home_weekly_report.js"):
            self.assertIn(asset, home)
        ui = (ROOT / "dashboard" / "home_selfcare_ui.js").read_text(encoding="utf-8")
        self.assertEqual(set(re.findall(r'\$\("([^"]+)"\)', ui)) - ids, set())
        self.assertIn('role="progressbar"', home)
        self.assertIn('aria-live="polite"', home)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for Home JavaScript smoke tests")
    def test_selfcare_repository_and_local_fallbacks(self) -> None:
        script = r"""
const fs=require('fs'),vm=require('vm');
const values=new Map();
global.localStorage={getItem:k=>values.has(k)?values.get(k):null,setItem:(k,v)=>values.set(k,String(v)),removeItem:k=>values.delete(k)};
for(const file of ['dashboard/home_selfcare.js','dashboard/home_emotion_chat.js','dashboard/home_weekly_report.js'])vm.runInThisContext(fs.readFileSync(file,'utf8'));
const repo=new XinyuSelfCare.SelfCareRepository();
if(repo.getWeek(new Date(2026,6,3,12)).length!==7)throw Error('week');
repo.incrementWater(new Date(2026,6,3,12));repo.recordWalk(new Date(2026,6,3,12));repo.recordBreathing(new Date(2026,6,3,12));
const today=repo.getToday(new Date(2026,6,3,12));
if(today.water.source!=='local'||today.exercise.source!=='local'||today.breathing.source!=='local')throw Error('source');
if(new XinyuSelfCare.NativeBridgeSelfCareProvider().getDay('2026-07-03')!==null)throw Error('native');
const reply=XinyuEmotionChat.fallbackReply('我有点难受');if(reply.length<40||reply.length>90||reply.includes('快乐'))throw Error('chat');
const care=repo.getWeek(new Date(2026,6,3,12));const entries=Array.from({length:4},(_,i)=>({date:`2026-07-0${i+1}`,emotion:'Neutral'}));
const report=XinyuWeeklyReport.buildFallback(entries,care);if(report.content.length<120||report.content.length>180)throw Error('report');
if(XinyuWeeklyReport.normalizeContent('太短',report.content)!==report.content)throw Error('report fallback');
"""
        subprocess.run(["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True)

    def test_chat_and_weekly_prompts_prioritize_user_text(self) -> None:
        chat = build_chat_system_prompt({"emotieff": {"emotion": "Happiness", "confidence": 0.99}}, "用户")
        self.assertIn("用户本轮自述 > 用户日记和近期聊天 > 摄像头视觉线索", chat)
        self.assertIn("只跟随用户文字", chat)
        entries = [{
            "date": "2026-07-01", "emotion": "Sadness", "content": "我今天有点难受",
            "observed_emotion": "Happiness", "selfcare_week": [{
                "date": "2026-07-01", "source": "demo", "steps": {"value": 5000},
                "water": {"cups": 5}, "exercise": {"sessions": []}, "breathing": {"sessions": []},
            }],
        }]
        messages = build_weekly_report_prompt(entries, [], "用户")
        prompt = "\n".join(message["content"] for message in messages)
        self.assertIn("我今天有点难受", prompt)
        self.assertIn("步数5000", prompt)
        self.assertIn("演示数据必须明确", prompt)
        self.assertIn("不要标题，不要列表", prompt)

    def test_demo_sop_covers_recording_flow(self) -> None:
        sop = (ROOT / "docs" / "home_demo_sop.md").read_text(encoding="utf-8")
        for phrase in ("Self-care 数据来源", "PWA 当前可实现能力", "APK 后续接口预留", "推荐视频录制脚本", "记录一次散步", "今天有点累"):
            self.assertIn(phrase, sop)


if __name__ == "__main__":
    unittest.main()
