from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from services.conversation_store import ConversationStore
from services.emotion_prompt import build_chat_system_prompt, build_weekly_report_prompt, describe_companion_context


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard"
LEGACY_HOME = ROOT / "archive" / "dashboard_cleanup_20260705" / "home_legacy"


class HomeUpgradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.home = (LEGACY_HOME / "home_legacy.html").read_text(encoding="utf-8")
        cls.css = (LEGACY_HOME / "home_selfcare.css").read_text(encoding="utf-8")
        cls.ui = (LEGACY_HOME / "home_selfcare_ui.js").read_text(encoding="utf-8")
        cls.ids = set(re.findall(r'\bid="([^"]+)"', cls.home))

    def test_home_structure_has_meditation_without_overview(self) -> None:
        self.assertNotIn('id="selfcare-overview"', self.home)
        for expected in (
            "selfcare-steps", "selfcare-water-add", "selfcare-walk-add",
            "selfcare-meditation", "selfcare-meditation-open", "meditation-dialog",
            "meditation-start", "mood-trend-chart", "rec-week-source",
        ):
            self.assertIn(expected, self.ids)
        for asset in (
            "home_selfcare.js", "home_selfcare_ui.js", "home_selfcare.css",
            "home_emotion_chat.js", "home_weekly_report.js", "home_mood_trend.js",
        ):
            self.assertIn(asset, self.home)
        self.assertEqual(set(re.findall(r'\$\("([^"]+)"\)', self.ui)) - self.ids, set())
        for option in ("3 分钟", "5 分钟", "10 分钟", "15 分钟", "雨声", "森林", "白噪音", "无音乐"):
            self.assertIn(option, self.home)

    def test_css_slots_replace_decorative_text_icons(self) -> None:
        self.assertIn(".xinyu-icon-slot", self.css)
        for variant in ("companion", "meeting", "diary", "device", "steps", "water", "walk", "meditation", "mood", "focus"):
            self.assertIn(f".xinyu-icon-slot--{variant}", self.css)
        slot_contents = re.findall(r'<span[^>]*class="[^"]*xinyu-icon-slot[^"]*"[^>]*>(.*?)</span>', self.home, re.S)
        self.assertTrue(slot_contents)
        self.assertTrue(all(not re.sub(r"\s+", "", content) for content in slot_contents))
        for emoji in ("😊", "😌", "😢", "😠", "😟", "😮", "🤢", "😏", "💛", "👥"):
            self.assertNotIn(emoji, self.home)
        self.assertNotIn("dashboard/assets/icons/xinyu", self.home + self.css)
        self.assertNotRegex(self.css, r"url\([^)]*\.(?:png|webp|svg)")
        self.assertEqual(re.findall(r'<img[^>]+src="([^"]+)"', self.home), ["/home-old-static/island_cutout.png"])

    def test_product_palette_is_scoped(self) -> None:
        for color in ("#F8F1E6", "#FFFDF8", "#FFF1C8", "#4A3A2B", "#6F6258", "#A49382", "#8A6A45", "#7D9BD6", "#A8CFA0"):
            self.assertIn(color, self.css)
        self.assertIn("#page-home", self.css)
        self.assertIn("#page-companion", self.css)
        self.assertIn("#page-records", self.css)
        self.assertNotIn("#000", self.css.lower())
        self.assertNotRegex(self.css, r"(?m)^\s*:root\s*\{")

    def test_wellbeing_focus_and_gesture_are_productized(self) -> None:
        self.assertNotIn('id="comp-ask-advice"', self.home)
        self.assertIn('id="comp-care-text"', self.home)
        self.assertIn("buildProactiveCareSuggestion", (LEGACY_HOME / "home_selfcare.js").read_text(encoding="utf-8"))
        focus = self.home[self.home.index('class="card comp-focus-card"'):self.home.index('id="comp-chat-thread"')]
        for engineering_term in ("EAR", "闪烁率", "校准", "comp-ear", "comp-blink", "comp-fatigue"):
            self.assertNotIn(engineering_term, focus)
        self.assertIn("等待观察", focus)
        self.assertNotIn("<h2>手势</h2>", self.home)
        self.assertIn("App.state?.gesture", self.home, "底层手势状态读取应保留")

    def test_record_page_contains_lightweight_mood_trend(self) -> None:
        trend = (LEGACY_HOME / "home_mood_trend.js").read_text(encoding="utf-8")
        self.assertIn("buildMoodTrendPointsForDate", trend)
        self.assertIn("<svg", trend)
        self.assertIn("polyline", trend)
        self.assertIn("今天还没有明显转折点", trend)
        self.assertNotIn("chart.js", self.home.lower())
        self.assertNotIn("canvas", trend.lower())
        self.assertIn("今日情绪趋势", self.home)
        self.assertIn("只记录明显转折点", self.home)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for Home JavaScript smoke tests")
    def test_selfcare_suggestions_and_mood_trend_smoke(self) -> None:
        script = r"""
const fs=require('fs'),vm=require('vm');
const values=new Map();
global.localStorage={getItem:k=>values.has(k)?values.get(k):null,setItem:(k,v)=>values.set(k,String(v)),removeItem:k=>values.delete(k)};
for(const file of ['archive/dashboard_cleanup_20260705/home_legacy/home_selfcare.js','archive/dashboard_cleanup_20260705/home_legacy/home_emotion_chat.js','archive/dashboard_cleanup_20260705/home_legacy/home_weekly_report.js','archive/dashboard_cleanup_20260705/home_legacy/home_mood_trend.js'])vm.runInThisContext(fs.readFileSync(file,'utf8'));
const anchor=new Date(2026,6,3,12);const repo=new XinyuSelfCare.SelfCareRepository();
if(repo.getWeek(anchor).length!==7)throw Error('week');
repo.incrementWater(anchor);repo.recordWalk(anchor);repo.recordMeditation(anchor,10,'forest');
const today=repo.getToday(anchor);
if(today.water.source!=='local'||today.exercise.source!=='local'||today.meditation.source!=='local')throw Error('source');
if(today.meditation.sessions.at(-1).music!=='forest')throw Error('music');
values.clear();values.set('xinyu.selfcare.v1',JSON.stringify({'2026-07-03':{date:'2026-07-03',source:'local',breathing:{source:'local',sessions:[{minutes:3,source:'local'}]}}}));
const legacy=new XinyuSelfCare.SelfCareRepository().getToday(anchor);if(legacy.meditation.sessions[0].minutes!==3)throw Error('legacy');
const base={water:{cups:1,goal:8},steps:{value:1000},exercise:{sessions:[]},meditation:{sessions:[]}};
const multi=XinyuSelfCare.buildProactiveCareSuggestion(base,{}, {},{multiScene:true});if(!multi.includes('不判断'))throw Error('multi');
const low=XinyuSelfCare.buildProactiveCareSuggestion(base,{selfReport:'我有点难受'});if(!low.includes('不好受'))throw Error('self report');
const water=XinyuSelfCare.buildProactiveCareSuggestion(base,{});if(!water.includes('水喝得有点少'))throw Error('water');
const steps=XinyuSelfCare.buildProactiveCareSuggestion({...base,water:{cups:6,goal:8}},{});if(!steps.includes('走动'))throw Error('steps');
const rows=[{date:'2026-07-03',created_at:'2026-07-03T09:00:00',emotion:'Neutral',content:'平静'},{date:'2026-07-03',created_at:'2026-07-03T10:00:00',emotion:'Calm'},{date:'2026-07-03',created_at:'2026-07-03T12:30:00',emotion:'Sadness',content:'有点累'}];
const points=XinyuMoodTrend.buildMoodTrendPointsForDate('2026-07-03',rows,[],false);if(points.length!==2||points[1].time!=='12:30')throw Error('turns');
if(XinyuMoodTrend.buildMoodTrendPointsForDate('2026-07-04',[],[],true).length!==4)throw Error('demo');
const reply=XinyuEmotionChat.fallbackReply('我有点难受');if(reply.length<40||reply.length>90||reply.includes('快乐'))throw Error('chat');
const report=XinyuWeeklyReport.buildFallback(Array.from({length:4},(_,i)=>({date:`2026-07-0${i+1}`,emotion:'Neutral'})),repo.getWeek(anchor));if(report.content.length<120||report.content.length>180)throw Error('report');
"""
        subprocess.run(["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True)

    def test_chat_and_weekly_prompts_prioritize_user_text(self) -> None:
        chat = build_chat_system_prompt({"emotieff": {"emotion": "Happiness", "confidence": 0.99}}, "用户")
        self.assertIn("用户本轮自述 > 用户日记和近期聊天 > 摄像头视觉线索", chat)
        self.assertIn("只跟随用户文字", chat)
        self.assertIn("用户主动确认保存的记忆", chat)
        self.assertIn("区分倾诉和求助", chat)
        companion_context = describe_companion_context({
            "memory_context": {"confirmed_notes": [{"date": "2026-07-08", "content": "用户这周在赶预算表。"}]},
            "day_summary": {"main_state": "疲惫为主", "trend_text": "09:00平静，18:00疲惫", "care_text": "喝水3杯"},
            "work_context": {"current_meeting_title": "预算评审", "current_meeting_summary": "需要整理申报表。"},
        })
        self.assertIn("用户主动保存的记忆", companion_context)
        self.assertIn("预算表", companion_context)
        self.assertIn("今日状态参考", companion_context)
        self.assertIn("工作与会议参考", companion_context)
        entries = [{
            "date": "2026-07-01", "emotion": "Sadness", "content": "我今天有点难受",
            "observed_emotion": "Happiness", "selfcare_week": [{
                "date": "2026-07-01", "source": "demo", "steps": {"value": 5000},
                "water": {"cups": 5}, "exercise": {"sessions": []}, "breathing": {"sessions": []},
            }],
        }]
        prompt = "\n".join(message["content"] for message in build_weekly_report_prompt(entries, [], "用户"))
        self.assertIn("我今天有点难受", prompt)
        self.assertIn("步数5000", prompt)
        self.assertIn("演示数据必须明确", prompt)

    def test_conversation_store_sessions_memory_and_delete_cascade(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ConversationStore(root=str(Path(tmp) / "conversations"), memory_root=str(Path(tmp) / "memory"))
            session = store.create()
            store.append_message(session["id"], "user", "我这周在赶预算表，压力有点大")
            store.append_message(session["id"], "assistant", "预算表这件事确实压着你。")
            detail = store.get(session["id"])
            self.assertEqual(detail["category"], "work_planning")
            self.assertIn("预算表", detail["summary"])
            memory = store.add_memory("用户这周在赶预算表。", conversation_id=session["id"])
            self.assertTrue(memory["id"])
            self.assertEqual(len(store.memories()), 1)
            deleted = store.delete_conversation(session["id"])
            self.assertTrue(deleted["ok"])
            self.assertEqual(deleted["deleted_memories"], 1)
            self.assertEqual(store.memories(), [])

    def test_demo_sop_covers_polished_flow(self) -> None:
        sop = (ROOT / "docs" / "home_demo_sop.md").read_text(encoding="utf-8")
        for phrase in ("冥想当前不播放真实音频", "本地规则", "多人场景不判断个人情绪", "明显转折点", "icon slot", "没有生成真实图标文件"):
            self.assertIn(phrase, sop)


if __name__ == "__main__":
    unittest.main()
