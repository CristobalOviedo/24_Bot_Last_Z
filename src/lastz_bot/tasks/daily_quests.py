"""Reclama las recompensas diarias de Viviana detectando plantillas configurables."""

from __future__ import annotations

from typing import List, Sequence, Tuple

from .base import TaskContext
from .utils import tap_back_button, dismiss_overlay_if_present

Coord = Tuple[int, int]
Region = Tuple[Tuple[float, float], Tuple[float, float]]


def _as_list(value: object) -> List[str]:
    """Normaliza parámetros a listas de strings limpias."""
    if value is None:
        return []
    if isinstance(value, str):
        entry = value.strip()
        return [entry] if entry else []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _coord_from_param(value: object) -> Coord | None:
    """Convierte valores ``[x, y]`` o strings ``"x,y"`` en tuplas."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return int(value[0]), int(value[1])
    return None


def _region_from_param(value: object) -> Region | None:
    """Interpreta regiones normalizadas provistas en la configuración."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    y_pair, x_pair = value
    if (
        isinstance(y_pair, (list, tuple))
        and len(y_pair) == 2
        and isinstance(x_pair, (list, tuple))
        and len(x_pair) == 2
    ):
        try:
            return (
                (float(y_pair[0]), float(y_pair[1])),
                (float(x_pair[0]), float(x_pair[1])),
            )
        except (TypeError, ValueError):
            return None
    return None


def _build_template_paths(
    ctx: TaskContext,
    template_names: Sequence[str],
    missing: set[str],
) -> list:
    """Resuelve templates y guarda advertencias de faltantes solo una vez."""
    paths = []
    for name in template_names:
        if not name:
            continue
        try:
            paths.extend(ctx.layout.template_paths(name))
        except KeyError:
            if name not in missing:
                ctx.console.log(
                    f"[warning] Template '{name}' no está definido en el layout"
                )
                missing.add(name)
    return paths


class ClaimDailyQuestsTask:
    """Abre el menú de Viviana y toca 'reclamar todo' en tareas diarias."""

    name = "claim_daily_quests"
    manual_daily_logging = True
    allow_repeat_after_completion = True

    def __init__(self) -> None:
        self._missing_templates: set[str] = set()

    def _wait_for_template(
        self,
        ctx: TaskContext,
        template_names: Sequence[str],
        *,
        threshold: float,
        timeout: float,
        poll_interval: float,
        label: str,
    ):
        if not template_names:
            ctx.console.log(f"[warning] No hay templates configurados para '{label}'")
            return None
        paths = _build_template_paths(ctx, template_names, self._missing_templates)
        if not paths:
            return None
        return ctx.vision.wait_for_any_template(  # type: ignore[return-value]
            paths,
            timeout=timeout,
            poll_interval=poll_interval,
            threshold=threshold,
            raise_on_timeout=False,
        )

    def run(self, ctx: TaskContext, params: dict) -> None:  # type: ignore[override]
        """Ejecuta el flujo completo para reclamar recompensas diarias."""
        if not ctx.vision:
            ctx.console.log(
                "[warning] VisionHelper no disponible; claim_daily_quests requiere detecciones"
            )
            return

        icon_templates = _as_list(
            params.get("viviana_templates") or params.get("icon_templates")
        )
        tab_templates = _as_list(
            params.get("daily_tab_templates") or params.get("tab_templates")
        )
        claim_templates = _as_list(
            params.get("claim_all_templates")
            or params.get("claim_templates")
        )

        if not icon_templates or not tab_templates or not claim_templates:
            ctx.console.log(
                "[warning] claim_daily_quests requiere templates para icono, pestaña y botón de reclamar"
            )
            return

        base_threshold = float(params.get("template_threshold", 0.83))
        icon_threshold = float(params.get("icon_threshold", base_threshold))
        tab_threshold = float(params.get("tab_threshold", base_threshold))
        claim_threshold = float(params.get("claim_threshold", base_threshold))

        icon_timeout = float(params.get("icon_timeout", 6.0))
        tab_timeout = float(params.get("tab_timeout", 5.0))
        claim_timeout = float(params.get("claim_timeout", 6.0))

        poll_interval = float(params.get("poll_interval", 0.5))
        icon_poll = float(params.get("icon_poll_interval", poll_interval))
        tab_poll = float(params.get("tab_poll_interval", poll_interval))
        claim_poll = float(params.get("claim_poll_interval", poll_interval))

        tap_delay = float(params.get("tap_delay", 1.0))
        menu_delay = float(params.get("menu_delay", 2.0))
        tab_delay = float(params.get("tab_delay", 1.5))
        post_claim_delay = float(params.get("post_claim_delay", 1.5))
        overlay_dismiss_delay = float(params.get("overlay_dismiss_delay", 0.8))
        back_delay = float(params.get("back_delay", 1.0))
        overlay_templates = _as_list(params.get("overlay_templates"))
        overlay_timeout = float(params.get("overlay_timeout", 6.0))
        overlay_poll = float(params.get("overlay_poll_interval", poll_interval))
        overlay_threshold = float(params.get("overlay_threshold", base_threshold))
        overlay_use_brightness = bool(params.get("overlay_use_brightness", True))
        overlay_brightness_threshold = float(
            params.get("overlay_brightness_threshold", 0.35)
        )
        overlay_brightness_region = _region_from_param(
            params.get("overlay_brightness_region")
        )
        menu_return_templates = _as_list(params.get("menu_return_templates"))
        menu_return_timeout = float(params.get("menu_return_timeout", 6.0))
        menu_return_poll = float(
            params.get("menu_return_poll_interval", poll_interval)
        )

        claim_attempted = False

        icon_match = self._wait_for_template(
            ctx,
            icon_templates,
            threshold=icon_threshold,
            timeout=icon_timeout,
            poll_interval=icon_poll,
            label="icono de Viviana",
        )
        if not icon_match:
            ctx.console.log(
                "[info] No se detectó el icono de Viviana; se omite claim_daily_quests"
            )
            return

        icon_coords, icon_path = icon_match
        ctx.console.log(
            f"Icono de Viviana detectado ('{icon_path.name}'); abriendo misiones diarias"
        )
        ctx.device.tap(icon_coords, label="daily-quests-icon")
        if tap_delay > 0:
            ctx.device.sleep(tap_delay)
        if menu_delay > 0:
            ctx.device.sleep(menu_delay)

        tab_match = self._wait_for_template(
            ctx,
            tab_templates,
            threshold=tab_threshold,
            timeout=tab_timeout,
            poll_interval=tab_poll,
            label="pestaña de tareas diarias",
        )
        if not tab_match:
            ctx.console.log(
                "[warning] No se detectó la pestaña de tareas diarias tras abrir el menú"
            )
            return

        tab_coords, tab_path = tab_match
        ctx.console.log(
            f"Botón de tareas diarias detectado ('{tab_path.name}'); abriendo"
        )
        ctx.device.tap(tab_coords, label="daily-quests-tab")
        if tap_delay > 0:
            ctx.device.sleep(tap_delay)
        if tab_delay > 0:
            ctx.device.sleep(tab_delay)

        claim_match = self._wait_for_template(
            ctx,
            claim_templates,
            threshold=claim_threshold,
            timeout=claim_timeout,
            poll_interval=claim_poll,
            label="reclamar todo",
        )
        if not claim_match:
            ctx.console.log(
                "[warning] No se encontró el botón 'Reclamar todo' en las tareas diarias"
            )
            return

        claim_coords, claim_path = claim_match
        ctx.console.log(
            f"Botón 'Reclamar todo' detectado ('{claim_path.name}'); reclamando"
        )
        ctx.device.tap(claim_coords, label="daily-quests-claim-all")
        if post_claim_delay > 0:
            ctx.device.sleep(post_claim_delay)
        claim_attempted = True

        overlay_button = params.get("overlay_dismiss_button")
        overlay_fallback = _coord_from_param(params.get("overlay_dismiss_tap"))
        overlay_closed = dismiss_overlay_if_present(
            ctx,
            overlay_templates or None,
            str(overlay_button) if overlay_button else None,
            timeout=overlay_timeout,
            poll_interval=overlay_poll,
            threshold=overlay_threshold,
            delay=overlay_dismiss_delay,
            use_brightness=overlay_use_brightness,
            brightness_threshold=overlay_brightness_threshold,
            brightness_region=overlay_brightness_region,
            fallback_tap=overlay_fallback,
        )
        if not overlay_closed:
            ctx.console.log(
                "[warning] No se pudo detectar/cerrar el overlay de recompensas de tareas diarias"
            )

        return_templates = menu_return_templates or claim_templates
        if return_templates:
            returned = self._wait_for_template(
                ctx,
                return_templates,
                threshold=claim_threshold,
                timeout=menu_return_timeout,
                poll_interval=menu_return_poll,
                label="menú de tareas diarias",
            )
            if not returned:
                ctx.console.log(
                    "[warning] No se logró confirmar el retorno al menú de tareas diarias tras reclamar"
                )

        if back_delay > 0:
            ctx.device.sleep(back_delay)

        back_templates = _as_list(params.get("back_template_names"))
        back_tapped = tap_back_button(
            ctx,
            label="daily-quests-back",
            timeout=float(params.get("back_timeout", 4.0)),
            threshold=float(params.get("back_threshold", 0.83)),
            poll_interval=float(params.get("back_poll_interval", 0.5)),
            template_names=back_templates or None,
        )
        if not back_tapped:
            ctx.console.log(
                "[warning] No se pudo regresar a la pantalla principal tras reclamar las tareas diarias"
            )

        if claim_attempted and ctx.daily_tracker:
            ctx.daily_tracker.record_progress(ctx.farm.name, self.name)
            ctx.console.log(
                "[info] Reclamo de tareas diarias registrado en el tracker"
            )
        elif ctx.daily_tracker:
            ctx.console.log(
                "[info] Reclamo de tareas diarias no confirmado; no se actualizó el tracker"
            )
