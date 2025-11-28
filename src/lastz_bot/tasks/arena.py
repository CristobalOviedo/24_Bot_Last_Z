"""Automatiza el combate diario de arena seleccionando oponentes y cerrando overlays."""

from __future__ import annotations

import time
from typing import List, Sequence, Tuple

from .base import TaskContext
from .utils import tap_back_button, dismiss_overlay_if_present

Coord = Tuple[int, int]


def _as_list(value: object) -> List[str]:
    """Normaliza valores de config a listas de strings limpias."""
    if value is None:
        return []
    if isinstance(value, str):
        entry = value.strip()
        return [entry] if entry else []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _coord_from_param(value: object) -> Coord | None:
    """Convierte valores ``[x, y]`` en tuplas de enteros."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return int(value[0]), int(value[1])
    return None


def _build_template_paths(
    ctx: TaskContext,
    template_names: Sequence[str],
    missing: set[str],
) -> List:
    """Resuelve templates a rutas absolutas y lleva registro de faltantes."""
    paths: List = []
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


class DailyArenaTask:
    """Ejecuta un ciclo de batalla en la arena diaria."""

    name = "daily_arena"
    manual_daily_logging = True

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
        """Espera a que uno de los templates aparezca y devuelve sus coords."""
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

    def _select_attack_target(
        self,
        ctx: TaskContext,
        template_names: Sequence[str],
        *,
        threshold: float,
        timeout: float,
        poll_interval: float,
        max_results: int,
    ):
        """Escanea la lista de oponentes y devuelve el botón inferior disponible."""
        if not template_names:
            ctx.console.log("[warning] No hay templates para el botón de ataque en arena")
            return None
        paths = _build_template_paths(ctx, template_names, self._missing_templates)
        if not paths:
            return None
        start = time.monotonic()
        while time.monotonic() - start <= timeout:
            matches = ctx.vision.find_all_templates(
                paths,
                threshold=threshold,
                max_results=max(1, max_results),
            )
            if matches:
                matches.sort(key=lambda item: item[0][1], reverse=True)
                return matches[0]
            time.sleep(max(0.1, poll_interval))
        return None

    def run(self, ctx: TaskContext, params: dict) -> None:  # type: ignore[override]
        """Abre la arena, selecciona un rival, lanza combate y registra progreso."""
        if not ctx.vision:
            ctx.console.log(
                "[warning] VisionHelper no disponible; daily_arena requiere detecciones"
            )
            return

        battle_completed = False

        icon_templates = _as_list(params.get("icon_templates") or params.get("arena_icon_templates"))
        mode_templates = _as_list(params.get("mode_templates") or params.get("silver_arena_templates"))
        challenge_templates = _as_list(params.get("challenge_templates") or params.get("challenge_button_templates"))
        attack_templates = _as_list(params.get("attack_templates") or params.get("opponent_attack_templates"))
        combat_templates = _as_list(params.get("combat_templates") or params.get("combat_button_templates"))
        skip_templates = _as_list(params.get("skip_templates") or params.get("skip_button_templates"))
        result_overlay_templates = _as_list(
            params.get("overlay_templates") or params.get("result_overlay_templates")
        )

        if not icon_templates or not challenge_templates or not attack_templates or not combat_templates:
            ctx.console.log(
                "[warning] daily_arena requiere templates para icono, desafío, ataques y botón de combate"
            )
            return

        base_threshold = float(params.get("template_threshold", 0.83))
        icon_threshold = float(params.get("icon_threshold", base_threshold))
        mode_threshold = float(params.get("mode_threshold", base_threshold))
        challenge_threshold = float(params.get("challenge_threshold", base_threshold))
        attack_threshold = float(params.get("attack_threshold", base_threshold))
        combat_threshold = float(params.get("combat_threshold", base_threshold))
        skip_threshold = float(params.get("skip_threshold", base_threshold))

        icon_timeout = float(params.get("icon_timeout", 6.0))
        mode_timeout = float(params.get("mode_timeout", 6.0))
        challenge_timeout = float(params.get("challenge_timeout", 6.0))
        attack_timeout = float(params.get("attack_timeout", 6.0))
        combat_timeout = float(params.get("combat_timeout", 6.0))
        skip_timeout = float(params.get("skip_timeout", 4.0))
        menu_return_timeout = float(params.get("menu_return_timeout", 8.0))

        poll_interval = float(params.get("poll_interval", 0.5))
        icon_poll = float(params.get("icon_poll_interval", poll_interval))
        mode_poll = float(params.get("mode_poll_interval", poll_interval))
        challenge_poll = float(params.get("challenge_poll_interval", poll_interval))
        attack_poll = float(params.get("attack_poll_interval", poll_interval))
        combat_poll = float(params.get("combat_poll_interval", poll_interval))
        skip_poll = float(params.get("skip_poll_interval", poll_interval))

        tap_delay = float(params.get("tap_delay", 1.0))
        menu_delay = float(params.get("menu_delay", 2.0))
        mode_delay = float(params.get("mode_delay", 1.5))
        challenge_delay = float(params.get("challenge_delay", 1.0))
        fight_transition_delay = float(params.get("fight_transition_delay", 3.5))
        post_combat_delay = float(params.get("post_combat_delay", 3.0))
        result_overlay_delay = float(params.get("result_overlay_delay", 2.0))
        overlay_dismiss_delay = float(params.get("overlay_dismiss_delay", 0.8))
        overlay_timeout = float(params.get("overlay_timeout", 6.0))
        overlay_threshold = float(params.get("overlay_threshold", base_threshold))
        overlay_poll = float(params.get("overlay_poll_interval", poll_interval))
        overlay_use_brightness = bool(params.get("overlay_use_brightness", True))
        overlay_brightness_threshold = float(
            params.get("overlay_brightness_threshold", 0.35)
        )
        back_delay = float(params.get("back_delay", 1.0))

        max_attack_results = int(params.get("attack_scan_limit", 5))

        icon_match = self._wait_for_template(
            ctx,
            icon_templates,
            threshold=icon_threshold,
            timeout=icon_timeout,
            poll_interval=icon_poll,
            label="icono de arena",
        )
        if not icon_match:
            ctx.console.log("[info] No se detectó el icono de arena; se omite daily_arena")
            return

        icon_coords, icon_path = icon_match
        ctx.console.log(f"Icono de arena detectado ('{icon_path.name}'); abriendo menú")
        ctx.device.tap(icon_coords, label="arena-icon")
        if tap_delay > 0:
            ctx.device.sleep(tap_delay)
        if menu_delay > 0:
            ctx.device.sleep(menu_delay)

        if mode_templates:
            mode_match = self._wait_for_template(
                ctx,
                mode_templates,
                threshold=mode_threshold,
                timeout=mode_timeout,
                poll_interval=mode_poll,
                label="modo arena",
            )
            if not mode_match:
                ctx.console.log("[warning] No se detectó el botón del modo de arena deseado")
                return
            mode_coords, mode_path = mode_match
            ctx.console.log(f"Modo de arena detectado ('{mode_path.name}'); entrando")
            ctx.device.tap(mode_coords, label="arena-mode")
            if tap_delay > 0:
                ctx.device.sleep(tap_delay)
            if mode_delay > 0:
                ctx.device.sleep(mode_delay)

        challenge_match = self._wait_for_template(
            ctx,
            challenge_templates,
            threshold=challenge_threshold,
            timeout=challenge_timeout,
            poll_interval=challenge_poll,
            label="botón desafío",
        )
        if not challenge_match:
            ctx.console.log("[warning] No se detectó el botón 'Desafío' en arena")
            return

        challenge_coords, challenge_path = challenge_match
        ctx.console.log(f"Botón 'Desafío' detectado ('{challenge_path.name}'); abriendo combates")
        ctx.device.tap(challenge_coords, label="arena-challenge")
        if tap_delay > 0:
            ctx.device.sleep(tap_delay)
        if challenge_delay > 0:
            ctx.device.sleep(challenge_delay)

        attack_match = self._select_attack_target(
            ctx,
            attack_templates,
            threshold=attack_threshold,
            timeout=attack_timeout,
            poll_interval=attack_poll,
            max_results=max_attack_results,
        )
        if not attack_match:
            ctx.console.log("[warning] No se detectaron botones de ataque en la lista de oponentes")
            return

        attack_coords, attack_path = attack_match
        ctx.console.log(
            f"Botón de ataque inferior detectado ('{attack_path.name}' en y={attack_coords[1]}); atacando"
        )
        ctx.device.tap(attack_coords, label="arena-attack-target")
        if fight_transition_delay > 0:
            ctx.device.sleep(fight_transition_delay)

        combat_match = self._wait_for_template(
            ctx,
            combat_templates,
            threshold=combat_threshold,
            timeout=combat_timeout,
            poll_interval=combat_poll,
            label="botón combate",
        )
        if not combat_match:
            ctx.console.log("[warning] No se detectó el botón 'Combate' tras iniciar el desafío")
            return

        combat_coords, combat_path = combat_match
        ctx.console.log(f"Botón 'Combate' detectado ('{combat_path.name}'); iniciando pelea")
        ctx.device.tap(combat_coords, label="arena-combat")
        if post_combat_delay > 0:
            ctx.device.sleep(post_combat_delay)

        if skip_templates:
            skip_paths = _build_template_paths(ctx, skip_templates, self._missing_templates)
            if skip_paths:
                skip_result = ctx.vision.wait_for_any_template(
                    skip_paths,
                    timeout=skip_timeout,
                    poll_interval=skip_poll,
                    threshold=skip_threshold,
                    raise_on_timeout=False,
                )
                if skip_result:
                    skip_coords, skip_path = skip_result
                    ctx.console.log(f"Botón 'Skip' detectado ('{skip_path.name}'); saltando animación")
                    ctx.device.tap(skip_coords, label="arena-skip")

        if result_overlay_delay > 0:
            ctx.device.sleep(result_overlay_delay)

        overlay_button = params.get("overlay_dismiss_button")
        overlay_fallback = _coord_from_param(params.get("overlay_dismiss_tap"))
        overlay_closed = dismiss_overlay_if_present(
            ctx,
            result_overlay_templates or None,
            str(overlay_button) if overlay_button else None,
            timeout=overlay_timeout,
            poll_interval=overlay_poll,
            threshold=overlay_threshold,
            delay=overlay_dismiss_delay,
            use_brightness=overlay_use_brightness,
            brightness_threshold=overlay_brightness_threshold,
            fallback_tap=overlay_fallback,
        )
        battle_completed = True
        if not overlay_closed:
            ctx.console.log("[warning] No se pudo cerrar el overlay de resultados de arena")

        menu_return = self._wait_for_template(
            ctx,
            challenge_templates,
            threshold=challenge_threshold,
            timeout=menu_return_timeout,
            poll_interval=challenge_poll,
            label="botón desafío (retorno)",
        )
        if not menu_return:
            ctx.console.log(
                "[warning] No se pudo confirmar el retorno al menú de arena tras el combate"
            )

        if back_delay > 0:
            ctx.device.sleep(back_delay)

        back_templates = _as_list(params.get("back_template_names"))
        if not tap_back_button(
            ctx,
            label="arena-back",
            timeout=float(params.get("back_timeout", 4.0)),
            threshold=float(params.get("back_threshold", 0.83)),
            poll_interval=float(params.get("back_poll_interval", 0.5)),
            template_names=back_templates or None,
        ):
            ctx.console.log(
                "[warning] No se pudo regresar al menú principal después de la arena"
            )

        if ctx.daily_tracker:
            if battle_completed:
                ctx.daily_tracker.record_progress(ctx.farm.name, self.name)
                ctx.console.log("[info] Combate de arena registrado en el tracker diario")
            else:
                ctx.console.log(
                    "[info] No se completó el combate de arena; el tracker diario queda sin cambios"
                )
