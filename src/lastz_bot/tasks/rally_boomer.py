"""Gestiona los rallies contra Boomer y la activación del Auto Union."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import cv2

from .base import TaskContext
from .utils import tap_back_button
from ..troop_state import (
    TroopActivity,
    TroopSlotStatus,
    describe_activity,
    detect_departing_slot,
    detect_troop_states,
    idle_slots as detect_idle_slots,
    layout_supports_troop_states,
    resolve_slot_for_tap,
    wait_for_slot_state_change,
)

Coord = Tuple[int, int]


@dataclass
class RallyBoomerConfig:
    """Configura templates, thresholds y topes diarios usados por la tarea."""
    search_icon_templates: List[str]
    boomer_icon_templates: List[str]
    team_button_templates: List[str]
    rally_icon_templates: List[str]
    auto_union_menu_templates: List[str]
    auto_union_button_templates: List[str]
    auto_union_activate_templates: List[str]
    event_center_templates: List[str]
    event_boomer_templates: List[str]
    search_button_templates: List[str]
    level_increase_templates: List[str]
    level_decrease_templates: List[str]
    level_indicator_templates: Dict[int, List[str]]
    level_indicator_regions: Dict[int, Tuple[Tuple[float, float], Tuple[float, float]]]
    level_detection_delay: float
    world_button_templates: List[str]
    sede_button_templates: List[str]
    map_result_tap: Coord
    drag_start: Coord
    drag_end: Coord
    reverse_drag_start: Coord
    reverse_drag_end: Coord
    event_drag_start: Coord
    event_drag_end: Coord
    drag_duration_ms: int
    search_panel_settle_delay: float
    post_drag_delay: float
    focus_result_delay: float
    world_transition_delay: float
    troop_state_sample_delay: float
    rally_grace_period: float
    rally_timeout: float
    rally_poll_interval: float
    team_button_timeout: float
    rally_icon_timeout: float
    auto_union_timeout: float
    auto_union_task_name: str
    auto_union_refresh_hours: float
    preferred_slots: List[str]
    march_button_templates: List[str]
    empty_troop_template_names: List[str]
    empty_troop_threshold: float
    empty_troop_wait_timeout: float
    dispatch_confirm_timeout: float
    search_button_threshold: float
    template_threshold: float
    level_indicator_threshold: float
    world_button_threshold: float
    sede_button_threshold: float
    level_detection_order: str
    map_icon_threshold: float
    team_button_threshold: float
    rally_icon_threshold: float
    auto_union_threshold: float
    target_level: int
    level_overrides: Dict[str, int]
    max_parallel_rallies: int
    daily_task_name: str
    daily_limit: int
    skip_daily_limit_check: bool

    def level_for_farm(self, farm_name: str) -> int:
        """Devuelve el nivel objetivo específico para la granja (o el global)."""
        return self.level_overrides.get(farm_name, self.target_level)

    @staticmethod
    def from_params(ctx: TaskContext, params: Dict[str, object]) -> "RallyBoomerConfig":
        """Construye la configuración leyendo nombres de templates y tiempos declarados."""
        layout = ctx.layout
        console = ctx.console

        def as_list(value: object) -> List[str]:
            """Normaliza cualquier valor a lista de strings sin espacios."""
            if value is None:
                return []
            if isinstance(value, str):
                text = value.strip()
                return [text] if text else []
            if isinstance(value, (list, tuple, set)):
                entries: List[str] = []
                for item in value:
                    text = str(item).strip()
                    if text:
                        entries.append(text)
                return entries
            text = str(value).strip()
            return [text] if text else []

        def as_coord(value: object, default: Coord) -> Coord:
            """Convierte listas/tuplas a coordenadas o devuelve el valor por defecto."""
            if isinstance(value, (list, tuple)) and len(value) == 2:
                return int(value[0]), int(value[1])
            return default

        def resolve(names: Sequence[str]) -> List[str]:
            """Filtra nombres de template garantizando que existan en el layout."""
            resolved: List[str] = []
            for name in names:
                try:
                    layout.template_paths(name)
                except KeyError:
                    console.log(f"[warning] Template '{name}' no está definido para rally_boomer")
                else:
                    resolved.append(name)
            return resolved

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
        if detection_order not in {"asc", "desc"}:
            detection_order = "desc"

        preferred_slots = [entry.strip().lower() for entry in as_list(params.get("preferred_idle_slots", ["a"])) if entry.strip()]

        return RallyBoomerConfig(
            search_icon_templates=resolve(as_list(params.get("search_icon_template", "search_icon"))),
            boomer_icon_templates=resolve(as_list(params.get("boomer_icon_template", "boomer_icon"))),
            team_button_templates=resolve(as_list(params.get("team_up_button_template", "boomer_team_up_button"))),
            rally_icon_templates=resolve(as_list(params.get("rally_icon_templates", ["boomer_rally_icon"]))),
            auto_union_menu_templates=resolve(as_list(params.get("auto_union_menu_template", "boomer_auto_union_menu"))),
            auto_union_button_templates=resolve(as_list(params.get("auto_union_button_template", "boomer_auto_union_button"))),
            auto_union_activate_templates=resolve(as_list(params.get("auto_union_activate_template", "boomer_auto_union_activate"))),
            event_center_templates=resolve(as_list(params.get("event_center_template", "event_center_button"))),
            event_boomer_templates=resolve(as_list(params.get("event_boomer_template", "boomer_event_icon"))),
            search_button_templates=resolve(as_list(params.get("search_button_template", "search_button"))),
            level_increase_templates=resolve(as_list(params.get("level_increase_template", "level_increase_button"))),
            level_decrease_templates=resolve(as_list(params.get("level_decrease_template", "level_decrease_button"))),
            level_indicator_templates=level_indicator_templates,
            level_indicator_regions=level_indicator_regions,
            level_detection_delay=float(params.get("level_detection_delay", 1.0)),
            world_button_templates=resolve(as_list(params.get("world_button_template", "world_button"))),
            sede_button_templates=resolve(as_list(params.get("sede_button_template", "sede_button"))),
            map_result_tap=as_coord(params.get("map_result_tap"), (270, 480)),
            drag_start=as_coord(params.get("drag_start"), (460, 630)),
            drag_end=as_coord(params.get("drag_end"), (80, 630)),
            reverse_drag_start=as_coord(params.get("reverse_drag_start"), (80, 630)),
            reverse_drag_end=as_coord(params.get("reverse_drag_end"), (460, 630)),
            event_drag_start=as_coord(params.get("event_drag_start"), (450, 915)),
            event_drag_end=as_coord(params.get("event_drag_end"), (105, 915)),
            drag_duration_ms=int(params.get("drag_duration_ms", 800)),
            search_panel_settle_delay=float(params.get("search_panel_settle_delay", 3.0)),
            post_drag_delay=float(params.get("post_drag_delay", 3.0)),
            focus_result_delay=float(params.get("focus_result_delay", 3.0)),
            world_transition_delay=float(params.get("world_transition_delay", 3.0)),
            troop_state_sample_delay=float(params.get("troop_state_sample_delay", 1.5)),
            rally_grace_period=float(params.get("rally_grace_period", 45.0)),
            rally_timeout=float(params.get("rally_timeout", 180.0)),
            rally_poll_interval=float(params.get("rally_poll_interval", 5.0)),
            team_button_timeout=float(params.get("team_button_timeout", 6.0)),
            rally_icon_timeout=float(params.get("rally_icon_timeout", 8.0)),
            auto_union_timeout=float(params.get("auto_union_timeout", 8.0)),
            auto_union_task_name=str(params.get("auto_union_task_name", "boomer_auto_union")),
            auto_union_refresh_hours=float(params.get("auto_union_refresh_hours", 12.0)),
            preferred_slots=preferred_slots,
            march_button_templates=resolve(as_list(params.get("march_button_template", "march_button"))),
            empty_troop_template_names=resolve(as_list(params.get("empty_troop_templates", []))),
            empty_troop_threshold=float(params.get("empty_troop_threshold", params.get("template_threshold", 0.82))),
            empty_troop_wait_timeout=float(params.get("empty_troop_wait_timeout", 240.0)),
            dispatch_confirm_timeout=float(params.get("dispatch_confirm_timeout", 15.0)),
            search_button_threshold=float(params.get("search_button_threshold", params.get("template_threshold", 0.82))),
            template_threshold=float(params.get("template_threshold", 0.82)),
            level_indicator_threshold=float(params.get("level_indicator_threshold", params.get("template_threshold", 0.82))),
            world_button_threshold=float(params.get("world_button_threshold", params.get("template_threshold", 0.82))),
            sede_button_threshold=float(params.get("sede_button_threshold", params.get("template_threshold", 0.82))),
            level_detection_order=detection_order,
            map_icon_threshold=float(params.get("map_icon_threshold", params.get("template_threshold", 0.82))),
            team_button_threshold=float(params.get("team_button_threshold", params.get("template_threshold", 0.82))),
            rally_icon_threshold=float(params.get("rally_icon_threshold", params.get("template_threshold", 0.82))),
            auto_union_threshold=float(params.get("auto_union_threshold", params.get("template_threshold", 0.82))),
            target_level=int(params.get("target_level", params.get("max_level", 6))),
            level_overrides={
                str(name).strip(): int(value)
                for name, value in (params.get("level_overrides", {}) or {}).items()
                if str(name).strip()
            },
            max_parallel_rallies=max(1, int(params.get("max_parallel_rallies", 1))),
            daily_task_name=str(params.get("daily_task_name", "rally_boomer")),
            daily_limit=max(1, int(params.get("daily_limit", 7))),
            skip_daily_limit_check=bool(params.get("skip_daily_limit_check", False)),
        )


class DispatchOutcome(str, Enum):
    SENT = "sent"
    RECOVER = "recover"
    ABORT = "abort"


class RallyBoomerTask:
    """Envía rallies consecutivos contra Boomer respetando límites y auto union."""
    name = "rally_boomer"
    manual_daily_logging = True

    def __init__(self) -> None:
        self._missing_templates: set[str] = set()

    def run(self, ctx: TaskContext, params: Dict[str, object]) -> None:  # type: ignore[override]
        """Lanza rallies seguidos, monitorea límites diarios y activa Auto Union."""
        if not ctx.vision:
            ctx.console.log("[warning] VisionHelper no disponible; rally_boomer requiere detecciones")
            return

        config = RallyBoomerConfig.from_params(ctx, params)
        if not config.search_icon_templates or not config.boomer_icon_templates:
            ctx.console.log("[warning] Faltan templates críticos para rally_boomer")
            return
        tracker_count = self._current_tracker(ctx, config.daily_task_name)
        target_limit = config.daily_limit
        if ctx.daily_tracker:
            tracker_target = ctx.daily_tracker.task_limits.get(config.daily_task_name)
            if tracker_target:
                target_limit = max(1, int(tracker_target))
        limit_enforced = not config.skip_daily_limit_check and target_limit > 0
        if limit_enforced and tracker_count >= target_limit:
            ctx.console.log("[info] Los rallies diarios ya se completaron; se omite la tarea")
            return
        remaining = max(0, target_limit - tracker_count)
        if limit_enforced:
            ctx.console.log(
                f"[info] Progreso actual de rallies: {tracker_count}/{target_limit} (pendientes: {remaining})"
            )

        auto_union_pending = False
        if self._auto_union_due(ctx, config):
            if self._activate_auto_union_from_event_center(ctx, config):
                self._mark_auto_union(ctx, config)
            else:
                auto_union_pending = True

        sent = 0
        parallel_limit = config.max_parallel_rallies
        if not layout_supports_troop_states(ctx.layout):
            parallel_limit = 1
        active_slots: Dict[str, float] = {}
        target_level = config.level_for_farm(ctx.farm.name)
        waiting_for_slot = False
        consecutive_dispatch_failures = 0
        while sent < target_limit:
            if limit_enforced and tracker_count >= target_limit:
                break
            if not self._ensure_world_scene(ctx, config):
                ctx.console.log("No se pudo acceder al mapa del mundo; deteniendo rallies")
                break
            if parallel_limit > 1:
                self._purge_completed_slots(ctx, config, active_slots)
                if len(active_slots) >= parallel_limit:
                    if not waiting_for_slot:
                        ctx.console.log("[info] Todas las tropas permitidas están marchando; esperando un slot libre")
                        waiting_for_slot = True
                    ctx.device.sleep(max(1.0, config.rally_poll_interval))
                    continue
                waiting_for_slot = False

            slot = self._select_idle_slot(ctx, config, blocked_keys=active_slots.keys())
            if not slot:
                if parallel_limit > 1 and active_slots:
                    if not waiting_for_slot:
                        ctx.console.log("[info] Tropas en marcha; esperando a que una regrese para continuar")
                        waiting_for_slot = True
                    ctx.device.sleep(max(1.0, config.rally_poll_interval))
                    continue
                ctx.console.log("No hay tropas descansando para iniciar el rally")
                break
            waiting_for_slot = False
            if not self._open_search_panel(ctx, config):
                break
            if not self._select_boomer_target(ctx, config):
                break
            self._ensure_target_level(ctx, config, target_level)
            if not self._perform_search(ctx, config):
                break
            if not self._engage_team_button(ctx, config):
                break
            slot = self._select_idle_slot(
                ctx,
                config,
                blocked_keys=active_slots.keys(),
            )
            if not slot:
                ctx.console.log(
                    "[info] 'Team Up' abierto pero todas las tropas siguen ocupadas; se reintentará en unos segundos"
                )
                self._tap_back(ctx, label="boomer-team-exit")
                ctx.device.sleep(max(1.5, config.rally_poll_interval))
                continue
            outcome, dispatched_slot = self._dispatch_rally(ctx, config, slot)
            if outcome is DispatchOutcome.SENT:
                consecutive_dispatch_failures = 0
            elif outcome is DispatchOutcome.RECOVER:
                consecutive_dispatch_failures += 1
                if consecutive_dispatch_failures > 2:
                    ctx.console.log("[warning] Se agotaron los reintentos tras fallar el envío del rally")
                    break
                if self._recover_after_dispatch_failure(ctx, config):
                    continue
                break
            else:
                break
            sent += 1
            tracker_count = self._record_progress(ctx, config.daily_task_name, tracker_count)
            ctx.console.log(f"Rally contra Boomer enviado #{sent}")
            if self._auto_union_due(ctx, config):
                if self._activate_auto_union_from_rally_icon(ctx, config):
                    self._mark_auto_union(ctx, config)
                    auto_union_pending = False
                else:
                    auto_union_pending = True
            if parallel_limit <= 1:
                self._wait_for_rally_completion(ctx, config, dispatched_slot)
            else:
                self._register_active_slot(dispatched_slot, active_slots, config)

        if auto_union_pending:
            if self._activate_auto_union_from_event_center(ctx, config):
                self._mark_auto_union(ctx, config)

        self._return_home(ctx, config)

    # --- flujo de mapa ---
    def _ensure_world_scene(self, ctx: TaskContext, config: RallyBoomerConfig) -> bool:
        """Verifica si ya estamos en el mapa mundial y, si no, toca el botón World."""
        if self._wait_for_template_group(
            ctx,
            config.sede_button_templates,
            label="sede-check",
            timeout=1.0,
            threshold=config.sede_button_threshold,
        ):
            return True
        if not self._tap_template_group(
            ctx,
            config.world_button_templates,
            label="world-button",
            timeout=6.0,
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

    def _open_search_panel(self, ctx: TaskContext, config: RallyBoomerConfig) -> bool:
        """Abre la lupa de búsqueda y desplaza el panel hacia Boomer."""
        if not self._tap_template_group(
            ctx,
            config.search_icon_templates,
            label="open-search",
            timeout=6.0,
            threshold=config.template_threshold,
        ):
            return False
        ctx.device.sleep(config.search_panel_settle_delay)
        ctx.device.swipe(
            config.reverse_drag_start,
            config.reverse_drag_end,
            duration_ms=config.drag_duration_ms,
            label="search-reverse-drag",
        )
        ctx.device.sleep(config.post_drag_delay)
        return True

    def _select_boomer_target(self, ctx: TaskContext, config: RallyBoomerConfig) -> bool:
        """Toca el icono de Boomer dentro del panel de búsqueda."""
        return self._tap_template_group(
            ctx,
            config.boomer_icon_templates,
            label="boomer-icon",
            timeout=4.0,
            threshold=config.map_icon_threshold,
        )

    def _perform_search(self, ctx: TaskContext, config: RallyBoomerConfig) -> bool:
        """Pulsa el botón Search y aguarda a que aparezca el resultado en el mapa."""
        if not self._tap_template_group(
            ctx,
            config.search_button_templates,
            label="search-btn",
            timeout=4.0,
            threshold=config.search_button_threshold,
        ):
            return False
        ctx.device.sleep(config.post_drag_delay)
        return True

    def _focus_result(self, ctx: TaskContext, config: RallyBoomerConfig) -> bool:
        """Toca la coordenada predefinida para centrar el objetivo hallado."""
        if not config.map_result_tap:
            return True
        ctx.device.tap(config.map_result_tap, label="focus-result")
        ctx.device.sleep(config.focus_result_delay)
        return True

    def _tap_team_button(
        self,
        ctx: TaskContext,
        config: RallyBoomerConfig,
        *,
        suppress_warning: bool = False,
    ) -> bool:
        """Pulsa el botón Team Up detectando cualquiera de los templates configurados."""
        return self._tap_template_group(
            ctx,
            config.team_button_templates,
            label="boomer-team",
            timeout=config.team_button_timeout,
            threshold=config.team_button_threshold,
            suppress_warning=suppress_warning,
        )

    def _engage_team_button(self, ctx: TaskContext, config: RallyBoomerConfig) -> bool:
        """Intenta abrir Team Up y re-enfoca el objetivo si no aparece al primer intento."""
        if self._tap_team_button(ctx, config, suppress_warning=True):
            return True
        ctx.console.log("[info] 'Team Up' no apareció tras la búsqueda; se enfocará el objetivo y se reintentará")
        if not self._focus_result(ctx, config):
            return False
        return self._tap_team_button(ctx, config)

    def _await_troop_state_sample(
        self,
        ctx: TaskContext,
        config: RallyBoomerConfig,
        *,
        minimum: float = 0.0,
    ) -> None:
        """Garantiza que el HUD de tropas esté estable antes de leer sus estados."""
        delay = max(config.troop_state_sample_delay, minimum)
        if delay > 0:
            ctx.device.sleep(delay)

    # --- tropas ---
    def _select_idle_slot(
        self,
        ctx: TaskContext,
        config: RallyBoomerConfig,
        *,
        blocked_keys: Sequence[str] | None = None,
    ) -> TroopSlotStatus | None:
        """Elige una tropa libre evitando las que ya están marchando en paralelo."""
        self._await_troop_state_sample(ctx, config)
        slots = detect_idle_slots(ctx)
        if not slots:
            return None
        blocked = {entry.lower() for entry in blocked_keys or []}
        candidates = [slot for slot in slots if self._slot_key(slot) not in blocked]
        if not candidates:
            return None
        preferred = [slot for slot in candidates if (slot.label or slot.slot_id or "").lower() in config.preferred_slots]
        target = preferred[0] if preferred else candidates[0]
        label = (target.label or target.slot_id or "?").upper()
        ctx.console.log(f"Seleccionando tropa {label} (estado: {describe_activity(target.state)})")
        return target

    def _dispatch_rally(
        self,
        ctx: TaskContext,
        config: RallyBoomerConfig,
        slot: TroopSlotStatus,
    ) -> Tuple[DispatchOutcome, TroopSlotStatus]:
        """Selecciona la tropa, valida disponibilidad y pulsa March registrando el resultado."""
        self._await_troop_state_sample(ctx, config)
        idle_snapshot = detect_idle_slots(ctx)
        ctx.device.sleep(2.0)
        tap_point = slot.tap
        ctx.device.tap(tap_point, label="select-idle-troop")
        self._await_troop_state_sample(ctx, config, minimum=2.0)
        resolved = resolve_slot_for_tap(ctx, tap_point, fallback=slot)
        if resolved and resolved.slot_id != slot.slot_id:
            prev_label = (slot.label or slot.slot_id or "?").upper()
            new_label = (resolved.label or resolved.slot_id or "?").upper()
            ctx.console.log(
                f"[info] Seguimiento ajustado: se seleccionó {prev_label} pero se controlará {new_label}"
            )
        slot = resolved or slot
        label = (slot.label or slot.slot_id or "?").upper()
        if not self._ensure_troops_available(ctx, config, slot):
            ctx.console.log(f"[warning] La tropa {label} sigue sin unidades tras esperar; deteniendo rallies")
            return DispatchOutcome.ABORT, slot
        if not self._tap_template_group(
            ctx,
            config.march_button_templates,
            label="march",
            timeout=5.0,
            threshold=config.template_threshold,
        ):
            ctx.console.log("[info] No se detectó el botón 'March' en el envío actual")
            return DispatchOutcome.RECOVER, slot
        self._await_troop_state_sample(ctx, config, minimum=1.5)
        slot = detect_departing_slot(
            ctx,
            expected=slot,
            idle_snapshot=idle_snapshot,
            context_label="rally_boomer",
        ) or slot
        # Espera breve para que se cierre el panel del rally, pero sin abandonar el mapa
        self._wait_for_template_group(
            ctx,
            config.sede_button_templates,
            label="sede-check",
            timeout=2.0,
            threshold=config.sede_button_threshold,
        )
        if not self._confirm_rally_departure(ctx, config, slot):
            ctx.console.log(f"[info] La tropa {label} no cambió a estado de rally tras pulsar 'March'")
            return DispatchOutcome.RECOVER, slot
        ctx.device.sleep(3.0)
        return DispatchOutcome.SENT, slot

    def _ensure_troops_available(
        self,
        ctx: TaskContext,
        config: RallyBoomerConfig,
        slot: TroopSlotStatus,
    ) -> bool:
        """Espera a que la tropa tenga unidades si detecta overlay de ejército vacío."""
        if not self._detect_empty_troop_overlay(ctx, config):
            return True
        label = (slot.label or slot.slot_id or "?").upper()
        ctx.console.log(
            f"[info] La tropa {label} está sin unidades (0); se esperará a que regresen otras tropas"
        )
        wait_timeout = max(config.empty_troop_wait_timeout, config.rally_timeout)
        deadline = time.monotonic() + wait_timeout
        while time.monotonic() < deadline:
            ctx.device.sleep(max(1.0, config.rally_poll_interval))
            if not self._detect_empty_troop_overlay(ctx, config):
                ctx.console.log(f"[info] La tropa {label} recuperó unidades; reintentando envío")
                return True
        return False

    def _confirm_rally_departure(
        self,
        ctx: TaskContext,
        config: RallyBoomerConfig,
        slot: TroopSlotStatus,
    ) -> bool:
        """Valida mediante el HUD que la tropa cambió a estado activo tras March."""
        if not layout_supports_troop_states(ctx.layout) or not slot.slot_id:
            return True
        self._await_troop_state_sample(ctx, config)
        deadline = time.monotonic() + max(2.0, config.dispatch_confirm_timeout)
        poll = max(0.5, config.rally_poll_interval)
        active_states = {
            TroopActivity.RALLY,
            TroopActivity.MARCHING,
            TroopActivity.COMBAT,
            TroopActivity.BUSY,
            TroopActivity.RETURNING,
        }
        while time.monotonic() < deadline:
            states = detect_troop_states(ctx)
            target = next((candidate for candidate in states if candidate.slot_id == slot.slot_id), None)
            if not target:
                self._await_troop_state_sample(ctx, config, minimum=poll)
                continue
            if target.state in active_states:
                ctx.console.log(
                    f"[info] La tropa {(slot.label or slot.slot_id or '?').upper()} está activa ({describe_activity(target.state)})"
                )
                return True
            if target.state != TroopActivity.IDLE:
                ctx.console.log(
                    f"[info] Estado actual de la tropa {(slot.label or slot.slot_id or '?').upper()}: {describe_activity(target.state)}"
                )
            self._await_troop_state_sample(ctx, config, minimum=poll)
        return False

    def _detect_empty_troop_overlay(self, ctx: TaskContext, config: RallyBoomerConfig) -> bool:
        """Busca overlays del hospital que indican tropas sin unidades disponibles."""
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

    def _recover_after_dispatch_failure(
        self,
        ctx: TaskContext,
        config: RallyBoomerConfig,
    ) -> bool:
        """Reenfoca o reabre la búsqueda cuando el botón March falló."""
        ctx.console.log("[info] Reenfocando el objetivo tras fallar 'March'")
        if self._focus_result(ctx, config):
            ctx.device.sleep(1.5)
            return True
        ctx.console.log("[info] El reenfoque no bastó; reabriendo el panel de búsqueda")
        if not self._ensure_world_scene(ctx, config):
            return False
        return self._open_search_panel(ctx, config)

    def _wait_for_rally_completion(
        self,
        ctx: TaskContext,
        config: RallyBoomerConfig,
        slot: TroopSlotStatus,
    ) -> None:
        """Monitorea hasta que la tropa vuelva a IDLE o venza el timeout configurado."""
        ctx.device.sleep(max(0.0, config.rally_grace_period))
        if slot.source != "legacy" and slot.slot_id and layout_supports_troop_states(ctx.layout):
            self._await_troop_state_sample(ctx, config)
            wait_for_slot_state_change(
                ctx,
                slot.slot_id,
                from_state=TroopActivity.IDLE,
                timeout=config.rally_timeout,
                poll=config.rally_poll_interval,
            )
            return
        self._await_troop_state_sample(ctx, config)
        deadline = time.monotonic() + config.rally_timeout
        while time.monotonic() < deadline:
            slots = detect_idle_slots(ctx)
            if any(self._same_slot(slot, candidate) for candidate in slots):
                return
            self._await_troop_state_sample(ctx, config, minimum=config.rally_poll_interval)
        ctx.console.log("[warning] La tropa seleccionada no regresó al estado de descanso dentro del tiempo esperado")

    def _register_active_slot(
        self,
        slot: TroopSlotStatus,
        active_slots: Dict[str, float],
        config: RallyBoomerConfig,
    ) -> None:
        """Registra el slot como activo para respetar el máximo de rallies concurrentes."""
        key = self._slot_key(slot)
        if not key:
            return
        timeout = max(0.0, config.rally_timeout) + max(0.0, config.rally_grace_period)
        active_slots[key] = time.monotonic() + timeout

    def _purge_completed_slots(
        self,
        ctx: TaskContext,
        config: RallyBoomerConfig,
        active_slots: Dict[str, float],
    ) -> None:
        """Elimina slots cuya tropa ya regresó o cuyo timeout expiró."""
        if not active_slots:
            return
        self._await_troop_state_sample(ctx, config)
        current_idle = detect_idle_slots(ctx)
        idle_keys = {self._slot_key(slot) for slot in current_idle if self._slot_key(slot)}
        now = time.monotonic()
        for key in list(active_slots.keys()):
            deadline = active_slots[key]
            if key in idle_keys or now >= deadline:
                active_slots.pop(key, None)

    # --- auto union ---
    def _auto_union_due(self, ctx: TaskContext, config: RallyBoomerConfig) -> bool:
        """Determina si ya pasó el intervalo configurado para reactivar Auto Union."""
        tracker = ctx.daily_tracker
        if not tracker:
            return True
        last = tracker.last_timestamp(ctx.farm.name, config.auto_union_task_name)
        if not last:
            return True
        return datetime.now() - last >= timedelta(hours=config.auto_union_refresh_hours)

    def _activate_auto_union_from_rally_icon(self, ctx: TaskContext, config: RallyBoomerConfig) -> bool:
        """Abre el icono de rally y atraviesa el flujo de activación de Auto Union."""
        panel_opened = False
        if not self._tap_template_group(
            ctx,
            config.rally_icon_templates,
            label="rally-icon",
            timeout=config.rally_icon_timeout,
            threshold=config.rally_icon_threshold,
        ):
            return False
        if not self._wait_for_template_group(
            ctx,
            config.auto_union_menu_templates,
            label="auto-union-menu",
            timeout=config.auto_union_timeout,
            threshold=config.auto_union_threshold,
        ):
            self._close_auto_union_panel(ctx, label="auto-union-menu-missing")
            return False
        panel_opened = True
        if not self._tap_template_group(
            ctx,
            config.auto_union_button_templates,
            label="auto-union",
            timeout=config.auto_union_timeout,
            threshold=config.auto_union_threshold,
        ):
            self._close_auto_union_panel(ctx, label="auto-union-button-missing")
            return False
        if not self._tap_template_group(
            ctx,
            config.auto_union_activate_templates,
            label="auto-union-activate",
            timeout=config.auto_union_timeout,
            threshold=config.auto_union_threshold,
        ):
            self._close_auto_union_panel(ctx, label="auto-union-activate-missing")
            return False
        ctx.device.sleep(2.0)
        self._dismiss_auto_union_overlay(ctx, label="auto-union-overlay-dismiss")
        ctx.device.sleep(1.0)
        if panel_opened:
            self._close_auto_union_panel(ctx, label="auto-union-exit")
        return True

    def _activate_auto_union_from_event_center(self, ctx: TaskContext, config: RallyBoomerConfig) -> bool:
        """Accede al Event Center para activar Auto Union cuando el icono directo falla."""
        if not self._ensure_city_scene(ctx, config):
            return False
        if not self._tap_template_group(
            ctx,
            config.event_center_templates,
            label="event-center",
            timeout=6.0,
            threshold=config.template_threshold,
        ):
            return False
        ctx.device.sleep(config.world_transition_delay)
        ctx.device.swipe(
            config.event_drag_start,
            config.event_drag_end,
            duration_ms=config.drag_duration_ms,
            label="event-scroll",
        )
        ctx.device.sleep(config.post_drag_delay)
        if not self._tap_template_group(
            ctx,
            config.event_boomer_templates,
            label="event-boomer",
            timeout=6.0,
            threshold=config.template_threshold,
        ):
            return False
        ctx.device.sleep(2.0)
        if not self._tap_template_group(
            ctx,
            config.auto_union_button_templates,
            label="auto-union",
            timeout=config.auto_union_timeout,
            threshold=config.auto_union_threshold,
        ):
            return False
        if not self._tap_template_group(
            ctx,
            config.auto_union_activate_templates,
            label="auto-union-activate",
            timeout=config.auto_union_timeout,
            threshold=config.auto_union_threshold,
        ):
            return False
        ctx.device.sleep(2.0)
        self._dismiss_auto_union_overlay(ctx, label="event-auto-union-exit-1")
        self._dismiss_auto_union_overlay(ctx, label="event-auto-union-exit-2")
        return True

    def _ensure_city_scene(self, ctx: TaskContext, config: RallyBoomerConfig) -> bool:
        """Se asegura de que la cámara regrese a la ciudad usando back/world buttons."""
        if self._wait_for_template_group(
            ctx,
            config.world_button_templates,
            label="world-button",
            timeout=2.0,
            threshold=config.world_button_threshold,
        ):
            return True
        if not self._tap_back(ctx, label="ensure-city-back"):
            return False
        ctx.device.sleep(config.world_transition_delay)
        return self._wait_for_template_group(
            ctx,
            config.world_button_templates,
            label="world-button",
            timeout=4.0,
            threshold=config.world_button_threshold,
        )

    def _tap_back(self, ctx: TaskContext, *, label: str = "back-button") -> bool:
        """Intenta tocar el botón back mediante template y agrega un pequeño delay."""
        tapped = tap_back_button(ctx, label=label)
        if not tapped:
            ctx.console.log(f"[warning] No se detectó el botón 'back' ({label})")
            return False
        ctx.device.sleep(1.0)
        return True

    def _dismiss_auto_union_overlay(self, ctx: TaskContext, *, label: str) -> None:
        """Cierra modales del evento tocando el botón back o coordenada fija."""
        coord = ctx.layout.buttons.get("back_button")
        if not coord:
            coord = (270, 120)
        ctx.device.tap(coord, label=label)
        ctx.device.sleep(1.0)

    def _close_auto_union_panel(self, ctx: TaskContext, *, label: str) -> None:
        """Helper que reutiliza el dismiss overlay para cerrar el panel actual."""
        self._dismiss_auto_union_overlay(ctx, label=label)

    def _mark_auto_union(self, ctx: TaskContext, config: RallyBoomerConfig) -> None:
        """Registra en el tracker cuándo se activó Auto Union para calcular vencimientos."""
        if ctx.daily_tracker:
            ctx.daily_tracker.record_progress(ctx.farm.name, config.auto_union_task_name)

    # --- utilidades de nivel ---
    def _ensure_target_level(
        self,
        ctx: TaskContext,
        config: RallyBoomerConfig,
        target_level: int | None = None,
    ) -> None:
        """Detecta el nivel actual y ajusta hasta llegar al objetivo configurado."""
        if config.level_detection_delay > 0:
            ctx.device.sleep(config.level_detection_delay)
        target = config.target_level if target_level is None else target_level
        primary_threshold = max(0.1, config.level_indicator_threshold)
        current = self._detect_current_level(ctx, config, threshold=primary_threshold)
        if current is None and primary_threshold > 0.78:
            fallback_threshold = max(0.7, primary_threshold - 0.1)
            ctx.console.log(
                f"[info] No se detectó nivel con umbral {primary_threshold:.2f}; reintentando con {fallback_threshold:.2f}"
            )
            current = self._detect_current_level(ctx, config, threshold=fallback_threshold)
        if current is None:
            ctx.console.log("[info] Nivel de Boomer desconocido; se intentará subir al máximo")
            self._set_level_to_max(ctx, config)
            return
        self._sync_level(ctx, config, current_level=current, target_level=target)

    def _set_level_to_max(self, ctx: TaskContext, config: RallyBoomerConfig) -> None:
        """Pulsa repetidamente el botón de incremento para llegar al máximo posible."""
        for _ in range(8):
            self._tap_template_group(
                ctx,
                config.level_increase_templates,
                label="level-up",
                timeout=1.0,
                threshold=config.template_threshold,
            )
            ctx.device.sleep(0.5)

    def _sync_level(
        self,
        ctx: TaskContext,
        config: RallyBoomerConfig,
        *,
        current_level: int,
        target_level: int,
    ) -> None:
        """Incrementa o decrementa paso a paso hasta alinear el nivel actual."""
        if current_level == target_level:
            return
        direction = "up" if target_level > current_level else "down"
        templates = config.level_increase_templates if direction == "up" else config.level_decrease_templates
        if not templates:
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
                break
            ctx.device.sleep(0.5)
            level += step

    def _detect_current_level(
        self,
        ctx: TaskContext,
        config: RallyBoomerConfig,
        *,
        threshold: float,
    ) -> int | None:
        """Evalúa los templates configurados para inferir el nivel actual del panel."""
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
        best_candidate_any: int | None = None
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
                    result = cv2.matchTemplate(search_image, template, cv2.TM_CCOEFF_NORMED)
                    _, max_val, _, _ = cv2.minMaxLoc(result)
                    if max_val > level_best:
                        level_best = max_val
            if level_best >= threshold:
                should_update = False
                if best_level is None or level_best > best_value:
                    should_update = True
                elif level_best == best_value and order_index < best_order_index:
                    should_update = True
                if should_update:
                    best_value = level_best
                    best_level = level
                    best_order_index = order_index
            elif level_best > best_value:
                best_value = level_best
                best_candidate_any = level
        if best_level is not None:
            ctx.console.log(f"Nivel de Boomer detectado: {best_level}")
        elif best_candidate_any is not None and best_value > float("-inf"):
            ctx.console.log(
                f"[info] Mejor coincidencia de nivel: {best_candidate_any} con {best_value:.3f} (< umbral {threshold:.2f})"
            )
        return best_level

    # --- utilidades varias ---
    def _record_progress(
        self,
        ctx: TaskContext,
        task_name: str,
        fallback: int,
    ) -> int:
        """Actualiza el tracker diario y devuelve el conteo posterior al registro."""
        if not ctx.daily_tracker:
            return fallback + 1
        ctx.daily_tracker.record_progress(ctx.farm.name, task_name)
        return ctx.daily_tracker.current_count(ctx.farm.name, task_name)

    def _current_tracker(self, ctx: TaskContext, task_name: str) -> int:
        """Lee cuántos rallies se han registrado ya en el tracker diario."""
        if not ctx.daily_tracker:
            return 0
        return ctx.daily_tracker.current_count(ctx.farm.name, task_name)

    def _return_home(self, ctx: TaskContext, config: RallyBoomerConfig) -> None:
        """Intenta volver a la sede tocando el botón correspondiente tras la rutina."""
        self._tap_template_group(
            ctx,
            config.sede_button_templates,
            label="return-base",
            timeout=5.0,
            threshold=config.sede_button_threshold,
        )
        ctx.device.sleep(config.world_transition_delay)

    def _slot_key(self, slot: TroopSlotStatus) -> str | None:
        """Devuelve una llave estable (slot_id o etiqueta) para identificar tropas."""
        if slot.slot_id:
            return slot.slot_id.lower()
        if slot.label:
            return slot.label.lower()
        return None

    def _same_slot(self, reference: TroopSlotStatus, candidate: TroopSlotStatus) -> bool:
        """Compara slots por id o por cercanía de coordenadas para detectar retornos."""
        if reference.slot_id and candidate.slot_id:
            return reference.slot_id == candidate.slot_id
        if reference.reference_coord:
            return (
                abs(reference.reference_coord[0] - candidate.tap[0]) <= 15
                and abs(reference.reference_coord[1] - candidate.tap[1]) <= 15
            )
        return False

    def _tap_template_group(
        self,
        ctx: TaskContext,
        template_names: Sequence[str],
        *,
        label: str,
        timeout: float,
        threshold: float,
        suppress_warning: bool = False,
    ) -> bool:
        """Resuelve templates y toca el primero detectado, opcionalmente sin warning."""
        if not template_names or not ctx.vision:
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
            if not suppress_warning:
                ctx.console.log(f"[warning] No se detectó template para '{label}' dentro del tiempo")
            return False
        coords, matched = result
        ctx.console.log(f"Template '{matched.name}' seleccionado ({label})")
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
        """Solo valida la presencia del grupo de templates sin interactuar."""
        if not template_names or not ctx.vision:
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
        return bool(result)

    def _paths_from_names(
        self,
        ctx: TaskContext,
        template_names: Sequence[str],
    ) -> List[Path]:
        """Mapea nombres lógicos del layout a rutas físicas, evitando warnings duplicados."""
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
