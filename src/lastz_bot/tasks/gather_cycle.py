"""Automatiza envíos de recolección rotando recursos y niveles en el mapa mundial."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import cv2

from .base import TaskContext
from ..troop_state import (
    TroopActivity,
    TroopSlotStatus,
    describe_activity,
    detect_departing_slot,
    detect_troop_states,
    idle_slots as detect_idle_slots,
    layout_supports_troop_states,
    resolve_slot_for_tap,
    wait_for_idle_slots,
    wait_for_slot_state_change,
)


Coord = Tuple[int, int]


@dataclass
class GatherConfig:
    """Parámetros de plantillas, tiempos y límites usados por `GatherCycleTask`."""
    idle_template_names: List[str]
    search_icon_templates: List[str]
    resource_templates: Dict[str, List[str]]
    search_button_templates: List[str]
    level_increase_templates: List[str]
    level_decrease_templates: List[str]
    gather_button_templates: List[str]
    march_button_templates: List[str]
    sede_button_templates: List[str]
    world_button_templates: List[str]
    level_indicator_templates: Dict[int, List[str]]
    level_indicator_regions: Dict[int, Tuple[Tuple[float, float], Tuple[float, float]]]
    resource_priority: List[str]
    preferred_idle_slots: List[str]
    empty_troop_template_names: List[str]
    empty_troop_threshold: float
    resource_tab_button: str | None
    resource_tab_delay: float
    max_troops: int
    max_level: int
    min_level: int
    idle_threshold: float
    idle_max_matches: int
    idle_tap_offset: Coord
    drag_start: Coord
    drag_end: Coord
    drag_duration_ms: int
    map_result_tap: Coord
    search_panel_settle_delay: float
    post_drag_delay: float
    resource_select_delay: float
    level_adjust_delay: float
    focus_result_delay: float
    troop_select_delay: float
    return_home_delay: float
    idle_detection_delay: float
    idle_detection_poll: float
    world_transition_delay: float
    search_timeout: float
    search_retry_delay: float
    gather_timeout: float
    pre_troop_delay: float
    post_march_delay: float
    troop_state_sample_delay: float
    idle_clear_timeout: float
    idle_clear_poll: float
    template_threshold: float
    level_indicator_threshold: float
    world_button_threshold: float
    sede_button_threshold: float
    level_detection_order: str

    @staticmethod
    def from_params(params: Dict[str, object]) -> "GatherConfig":
        """Convierte parámetros genéricos (YAML/JSON) a un `GatherConfig` tipado."""
        def as_list(value: object) -> List[str]:
            if value is None:
                return []
            if isinstance(value, str):
                return [value]
            return [str(item) for item in value]

        def as_coord(value: object, default: Coord) -> Coord:
            if isinstance(value, (list, tuple)) and len(value) == 2:
                return int(value[0]), int(value[1])
            return default

        resource_templates_raw = params.get("resource_templates", {}) or {}
        resource_templates: Dict[str, List[str]] = {}
        for key, value in resource_templates_raw.items():
            resource_templates[key] = as_list(value)

        level_indicator_raw = params.get("level_indicator_templates", {}) or {}
        level_indicator_templates: Dict[int, List[str]] = {}
        level_indicator_regions: Dict[int, Tuple[Tuple[float, float], Tuple[float, float]]] = {}
        for key, value in level_indicator_raw.items():
            try:
                level_key = int(key)
            except (TypeError, ValueError):
                continue
            level_indicator_templates[level_key] = as_list(value)
            region_key = f"level_indicator_region_{level_key}"
            region_value = params.get(region_key)
            if (
                isinstance(region_value, (list, tuple))
                and len(region_value) == 2
            ):
                y_range, x_range = region_value
                if (
                    isinstance(y_range, (list, tuple))
                    and isinstance(x_range, (list, tuple))
                    and len(y_range) == len(x_range) == 2
                ):
                    level_indicator_regions[level_key] = (
                        (float(y_range[0]), float(y_range[1])),
                        (float(x_range[0]), float(x_range[1])),
                    )

        detection_order = str(params.get("level_detection_order", "desc")).lower()
        if detection_order not in ("asc", "desc"):
            detection_order = "desc"

        return GatherConfig(
            idle_template_names=as_list(params.get("idle_template")),
            search_icon_templates=as_list(params.get("search_icon_template")),
            resource_templates=resource_templates,
            search_button_templates=as_list(params.get("search_button_template")),
            level_increase_templates=as_list(params.get("level_increase_template")),
            level_decrease_templates=as_list(params.get("level_decrease_template")),
            gather_button_templates=as_list(params.get("gather_button_template")),
            march_button_templates=as_list(params.get("march_button_template")),
            sede_button_templates=as_list(params.get("sede_button_template")),
            world_button_templates=as_list(params.get("world_button_template")),
            level_indicator_templates=level_indicator_templates,
            level_indicator_regions=level_indicator_regions,
            resource_priority=as_list(
                params.get("resource_priority", ["wood", "food"])
            ),
            preferred_idle_slots=[
                entry.strip().lower()
                for entry in as_list(params.get("preferred_idle_slots"))
                if entry.strip()
            ],
            empty_troop_template_names=as_list(params.get("empty_troop_templates")),
            empty_troop_threshold=float(
                params.get(
                    "empty_troop_threshold",
                    params.get("template_threshold", 0.85),
                )
            ),
            resource_tab_button=str(params.get("resource_tab_button"))
            if params.get("resource_tab_button")
            else None,
            resource_tab_delay=float(params.get("resource_tab_delay", 0.5)),
            max_troops=int(params.get("max_troops", 2)),
            max_level=int(params.get("max_level", 6)),
            min_level=int(params.get("min_level", 1)),
            idle_threshold=float(params.get("idle_template_threshold", 0.85)),
            idle_max_matches=int(params.get("idle_max_matches", 3)),
            idle_tap_offset=as_coord(params.get("idle_tap_offset"), (0, 0)),
            drag_start=as_coord(params.get("drag_start"), (460, 630)),
            drag_end=as_coord(params.get("drag_end"), (80, 630)),
            drag_duration_ms=int(params.get("drag_duration_ms", 600)),
            map_result_tap=as_coord(params.get("map_result_tap"), (270, 480)),
            search_panel_settle_delay=float(params.get("search_panel_settle_delay", 3.0)),
            post_drag_delay=float(params.get("post_drag_delay", 3.0)),
            resource_select_delay=float(params.get("resource_select_delay", 3.0)),
            level_adjust_delay=float(params.get("level_adjust_delay", 3.0)),
            focus_result_delay=float(params.get("focus_result_delay", 3.0)),
            troop_select_delay=float(params.get("troop_select_delay", 3.0)),
            return_home_delay=float(params.get("return_home_delay", 3.0)),
            idle_detection_delay=float(params.get("idle_detection_delay", 3.0)),
            idle_detection_poll=float(params.get("idle_detection_poll", 0.5)),
            world_transition_delay=float(params.get("world_transition_delay", 2.0)),
            search_timeout=float(params.get("search_timeout", 6.0)),
            search_retry_delay=float(params.get("search_retry_delay", 3.0)),
            gather_timeout=float(params.get("gather_timeout", 7.0)),
            pre_troop_delay=float(params.get("pre_troop_delay", 3.0)),
            post_march_delay=float(params.get("post_march_delay", 3.0)),
            troop_state_sample_delay=float(params.get("troop_state_sample_delay", 1.5)),
            idle_clear_timeout=float(params.get("idle_clear_timeout", 10.0)),
            idle_clear_poll=float(params.get("idle_clear_poll", 1.0)),
            template_threshold=float(params.get("template_threshold", 0.85)),
            level_indicator_threshold=float(
                params.get("level_indicator_threshold", params.get("template_threshold", 0.85))
            ),
            world_button_threshold=float(
                params.get("world_button_threshold", params.get("template_threshold", 0.85))
            ),
            sede_button_threshold=float(
                params.get("sede_button_threshold", params.get("template_threshold", 0.85))
            ),
            level_detection_order=detection_order,
        )


class GatherCycleTask:
    """Controla el flujo completo de búsqueda y envío de tropas recolectoras."""
    name = "gather_cycle"

    def __init__(self) -> None:
        self._missing_templates: set[str] = set()
        self._missing_buttons: set[str] = set()

    def _await_troop_state_sample(
        self,
        ctx: TaskContext,
        config: GatherConfig,
        *,
        minimum: float = 0.0,
    ) -> None:
        """Inserta un delay mínimo antes de consultar estados de tropas."""
        delay = max(config.troop_state_sample_delay, minimum)
        if delay > 0:
            ctx.device.sleep(delay)

    def run(self, ctx: TaskContext, params: Dict[str, object]) -> None:  # type: ignore[override]
        """Ejecuta envíos consecutivos hasta agotar tropas o configuraciones válidas."""
        if not ctx.vision:
            ctx.console.log(
                "[warning] VisionHelper no disponible; gather_cycle requiere detecciones"
            )
            return

        params = dict(params)
        self._apply_level_override(ctx, params)
        config = GatherConfig.from_params(params)
        self._missing_templates.clear()
        manual_dispatches = 0
        while True:
            if manual_dispatches >= config.max_troops:
                ctx.console.log(
                    "Límite de envíos alcanzado según configuracion local; se detiene el ciclo"
                )
                break
            if not self._ensure_world_scene(ctx, config):
                ctx.console.log(
                    "No se pudo entrar al mapa del mundo; abortando gather_cycle"
                )
                break
            if not self._log_and_enforce_limit(
                ctx,
                config,
                manual_dispatches,
                context="Tras abrir el mapa",
            ):
                break
            idle_slots = self._available_idle_slots(
                ctx,
                config,
                min_required=1,
                wait_for_slots=True,
            )
            if not idle_slots:
                ctx.console.log("No hay tropas libres para enviar; finalizando rutina")
                break

            ctx.console.log(
                f"Tropas en descanso detectadas: {len(idle_slots)} (envío #{manual_dispatches + 1})"
            )
            selected_idle = idle_slots[0]
            success = self._dispatch_single(ctx, config, selected_idle)
            if not success:
                ctx.console.log("No se pudo completar el envío actual; deteniendo gather_cycle")
                break
            manual_dispatches += 1

        self._return_home(ctx, config)

    # --- flujo principal -------------------------------------------------
    def _dispatch_single(
        self,
        ctx: TaskContext,
        config: GatherConfig,
        idle_slot: TroopSlotStatus,
    ) -> bool:
        """Realiza un envío completo: busca recurso, enfoca, inicia y asigna tropa."""
        if not self._open_search_panel(ctx, config):
            return False
        if not config.resource_priority:
            ctx.console.log("[warning] No hay recursos configurados para gather_cycle")
            return False

        primary_resource = config.resource_priority[0]
        if not self._select_resource(ctx, config, primary_resource):
            ctx.console.log(
                "[warning] No se pudo seleccionar ningún recurso tras abrir la lupa"
            )
            return False
        current_resource = primary_resource
        self._set_level_to_max(ctx, config)

        level = config.max_level
        found_site = False
        while level >= config.min_level:
            ctx.console.log(f"Intentando nivel {level} con recursos {config.resource_priority}")
            for resource in config.resource_priority:
                if resource != current_resource:
                    if not self._select_resource(ctx, config, resource):
                        continue
                    current_resource = resource
                self._ensure_level_alignment(ctx, config, level)
                if self._perform_search(ctx, config, level):
                    found_site = True
                    break
                ctx.console.log(
                    f"[info] No se encontró recurso '{resource}' nivel {level}; probando siguiente recurso"
                )
            if found_site:
                break
            level -= 1
            if level < config.min_level:
                break
            self._adjust_level(ctx, config, direction="down")

        if not found_site:
            ctx.console.log("No se encontró punto de recolección en los niveles configurados")
            return False

        if not self._focus_search_result(ctx, config):
            return False
        if not self._start_gather(ctx, config):
            return False
        if not self._assign_troop(ctx, config, idle_slot):
            return False
        return True

    def _apply_level_override(
        self, ctx: TaskContext, params: Dict[str, object]
    ) -> None:
        """Permite que cada granja defina min/max level específicos en el config."""
        overrides = params.get("level_overrides")
        if not isinstance(overrides, dict):
            return
        farm_name = ctx.farm.name
        entry = overrides.get(farm_name)
        if not isinstance(entry, dict):
            return
        for key in ("min_level", "max_level"):
            if key in entry:
                try:
                    params[key] = int(entry[key])
                except (TypeError, ValueError):
                    continue

    # --- pasos individuales ---------------------------------------------
    def _ensure_world_scene(self, ctx: TaskContext, config: GatherConfig) -> bool:
        """Asegura que la cámara esté en el mapa mundial detectando sede/mundo."""
        if self._wait_for_template_group(
            ctx,
            config.sede_button_templates,
            label="sede-check",
            timeout=1.5,
            threshold=config.sede_button_threshold,
        ):
            return True

        if not self._tap_template_group(
            ctx,
            config.world_button_templates,
            label="world-button",
            timeout=5.0,
            threshold=config.world_button_threshold,
        ):
            return False
        ctx.device.sleep(config.world_transition_delay)
        return self._wait_for_template_group(
            ctx,
            config.sede_button_templates,
            label="sede-check",
            timeout=4.0,
            threshold=config.sede_button_threshold,
        )

    def _open_search_panel(self, ctx: TaskContext, config: GatherConfig) -> bool:
        """Abre la lupa de recursos y desplaza el panel para mostrar niveles."""
        if not self._tap_template_group(
            ctx,
            config.search_icon_templates,
            label="open-search",
            timeout=config.search_timeout,
            threshold=config.template_threshold,
        ):
            return False
        ctx.device.sleep(config.search_panel_settle_delay)
        ctx.device.swipe(
            config.drag_start,
            config.drag_end,
            duration_ms=config.drag_duration_ms,
            label="search-drag",
        )
        ctx.device.sleep(config.post_drag_delay)
        return True

    def _select_resource(
        self,
        ctx: TaskContext,
        config: GatherConfig,
        resource: str,
        *,
        settle: bool = True,
    ) -> bool:
        """Selecciona un recurso específico en el panel según sus templates."""
        self._ensure_resource_tab(ctx, config)
        template_names = config.resource_templates.get(resource, [])
        if not template_names:
            ctx.console.log(
                f"[warning] No hay templates configurados para el recurso '{resource}'"
            )
            return False
        if self._tap_template_group(
            ctx,
            template_names,
            label=f"resource-{resource}",
            timeout=2.5,
            threshold=config.template_threshold,
        ):
            ctx.console.log(f"Seleccionado recurso {resource}")
            if settle and config.resource_select_delay > 0:
                ctx.device.sleep(config.resource_select_delay)
            return True
        return False

    def _ensure_resource_tab(self, ctx: TaskContext, config: GatherConfig) -> None:
        """Pulsa el botón de pestaña configurado para mostrar la lista de recursos."""
        if not config.resource_tab_button:
            return
        if not self._tap_layout_button(
            ctx, config.resource_tab_button, label="resource-tab"
        ):
            return
        if config.resource_tab_delay > 0:
            ctx.device.sleep(config.resource_tab_delay)

    def _set_level_to_max(self, ctx: TaskContext, config: GatherConfig) -> None:
        """Fuerza el selector de nivel al máximo disponible para reiniciar el ciclo."""
        target = config.max_level
        current = self._detect_current_level(ctx, config)
        if current is None:
            ctx.console.log(
                "[info] Nivel actual desconocido; incrementando hasta el máximo por defecto"
            )
            for _ in range(max(config.max_level - config.min_level, 1)):
                self._tap_template_group(
                    ctx,
                    config.level_increase_templates,
                    label="level-up",
                    timeout=1.0,
                    threshold=config.template_threshold,
                )
                ctx.device.sleep(config.level_adjust_delay)
            return
        self._sync_level(ctx, config, current_level=current, target_level=target)

    def _ensure_level_alignment(
        self, ctx: TaskContext, config: GatherConfig, target_level: int
    ) -> None:
        """Verifica el nivel actual y lo mueve hasta el objetivo indicado."""
        current = self._detect_current_level(ctx, config)
        if current is None:
            ctx.console.log(
                "[info] No se pudo leer el nivel tras cambiar de recurso; restableciendo al máximo"
            )
            self._set_level_to_max(ctx, config)
            current = config.max_level
        if current is None:
            ctx.console.log(
                "[warning] Seguimos sin detectar nivel; se intentará continuar igualmente"
            )
            return
        self._sync_level(ctx, config, current_level=current, target_level=target_level)

    def _adjust_level(
        self, ctx: TaskContext, config: GatherConfig, *, direction: str
    ) -> None:
        """Realiza un tap puntual al botón up/down según la dirección solicitada."""
        templates = (
            config.level_increase_templates
            if direction == "up"
            else config.level_decrease_templates
        )
        if not templates:
            return
        self._tap_template_group(
            ctx,
            templates,
            label=f"level-{direction}",
            timeout=1.0,
            threshold=config.template_threshold,
        )
        ctx.device.sleep(config.level_adjust_delay)

    def _sync_level(
        self,
        ctx: TaskContext,
        config: GatherConfig,
        *,
        current_level: int,
        target_level: int,
    ) -> None:
        """Incrementa o decrementa repetidamente hasta igualar el nivel deseado."""
        if current_level == target_level:
            ctx.console.log(f"Nivel actual ya es {target_level}")
            return

        direction = "up" if current_level < target_level else "down"
        templates = (
            config.level_increase_templates
            if direction == "up"
            else config.level_decrease_templates
        )
        if not templates:
            ctx.console.log(
                f"[warning] No hay templates configurados para mover nivel hacia '{direction}'"
            )
            return

        step = 1 if direction == "up" else -1
        level = current_level
        while level != target_level:
            if not self._tap_template_group(
                ctx,
                templates,
                label=f"level-{direction}",
                timeout=1.0,
                threshold=config.template_threshold,
            ):
                ctx.console.log(
                    f"[warning] No se pudo ajustar nivel hacia '{direction}' (nivel actual {level})"
                )
                break
            ctx.device.sleep(config.level_adjust_delay)
            level += step

    def _perform_search(
        self, ctx: TaskContext, config: GatherConfig, level: int
    ) -> bool:
        """Pulsa el botón Search y confirma si el panel se cerró con resultados."""
        ctx.console.log(f"Buscando recurso de nivel {level}")
        if not self._tap_template_group(
            ctx,
            config.search_button_templates,
            label="search-btn",
            timeout=2.0,
            threshold=config.template_threshold,
        ):
            return False
        ctx.device.sleep(config.search_retry_delay)
        if self._is_search_panel_visible(ctx, config):
            ctx.console.log("No hubo resultados; bajando nivel")
            return False
        return True

    def _focus_search_result(self, ctx: TaskContext, config: GatherConfig) -> bool:
        """Toca la coordenada configurada para centrar la cámara en el hallazgo."""
        if config.map_result_tap:
            ctx.device.tap(config.map_result_tap, label="focus-resource")
            ctx.device.sleep(config.focus_result_delay)
        return True

    def _start_gather(self, ctx: TaskContext, config: GatherConfig) -> bool:
        """Verifica tropas libres y presiona el botón Gather, re-enfocando si falta."""
        idle = self._available_idle_slots(
            ctx,
            config,
            min_required=1,
            wait_for_slots=False,
        )
        if not idle:
            ctx.console.log(
                "[warning] No hay tropas disponibles justo antes de iniciar la recolección"
            )
            return False
        if self._tap_template_group(
            ctx,
            config.gather_button_templates,
            label="gather",
            timeout=config.gather_timeout,
            threshold=config.template_threshold,
        ):
            return True

        if config.map_result_tap:
            ctx.console.log(
                "[info] Botón de recolección no visible; re-enfocando el recurso"
            )
            ctx.device.tap(config.map_result_tap, label="refocus-resource")
            ctx.device.sleep(config.focus_result_delay)
            return self._tap_template_group(
                ctx,
                config.gather_button_templates,
                label="gather",
                timeout=config.gather_timeout,
                threshold=config.template_threshold,
            )

        return False

    def _assign_troop(
        self,
        ctx: TaskContext,
        config: GatherConfig,
        idle_slot: TroopSlotStatus,
    ) -> bool:
        """Selecciona la tropa preferida y monitoriza que salga en marcha."""
        ctx.device.sleep(config.pre_troop_delay)
        refreshed_slots = self._available_idle_slots(
            ctx,
            config,
            min_required=1,
            wait_for_slots=False,
        )
        if not refreshed_slots:
            ctx.console.log("No se encontraron tropas libres para asignar")
            return False
        target_slot = self._match_idle_slot(idle_slot, refreshed_slots)
        self._log_slot_selection(ctx, target_slot)
        tap_point = self._apply_idle_offset(target_slot.tap, config)
        ctx.device.tap(tap_point, label="select-idle-troop")
        self._await_troop_state_sample(ctx, config, minimum=config.troop_select_delay)
        monitored_slot = resolve_slot_for_tap(ctx, tap_point, fallback=target_slot) or target_slot
        if monitored_slot.slot_id != target_slot.slot_id:
            prev_label = (target_slot.label or target_slot.slot_id or "?").upper()
            new_label = (monitored_slot.label or monitored_slot.slot_id or "?").upper()
            ctx.console.log(
                f"[info] Seguimiento ajustado: se seleccionó {prev_label} pero se controlará {new_label}"
            )
        if not self._tap_template_group(
            ctx,
            config.march_button_templates,
            label="march",
            timeout=3.0,
            threshold=config.template_threshold,
        ):
            return False
        self._await_troop_state_sample(ctx, config, minimum=config.post_march_delay)
        monitored_slot = detect_departing_slot(
            ctx,
            expected=monitored_slot,
            idle_snapshot=refreshed_slots,
            context_label="gather_cycle",
        ) or monitored_slot
        if not self._confirm_slot_departure(ctx, config, monitored_slot):
            self._report_departure_issue(ctx, config, monitored_slot, task="gather_cycle")
            return False
        return True

    def _return_home(self, ctx: TaskContext, config: GatherConfig) -> None:
        """Pulsa el botón de sede para volver a la ciudad tras terminar la rutina."""
        self._tap_template_group(
            ctx,
            config.sede_button_templates,
            label="return-base",
            timeout=5.0,
            threshold=config.template_threshold,
        )
        ctx.device.sleep(config.return_home_delay)

    # --- nueva detección de tropas ------------------------------------
    def _available_idle_slots(
        self,
        ctx: TaskContext,
        config: GatherConfig,
        *,
        min_required: int,
        wait_for_slots: bool,
    ) -> List[TroopSlotStatus]:
        """Obtiene tropas libres usando HUD estructurado o fallback por templates."""
        if layout_supports_troop_states(ctx.layout):
            slots: List[TroopSlotStatus]
            self._await_troop_state_sample(ctx, config)
            if wait_for_slots and config.idle_detection_delay > 0:
                slots = wait_for_idle_slots(
                    ctx,
                    min_idle=min_required,
                    timeout=config.idle_detection_delay,
                    poll=config.idle_detection_poll,
                )
            else:
                slots = detect_idle_slots(ctx)
            filtered = self._filter_preferred_idle_slots(slots, config)
            if (
                config.preferred_idle_slots
                and not filtered
                and slots
            ):
                available = ", ".join(
                    (self._slot_name(slot) or "?").upper() for slot in slots
                )
                preferred = ", ".join(entry.upper() for entry in config.preferred_idle_slots)
                ctx.console.log(
                    f"[info] Tropas libres detectadas ({available}) pero se requieren slots preferidos ({preferred})"
                )
            return filtered
        return self._legacy_idle_slots(ctx, config, wait=wait_for_slots)

    def _filter_preferred_idle_slots(
        self,
        slots: List[TroopSlotStatus],
        config: GatherConfig,
    ) -> List[TroopSlotStatus]:
        """Limita las tropas seleccionables a un subconjunto preferido."""
        if not config.preferred_idle_slots:
            return slots
        preferred = {
            entry.strip().lower()
            for entry in config.preferred_idle_slots
            if entry.strip()
        }
        filtered = [slot for slot in slots if self._slot_name(slot) in preferred]
        return filtered

    def _legacy_idle_slots(
        self, ctx: TaskContext, config: GatherConfig, *, wait: bool
    ) -> List[TroopSlotStatus]:
        """Construye slots sintéticos en base a íconos ZZZ cuando no hay HUD."""
        coords = (
            self._wait_idle_detection(ctx, config)
            if wait
            else self._find_idle_troops(ctx, config)
        )
        legacy_slots: List[TroopSlotStatus] = []
        for idx, coord in enumerate(coords):
            legacy_slots.append(
                TroopSlotStatus(
                    slot_id=f"legacy_{idx}",
                    tap=coord,
                    state=TroopActivity.IDLE,
                    state_key="legacy",
                    source="legacy",
                    reference_coord=coord,
                )
            )
        return legacy_slots

    def _match_idle_slot(
        self,
        preferred: TroopSlotStatus,
        candidates: List[TroopSlotStatus],
    ) -> TroopSlotStatus:
        """Devuelve el slot que mejor coincide con el previamente seleccionado."""
        if preferred.slot_id:
            for slot in candidates:
                if slot.slot_id == preferred.slot_id:
                    return slot
        if preferred.reference_coord:
            return min(
                candidates,
                key=lambda slot: self._distance(slot.tap, preferred.reference_coord),
            )
        return candidates[0]

    def _confirm_slot_departure(
        self, ctx: TaskContext, config: GatherConfig, slot: TroopSlotStatus
    ) -> bool:
        """Confirma que la tropa cambió de estado o desapareció el ícono de reposo."""
        if slot.source != "legacy" and slot.slot_id and layout_supports_troop_states(ctx.layout):
            self._await_troop_state_sample(ctx, config)
            return wait_for_slot_state_change(
                ctx,
                slot.slot_id,
                from_state=TroopActivity.IDLE,
                timeout=config.idle_clear_timeout,
                poll=config.idle_clear_poll,
            )
        if slot.reference_coord:
            return self._wait_idle_clear(ctx, config, slot.reference_coord)
        return True

    def _report_departure_issue(
        self,
        ctx: TaskContext,
        config: GatherConfig,
        slot: TroopSlotStatus,
        *,
        task: str,
    ) -> None:
        """Registra por qué no salió la tropa e identifica falta de unidades."""
        label = (slot.label or slot.slot_id).upper()
        if self._detect_empty_troop_overlay(ctx, config):
            ctx.console.log(
                f"[warning] {task}: la tropa {label} no tiene unidades (0); se omite la tarea"
            )
            return
        ctx.console.log(
            f"[warning] {task}: la tropa {label} no salió tras pulsar 'March'; revisa manualmente"
        )

    def _detect_empty_troop_overlay(self, ctx: TaskContext, config: GatherConfig) -> bool:
        """Busca overlays de tropas sin unidades para evitar reintentos inútiles."""
        if not ctx.vision or not config.empty_troop_template_names:
            return False
        paths = self._paths_from_names(ctx, config.empty_troop_template_names)
        if not paths:
            return False
        result = ctx.vision.find_any_template(
            paths,
            threshold=config.empty_troop_threshold,
        )
        return result is not None

    def _log_and_enforce_limit(
        self,
        ctx: TaskContext,
        config: GatherConfig,
        manual_dispatches: int,
        *,
        context: str | None = None,
    ) -> bool:
        """Sincroniza el límite de envíos con el estado real reportado por el HUD."""
        info = self._count_active_gatherers(ctx, config)
        if info is None:
            if manual_dispatches >= config.max_troops:
                ctx.console.log(
                    "Límite de envíos alcanzado según configuracion local; se detiene el ciclo"
                )
                return False
            return True

        active_count, active_slots = info
        prefix = f"{context}: " if context else ""
        ctx.console.log(
            f"{prefix}Tropas recolectando/marchando actualmente: {active_count}/{config.max_troops}"
        )
        if active_slots:
            labels = ", ".join(
                (slot.label or slot.slot_id).upper() for slot in active_slots
            )
            ctx.console.log(f"{prefix}Slots ocupados: {labels}")

        effective_count = max(active_count, manual_dispatches)
        if effective_count >= config.max_troops:
            ctx.console.log(
                "Límite alcanzado; gather_cycle termina sin nuevos envíos"
            )
            return False
        return True

    def _count_active_gatherers(
        self,
        ctx: TaskContext,
        config: GatherConfig,
    ) -> tuple[int, List[TroopSlotStatus]] | None:
        """Devuelve cuántas tropas están recolectando/marchando si el layout lo permite."""
        if not layout_supports_troop_states(ctx.layout):
            return None
        self._await_troop_state_sample(ctx, config)
        slots = detect_troop_states(ctx)
        if not slots:
            return 0, []
        active_slots = [
            slot
            for slot in slots
            if slot.state in (TroopActivity.GATHERING, TroopActivity.MARCHING)
        ]
        return len(active_slots), active_slots

    @staticmethod
    def _distance(a: Coord, b: Coord) -> int:
        """Distancia Manhattan entre dos coordenadas de tap."""
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    @staticmethod
    def _apply_idle_offset(point: Coord, config: GatherConfig) -> Coord:
        """Aplica el offset configurado para evitar toques centrados."""
        return (point[0] + config.idle_tap_offset[0], point[1] + config.idle_tap_offset[1])

    @staticmethod
    def _slot_name(slot: TroopSlotStatus) -> str:
        """Normaliza el nombre/slot_id a minúsculas para comparaciones internas."""
        if slot.label:
            return slot.label.strip().lower()
        return (slot.slot_id or "").strip().lower()

    def _log_slot_selection(self, ctx: TaskContext, slot: TroopSlotStatus) -> None:
        """Imprime qué tropa se selecciona para facilitar auditorías."""
        label = (slot.label or slot.slot_id).upper()
        ctx.console.log(
            f"Seleccionando tropa {label} (estado actual: {describe_activity(slot.state)})"
        )

    # --- utilidades ------------------------------------------------------
    def _detect_current_level(
        self, ctx: TaskContext, config: GatherConfig
    ) -> int | None:
        """Analiza templates configurados para inferir el nivel seleccionado actualmente."""
        if not ctx.vision or not config.level_indicator_templates:
            return None
        screenshot = ctx.vision.capture()
        if screenshot is None:
            return None
        levels = sorted(
            config.level_indicator_templates.keys(),
            reverse=config.level_detection_order != "asc",
        )
        best_level: int | None = None
        best_value = float("-inf")
        best_order_index = -1
        for order_index, level in enumerate(levels):
            template_names = config.level_indicator_templates[level]
            if not template_names:
                continue
            region = config.level_indicator_regions.get(level)
            cropped = screenshot
            if region:
                h, w, _ = screenshot.shape
                (y_start, y_end), (x_start, x_end) = region
                y1 = max(int(h * y_start), 0)
                y2 = min(int(h * y_end), h)
                x1 = max(int(w * x_start), 0)
                x2 = min(int(w * x_end), w)
                if y2 > y1 and x2 > x1:
                    cropped = screenshot[y1:y2, x1:x2]
            level_best = float("-inf")
            for template_name in template_names:
                try:
                    template_paths = ctx.layout.template_paths(template_name)
                except KeyError:
                    continue
                for template_path in template_paths:
                    template = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
                    if template is None:
                        continue
                    search_image = cropped
                    ch, cw = search_image.shape[:2]
                    th, tw = template.shape[:2]
                    if ch < th or cw < tw:
                        search_image = screenshot
                        ch, cw = search_image.shape[:2]
                    if ch < th or cw < tw:
                        continue
                    result = cv2.matchTemplate(
                        search_image, template, cv2.TM_CCOEFF_NORMED
                    )
                    _, max_val, _, _ = cv2.minMaxLoc(result)
                    if max_val > level_best:
                        level_best = max_val
            if level_best >= config.level_indicator_threshold:
                should_update = False
                if best_level is None or level_best > best_value:
                    should_update = True
                elif level_best == best_value and order_index < best_order_index:
                    should_update = True
                if should_update:
                    best_value = level_best
                    best_level = level
                    best_order_index = order_index
        if best_level is not None:
            ctx.console.log(f"Nivel detectado: {best_level} (confianza {best_value:.3f})")
            return best_level
        ctx.console.log("[info] No se detectó ningún indicador de nivel configurado")
        return None

    def _wait_idle_detection(
        self, ctx: TaskContext, config: GatherConfig
    ) -> List[Coord]:
        """Sondea repetidamente los iconos ZZZ hasta que aparezca alguno o venza el timeout."""
        if config.idle_detection_delay <= 0:
            return self._find_idle_troops(ctx, config)

        elapsed = 0.0
        while elapsed <= config.idle_detection_delay:
            coords = self._find_idle_troops(ctx, config)
            if coords:
                return coords
            remaining = config.idle_detection_delay - elapsed
            if remaining <= 0:
                break
            sleep_time = min(config.idle_detection_poll, remaining)
            ctx.device.sleep(sleep_time)
            elapsed += sleep_time
        return []

    def _find_idle_troops(
        self, ctx: TaskContext, config: GatherConfig
    ) -> List[Coord]:
        """Detecta iconos ZZZ con VisionHelper y devuelve sus centros."""
        if not ctx.vision:
            return []
        paths = self._paths_from_names(ctx, config.idle_template_names)
        if not paths:
            return []
        matches = ctx.vision.find_all_templates(
            paths,
            threshold=config.idle_threshold,
            max_results=config.idle_max_matches,
        )
        return [coord for coord, _ in matches]

    def _tap_template_group(
        self,
        ctx: TaskContext,
        template_names: Sequence[str],
        *,
        label: str,
        timeout: float,
        threshold: float,
    ) -> bool:
        """Espera el primer template disponible y hace tap en sus coordenadas."""
        if not ctx.vision:
            return False
        paths = self._paths_from_names(ctx, template_names)
        if not paths:
            return False
        result = ctx.vision.wait_for_any_template(
            paths,
            timeout=timeout,
            threshold=threshold,
            poll_interval=0.5,
            raise_on_timeout=False,
        )
        if not result:
            ctx.console.log(
                f"[warning] No se detectó template para '{label}' dentro del tiempo"
            )
            return False
        coords, matched_path = result
        ctx.console.log(f"Template '{matched_path.name}' seleccionado ({label})")
        ctx.device.tap(coords, label=label)
        return True

    def _wait_for_template_group(
        self,
        ctx: TaskContext,
        template_names: Sequence[str],
        *,
        label: str,
        timeout: float,
        threshold: float,
    ) -> bool:
        """Solo verifica la presencia del grupo de templates sin tocar pantalla."""
        if not ctx.vision:
            return False
        paths = self._paths_from_names(ctx, template_names)
        if not paths:
            return False
        result = ctx.vision.wait_for_any_template(
            paths,
            timeout=timeout,
            threshold=threshold,
            poll_interval=0.5,
            raise_on_timeout=False,
        )
        if not result:
            ctx.console.log(
                f"[warning] No se detectó template para '{label}' dentro del tiempo"
            )
            return False
        return True

    def _paths_from_names(
        self, ctx: TaskContext, template_names: Sequence[str]
    ) -> List[Path]:
        """Resuelve nombres declarativos del layout a rutas absolutas de template."""
        paths: List[Path] = []
        for name in template_names:
            try:
                paths.extend(ctx.layout.template_paths(name))
            except KeyError:
                if name not in self._missing_templates:
                    ctx.console.log(
                        f"[warning] Template '{name}' no está definido en el layout"
                    )
                    self._missing_templates.add(name)
        return paths

    def _is_search_panel_visible(self, ctx: TaskContext, config: GatherConfig) -> bool:
        """Determina si la lupa sigue abierta buscando el botón Search."""
        if not ctx.vision:
            return False
        paths = self._paths_from_names(ctx, config.search_button_templates)
        if not paths:
            return False
        result = ctx.vision.find_any_template(paths, threshold=config.template_threshold)
        return result is not None

    def _closest_coord(self, target: Coord, options: List[Coord]) -> Coord:
        """Devuelve el punto más cercano al target usando distancia Manhattan."""
        def distance(a: Coord, b: Coord) -> int:
            return abs(a[0] - b[0]) + abs(a[1] - b[1])

        return min(options, key=lambda coord: distance(coord, target))

    def _wait_idle_clear(
        self, ctx: TaskContext, config: GatherConfig, coord: Coord
    ) -> bool:
        """Espera a que el icono ZZZ de una coordenada desaparezca tras enviar la tropa."""
        if not ctx.vision:
            return False
        elapsed = 0.0
        while elapsed <= config.idle_clear_timeout:
            matches = self._find_idle_troops(ctx, config)
            if not self._coord_present(coord, matches):
                return True
            ctx.device.sleep(config.idle_clear_poll)
            elapsed += config.idle_clear_poll
        ctx.console.log("[warning] El ícono ZZZ no desapareció tras enviar la tropa")
        return False

    @staticmethod
    def _coord_present(target: Coord, pool: List[Coord], tolerance: int = 25) -> bool:
        """Verifica si una coordenada aproximada existe dentro de una lista de matches."""
        for coord in pool:
            if abs(coord[0] - target[0]) <= tolerance and abs(coord[1] - target[1]) <= tolerance:
                return True
        return False

    def _tap_layout_button(
        self, ctx: TaskContext, button_name: str, *, label: str
    ) -> bool:
        """Obtiene coordenadas del layout para un botón lógico y ejecuta el tap."""
        try:
            coords = ctx.layout.button(button_name)
        except KeyError:
            if button_name not in self._missing_buttons:
                ctx.console.log(
                    f"[warning] Botón '{button_name}' no está definido en el layout"
                )
                self._missing_buttons.add(button_name)
            return False
        ctx.device.tap(coords, label=label)
        return True
