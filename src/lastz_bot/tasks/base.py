"""Clases base y registro de tareas ejecutables por el bot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Protocol

from rich.console import Console

from ..config import InstanceConfig, LayoutConfig
from ..daily_tracker import DailyTaskTracker
from ..devices import DeviceController
from ..vision import VisionHelper


@dataclass
class TaskContext:
    """Datos compartidos que cada tarea necesita para operar."""

    device: DeviceController
    farm: InstanceConfig
    layout: LayoutConfig
    console: Console
    simulate: bool = False
    vision: VisionHelper | None = None
    daily_tracker: DailyTaskTracker | None = None


class Task(Protocol):
    name: str

    def run(self, ctx: TaskContext, params: Dict[str, Any]) -> None:  # pragma: no cover - interface
        ...


class TaskRegistry:
    """Colección con nombre -> tarea para resolver dinámicamente tareas."""

    def __init__(self) -> None:
        self._tasks: Dict[str, Task] = {}

    def register(self, task: Task) -> None:
        """Agrega una tarea al registro, validando duplicados."""
        if task.name in self._tasks:
            raise ValueError(f"Task '{task.name}' already registered")
        self._tasks[task.name] = task

    def get(self, name: str) -> Task:
        """Obtiene una tarea registrada o lanza KeyError con contexto."""
        try:
            return self._tasks[name]
        except KeyError as exc:
            raise KeyError(f"Task '{name}' no existe en el registro") from exc

    def tasks(self) -> Dict[str, Task]:
        """Devuelve una copia del diccionario interno para inspección."""
        return dict(self._tasks)
