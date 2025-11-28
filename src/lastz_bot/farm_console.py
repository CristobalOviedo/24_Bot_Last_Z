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
        self._prefix = Text(f"[{farm_name}] ", style="bold cyan")
        self._debug = debug_reporter or get_debug_reporter()

    def log(self, *objects, **kwargs) -> None:  # type: ignore[override]
        """Replica ``Console.log`` agregando prefijo y registro persistente."""
        self._console.log(self._prefix, *objects, **kwargs)
        self._record(objects)

    def print(self, *objects, **kwargs) -> None:  # type: ignore[override]
        """Replica ``Console.print`` con el mismo prefijo y seguimiento."""
        self._console.print(self._prefix, *objects, **kwargs)
        self._record(objects)

    def __getattr__(self, name: str):
        return getattr(self._console, name)

    def _record(self, objects) -> None:
        """Serializa brevemente los objetos y los envía al ``DebugReporter``."""
        if not self._debug:
            return
        try:
            text = " ".join(self._stringify(obj) for obj in objects if obj is not None)
        except Exception:  # pragma: no cover - defensive
            return
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
