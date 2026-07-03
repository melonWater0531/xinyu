"""Server-side per-day chat memory for 小屿 conversations.

Keeps a rolling window of recent turns per local day, persisted to
records/chat/YYYY-MM-DD.json, so the LLM sees real conversation history
instead of client-assembled context strings.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)

MAX_TURNS = 20          # kept in memory / sent to the LLM
MAX_STORED = 200        # kept on disk per day


class ChatMemory:
    def __init__(self, root: str = "records/chat"):
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._date = self._today()
        self._turns: list = []
        self._load(self._date)

    @staticmethod
    def _today() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _path(self, date_key: str) -> Path:
        return self._root / f"{date_key}.json"

    def _load(self, date_key: str) -> None:
        p = self._path(date_key)
        if p.is_file():
            try:
                self._turns = json.loads(p.read_text(encoding="utf-8"))[-MAX_STORED:]
            except Exception as exc:
                logger.warning("ChatMemory load failed: %s", str(exc)[:80])
                self._turns = []

    def _persist(self) -> None:
        try:
            self._path(self._date).write_text(
                json.dumps(self._turns[-MAX_STORED:], ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.warning("ChatMemory persist failed: %s", str(exc)[:80])

    def _rollover_if_needed(self) -> None:
        today = self._today()
        if today != self._date:
            self._persist()
            self._date = today
            self._turns = []
            self._load(today)

    def append(self, role: str, content: str) -> None:
        if not content:
            return
        self._rollover_if_needed()
        self._turns.append({"role": role, "content": str(content)[:2000],
                            "ts": datetime.now().isoformat(timespec="seconds")})
        self._persist()

    def recent_messages(self, limit: int = MAX_TURNS) -> list:
        """Chat-completions style [{role, content}] of the most recent turns."""
        self._rollover_if_needed()
        return [{"role": t["role"], "content": t["content"]}
                for t in self._turns[-limit:]]

    def history(self, date_key: str = "") -> list:
        date_key = date_key or self._today()
        if date_key == self._date:
            return list(self._turns)
        p = self._path(date_key)
        if not p.is_file():
            return []
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return []


chat_memory = ChatMemory()
