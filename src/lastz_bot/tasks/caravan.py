"""Automatiza la actividad Caravan recorriendo campos de batalla configurados."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import cv2

from ..devices import resolve_button
from .base import TaskContext
from .utils import tap_back_button

Coord = Tuple[int, int]


@dataclass
class BattlefieldSpec:
    """Describe un campo de batalla/caravana y cómo cambiar a él."""

    name: str
    color: str
    switch_coord: Coord | None


class CaravanTask:
    """Ejecuta todos los pasos para limpiar campos de la caravana."""

    name = "caravan"

    def __init__(self) -> None:
        self._missing_templates: set[str] = set()
        self._last_start_coords: Coord | None = None

    def run(self, ctx: TaskContext, params: Dict[str, Any]) -> None:  # type: ignore[override]
        """Abre la interfaz de caravana, recorre campos y valida resultados."""
        self._last_start_coords = None
        if not ctx.vision:
            ctx.console.log("[warning] VisionHelper no disponible; 'caravan' requiere detecciones")
            return

        icon_template = params.get("icon_template") or "caravan_icon"
        icon_paths = self._template_paths(ctx, icon_template)
        if not icon_paths:
            ctx.console.log(
                f"[warning] No se encontró el template '{icon_template}' para detectar la caravana"
            )
            return

        icon_threshold = float(params.get("icon_threshold", 0.82))
        icon_match = ctx.vision.find_any_template(icon_paths, threshold=icon_threshold)
        if not icon_match:
            ctx.console.log("[info] Icono de caravana no presente; se omite la tarea")
            return

        tap_delay = float(params.get("tap_delay", 2.0))
        panel_delay = float(params.get("panel_delay", 3.0))
        ctx.console.log("Icono de caravana detectado; iniciando rutina")
        icon_coords, matched_path = icon_match
        ctx.console.log(f"Caravana ubicada con template '{matched_path.name}'")
        ctx.device.tap(icon_coords, label="caravan-icon")
        if tap_delay > 0:
            ctx.device.sleep(tap_delay)
        if panel_delay > 0:
            ctx.device.sleep(panel_delay)

        prev_level = self._coord_from_value(
            ctx,
            params.get("battlefield_prev_level_button"),
            "battlefield_prev_level_button",
        )
        start_button = self._coord_from_value(ctx, params.get("start_button"), "start_button")
        skip_button = self._coord_from_value(ctx, params.get("skip_button"), "skip_button")

        if prev_level is None or skip_button is None:
            ctx.console.log("[warning] Faltan botones críticos (prev_level o skip_button); se omite la caravana")
            return

        color_buttons = self._build_color_map(ctx, params.get("color_buttons"))
        if not color_buttons:
            ctx.console.log("[warning] No hay botones de color configurados; se omite la caravana")
            return

        hero_coords = self._build_hero_coords(ctx, params.get("hero_buttons"))
        if not hero_coords:
            ctx.console.log("[warning] No hay coordenadas para héroes; se omite la caravana")
            return

        battlefields = self._build_battlefields(ctx, params.get("battlefields"))
        if not battlefields:
            ctx.console.log("[warning] No se configuraron campos de batalla; se omite la caravana")
            return

        continue_paths = self._template_paths(ctx, params.get("continue_template"))
        continue_threshold = float(params.get("continue_threshold", 0.82))
        continue_timeout = float(params.get("continue_timeout", 5.0))
        start_template_paths = self._template_paths(ctx, params.get("start_template"))
        start_threshold = float(params.get("start_threshold", 0.82))
        start_timeout = float(params.get("start_timeout", 6.0))
        start_template_label = self._resolve_template_label(
            params.get("start_template"),
            "caravan_start_button",
        )
        warning_template_paths = self._template_paths(ctx, params.get("warning_template"))
        warning_threshold = float(params.get("warning_threshold", 0.82))
        warning_timeout = float(params.get("warning_timeout", 6.0))
        warning_accept_template_paths = self._template_paths(ctx, params.get("warning_accept_template"))
        warning_accept_threshold = float(params.get("warning_accept_threshold", 0.82))
        pre_combat_paths = self._template_paths(ctx, params.get("pre_combat_template"))
        pre_combat_threshold = float(params.get("pre_combat_threshold", 0.82))
        pre_combat_timeout = float(params.get("pre_combat_timeout", 6.0))
        post_combat_paths = self._template_paths(ctx, params.get("post_combat_template"))
        post_combat_threshold = float(params.get("post_combat_threshold", 0.82))
        post_combat_timeout = float(params.get("post_combat_timeout", 6.0))

        level_select_delay = float(params.get("level_select_delay", 2.0))
        start_delay = float(params.get("start_delay", 2.0))
        warning_delay = float(params.get("warning_delay", 2.0))
        hero_setup_delay = float(params.get("hero_setup_delay", 2.0))
        hero_select_delay = float(params.get("hero_select_delay", 1.0))
        hero_dark_check_enabled = bool(params.get("hero_dark_check_enabled", True))
        hero_dark_threshold = float(params.get("hero_dark_threshold", 0.32))
        hero_dark_sample_radius = max(1, int(params.get("hero_dark_sample_radius", 24)))
        combat_delay = float(params.get("combat_delay", 2.0))
        skip_delay = float(params.get("skip_delay", 3.0))
        battlefield_switch_delay = float(params.get("battlefield_switch_delay", 2.0))
        stage_transition_delay = float(params.get("stage_transition_delay", 3.0))
        stage_count = int(params.get("stage_count", 20))
        stage_retry_limit = max(1, int(params.get("stage_retry_limit", 3)))
        post_warning_start_delay = float(params.get("post_warning_start_delay", start_delay))

        world_template = params.get("world_button_template") or "world_button"
        world_threshold = float(params.get("world_button_threshold", 0.7))
        world_paths = self._template_paths(ctx, world_template)
        battle_ready_paths = self._template_paths(ctx, params.get("battle_ready_template"))
        battle_ready_threshold = float(params.get("battle_ready_threshold", 0.82))
        battle_ready_timeout = float(params.get("battle_ready_timeout", 6.0))
        color_template_map = self._build_color_template_map(
            ctx, params.get("battlefield_color_templates")
        )
        color_detect_threshold = float(params.get("battlefield_color_threshold", 0.82))
        color_detect_timeout = float(params.get("battlefield_color_timeout", 4.0))
        color_detect_poll = float(params.get("battlefield_color_poll", 0.5))
        completed_template_map = self._build_color_template_map(
            ctx, params.get("battlefield_completed_templates")
        )
        completed_threshold = float(params.get("battlefield_completed_threshold", 0.85))
        battlefield_switch_attempts = max(1, int(params.get("battlefield_switch_attempts", 3)))
        post_field_delay = float(params.get("post_field_delay", 5.0))

        previous_field_completed = False
        for spec in battlefields:
            if spec.switch_coord is not None and previous_field_completed and post_field_delay > 0:
                ctx.device.sleep(post_field_delay)
                previous_field_completed = False

            detected_color, color_verified = self._focus_battlefield_color(
                ctx,
                spec,
                color_template_map,
                color_detect_threshold,
                color_detect_timeout,
                color_detect_poll,
                spec.switch_coord,
                battlefield_switch_delay,
                battlefield_switch_attempts,
            )

            if color_template_map and not color_verified:
                ctx.console.log(
                    f"[warning] No se pudo confirmar el campo '{spec.color}'; se detectó '{detected_color or 'desconocido'}'. Se aborta la caravana."
                )
                break

            color_name = detected_color or spec.color
            color_coord = color_buttons.get(color_name.lower())
            if color_coord is None and detected_color:
                ctx.console.log(
                    f"[warning] No hay botón configurado para el color detectado '{color_name}'; usando '{spec.color}'"
                )
                color_name = spec.color
                color_coord = color_buttons.get(color_name.lower())
            if color_coord is None:
                ctx.console.log(
                    f"[warning] No hay botón configurado para el color '{color_name}' (campo '{spec.name}')"
                )
                continue

            if self._is_completed_field(
                ctx,
                color_name,
                completed_template_map,
                completed_threshold,
            ):
                ctx.console.log(
                    f"Campo '{color_name}' ya completado; avanzando al siguiente"
                )
                continue

            completed_paths = completed_template_map.get(color_name.lower(), [])

            success = self._run_battlefield(
                ctx,
                spec,
                prev_level,
                start_button,
                color_name,
                color_coord,
                hero_coords,
                completed_paths,
                completed_threshold,
                hero_dark_check_enabled,
                hero_dark_threshold,
                hero_dark_sample_radius,
                skip_button,
                continue_paths,
                level_select_delay,
                start_delay,
                warning_delay,
                post_warning_start_delay,
                hero_setup_delay,
                hero_select_delay,
                combat_delay,
                skip_delay,
                stage_transition_delay,
                stage_count,
                stage_retry_limit,
                continue_threshold,
                continue_timeout,
                battle_ready_paths,
                battle_ready_threshold,
                battle_ready_timeout,
                start_template_paths,
                start_threshold,
                start_timeout,
                warning_template_paths,
                warning_threshold,
                warning_timeout,
                warning_accept_template_paths,
                warning_accept_threshold,
                pre_combat_paths,
                pre_combat_threshold,
                pre_combat_timeout,
                post_combat_paths,
                post_combat_threshold,
                post_combat_timeout,
                start_template_label,
            )
            if not success:
                ctx.console.log(
                    f"[warning] Se abortó la caravana durante el campo '{spec.name}'"
                )
                break
            previous_field_completed = True

        if tap_back_button(ctx, label="caravan-exit"):
            ctx.console.log("Regresando a la pantalla principal")
            ctx.device.sleep(2.0)
        else:
            ctx.console.log("[warning] No se detectó el botón 'back' para cerrar la caravana")

        if world_paths:
            result = ctx.vision.find_any_template(world_paths, threshold=world_threshold)
            if result:
                ctx.console.log("Pantalla principal confirmada tras la caravana")
            else:
                ctx.console.log(
                    "[warning] No se detectó el botón del mundo tras la caravana; verifica manualmente"
                )

    def _run_battlefield(
        self,
        ctx: TaskContext,
        spec: BattlefieldSpec,
        prev_level: Coord,
        start_button: Coord | None,
        color_name: str,
        color_button: Coord,
        hero_coords: Sequence[Coord],
        completed_paths: Sequence[Any],
        completed_threshold: float,
        hero_dark_check_enabled: bool,
        hero_dark_threshold: float,
        hero_dark_sample_radius: int,
        skip_button: Coord,
        continue_paths: Sequence[Any],
        level_select_delay: float,
        start_delay: float,
        warning_delay: float,
        post_warning_start_delay: float,
        hero_setup_delay: float,
        hero_select_delay: float,
        combat_delay: float,
        skip_delay: float,
        stage_transition_delay: float,
        stage_count: int,
        stage_retry_limit: int,
        continue_threshold: float,
        continue_timeout: float,
        battle_ready_paths: Sequence[Any],
        battle_ready_threshold: float,
        battle_ready_timeout: float,
        start_template_paths: Sequence[Any],
        start_threshold: float,
        start_timeout: float,
        warning_template_paths: Sequence[Any],
        warning_threshold: float,
        warning_timeout: float,
        warning_accept_template_paths: Sequence[Any],
        warning_accept_threshold: float,
        pre_combat_paths: Sequence[Any],
        pre_combat_threshold: float,
        pre_combat_timeout: float,
        post_combat_paths: Sequence[Any],
        post_combat_threshold: float,
        post_combat_timeout: float,
        start_template_label: str,
    ) -> bool:
        ctx.console.log(f"Comenzando campo '{spec.name}' ({color_name})")
        self._tap(ctx, prev_level, f"caravan-{spec.name}-level", level_select_delay)

        if not self._tap_start(
            ctx,
            start_template_paths,
            start_button,
            start_threshold,
            start_timeout,
            start_template_label,
            start_delay,
        ):
            return False

        warning_displayed = False
        if warning_template_paths and ctx.vision:
            warning_result = ctx.vision.wait_for_any_template(
                warning_template_paths,
                timeout=warning_timeout,
                threshold=warning_threshold,
                poll_interval=0.5,
                raise_on_timeout=False,
            )
            if warning_result:
                warning_displayed = True
                _, matched = warning_result
                ctx.console.log(
                    f"Advertencia detectada en campo '{spec.name}' usando '{matched.name}'"
                )
                if not warning_accept_template_paths:
                    ctx.console.log(
                        "[warning] No hay template configurado para aceptar la advertencia"
                    )
                    return False
                if not self._tap_template_button(
                    ctx,
                    warning_accept_template_paths,
                    warning_accept_threshold,
                    warning_timeout,
                    f"caravan-{spec.name}-warning-accept",
                ):
                    return False
                if warning_delay > 0:
                    ctx.device.sleep(warning_delay)

        if warning_displayed:
            if not self._tap_start(
                ctx,
                start_template_paths,
                start_button,
                start_threshold,
                start_timeout,
                start_template_label,
                post_warning_start_delay,
            ):
                return False

        if not self._tap_combat_button(
            ctx,
            pre_combat_paths,
            pre_combat_threshold,
            pre_combat_timeout,
            f"caravan-{spec.name}-pre-combat",
            combat_delay,
        ):
            return False

        if hero_setup_delay > 0:
            ctx.device.sleep(hero_setup_delay)

        if battle_ready_paths:
            ready = ctx.vision.wait_for_any_template(
                battle_ready_paths,
                timeout=battle_ready_timeout,
                threshold=battle_ready_threshold,
                poll_interval=0.5,
                raise_on_timeout=False,
            )
            if ready:
                _, matched = ready
                ctx.console.log(
                    f"Pantalla de combate lista (template '{matched.name}')"
                )
            else:
                ctx.console.log(
                    "[warning] No se pudo confirmar la pantalla de combate antes de seleccionar héroes; se aborta el campo"
                )
                return False

        self._tap(ctx, color_button, f"caravan-color-{color_name}", hero_select_delay)
        skip_hero_selection = False
        if hero_dark_check_enabled:
            skip_hero_selection = self._all_slots_dark(
                ctx,
                hero_coords,
                hero_dark_threshold,
                hero_dark_sample_radius,
            )
            if skip_hero_selection:
                ctx.console.log(
                    "Todas las ranuras de héroe se ven oscuras; saltando la selección manual"
                )
        if not skip_hero_selection:
            for idx, coord in enumerate(hero_coords, start=1):
                self._tap(ctx, coord, f"caravan-hero-{idx}", hero_select_delay)
            if hero_dark_check_enabled:
                self._ensure_slots_selected(
                    ctx,
                    hero_coords,
                    hero_dark_threshold,
                    hero_dark_sample_radius,
                    hero_select_delay,
                )

        if not self._tap_combat_button(
            ctx,
            post_combat_paths,
            post_combat_threshold,
            post_combat_timeout,
            f"caravan-{spec.name}-confirm-combat",
            combat_delay,
        ):
            return False

        if stage_transition_delay > 0:
            ctx.device.sleep(stage_transition_delay)

        for stage in range(1, stage_count + 1):
            ctx.console.log(f"Campo '{spec.name}': etapa {stage}/{stage_count}")
            completed = self._complete_stage(
                ctx,
                skip_button,
                continue_paths,
                skip_delay,
                stage_retry_limit,
                continue_threshold,
                continue_timeout,
            )
            if not completed:
                if self._field_is_marked_completed(
                    ctx,
                    completed_paths,
                    completed_threshold,
                ):
                    ctx.console.log(
                        f"Campo '{spec.name}' mostró la pantalla de nivel completado; avanzando"
                    )
                    return True
                return False
            if stage < stage_count and stage_transition_delay > 0:
                ctx.device.sleep(stage_transition_delay)
        ctx.console.log(f"Campo '{spec.name}' finalizado")
        return True

    def _complete_stage(
        self,
        ctx: TaskContext,
        skip_button: Coord,
        continue_paths: Sequence[Any],
        skip_delay: float,
        stage_retry_limit: int,
        continue_threshold: float,
        continue_timeout: float,
    ) -> bool:
        if not continue_paths:
            ctx.device.tap(skip_button, label="caravan-skip")
            if skip_delay > 0:
                ctx.device.sleep(skip_delay)
            ctx.device.tap(skip_button, label="caravan-continue")
            if skip_delay > 0:
                ctx.device.sleep(skip_delay)
            return True

        for attempt in range(1, stage_retry_limit + 1):
            ctx.device.tap(skip_button, label=f"caravan-skip#{attempt}")
            if skip_delay > 0:
                ctx.device.sleep(skip_delay)
            result = ctx.vision.wait_for_any_template(
                continue_paths,
                timeout=continue_timeout,
                poll_interval=0.5,
                threshold=continue_threshold,
                raise_on_timeout=False,
            )
            if result:
                continue_coords, matched_path = result
                ctx.console.log(
                    f"'Seguir explorando' detectado con '{matched_path.name}'"
                )
                ctx.device.tap(continue_coords, label="caravan-continue")
                if skip_delay > 0:
                    ctx.device.sleep(skip_delay)
                return True
            ctx.console.log(
                f"[warning] No apareció 'seguir explorando' (intento {attempt}/{stage_retry_limit})"
            )
        return False

    def _ensure_slots_selected(
        self,
        ctx: TaskContext,
        hero_coords: Sequence[Coord],
        threshold: float,
        sample_radius: int,
        hero_select_delay: float,
    ) -> None:
        if not hero_coords or not ctx.vision:
            return
        missing = self._detect_dark_slots(
            ctx,
            hero_coords,
            threshold,
            sample_radius,
        )
        if not missing:
            return
        for idx in missing:
            coord = hero_coords[idx - 1]
            ctx.console.log(
                f"Ranura de héroe #{idx} no parece seleccionada; reintentando"
            )
            self._tap(ctx, coord, f"caravan-hero-retry-{idx}", hero_select_delay)

    def _all_slots_dark(
        self,
        ctx: TaskContext,
        hero_coords: Sequence[Coord],
        threshold: float,
        sample_radius: int,
    ) -> bool:
        if not hero_coords or not ctx.vision:
            return False
        missing = self._detect_dark_slots(
            ctx,
            hero_coords,
            threshold,
            sample_radius,
        )
        if missing is None:
            return False
        return len(missing) == 0

    def _detect_dark_slots(
        self,
        ctx: TaskContext,
        hero_coords: Sequence[Coord],
        threshold: float,
        sample_radius: int,
    ) -> List[int] | None:
        if not hero_coords or not ctx.vision:
            return None
        screenshot = ctx.vision.capture()
        if screenshot is None:
            ctx.console.log(
                "[warning] No se pudo capturar pantalla para verificar ranuras de héroes"
            )
            return None
        gray = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
        height, width = gray.shape[:2]
        radius = max(1, sample_radius)
        dark_slots: List[int] = []
        for idx, (x, y) in enumerate(hero_coords, start=1):
            x1 = max(int(x) - radius, 0)
            y1 = max(int(y) - radius, 0)
            x2 = min(int(x) + radius, width - 1)
            y2 = min(int(y) + radius, height - 1)
            if x1 >= x2 or y1 >= y2:
                ctx.console.log(
                    f"[info] No se pudo medir brillo en el héroe #{idx}; se continuará con la selección"
                )
                return None
            region = gray[y1:y2, x1:x2]
            if region.size == 0:
                ctx.console.log(
                    f"[info] Región vacía al medir héroe #{idx}; se continuará con la selección"
                )
                return None
            brightness = float(region.mean()) / 255.0
            ctx.console.log(
                f"Brillo ranura héroe #{idx}: {brightness:.2f} (umbral {threshold:.2f})"
            )
            if brightness > threshold:
                dark_slots.append(idx)
        return dark_slots

    def _field_is_marked_completed(
        self,
        ctx: TaskContext,
        completed_paths: Sequence[Any],
        threshold: float,
    ) -> bool:
        if not completed_paths or not ctx.vision:
            return False
        result = ctx.vision.find_any_template(
            completed_paths,
            threshold=threshold,
        )
        return bool(result)

    def _build_battlefields(
        self,
        ctx: TaskContext,
        raw: Any,
    ) -> List[BattlefieldSpec]:
        specs: List[BattlefieldSpec] = []
        if not isinstance(raw, Sequence):
            return specs
        for entry in raw:
            if not isinstance(entry, Mapping):
                continue
            name = str(entry.get("name") or entry.get("color") or f"field-{len(specs) + 1}")
            color = str(entry.get("color") or name).lower()
            switch_coord = self._coord_from_value(
                ctx,
                entry.get("switch_button"),
                f"switch_button:{name}",
            )
            specs.append(BattlefieldSpec(name=name, color=color, switch_coord=switch_coord))
        return specs

    def _build_color_map(self, ctx: TaskContext, raw: Any) -> Dict[str, Coord]:
        mapping: Dict[str, Coord] = {}
        if not isinstance(raw, Mapping):
            return mapping
        for color, value in raw.items():
            coord = self._coord_from_value(ctx, value, f"color:{color}")
            if coord:
                mapping[str(color).lower()] = coord
        return mapping

    def _build_color_template_map(self, ctx: TaskContext, raw: Any) -> Dict[str, List[Any]]:
        mapping: Dict[str, List[Any]] = {}
        if not isinstance(raw, Mapping):
            return mapping
        for color, template_spec in raw.items():
            paths = self._template_paths(ctx, template_spec)
            if paths:
                mapping[str(color).lower()] = list(paths)
        return mapping

    def _is_completed_field(
        self,
        ctx: TaskContext,
        color_name: str,
        completed_templates: Mapping[str, Sequence[Any]],
        threshold: float,
    ) -> bool:
        if not completed_templates or not ctx.vision:
            return False
        paths = completed_templates.get(color_name.lower())
        if not paths:
            return False
        result = ctx.vision.find_any_template(paths, threshold=threshold)
        return bool(result)

    def _build_hero_coords(self, ctx: TaskContext, raw: Any) -> List[Coord]:
        coords: List[Coord] = []
        if not isinstance(raw, Sequence):
            return coords
        for idx, value in enumerate(raw, start=1):
            coord = self._coord_from_value(ctx, value, f"hero:{idx}")
            if coord:
                coords.append(coord)
        return coords

    def _coord_from_value(
        self,
        ctx: TaskContext,
        value: Any,
        label: str,
    ) -> Coord | None:
        if value is None:
            return None
        if isinstance(value, str):
            try:
                return resolve_button(ctx.layout, value)
            except KeyError:
                ctx.console.log(
                    f"[warning] Botón '{value}' no está definido en el layout (referencia {label})"
                )
                return None
        if isinstance(value, Sequence) and len(value) == 2:
            try:
                return int(value[0]), int(value[1])
            except (TypeError, ValueError):
                ctx.console.log(f"[warning] Coordenada inválida para {label}: {value}")
                return None
        ctx.console.log(f"[warning] Valor inválido para {label}: {value}")
        return None

    def _tap(self, ctx: TaskContext, coord: Coord | None, label: str, delay: float) -> None:
        if coord is None:
            ctx.console.log(f"[warning] No se pudo realizar tap en '{label}' (coordenada ausente)")
            return
        ctx.device.tap(coord, label=label)
        if delay > 0:
            ctx.device.sleep(delay)

    def _tap_template_button(
        self,
        ctx: TaskContext,
        template_paths: Sequence[Any],
        threshold: float,
        timeout: float,
        label: str,
        delay: float = 0.0,
    ) -> bool:
        if not template_paths:
            return False
        result = ctx.vision.wait_for_any_template(
            template_paths,
            timeout=timeout,
            poll_interval=0.5,
            threshold=threshold,
            raise_on_timeout=False,
        )
        if not result:
            ctx.console.log(f"[warning] No se encontró template para '{label}' dentro del tiempo esperado")
            return False
        coords, matched = result
        ctx.console.log(f"Template '{matched.name}' detectado para '{label}'")
        ctx.device.tap(coords, label=label)
        if delay > 0:
            ctx.device.sleep(delay)
        return True

    def _tap_start(
        self,
        ctx: TaskContext,
        template_paths: Sequence[Any],
        fallback_button: Coord | None,
        threshold: float,
        timeout: float,
        label: str,
        delay: float,
    ) -> bool:
        if template_paths and ctx.vision:
            result = ctx.vision.wait_for_any_template(
                template_paths,
                timeout=timeout,
                poll_interval=0.5,
                threshold=threshold,
                raise_on_timeout=False,
            )
            if result:
                coords, matched = result
                ctx.console.log(f"Template '{matched.name}' detectado para '{label}'")
                ctx.device.tap(coords, label=label)
                if delay > 0:
                    ctx.device.sleep(delay)
                self._last_start_coords = coords
                return True
            ctx.console.log(
                f"[warning] No se encontró template para '{label}' dentro del tiempo esperado"
            )
        if fallback_button is not None:
            self._tap(ctx, fallback_button, label, delay)
            self._last_start_coords = fallback_button
            return True
        if self._last_start_coords is not None:
            ctx.console.log(
                f"[warning] Reutilizando la última coordenada conocida para '{label}'"
            )
            self._tap(ctx, self._last_start_coords, label, delay)
            return True
        ctx.console.log(
            f"[warning] No hay template ni coordenada disponible para '{label}'"
        )
        return False

    def _resolve_template_label(self, template_spec: Any, default_label: str) -> str:
        if isinstance(template_spec, str) and template_spec.strip():
            return template_spec.strip()
        if isinstance(template_spec, Sequence):
            for entry in template_spec:
                name = str(entry).strip()
                if name:
                    return name
        return default_label

    def _tap_combat_button(
        self,
        ctx: TaskContext,
        template_paths: Sequence[Any],
        threshold: float,
        timeout: float,
        label: str,
        delay: float,
    ) -> bool:
        if not template_paths:
            ctx.console.log(f"[warning] No hay template configurado para '{label}'")
            return False
        return self._tap_template_button(
            ctx,
            template_paths,
            threshold,
            timeout,
            label,
            delay,
        )

    def _template_paths(self, ctx: TaskContext, template_spec: Any) -> List[Any]:
        if template_spec is None:
            return []
        names: List[str] = []
        if isinstance(template_spec, str):
            names = [template_spec]
        elif isinstance(template_spec, Sequence):
            for entry in template_spec:
                name = str(entry).strip()
                if name:
                    names.append(name)
        else:
            ctx.console.log(f"[warning] Referencia de template inválida: {template_spec}")
            return []

        paths: List[Any] = []
        for name in names:
            try:
                paths.extend(ctx.layout.template_paths(name))
            except KeyError:
                self._log_missing_template(ctx, name)
        return paths

    def _log_missing_template(self, ctx: TaskContext, name: str) -> None:
        if name in self._missing_templates:
            return
        self._missing_templates.add(name)
        ctx.console.log(f"[warning] Template '{name}' no está definido en el layout")

    def _detect_battlefield_color(
        self,
        ctx: TaskContext,
        color_templates: Mapping[str, Sequence[Any]],
        threshold: float,
        timeout: float,
        poll_interval: float,
    ) -> str | None:
        if not color_templates or not ctx.vision:
            return None
        template_paths: List[Any] = []
        template_to_color: Dict[Any, str] = {}
        for color, paths in color_templates.items():
            for path in paths:
                template_paths.append(path)
                template_to_color[path] = color
        if not template_paths:
            return None
        result = ctx.vision.wait_for_any_template(
            template_paths,
            timeout=timeout,
            poll_interval=poll_interval,
            threshold=threshold,
            raise_on_timeout=False,
        )
        if not result:
            return None
        _, matched_path = result
        color = template_to_color.get(matched_path)
        if color is None:
            for color_name, paths in color_templates.items():
                if matched_path in paths:
                    color = color_name
                    break
        if color:
            ctx.console.log(f"Campo actual detectado como '{color}'")
        return color

    def _focus_battlefield_color(
        self,
        ctx: TaskContext,
        spec: BattlefieldSpec,
        color_templates: Mapping[str, Sequence[Any]],
        threshold: float,
        timeout: float,
        poll_interval: float,
        switch_coord: Coord | None,
        switch_delay: float,
        max_attempts: int,
    ) -> Tuple[str | None, bool]:
        expected = spec.color.lower()
        if not color_templates:
            if switch_coord is not None:
                self._tap(ctx, switch_coord, f"caravan-switch-{spec.name}", switch_delay)
            return None, True

        if switch_coord is None:
            detected = self._detect_battlefield_color(
                ctx, color_templates, threshold, timeout, poll_interval
            )
            if detected is None:
                return None, False
            return detected, detected == expected

        attempts = max(1, max_attempts)
        last_detected: str | None = None
        for attempt in range(1, attempts + 1):
            self._tap(
                ctx,
                switch_coord,
                f"caravan-switch-{spec.name}#{attempt}",
                switch_delay,
            )
            last_detected = self._detect_battlefield_color(
                ctx, color_templates, threshold, timeout, poll_interval
            )
            if last_detected == expected:
                return last_detected, True
            msg = last_detected or "desconocido"
            ctx.console.log(
                f"[warning] Campo detectado '{msg}' tras cambio '{spec.name}' (esperado '{expected}')"
            )
        return last_detected, False
