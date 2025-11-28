"""Reclama recompensas del buzón (sistema + alianza) y maneja overlays."""

from __future__ import annotations

from ..devices import resolve_button
from .base import TaskContext
from .utils import dismiss_overlay_if_present, tap_back_button


class CollectMailTask:
    """Abre el buzón, recorre pestañas configuradas y pulsa "Collect All"."""
    name = "collect_mail"
    manual_daily_logging = True

    def run(self, ctx: TaskContext, params):
        """Ejecuta la secuencia de tabs, reclamos y cierre con registro diario."""
        mail_button = params.get("mail_button", "mail")
        alliance_tab = params.get("alliance_tab", "mail_alliance_tab")
        system_tab = params.get("system_tab", "mail_system_tab")
        collect_button = params.get("collect_button", "mail_collect_all")
        delay = float(params.get("delay", 3))
        tab_settle_delay = float(params.get("tab_settle_delay", 5))
        overlay_template = params.get("overlay_template")
        overlay_close_button = params.get("overlay_close_button", "back_button")
        overlay_timeout = float(params.get("overlay_timeout", 3.0))
        overlay_poll = float(params.get("overlay_poll", 0.5))
        overlay_threshold = float(params.get("overlay_threshold", 0.85))
        overlay_delay = float(params.get("overlay_delay", 0.3))
        overlay_use_brightness = bool(params.get("overlay_use_brightness", True))
        overlay_dark_threshold = float(params.get("overlay_dark_threshold", 0.35))

        ctx.console.log("Abriendo correo")
        ctx.device.tap(resolve_button(ctx.layout, mail_button), label="mail")
        ctx.device.sleep(delay)

        collected_tabs = 0
        for tab_key in (system_tab, alliance_tab):
            ctx.device.tap(resolve_button(ctx.layout, tab_key), label=f"tab-{tab_key}")
            ctx.device.sleep(tab_settle_delay)
            ctx.device.tap(resolve_button(ctx.layout, collect_button), label="collect-all")
            ctx.device.sleep(delay)
            collected_tabs += 1

            dismissed = dismiss_overlay_if_present(
                ctx,
                overlay_template,
                overlay_close_button,
                timeout=overlay_timeout,
                poll_interval=overlay_poll,
                threshold=overlay_threshold,
                delay=overlay_delay,
                use_brightness=overlay_use_brightness,
                brightness_threshold=overlay_dark_threshold,
            )
            if not dismissed:
                ctx.console.log(
                    "No apareció overlay de recompensa tras recolectar; continuando"
                )

        if not tap_back_button(ctx, label="mail-exit"):
            ctx.console.log("[warning] No se detectó el botón 'back' al salir del buzón")

        if ctx.daily_tracker:
            if collected_tabs > 0:
                ctx.daily_tracker.record_progress(ctx.farm.name, self.name)
                ctx.console.log("[info] Recolección de correo registrada en el tracker")
            else:
                ctx.console.log("[info] No se reclamó correo; el tracker diario no se modificó")
