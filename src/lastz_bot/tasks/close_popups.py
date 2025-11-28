"""Tarea que cierra popups repetidamente usando template o botón fijo."""

from __future__ import annotations

from pathlib import Path

from ..devices import resolve_button
from .base import TaskContext


class ClosePopupsTask:
    """Golpea la 'X' de overlays iniciales o usa un botón predeterminado."""

    name = "close_popups"

    def run(self, ctx: TaskContext, params):
        """Itera hasta ``attempts`` veces intentando cerrar el popup actual."""
        attempts = int(params.get("attempts", 2))
        button_key = params.get("button", "close_popup")
        delay = float(params.get("delay", 0.8))
        template_name = params.get("template")
        template_timeout = float(params.get("template_timeout", 10.0))
        threshold = float(params.get("template_threshold", 0.85))
        poll_interval = float(params.get("template_poll_interval", 1.0))

        template_paths: list[Path] = []
        if template_name:
            try:
                template_paths = ctx.layout.template_paths(template_name)
            except KeyError:
                ctx.console.log(
                    f"[warning] Template '{template_name}' no está definido para el layout"
                )
                template_paths = []

        for idx in range(attempts):
            ctx.console.log(f"Cerrando popup #{idx + 1}")

            coord = None
            if template_paths and ctx.vision:
                result = ctx.vision.wait_for_any_template(
                    template_paths,
                    timeout=template_timeout,
                    poll_interval=poll_interval,
                    threshold=threshold,
                    raise_on_timeout=False,
                )
                if result:
                    coord, matched_path = result
                    ctx.console.log(
                        f"Template '{matched_path.name}' detectado para cerrar popup"
                    )
                else:
                    ctx.console.log(
                        f"No se detectó la 'X' del popup (intento {idx + 1}); deteniendo cierres"
                    )
                    break

            if coord is None:
                coord = resolve_button(ctx.layout, button_key)

            ctx.device.tap(coord, label="close-popup")
            ctx.device.sleep(delay)
