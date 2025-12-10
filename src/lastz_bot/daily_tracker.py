"""Seguimiento diario de tareas completadas por granja."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from rich.console import Console


GATHER_SLOT_TASK_PREFIX = "gather_slot_"


class DailyTaskTracker:
    """Persiste qué tareas se han completado por granja durante el día vigente."""

    def __init__(
        self,
        storage_path: Path,
        reset_hour_local: int,
        task_limits: Mapping[str, int],
        console: Console,
    ) -> None:
        self.console = console
        self.storage_path = storage_path if storage_path.is_absolute() else Path.cwd() / storage_path
        self.reset_hour_local = max(0, min(23, reset_hour_local))
        self.task_limits = {task.strip(): max(1, limit) for task, limit in task_limits.items() if task.strip()}
        self._state: Dict[str, Dict[str, Dict[str, Any]]] = self._load_state()

    def should_skip(self, farm_name: str, task_name: str, now: Optional[datetime] = None) -> bool:
        """Indica si una tarea debe saltarse porque ya alcanzó su límite diario."""
        if task_name not in self.task_limits:
            return False
        entry = self._current_entry(farm_name, task_name, now)
        if not entry:
            return False
        if self._uses_boolean(task_name):
            return bool(entry.get("completed"))
        limit = self.task_limits[task_name]
        return int(entry.get("count", 0)) >= limit

    def mark_done(self, farm_name: str, task_name: str, now: Optional[datetime] = None) -> None:
        """Marca una tarea como completada (modo booleano)."""
        self.record_progress(farm_name, task_name, amount=1, now=now)

    def current_count(self, farm_name: str, task_name: str, now: Optional[datetime] = None) -> int:
        """Devuelve cuántas veces se ha registrado la tarea en el día actual."""
        entry = self._current_entry(farm_name, task_name, now)
        if not entry:
            return 0
        if self._uses_boolean(task_name):
            return 1 if entry.get("completed") else 0
        try:
            return int(entry.get("count", 0))
        except (TypeError, ValueError):
            return 0

    def is_flag_set(
        self,
        farm_name: str,
        task_name: str,
        flag_name: str,
        now: Optional[datetime] = None,
    ) -> bool:
        """Consulta flags auxiliares asociados a una tarea diaria."""
        entry = self._current_entry(farm_name, task_name, now)
        if not entry:
            return False
        return bool(entry.get(flag_name))

    def set_flag(
        self,
        farm_name: str,
        task_name: str,
        flag_name: str,
        value: bool = True,
        now: Optional[datetime] = None,
    ) -> None:
        """Actualiza un flag booleano adosado a una tarea."""
        if task_name not in self.task_limits:
            return
        now = now or datetime.now()
        entry = self._ensure_entry(farm_name, task_name, now)
        entry["timestamp"] = now.isoformat()
        entry[flag_name] = bool(value)
        self._save_state()

    def set_metadata(
        self,
        farm_name: str,
        task_name: str,
        key: str,
        value: Any,
        now: Optional[datetime] = None,
    ) -> None:
        """Guarda metadatos arbitrarios (por ejemplo timestamps o contadores)."""
        if task_name not in self.task_limits or not key:
            return
        now = now or datetime.now()
        entry = self._ensure_entry(farm_name, task_name, now)
        entry["timestamp"] = now.isoformat()
        metadata = entry.setdefault("metadata", {})
        metadata[key] = value
        self._save_state()

    def get_metadata(
        self,
        farm_name: str,
        task_name: str,
        key: str,
        now: Optional[datetime] = None,
    ) -> Any:
        """Recupera metadatos previamente almacenados para una tarea."""
        if not key:
            return None
        entry = self._current_entry(farm_name, task_name, now)
        if not entry:
            return None
        metadata = entry.get("metadata")
        if not isinstance(metadata, dict):
            return None
        return metadata.get(key)

    def record_progress(
        self,
        farm_name: str,
        task_name: str,
        *,
        amount: int = 1,
        now: Optional[datetime] = None,
    ) -> None:
        """Incrementa el contador de una tarea o la marca como completada."""
        if task_name not in self.task_limits or amount <= 0:
            return
        now = now or datetime.now()
        entry = self._ensure_entry(farm_name, task_name, now)
        entry["timestamp"] = now.isoformat()
        if self._uses_boolean(task_name):
            entry["completed"] = True
        else:
            entry["count"] = int(entry.get("count", 0)) + amount
        self._save_state()

    def last_timestamp(
        self,
        farm_name: str,
        task_name: str,
        now: Optional[datetime] = None,
    ) -> Optional[datetime]:
        """Devuelve cuándo fue la última vez que se registró esta tarea."""
        entry = self._current_entry(farm_name, task_name, now)
        if not entry:
            return None
        timestamp = entry.get("timestamp")
        if not isinstance(timestamp, str):
            return None
        return self._parse_timestamp(timestamp)

    def set_count(
        self,
        farm_name: str,
        task_name: str,
        count: int,
        *,
        now: Optional[datetime] = None,
    ) -> None:
        """Permite sobrescribir manualmente el contador de una tarea."""
        if task_name not in self.task_limits:
            return
        now = now or datetime.now()
        entry = self._ensure_entry(farm_name, task_name, now)
        entry["timestamp"] = now.isoformat()
        if self._uses_boolean(task_name):
            entry["completed"] = count > 0
        else:
            entry["count"] = max(0, int(count))
        self._save_state()

    def clear(self) -> None:
        """Elimina todo el estado persistido en disco."""
        self._state.clear()
        self._save_state()

    def _current_reset_anchor(self, now: datetime) -> datetime:
        anchor = now.replace(
            hour=self.reset_hour_local,
            minute=0,
            second=0,
            microsecond=0,
        )
        if now < anchor:
            anchor -= timedelta(days=1)
        return anchor

    def _parse_timestamp(self, value: str) -> Optional[datetime]:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            self.console.log(
                f"[warning] Timestamp inválido en registro diario: '{value}'"
            )
            return None

    def _current_entry(
        self,
        farm_name: str,
        task_name: str,
        now: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        farm_tasks = self._state.get(farm_name)
        if not farm_tasks:
            return None
        entry = farm_tasks.get(task_name)
        if not entry:
            return None
        timestamp = entry.get("timestamp")
        if not isinstance(timestamp, str):
            return None
        last_run = self._parse_timestamp(timestamp)
        if not last_run:
            return None
        now = now or datetime.now()
        if last_run < self._current_reset_anchor(now):
            return None
        return entry

    def _ensure_entry(
        self,
        farm_name: str,
        task_name: str,
        now: datetime,
    ) -> Dict[str, Any]:
        entry = self._current_entry(farm_name, task_name, now)
        if entry:
            return entry
        farm_tasks = self._state.setdefault(farm_name, {})
        if self._uses_boolean(task_name):
            entry = {"timestamp": now.isoformat(), "completed": False}
        else:
            entry = {"timestamp": now.isoformat(), "count": 0}
        farm_tasks[task_name] = entry
        return entry

    def _load_state(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        if not self.storage_path.exists():
            return {}
        try:
            raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.console.log(
                f"[warning] No se pudo leer el registro diario ({exc}); se reiniciará"
            )
            return {}
        if not isinstance(raw, dict):
            return {}
        state: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for farm, tasks in raw.items():
            if not isinstance(tasks, dict):
                continue
            cleaned: Dict[str, Dict[str, Any]] = {}
            for task, timestamp in tasks.items():
                if isinstance(task, str):
                    if isinstance(timestamp, dict):
                        ts_val = timestamp.get("timestamp")
                        if not isinstance(ts_val, str):
                            continue
                        entry: Dict[str, Any] = {"timestamp": ts_val}
                        if self._uses_boolean(task):
                            completed = bool(timestamp.get("completed"))
                            if not completed:
                                completed = int(timestamp.get("count", 0) or 0) > 0
                            entry["completed"] = completed
                        else:
                            entry["count"] = (
                                int(timestamp.get("count", 0))
                                if isinstance(timestamp.get("count", 0), int)
                                else 0
                            )
                        for key, value in timestamp.items():
                            if key in {"timestamp", "count"}:
                                continue
                            entry[key] = value
                        cleaned[task] = entry
                    elif isinstance(timestamp, str):
                        if self._uses_boolean(task):
                            cleaned[task] = {"timestamp": timestamp, "completed": True}
                        else:
                            cleaned[task] = {
                                "timestamp": timestamp,
                                "count": 1,
                            }
            if cleaned:
                state[str(farm)] = cleaned
        return state

    def _uses_boolean(self, task_name: str) -> bool:
        return self.task_limits.get(task_name, 0) == 1

    def _save_state(self) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            self.storage_path.write_text(
                json.dumps(self._state, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            self.console.log(
                f"[warning] No se pudo guardar el registro diario en {self.storage_path}: {exc}"
            )