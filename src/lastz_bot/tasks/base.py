"""Clases base y registro de tareas ejecutables por el bot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Protocol
import threading

from rich.console import Console

from ..config import BotConfig, InstanceConfig, LayoutConfig
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
    config: BotConfig | None = None
    pause_cb: callable | None = None
    stop_event: threading.Event | None = None

    def live_debug_enabled(self) -> bool:
        """Ejecuta la logica de `live_debug_enabled` dentro del flujo del bot.

        Returns:
            object: Resultado de la operacion.
        """
        debug_cfg = getattr(self.config, "debug", None) if self.config else None
        if debug_cfg is None:
            return True
        return bool(getattr(debug_cfg, "live_artifacts_enabled", True))

    def troop_debug_enabled(self) -> bool:
        """Ejecuta la logica de `troop_debug_enabled` dentro del flujo del bot.

        Returns:
            object: Resultado de la operacion.
        """
        debug_cfg = getattr(self.config, "debug", None) if self.config else None
        if debug_cfg is None:
            return True
        return bool(getattr(debug_cfg, "troop_snapshots_enabled", True))

    def wait_if_paused(self) -> None:
        """Ejecuta la logica de `wait_if_paused` dentro del flujo del bot.

        Returns:
            object: Resultado de la operacion.
        """
        if self.pause_cb:
            try:
                self.pause_cb()
            except Exception:
                pass


class Task(Protocol):
    """Modela la responsabilidad de `Task` en la automatizacion del bot."""
    name: str

    def run(self, ctx: TaskContext, params: Dict[str, Any]) -> None:  # pragma: no cover - interface
        """Ejecuta la logica de `run` dentro del flujo del bot.

        Args:
            ctx: Parametro usado por la rutina.
            params: Parametro usado por la rutina.
        Returns:
            object: Resultado de la operacion.
        """
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
