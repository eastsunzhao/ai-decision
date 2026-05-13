from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.logging_config import project_root

logger = logging.getLogger("ai_decision.core.session_store")


class SessionStore:
    def __init__(self, path: Optional[Path] = None, max_sessions: int = 300):
        self.path = path or project_root() / "data" / "sessions.json"
        self.max_sessions = max_sessions
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"sessions": []})

    def list_sessions(self) -> List[Dict[str, Any]]:
        payload = self._read()
        sessions = payload.get("sessions", [])
        return [
            {
                "session_id": item.get("session_id"),
                "title": item.get("title"),
                "scenario_id": item.get("scenario_id"),
                "query": item.get("query"),
                "created_at": item.get("created_at"),
                "confidence": item.get("response", {}).get("confidence"),
            }
            for item in sessions
        ]

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        payload = self._read()
        for item in payload.get("sessions", []):
            if item.get("session_id") == session_id:
                return item
        return None

    def delete_session(self, session_id: str) -> bool:
        payload = self._read()
        sessions = payload.get("sessions", [])
        remaining = [item for item in sessions if item.get("session_id") != session_id]
        if len(remaining) == len(sessions):
            return False
        payload["sessions"] = remaining
        self._write(payload)
        logger.info("session_deleted session_id=%s total_sessions=%s", session_id, len(remaining))
        return True

    def save_session(self, scenario_id: str, query: str, response: Dict[str, Any]) -> Dict[str, Any]:
        payload = self._read()
        sessions = payload.get("sessions", [])
        session = {
            "session_id": str(uuid.uuid4()),
            "title": self._title(query),
            "scenario_id": scenario_id,
            "query": query,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "response": response,
        }
        sessions.insert(0, session)
        payload["sessions"] = sessions[: self.max_sessions]
        self._write(payload)
        logger.info("session_saved session_id=%s scenario_id=%s total_sessions=%s", session["session_id"], scenario_id, len(payload["sessions"]))
        return session

    def _read(self) -> Dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("session_store_read_failed path=%s", self.path)
            return {"sessions": []}

    def _write(self, payload: Dict[str, Any]) -> None:
        tmp_path = self.path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self.path)

    def _title(self, query: str) -> str:
        normalized = " ".join(query.split())
        return normalized[:28] + ("..." if len(normalized) > 28 else "")
