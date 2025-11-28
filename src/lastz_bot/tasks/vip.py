"""Reclama recompensas VIP diarias y bonus adicionales."""

from __future__ import annotations

from typing import Tuple

from ..devices import resolve_button
from .base import TaskContext
from .utils import dismiss_overlay_if_present, tap_back_button

Coord = Tuple[int, int]


def _coord_from_param(value: object) -> Coord | None:
    """Convierte listas/tuplas en coordenadas enteras si son válidas."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return None
    return None


class CollectVIPTask:
    """Gestiona los taps necesarios para recoger VIP diario y bonos."""
    name = "collect_vip"
    manual_daily_logging = True

    def run(self, ctx: TaskContext, params):
        """Abre el panel VIP, reclama recompensas y cierra overlays."""
        vip_button = params.get("vip_button", "vip")
        reward_button = params.get("reward_button", "vip_reward")
        bonus_button = params.get("bonus_button", "vip_bonus")
        overlay_close = params.get("overlay_close", "vip_overlay_close")
        overlay_fallback = _coord_from_param(params.get("overlay_dismiss_tap"))
        reward_overlay_template = params.get(
            "reward_overlay_template"
        )
        bonus_overlay_template = params.get(
            "bonus_overlay_template"
        )
        overlay_timeout = float(params.get("overlay_timeout", 6.0))
        overlay_poll = float(params.get("overlay_poll", 0.5))
        overlay_threshold = float(params.get("overlay_threshold", 0.85))
        overlay_use_brightness = bool(params.get("overlay_use_brightness", True))
        overlay_dark_threshold = float(params.get("overlay_dark_threshold", 0.35))
        delay = float(params.get("delay", 3))

        ctx.console.log("Abriendo panel VIP")
        ctx.device.tap(resolve_button(ctx.layout, vip_button), label="vip")
        ctx.device.sleep(delay)

        claims_attempted = 0
        ctx.device.tap(resolve_button(ctx.layout, reward_button), label="vip-reward")
        ctx.device.sleep(delay)
        claims_attempted += 1
        dismissed_reward = dismiss_overlay_if_present(
            ctx,
            reward_overlay_template,
            overlay_close,
            timeout=overlay_timeout,
            poll_interval=overlay_poll,
            threshold=overlay_threshold,
            delay=0.3,
            use_brightness=overlay_use_brightness,
            brightness_threshold=overlay_dark_threshold,
            fallback_tap=overlay_fallback,
        )
        if not dismissed_reward:
            ctx.console.log("No apareció overlay tras reclamar VIP diario")

        ctx.device.tap(resolve_button(ctx.layout, bonus_button), label="vip-bonus")
        ctx.device.sleep(delay)
        claims_attempted += 1
        dismissed_bonus = dismiss_overlay_if_present(
            ctx,
            bonus_overlay_template,
            overlay_close,
            timeout=overlay_timeout,
            poll_interval=overlay_poll,
            threshold=overlay_threshold,
            delay=0.3,
            use_brightness=overlay_use_brightness,
            brightness_threshold=overlay_dark_threshold,
            fallback_tap=overlay_fallback,
        )
        if not dismissed_bonus:
            ctx.console.log("No apareció overlay tras reclamar bonus VIP")

        ctx.device.tap(resolve_button(ctx.layout, overlay_close), label="vip-overlay-close")
        ctx.device.sleep(0.3)
        if not tap_back_button(ctx, label="vip-exit"):
            ctx.console.log("[warning] No se detectó el botón 'back' tras cerrar el panel VIP")

        if ctx.daily_tracker:
            if claims_attempted > 0:
                ctx.daily_tracker.record_progress(ctx.farm.name, self.name)
                ctx.console.log("[info] Recompensas VIP registradas como completadas en el tracker")
            else:
                ctx.console.log("[info] No se detectaron taps de recompensa VIP; el tracker no cambió")
