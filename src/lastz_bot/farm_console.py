"""Decorador de consola que añade el nombre de la granja y registra logs."""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from .debug import get_debug_reporter, DebugReporter


class FarmConsole:
    """Envuelve una consola Rich para prefijar mensajes y guardarlos en debug."""

    def __init__(self, console: Console, farm_name: str, *, debug_reporter: DebugReporter | None = None) -> None:
        self._console = console
        self._farm_name = farm_name
        self._debug = debug_reporter or get_debug_reporter()
        self._task_name: str | None = None

    def log(self, *objects, **kwargs) -> None:  # type: ignore[override]
        """Replica ``Console.log`` agregando prefijo y registro persistente."""
        prefix = self._build_prefix()
        self._console.log(prefix, *objects, **kwargs)
        self._record(objects)

    def print(self, *objects, **kwargs) -> None:  # type: ignore[override]
        """Replica ``Console.print`` con el mismo prefijo y seguimiento."""
        prefix = self._build_prefix()
        self._console.print(prefix, *objects, **kwargs)
        self._record(objects)

    def __getattr__(self, name: str):
        return getattr(self._console, name)

    def set_task(self, task_name: str | None) -> None:
        """Actualiza el contexto de tarea para los logs siguientes."""

        self._task_name = task_name.strip() if task_name else None

    def current_task(self) -> str | None:
        """Retorna el nombre de la tarea activa (si existe)."""

        return self._task_name

    def _build_prefix(self) -> Text:
        base = Text(f"[{self._farm_name}] ", style="bold cyan")
        if self._task_name:
            task_text = Text(f"[{self._task_name}] ", style="bold yellow")
            return Text.assemble(base, task_text)
        return base

    def _record(self, objects) -> None:
        """Serializa brevemente los objetos y los envía al ``DebugReporter``."""
        if not self._debug:
            return
        try:
            text_parts = [self._stringify(obj) for obj in objects if obj is not None]
        except Exception:  # pragma: no cover - defensive
            return
        if not text_parts:
            return
        if self._task_name:
            text_parts.insert(0, f"[{self._task_name}]")
        text = " ".join(text_parts)
        if text:
            self._debug.record_log(self._farm_name, text)

    @staticmethod
    def _stringify(value: object) -> str:
        """Mejor esfuerzo por transformar Rich/Text en cadenas simples."""
        if hasattr(value, "plain"):
            try:
                return str(value.plain)
            except Exception:  # pragma: no cover - defensive
                return str(value)
        return str(value)
