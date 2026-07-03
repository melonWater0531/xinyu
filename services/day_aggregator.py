"""Per-day aggregation of observed emotion / attention for diary linkage.

Fed once per state-push tick with the smoothed, confidence-gated emotion.
Buckets by LOCAL hour so keys line up with the dashboard's dateKey().
Persists to records/day_summary/YYYY-MM-DD.json every few minutes and at
day rollover; keeps a bounded retention window.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)

RETENTION_DAYS = 14
PERSIST_INTERVAL_S = 300.0
VALENCE_EMA_TAU_S = 600.0     # ~10-minute EMA for dip detection
DIP_ENTER = -0.25             # EMA below this -> dip event
DIP_EXIT = -0.10              # EMA back above this -> dip over
MIN_EMOTION_CONF = 0.35


def _empty_hour() -> dict:
    return {"emotions": {}, "attention_sum": 0.0, "attention_n": 0, "presence_sec": 0.0}


class DayAggregator:
    def __init__(self, root: str = "records/day_summary"):
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._date = self._today_key()
        self._hours: dict = {}
        self._events: list = []
        self._valence_ema = None
        self._dip_active = False
        self._intervention_flag = False
        self._last_tick_ts = 0.0
        self._last_persist = 0.0
        self._load(self._date)

    @staticmethod
    def _today_key() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    # ── persistence ─────────────────────────────────────────

    def _path(self, date_key: str) -> Path:
        return self._root / f"{date_key}.json"

    def _load(self, date_key: str) -> None:
        p = self._path(date_key)
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                self._hours = data.get("hours", {})
                self._events = data.get("events", [])
                logger.info("DayAggregator resumed %s (%d hour buckets)", date_key, len(self._hours))
            except Exception as exc:
                logger.warning("DayAggregator load failed: %s", str(exc)[:80])

    def persist(self) -> None:
        try:
            payload = {"date": self._date, "hours": self._hours, "events": self._events,
                       "updated_at": datetime.now().isoformat(timespec="seconds")}
            self._path(self._date).write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            self._last_persist = time.time()
        except Exception as exc:
            logger.warning("DayAggregator persist failed: %s", str(exc)[:80])

    def _cleanup(self) -> None:
        cutoff = (datetime.now() - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%d")
        try:
            for f in self._root.glob("*.json"):
                if f.stem < cutoff:
                    f.unlink()
        except Exception:
            pass

    def _rollover(self) -> None:
        self.persist()
        self._date = self._today_key()
        self._hours = {}
        self._events = []
        self._valence_ema = None
        self._dip_active = False
        self._intervention_flag = False
        self._cleanup()

    # ── ingestion ───────────────────────────────────────────

    def update(self, emotion: str = "", confidence: float = 0.0, valence=None,
               attention=None, fatigue_level: str = "",
               intervention_active: bool = False, has_face: bool = False) -> None:
        now = datetime.now()
        if now.strftime("%Y-%m-%d") != self._date:
            self._rollover()

        ts = now.timestamp()
        dt = min(ts - self._last_tick_ts, 2.0) if self._last_tick_ts else 0.25
        self._last_tick_ts = ts

        if has_face:
            h = self._hours.setdefault(str(now.hour), _empty_hour())
            h["presence_sec"] = round(h["presence_sec"] + dt, 1)
            if emotion and confidence >= MIN_EMOTION_CONF:
                h["emotions"][emotion] = h["emotions"].get(emotion, 0) + 1
            if attention is not None:
                h["attention_sum"] += float(attention)
                h["attention_n"] += 1
            if valence is not None:
                alpha = min(1.0, dt / VALENCE_EMA_TAU_S)
                self._valence_ema = (float(valence) if self._valence_ema is None
                                     else (1 - alpha) * self._valence_ema + alpha * float(valence))
                if not self._dip_active and self._valence_ema < DIP_ENTER:
                    self._dip_active = True
                    self._events.append({"ts": now.strftime("%H:%M"), "type": "valence_dip",
                                         "detail": round(self._valence_ema, 2)})
                elif self._dip_active and self._valence_ema > DIP_EXIT:
                    self._dip_active = False

        # Edge-trigger intervention events
        if intervention_active and not self._intervention_flag:
            self._events.append({"ts": now.strftime("%H:%M"), "type": "intervention", "detail": ""})
        self._intervention_flag = bool(intervention_active)

        if time.time() - self._last_persist > PERSIST_INTERVAL_S:
            self.persist()

    # ── queries ─────────────────────────────────────────────

    def summary(self, date_key: str = "") -> dict:
        date_key = date_key or self._date
        if date_key == self._date:
            hours, events = self._hours, self._events
        else:
            p = self._path(date_key)
            if not p.is_file():
                return {"date": date_key, "available": False}
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                hours, events = data.get("hours", {}), data.get("events", [])
            except Exception:
                return {"date": date_key, "available": False}

        emotion_totals: dict = {}
        attention_sum = 0.0
        attention_n = 0
        presence_sec = 0.0
        hours_out = []
        for hr in sorted(hours, key=int):
            h = hours[hr]
            for emo, c in h.get("emotions", {}).items():
                emotion_totals[emo] = emotion_totals.get(emo, 0) + c
            attention_sum += h.get("attention_sum", 0.0)
            attention_n += h.get("attention_n", 0)
            presence_sec += h.get("presence_sec", 0.0)
            dom = max(h.get("emotions", {}), key=h["emotions"].get) if h.get("emotions") else ""
            hours_out.append({
                "hour": int(hr),
                "dominant_emotion": dom,
                "attention_avg": round(h["attention_sum"] / h["attention_n"], 1) if h.get("attention_n") else None,
                "presence_sec": round(h.get("presence_sec", 0.0)),
            })

        total_emo = sum(emotion_totals.values())
        dominant = max(emotion_totals, key=emotion_totals.get) if emotion_totals else ""
        return {
            "date": date_key,
            "available": bool(hours_out),
            "hours": hours_out,
            "dominant_emotion": dominant,
            "dominant_confidence": round(emotion_totals.get(dominant, 0) / total_emo, 2) if total_emo else 0.0,
            "emotion_counts": emotion_totals,
            "attention_avg": round(attention_sum / attention_n, 1) if attention_n else None,
            "presence_min": round(presence_sec / 60.0, 1),
            "dips": [e for e in events if e.get("type") == "valence_dip"],
            "intervention_count": sum(1 for e in events if e.get("type") == "intervention"),
        }

    def range_summaries(self, start_key: str, end_key: str) -> list:
        out = []
        try:
            cur = datetime.strptime(start_key, "%Y-%m-%d")
            end = datetime.strptime(end_key, "%Y-%m-%d")
        except ValueError:
            return out
        while cur <= end and len(out) < 31:
            out.append(self.summary(cur.strftime("%Y-%m-%d")))
            cur += timedelta(days=1)
        return out


day_aggregator = DayAggregator()
