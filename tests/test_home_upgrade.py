from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.conversation_store import ConversationStore
from services.emotion_prompt import (
    build_chat_system_prompt,
    build_weekly_report_prompt,
    describe_companion_context,
)


ROOT = Path(__file__).resolve().parents[1]


class HomeUpgradeTests(unittest.TestCase):
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

    def test_demo_sop_covers_current_product_flow(self) -> None:
        sop = (ROOT / "docs" / "home_demo_sop.md").read_text(encoding="utf-8")
        for phrase in (
            "多人场景不判断个人情绪",
            "本地会议样例",
            "会议纪要未实现为可交付能力",
            "http://localhost:8001/home",
            "POST /api/voice/chat",
        ):
            self.assertIn(phrase, sop)
        self.assertNotIn("home-old", sop)
        self.assertNotIn("archive/", sop)


if __name__ == "__main__":
    unittest.main()
