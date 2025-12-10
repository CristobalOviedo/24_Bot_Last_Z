"""Utilidades para ordenar tareas pendientes basadas en `daily_tasks.json`."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, MutableMapping


@dataclass
class PendingTaskEntry:
    farm: str
    name: str
    entry: Mapping[str, Any]
    next_ready: datetime | None
    updated: datetime | None


def load_daily_tasks(path: Path) -> Mapping[str, Any]:
    """Lee el JSON de estado diario; si falta retorna estructura vacía."""
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, Mapping):
        return {}
    return raw


def collect_pending_tasks(data: Mapping[str, Any]) -> list[PendingTaskEntry]:
    """Replica el orden mostrado en monitor.py: primero `next_ready`, luego timestamp."""
    pending: list[PendingTaskEntry] = []
    for farm_name, tasks in data.items():
        if not isinstance(tasks, MutableMapping):
            continue
        for task_name, entry in tasks.items():
            if not isinstance(entry, MutableMapping):
                continue
            if entry.get("completed") is not False:
                continue
            metadata = entry.get("metadata") if isinstance(entry.get("metadata"), MutableMapping) else {}
            next_ready = parse_datetime(metadata.get("next_ready_at"))
            timestamp = parse_datetime(entry.get("timestamp"))
            pending.append(
                PendingTaskEntry(
                    farm=farm_name,
                    name=task_name,
                    entry=entry,
                    next_ready=next_ready,
                    updated=timestamp,
                )
            )
    pending.sort(
        key=lambda item: (
            item.next_ready or datetime.max,
            item.updated or datetime.max,
        )
    )
    return pending


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None