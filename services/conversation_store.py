"""Conversation sessions and shared memory for companion chat.

Persists lightweight JSON files under records/conversations and records/memory.
The store is intentionally small and dependency-free so the dashboard can keep
working in dry-run/dev environments without a database.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)

MAX_RECENT_MESSAGES = 20


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _clip(value, limit: int = 160) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text[:limit]


def classify_conversation(text: str) -> str:
    value = str(text or "")
    groups = [
        ("meeting_followup", ("会议", "纪要", "评审", "参会", "待办")),
        ("work_planning", ("工作", "项目", "预算", "任务", "ddl", "DDL", "方案", "排期")),
        ("relaxation", ("放松", "休息", "冥想", "呼吸", "睡", "散步")),
        ("emotional_support", ("累", "疲惫", "烦", "难过", "焦虑", "压力", "低落", "崩溃", "委屈")),
        ("daily_checkin", ("今天", "整理", "日记", "状态", "回顾")),
    ]
    for category, tokens in groups:
        if any(token in value for token in tokens):
            return category
    return "general_chat"


def category_label(category: str) -> str:
    return {
        "daily_checkin": "今日整理",
        "emotional_support": "情绪陪伴",
        "work_planning": "工作梳理",
        "meeting_followup": "会议跟进",
        "relaxation": "放松休息",
        "general_chat": "随便聊聊",
    }.get(category or "", "随便聊聊")


def summarize_messages(messages: list[dict]) -> str:
    user_texts = [_clip(m.get("content"), 60) for m in messages if m.get("role") == "user" and m.get("content")]
    if not user_texts:
        return "还没有开始细聊。"
    if len(user_texts) == 1:
        return f"聊到：{user_texts[0]}"
    return f"聊到：{user_texts[0]}；后来又提到：{user_texts[-1]}"


def title_from_messages(messages: list[dict], category: str = "") -> str:
    first = next((m for m in messages if m.get("role") == "user" and m.get("content")), None)
    if first:
        return _clip(first.get("content"), 18)
    return category_label(category)


class ConversationStore:
    def __init__(self, root: str = "records/conversations", memory_root: str = "records/memory"):
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._memory_root = Path(memory_root)
        self._memory_root.mkdir(parents=True, exist_ok=True)

    def _session_path(self, conversation_id: str) -> Path:
        safe = "".join(ch for ch in str(conversation_id) if ch.isalnum() or ch in ("-", "_"))
        return self._root / f"{safe}.json"

    @property
    def _memory_path(self) -> Path:
        return self._memory_root / "global.json"

    def _read_session(self, conversation_id: str) -> dict | None:
        path = self._session_path(conversation_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception as exc:
            logger.warning("Conversation load failed: %s", str(exc)[:80])
            return None

    def _write_session(self, data: dict) -> None:
        try:
            self._session_path(data["id"]).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.warning("Conversation persist failed: %s", str(exc)[:80])

    def create(self, title: str = "", category: str = "general_chat") -> dict:
        conversation_id = f"conv_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        now = _now()
        session = {
            "id": conversation_id,
            "started_at": now,
            "updated_at": now,
            "date": _today(),
            "title": _clip(title, 40) or category_label(category),
            "category": category or "general_chat",
            "category_label": category_label(category),
            "summary": "还没有开始细聊。",
            "status": "active",
            "message_count": 0,
            "memory_ids": [],
            "messages": [],
        }
        self._write_session(session)
        return self.compact(session)

    def get(self, conversation_id: str) -> dict | None:
        return self._read_session(conversation_id)

    def compact(self, session: dict) -> dict:
        return {
            "id": session.get("id", ""),
            "started_at": session.get("started_at", ""),
            "updated_at": session.get("updated_at", ""),
            "date": session.get("date", ""),
            "title": session.get("title", ""),
            "category": session.get("category", "general_chat"),
            "category_label": category_label(session.get("category", "general_chat")),
            "summary": session.get("summary", ""),
            "status": session.get("status", "active"),
            "message_count": int(session.get("message_count") or len(session.get("messages") or [])),
            "memory_ids": list(session.get("memory_ids") or []),
        }

    def list(self, limit: int = 30) -> list[dict]:
        sessions = []
        for path in self._root.glob("conv_*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("status") != "deleted":
                    sessions.append(self.compact(data))
            except Exception:
                continue
        sessions.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        return sessions[:limit]

    def messages(self, conversation_id: str) -> list[dict]:
        session = self._read_session(conversation_id)
        if not session:
            return []
        return list(session.get("messages") or [])

    def recent_messages(self, conversation_id: str, limit: int = MAX_RECENT_MESSAGES) -> list[dict]:
        messages = self.messages(conversation_id)[-limit:]
        return [{"role": m.get("role", ""), "content": m.get("content", "")}
                for m in messages if m.get("role") in ("user", "assistant") and m.get("content")]

    def append_message(self, conversation_id: str, role: str, content: str) -> dict | None:
        if not conversation_id or role not in ("user", "assistant") or not content:
            return None
        session = self._read_session(conversation_id)
        if not session:
            return None
        message = {
            "id": f"msg_{uuid.uuid4().hex[:12]}",
            "role": role,
            "content": str(content)[:2000],
            "ts": _now(),
        }
        session.setdefault("messages", []).append(message)
        session["updated_at"] = message["ts"]
        session["message_count"] = len(session.get("messages") or [])
        if role == "user":
            text = " ".join(m.get("content", "") for m in session.get("messages", []) if m.get("role") == "user")
            session["category"] = classify_conversation(text)
        session["category_label"] = category_label(session.get("category", "general_chat"))
        session["title"] = title_from_messages(session.get("messages", []), session.get("category"))
        session["summary"] = summarize_messages(session.get("messages", []))
        self._write_session(session)
        return message

    def ensure(self, conversation_id: str = "") -> dict:
        session = self._read_session(conversation_id) if conversation_id else None
        if session and session.get("status") != "deleted":
            return self.compact(session)
        existing = self.list(limit=1)
        if existing:
            return existing[0]
        return self.create()

    def _read_memories(self) -> list[dict]:
        if not self._memory_path.is_file():
            return []
        try:
            data = json.loads(self._memory_path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception as exc:
            logger.warning("Memory load failed: %s", str(exc)[:80])
            return []

    def _write_memories(self, memories: list[dict]) -> None:
        try:
            self._memory_path.write_text(json.dumps(memories[-300:], ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.warning("Memory persist failed: %s", str(exc)[:80])

    def memories(self, include_deleted: bool = False) -> list[dict]:
        values = self._read_memories()
        if include_deleted:
            return values
        return [m for m in values if not m.get("deleted_at")]

    def add_memory(self, content: str, conversation_id: str = "", message_ids: list[str] | None = None,
                   source: str = "companion") -> dict | None:
        text = _clip(content, 220)
        if not text:
            return None
        memories = self._read_memories()
        memory = {
            "id": f"mem_{uuid.uuid4().hex[:12]}",
            "content": text,
            "source": source or "companion",
            "source_conversation_ids": [conversation_id] if conversation_id else [],
            "source_message_ids": list(message_ids or []),
            "tags": [],
            "confidence": "user_confirmed",
            "created_at": _now(),
            "deleted_at": "",
        }
        memories.append(memory)
        self._write_memories(memories)
        if conversation_id:
            session = self._read_session(conversation_id)
            if session:
                ids = set(session.get("memory_ids") or [])
                ids.add(memory["id"])
                session["memory_ids"] = list(ids)
                session["updated_at"] = _now()
                self._write_session(session)
        return memory

    def delete_memory(self, memory_id: str) -> bool:
        memories = self._read_memories()
        changed = False
        for memory in memories:
            if memory.get("id") == memory_id and not memory.get("deleted_at"):
                memory["deleted_at"] = _now()
                changed = True
        if changed:
            self._write_memories(memories)
        return changed

    def delete_conversation(self, conversation_id: str) -> dict:
        session = self._read_session(conversation_id)
        if not session:
            return {"ok": False, "deleted_messages": 0, "deleted_memories": 0}
        session["status"] = "deleted"
        session["deleted_at"] = _now()
        self._write_session(session)

        memories = self._read_memories()
        deleted_memories = 0
        for memory in memories:
            ids = list(memory.get("source_conversation_ids") or [])
            if conversation_id not in ids or memory.get("deleted_at"):
                continue
            remaining = [item for item in ids if item != conversation_id]
            if remaining:
                memory["source_conversation_ids"] = remaining
            else:
                memory["deleted_at"] = _now()
                deleted_memories += 1
        self._write_memories(memories)
        return {
            "ok": True,
            "conversation_id": conversation_id,
            "deleted_messages": len(session.get("messages") or []),
            "deleted_memories": deleted_memories,
        }


conversation_store = ConversationStore()
