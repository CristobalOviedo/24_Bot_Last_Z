"""Automatización de la actividad Fury Lord: ataques, detección de tropas y reclamos."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from enum import Enum
from typing import Dict, List, Sequence, Tuple
import time

from .base import TaskContext
from .utils import tap_back_button, dismiss_overlay_if_present
from ..troop_state import (
    TroopActivity,
    TroopSlotStatus,
    describe_activity,
    detect_departing_slot,
    idle_slots as detect_idle_slots,
    layout_supports_troop_states,
    resolve_slot_for_tap,
    wait_for_idle_slots,
    wait_for_slot_state_change,
)

Coord = Tuple[int, int]


@dataclass
class FuryLordConfig:
    """Parámetros extensos que controlan flujos, thresholds y horarios del evento."""
    idle_template_names: List[str]
    empty_troop_template_names: List[str]
    event_center_templates: List[str]
    fury_icon_templates: List[str]
    completion_templates: List[str]
    go_button_templates: List[str]
    attack_button_templates: List[str]
    march_button_templates: List[str]
    sede_button_templates: List[str]
    world_button_templates: List[str]
    warning_templates: List[str]
    record_templates: List[str]
    warning_cancel_templates: List[str]
    reward_templates: List[str]
    reward_overlay_templates: List[str]
    reward_overlay_button: str | None
    empty_troop_threshold: float
    available_hours: List[int]
    availability_window_hours: int
    skip_when_unavailable: bool
    attack_refocus_attempts: int
    max_attacks: int
    idle_threshold: float
    idle_max_matches: int
    idle_tap_offset: Coord
    target_focus_tap: Coord
    event_drag_start: Coord
    event_drag_end: Coord
    drag_duration_ms: int
    drag_settle_delay: float
    event_transition_delay: float
    attack_timeout: float
    go_timeout: float
    idle_detection_delay: float
    idle_detection_poll: float
    slot_departure_timeout: float
    slot_departure_poll: float
    pre_troop_delay: float
    troop_select_delay: float
    post_march_delay: float
    refocus_delay: float
    attack_overlay_tap: Coord
    attack_overlay_delay: float
    idle_retry_delay: float
    idle_wait_timeout: float
    troop_state_sample_delay: float
    template_threshold: float
    attack_button_threshold: float
    go_button_threshold: float
    world_button_threshold: float
    sede_button_threshold: float
    reward_threshold: float
    reward_timeout: float
    reward_overlay_threshold: float
    reward_overlay_timeout: float
    reward_overlay_poll: float
    reward_overlay_use_brightness: bool
    reward_overlay_brightness_threshold: float
    reward_overlay_tap: Coord
    reward_overlay_delay: float

    @staticmethod
    def from_params(params: Dict[str, object]) -> "FuryLordConfig":
        """Construye la configuración a partir de un diccionario crudo (YAML/JSON)."""
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

        def as_hour_list(value: object) -> List[int]:
            hours: List[int] = []
            for item in as_list(value):
                try:
                    hours.append(int(item) % 24)
                except (TypeError, ValueError):
                    continue
            return hours

        overlay_button_raw = params.get("reward_overlay_button")
        if overlay_button_raw is None:
            overlay_button_name = "close_popup"
        else:
            overlay_button_name = str(overlay_button_raw).strip()

        return FuryLordConfig(
            idle_template_names=as_list(params.get("idle_template")),
            empty_troop_template_names=as_list(params.get("empty_troop_templates")),
            event_center_templates=as_list(params.get("event_center_template")),
            fury_icon_templates=as_list(params.get("fury_icon_template")),
            completion_templates=as_list(params.get("fury_completion_template")),
            go_button_templates=as_list(params.get("fury_go_button_template")),
            attack_button_templates=as_list(params.get("fury_attack_button_template")),
            march_button_templates=as_list(params.get("march_button_template")),
            sede_button_templates=as_list(params.get("sede_button_template")),
            world_button_templates=as_list(params.get("world_button_template")),
            warning_templates=as_list(params.get("fury_warning_template")),
            record_templates=as_list(params.get("fury_record_template")),
            warning_cancel_templates=as_list(params.get("fury_warning_cancel_template")),
            reward_templates=as_list(params.get("fury_rewards_template")),
            reward_overlay_templates=as_list(params.get("reward_overlay_templates")),
            reward_overlay_button=overlay_button_name or None,
            empty_troop_threshold=float(
                params.get("empty_troop_threshold", params.get("template_threshold", 0.8))
            ),
            available_hours=as_hour_list(
                params.get("availability_hours", params.get("available_hours"))
            )
            or [23, 5, 11, 17],
            availability_window_hours=max(
                1, int(params.get("availability_window_hours", 3))
            ),
            skip_when_unavailable=bool(params.get("skip_when_unavailable", True)),
            attack_refocus_attempts=int(params.get("attack_refocus_attempts", 3)),
            max_attacks=int(params.get("max_attacks", 4)),
            idle_threshold=float(params.get("idle_template_threshold", 0.82)),
            idle_max_matches=int(params.get("idle_max_matches", 3)),
            idle_tap_offset=as_coord(params.get("idle_tap_offset"), (0, 0)),
            target_focus_tap=as_coord(params.get("target_focus_tap"), (270, 440)),
            event_drag_start=as_coord(params.get("event_drag_start"), (450, 915)),
            event_drag_end=as_coord(params.get("event_drag_end"), (105, 915)),
            drag_duration_ms=int(params.get("drag_duration_ms", 700)),
            drag_settle_delay=float(params.get("drag_settle_delay", 3.0)),
            event_transition_delay=float(params.get("event_transition_delay", 3.0)),
            attack_timeout=float(params.get("attack_timeout", 8.0)),
            go_timeout=float(params.get("go_timeout", 8.0)),
            idle_detection_delay=float(params.get("idle_detection_delay", 25.0)),
            idle_detection_poll=float(params.get("idle_detection_poll", 1.0)),
            slot_departure_timeout=float(
                params.get(
                    "slot_departure_timeout",
                    params.get("idle_detection_delay", 8.0),
                )
            ),
            slot_departure_poll=float(
                params.get(
                    "slot_departure_poll",
                    params.get("idle_detection_poll", 1.0),
                )
            ),
            pre_troop_delay=float(params.get("pre_troop_delay", 3.0)),
            troop_select_delay=float(params.get("troop_select_delay", 3.0)),
            post_march_delay=float(params.get("post_march_delay", 3.0)),
            refocus_delay=float(params.get("refocus_delay", 3.0)),
            attack_overlay_tap=as_coord(params.get("attack_overlay_tap"), (270, 440)),
            attack_overlay_delay=float(params.get("attack_overlay_delay", 0.5)),
            idle_retry_delay=float(params.get("idle_retry_delay", 30.0)),
            idle_wait_timeout=float(params.get("idle_wait_timeout", 0.0)),
            troop_state_sample_delay=float(params.get("troop_state_sample_delay", 1.5)),
            template_threshold=float(params.get("template_threshold", 0.8)),
            attack_button_threshold=float(
                params.get("attack_button_threshold", params.get("template_threshold", 0.8))
            ),
            go_button_threshold=float(
                params.get("go_button_threshold", params.get("template_threshold", 0.8))
            ),
            world_button_threshold=float(
                params.get("world_button_threshold", params.get("template_threshold", 0.8))
            ),
            sede_button_threshold=float(
                params.get("sede_button_threshold", params.get("template_threshold", 0.8))
            ),
            reward_threshold=float(params.get("reward_template_threshold", 0.82)),
            reward_timeout=float(params.get("reward_template_timeout", 6.0)),
            reward_overlay_threshold=float(
                params.get(
                    "reward_overlay_threshold",
                    params.get("template_threshold", 0.8),
                )
            ),
            reward_overlay_timeout=float(params.get("reward_overlay_timeout", 6.0)),
            reward_overlay_poll=float(params.get("reward_overlay_poll_interval", 0.5)),
            reward_overlay_use_brightness=bool(
                params.get("reward_overlay_use_brightness", True)
            ),
            reward_overlay_brightness_threshold=float(
                params.get("reward_overlay_brightness_threshold", 0.35)
            ),
            reward_overlay_tap=as_coord(params.get("reward_overlay_tap"), (270, 440)),
            reward_overlay_delay=float(params.get("reward_overlay_delay", 2.0)),
        )


class AttackOutcome(Enum):
    """Resultados posibles al intentar lanzar un ataque."""

    SUCCESS = "success"
    RETRY = "retry"
    ABORT = "abort"


class FuryLordTask:
    """Gestiona toda la lógica de combates y reclamos del evento Fury Lord."""

    name = "attack_furylord"
    manual_daily_logging = True
    CLAIMED_FLAG = "claimed"

    def __init__(self) -> None:
        self._missing_templates: set[str] = set()
        self._event_center_pinned: bool = False

    def _await_troop_state_sample(
        self,
        ctx: TaskContext,
        config: FuryLordConfig,
        *,
        minimum: float = 0.0,
    ) -> None:
        """Aplica un delay mínimo antes de consultar estados de tropas."""
        delay = max(config.troop_state_sample_delay, minimum)
        if delay > 0:
            ctx.device.sleep(delay)

    def run(self, ctx: TaskContext, params: Dict[str, object]) -> None:  # type: ignore[override]
        """Evalúa disponibilidad, lanza ataques si corresponde y reclama recompensas."""
        if not ctx.vision:
            ctx.console.log("[warning] VisionHelper no disponible; attack_furylord requiere detecciones")
            return

        config = FuryLordConfig.from_params(params)
        self._missing_templates.clear()

        if self._is_weekend_maintenance_window():
            ctx.console.log(
                "[info] Fury Lord deshabilitado entre sábado 23:00 y domingo 23:00; se omite la tarea"
            )
            return

        if self._has_already_claimed(ctx):
            ctx.console.log(
                "[info] Fury Lord ya reclamado hoy; se omite la tarea"
            )
            return

        outside_window = config.skip_when_unavailable and not self._is_within_availability(config)
        if outside_window:
            window_desc = self._format_availability_windows(config)
            ctx.console.log(
                f"[info] Fury Lord fuera de horario ({window_desc}); se evalúa solo el reclamo de recompensas"
            )

        panel_ready_for_claim = False
        needs_return_home = False
        try:
            if not outside_window:
                if not self._ensure_city_scene(ctx, config):
                    ctx.console.log("No se detectó la pantalla principal; abortando attack_furylord")
                    return
                if not self._open_event_center(
                    ctx,
                    config,
                    skip_scroll=self._event_center_pinned,
                ):
                    return
                needs_return_home = True
                if not self._open_fury_lord(ctx, config):
                    return
                panel_ready_for_claim = True
            tracker_count = self._tracker_count(ctx)
            needs_attacks = tracker_count < config.max_attacks and not outside_window

            completion_detected = False
            if needs_attacks:
                completion_detected = self._has_completed_attacks(ctx, config)
                if completion_detected:
                    self._sync_completion_count(ctx, config)
                    tracker_count = config.max_attacks
                    needs_attacks = False
                    ctx.console.log(
                        "[info] Los ataques diarios ya fueron completados; se procede a revisar recompensas"
                    )
            else:
                ctx.console.log(
                    "[info] El tracker diario ya registró los 4 ataques; se omite el intento de combate"
                )

            if needs_attacks:
                if not self._enter_fury_detail(ctx, config):
                    return
                panel_ready_for_claim = False
                attacks_done = self._run_attacks(ctx, config)
                if attacks_done < config.max_attacks:
                    ctx.console.log(
                        f"Se completaron {attacks_done} de {config.max_attacks} ataques antes de detenerse"
                    )
                if attacks_done > 0:
                    self._record_attacks(ctx, attacks_done)
                    tracker_count += attacks_done
                else:
                    ctx.console.log(
                        "[info] No se enviaron ataques nuevos; no se reclamará la recompensa"
                    )

            self._claim_rewards_if_needed(ctx, config, panel_ready=panel_ready_for_claim)
        finally:
            if needs_return_home:
                self._return_home(ctx, config)

    # --- flujo principal -------------------------------------------------
    def _ensure_city_scene(self, ctx: TaskContext, config: FuryLordConfig) -> bool:
        """Garantiza que estamos en la vista de ciudad o regresa a la base si no lo está."""
        if self._wait_for_template_group(
            ctx,
            config.world_button_templates,
            label="world-button",
            timeout=5.0,
            threshold=config.world_button_threshold,
        ):
            return True
        ctx.console.log(
            "[info] 'world-button' no visible; regresando a la base con 'return-base'"
        )
        if not self._return_home(ctx, config):
            return False
        return self._wait_for_template_group(
            ctx,
            config.world_button_templates,
            label="world-button",
            timeout=5.0,
            threshold=config.world_button_threshold,
        )

    def _is_weekend_maintenance_window(self) -> bool:
        """Retorna True entre el sábado 23:00 y el domingo 23:00 (hora local)."""

        now = datetime.now()
        weekday = now.weekday()
        if weekday == 5:
            # Saturday: skip only after 23:00
            return now.hour >= 23
        if weekday == 6:
            # Sunday: skip until 23:00
            return True if now.hour < 23 else False
        return False

    def _open_event_center(
        self,
        ctx: TaskContext,
        config: FuryLordConfig,
        *,
        skip_scroll: bool = False,
    ) -> bool:
        """Abre Event Center y desplaza hasta dejar Fury Lord visible."""
        if not self._tap_template_group(
            ctx,
            config.event_center_templates,
            label="event-center",
            timeout=5.0,
            threshold=config.template_threshold,
        ):
            return False
        ctx.device.sleep(config.event_transition_delay)
        if skip_scroll:
            if self._is_template_group_visible(
                ctx,
                config.fury_icon_templates,
                threshold=config.template_threshold,
            ):
                ctx.console.log(
                    "[info] Event Center ya enfocado en Fury Lord; no se realizará desplazamiento"
                )
                return True
            ctx.console.log(
                "[info] Fury Lord no estaba visible tras reabrir Event Center; realizando desplazamiento"
            )
        ctx.device.swipe(
            config.event_drag_start,
            config.event_drag_end,
            duration_ms=config.drag_duration_ms,
            label="event-scroll",
        )
        ctx.device.sleep(config.drag_settle_delay)
        return True

    def _is_template_group_visible(
        self,
        ctx: TaskContext,
        template_names: Sequence[str],
        *,
        threshold: float,
    ) -> bool:
        """Devuelve True cuando uno de los templates del grupo está presente en pantalla."""
        if not ctx.vision:
            return False
        paths = self._paths_from_names(ctx, template_names)
        if not paths:
            return False
        result = ctx.vision.find_any_template(paths, threshold=threshold)
        return bool(result)

    def _open_fury_lord(self, ctx: TaskContext, config: FuryLordConfig) -> bool:
        """Toca el icono del evento para abrir el panel de Fury Lord."""
        if not self._tap_template_group(
            ctx,
            config.fury_icon_templates,
            label="furylord-icon",
            timeout=5.0,
            threshold=config.template_threshold,
        ):
            return False
        ctx.device.sleep(config.event_transition_delay)
        self._event_center_pinned = True
        return True

    def _enter_fury_detail(self, ctx: TaskContext, config: FuryLordConfig) -> bool:
        """Pulsa el botón Go y espera a que aparezca el menú de ataque."""
        if not self._tap_template_group(
            ctx,
            config.go_button_templates,
            label="fury-go",
            timeout=config.go_timeout,
            threshold=config.go_button_threshold,
        ):
            return False
        ctx.device.sleep(config.event_transition_delay)
        return self._ensure_attack_button(ctx, config)

    def _run_attacks(self, ctx: TaskContext, config: FuryLordConfig) -> int:
        """Envía ataques secuenciales hasta agotar el límite o encontrar un bloqueo."""
        attacks_done = 0
        while attacks_done < config.max_attacks:
            outcome = self._attempt_attack(ctx, config, completed_attacks=attacks_done)
            if outcome is AttackOutcome.SUCCESS:
                attacks_done += 1
                ctx.console.log(f"Ataque al Fury Lord #{attacks_done} enviado")
                continue
            if outcome is AttackOutcome.RETRY:
                ctx.console.log("[info] Reintentando el envío contra Fury Lord tras re-enfocar")
                continue
            break
        return attacks_done

    def _attempt_attack(
        self,
        ctx: TaskContext,
        config: FuryLordConfig,
        *,
        completed_attacks: int,
    ) -> AttackOutcome:
        """Ejecuta todas las acciones necesarias para lanzar un ataque individual."""
        if self._dismiss_warning_if_present(ctx, config):
            return AttackOutcome.ABORT
        idle_slots = self._wait_for_resting_troops(ctx, config)
        if not idle_slots:
            ctx.console.log("No hay tropas descansando para enviar al Fury Lord")
            return AttackOutcome.ABORT
        preferred_slot = idle_slots[0]
        if not self._ensure_attack_button(ctx, config):
            return AttackOutcome.ABORT
        if not self._tap_template_group(
            ctx,
            config.attack_button_templates,
            label="fury-attack",
            timeout=config.attack_timeout,
            threshold=config.attack_button_threshold,
        ):
            return AttackOutcome.ABORT
        refreshed_slots = self._available_idle_slots(
            ctx,
            config,
            wait_for_slots=False,
        )
        if not refreshed_slots:
            ctx.console.log("[warning] El botón ataque se abrió pero no hay tropas libres")
            return AttackOutcome.ABORT
        target_slot = self._match_idle_slot(preferred_slot, refreshed_slots)
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
            timeout=5.0,
            threshold=config.template_threshold,
        ):
            if self._dismiss_warning_if_present(ctx, config):
                return AttackOutcome.ABORT
            if self._recover_after_missing_march(ctx, config):
                return AttackOutcome.RETRY
            return AttackOutcome.ABORT
        self._await_troop_state_sample(ctx, config, minimum=config.post_march_delay)
        monitored_slot = detect_departing_slot(
            ctx,
            expected=monitored_slot,
            idle_snapshot=refreshed_slots,
            context_label="attack_furylord",
        ) or monitored_slot
        if not self._await_slot_completion(ctx, config, monitored_slot):
            self._report_departure_issue(ctx, config, monitored_slot, task="attack_furylord")
            return AttackOutcome.ABORT
        self._dismiss_record_if_present(ctx, config)
        self._refocus_target(ctx, config)
        return AttackOutcome.SUCCESS

    def _available_idle_slots(
        self,
        ctx: TaskContext,
        config: FuryLordConfig,
        *,
        wait_for_slots: bool,
    ) -> List[TroopSlotStatus]:
        """Obtiene slots libres usando HUD moderno o heurísticas de templates."""
        slots: List[TroopSlotStatus] = []
        if layout_supports_troop_states(ctx.layout):
            self._await_troop_state_sample(ctx, config)
            slots = detect_idle_slots(ctx)
            if not slots and wait_for_slots and config.idle_detection_delay > 0:
                self._await_troop_state_sample(ctx, config, minimum=config.idle_detection_delay)
                slots = detect_idle_slots(ctx)
            if slots:
                return slots
        return self._legacy_idle_slots(ctx, config, wait=wait_for_slots)

    def _legacy_idle_slots(
        self, ctx: TaskContext, config: FuryLordConfig, *, wait: bool
    ) -> List[TroopSlotStatus]:
        """Crea slots ficticios basados en coordenadas ZZZ cuando no hay info estructurada."""
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
                    state_key="legacy",
                    state=TroopActivity.IDLE,
                    source="legacy",
                    reference_coord=coord,
                )
            )
        return legacy_slots

    def _wait_for_resting_troops(
        self,
        ctx: TaskContext,
        config: FuryLordConfig,
    ) -> List[TroopSlotStatus]:
        """Espera activa opcionalmente hasta que una tropa quede libre para atacar."""
        wait_delay = max(5.0, config.idle_retry_delay)
        deadline = (
            time.monotonic() + config.idle_wait_timeout
            if config.idle_wait_timeout > 0
            else None
        )
        logged_wait = False
        while True:
            slots = self._available_idle_slots(
                ctx,
                config,
                wait_for_slots=False,
            )
            if slots:
                if logged_wait:
                    ctx.console.log("Una tropa volvió a descansar; retomando ataques")
                return slots
            if deadline and time.monotonic() >= deadline:
                return []
            if not logged_wait:
                ctx.console.log(
                    "No hay tropas descansando; esperando a que finalicen los ataques previos"
                )
                logged_wait = True
            ctx.device.sleep(wait_delay)

    def _match_idle_slot(
        self,
        preferred: TroopSlotStatus,
        candidates: List[TroopSlotStatus],
    ) -> TroopSlotStatus:
        """Devuelve la tropa seleccionada considerando id consistente o proximidad espacial."""
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

    @staticmethod
    def _apply_idle_offset(point: Coord, config: FuryLordConfig) -> Coord:
        """Desplaza el punto de tap para compensar templates que tapan el botón real."""
        return (point[0] + config.idle_tap_offset[0], point[1] + config.idle_tap_offset[1])

    @staticmethod
    def _log_slot_selection(ctx: TaskContext, slot: TroopSlotStatus) -> None:
        """Loguea qué tropa fue seleccionada para facilitar auditorías en video."""
        label = (slot.label or slot.slot_id).upper()
        ctx.console.log(
            f"Seleccionando tropa {label} (estado actual: {describe_activity(slot.state)})"
        )

    def _await_slot_completion(
        self,
        ctx: TaskContext,
        config: FuryLordConfig,
        slot: TroopSlotStatus,
    ) -> bool:
        """Monitorea que la tropa cambie de estado o desaparezca el ícono de reposo."""
        if slot.source != "legacy" and slot.slot_id and layout_supports_troop_states(ctx.layout):
            self._await_troop_state_sample(ctx, config)
            return wait_for_slot_state_change(
                ctx,
                slot.slot_id,
                from_state=TroopActivity.IDLE,
                timeout=max(config.slot_departure_timeout, config.post_march_delay),
                poll=config.slot_departure_poll,
            )
        if slot.reference_coord:
            return self._wait_idle_clear(ctx, config, slot.reference_coord)
        return True

    def _report_departure_issue(
        self,
        ctx: TaskContext,
        config: FuryLordConfig,
        slot: TroopSlotStatus,
        *,
        task: str,
    ) -> None:
        """Reporta las causas detectadas cuando la tropa no logra abandonar la base."""
        label = (slot.label or slot.slot_id).upper()
        if self._detect_empty_troop_overlay(ctx, config):
            ctx.console.log(
                f"[warning] {task}: la tropa {label} no tiene unidades (0); se omite la tarea"
            )
            return
        ctx.console.log(
            f"[warning] {task}: la tropa {label} no abandonó la base tras marchar; revisa el ejército"
        )

    def _detect_empty_troop_overlay(
        self, ctx: TaskContext, config: FuryLordConfig
    ) -> bool:
        """Busca overlays que indican tropas vacías para frenar los reintentos."""
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

    def _wait_idle_clear(
        self, ctx: TaskContext, config: FuryLordConfig, coord: Coord
    ) -> bool:
        """Confirma que el icono ZZZ de la coordenada desaparece tras el envío."""
        if not ctx.vision:
            return False
        elapsed = 0.0
        timeout = max(config.slot_departure_timeout, config.post_march_delay)
        poll = max(0.2, config.slot_departure_poll)
        while elapsed <= timeout:
            matches = self._find_idle_troops(ctx, config)
            if not self._coord_present(coord, matches):
                return True
            ctx.device.sleep(poll)
            elapsed += poll
        ctx.console.log("[warning] El ícono ZZZ no desapareció tras enviar la tropa")
        return False

    # --- utilidades ------------------------------------------------------
    def _ensure_attack_button(self, ctx: TaskContext, config: FuryLordConfig) -> bool:
        """Intenta varias veces re-enfocar hasta que aparezca el botón Attack."""
        attempts = 0
        total_attempts = max(1, config.attack_refocus_attempts)
        while attempts <= total_attempts:
            if self._wait_for_template_group(
                ctx,
                config.attack_button_templates,
                label="fury-attack",
                timeout=3.0,
                threshold=config.attack_button_threshold,
            ):
                return True
            attempts += 1
            if attempts > total_attempts:
                break
            ctx.console.log(
                f"[info] Botón de ataque no visible; re-enfoque #{attempts}"
            )
            self._refocus_target(ctx, config)
        return False

    def _refocus_target(self, ctx: TaskContext, config: FuryLordConfig) -> None:
        """Toca el punto configurado para volver a centrar la cámara en el objetivo."""
        ctx.device.tap(config.target_focus_tap, label="refocus-fury")
        ctx.device.sleep(config.refocus_delay)

    def _dismiss_record_if_present(self, ctx: TaskContext, config: FuryLordConfig) -> None:
        """Cierra el modal de récord si aparece tras completar ataques."""
        if not ctx.vision or not config.record_templates:
            return
        paths = self._paths_from_names(ctx, config.record_templates)
        if not paths:
            return
        record = ctx.vision.find_any_template(paths, threshold=config.template_threshold)
        if not record:
            return
        ctx.console.log("[info] Cuadro de récord detectado; cerrando")
        ctx.device.tap((config.target_focus_tap[0], config.target_focus_tap[1] + 200), label="dismiss-record")
        ctx.device.sleep(config.refocus_delay)

    def _recover_after_missing_march(self, ctx: TaskContext, config: FuryLordConfig) -> bool:
        """Reintenta la secuencia cuando el botón March no fue detectado a tiempo."""
        ctx.console.log(
            "[warning] No se detectó el botón 'March'; se re-enfoca el Fury Lord y se reintenta"
        )
        self._dismiss_attack_overlay(
            ctx,
            config,
            reason="Cerrando overlay tras fallar la detección de 'March'",
        )
        self._refocus_target(ctx, config)
        return self._ensure_attack_button(ctx, config)

    def _dismiss_warning_if_present(self, ctx: TaskContext, config: FuryLordConfig) -> bool:
        """Gestiona la advertencia que aparece al completar los 4 ataques diarios."""
        if not ctx.vision or not config.warning_templates:
            return False
        paths = self._paths_from_names(ctx, config.warning_templates)
        if not paths:
            return False
        warning = ctx.vision.find_any_template(paths, threshold=config.template_threshold)
        if not warning:
            return False
        ctx.console.log("[info] Se detectó advertencia de ataques completados; cerrando")
        dismissed = False
        if config.warning_cancel_templates:
            dismissed = self._tap_template_group(
                ctx,
                config.warning_cancel_templates,
                label="fury-warning-cancel",
                timeout=3.0,
                threshold=config.template_threshold,
            )
        if not dismissed:
            self._tap_back(ctx, config)
        self._sync_completion_count(ctx, config)
        return True

    def _has_completed_attacks(self, ctx: TaskContext, config: FuryLordConfig) -> bool:
        """Comprueba si el juego ya muestra el estado de ataques completados."""
        if not ctx.vision or not config.completion_templates:
            return False
        paths = self._paths_from_names(ctx, config.completion_templates)
        if not paths:
            return False
        result = ctx.vision.find_any_template(
            paths, threshold=config.template_threshold
        )
        return result is not None

    def _tap_back(self, ctx: TaskContext, config: FuryLordConfig) -> None:
        """Intenta salir del panel con el botón back y, si falla, cierra el overlay manualmente."""
        if not tap_back_button(ctx, label="fury-back"):
            ctx.console.log("[warning] No se pudo detectar el botón 'back' para salir del evento de Fury Lord")
            self._dismiss_attack_overlay(
                ctx,
                config,
                reason="Forzando cierre del overlay del menú de ataque",
            )
            return
        ctx.device.sleep(config.event_transition_delay)

    def _dismiss_attack_overlay(
        self,
        ctx: TaskContext,
        config: FuryLordConfig,
        *,
        reason: str | None = None,
        log: bool = True,
    ) -> None:
        """Pulsa la zona configurada para cerrar el menú radial de ataques."""
        if log:
            message = reason or "Cerrando overlay del menú de ataque"
            ctx.console.log(f"[info] {message}")
        tap_point = config.attack_overlay_tap or ctx.layout.buttons.get("back_button")
        if tap_point:
            ctx.device.tap(tap_point, label="fury-overlay-dismiss")
        elif not tap_back_button(ctx, label="fury-overlay-dismiss"):
            ctx.console.log(
                "[warning] Botón 'back' no detectado al cerrar overlay de Fury Lord; se usará coordenada (539, 0)"
            )
            ctx.device.tap((539, 0), label="fury-overlay-fallback")
        if config.attack_overlay_delay > 0:
            ctx.device.sleep(config.attack_overlay_delay)

    def _wait_idle_detection(self, ctx: TaskContext, config: FuryLordConfig) -> List[Coord]:
        """Realiza polls repetidos para detectar íconos ZZZ hasta agotar el timeout."""
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

    def _find_idle_troops(self, ctx: TaskContext, config: FuryLordConfig) -> List[Coord]:
        """Localiza íconos ZZZ en pantalla y devuelve sus coordenadas."""
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
        """Busca cualquiera de los templates y toca el primero que aparezca."""
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
            ctx.console.log(f"[warning] No se detectó template para '{label}' dentro del tiempo")
            return False
        coords, matched_path = result
        ctx.console.log(f"Template '{matched_path.name}' seleccionado ({label})")
        ctx.device.tap(coords, label=label)
        return True

    def _record_attacks(self, ctx: TaskContext, amount: int) -> None:
        """Actualiza el tracker para que otras tareas sepan cuántos ataques van."""
        if amount <= 0 or not ctx.daily_tracker:
            return
        ctx.daily_tracker.record_progress(ctx.farm.name, self.name, amount=amount)

    def _wait_for_template_group(
        self,
        ctx: TaskContext,
        template_names: Sequence[str],
        *,
        label: str,
        timeout: float,
        threshold: float,
    ) -> bool:
        """Solo espera la aparición de un template sin ejecutar taps."""
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
            ctx.console.log(f"[warning] No se detectó template para '{label}' dentro del tiempo")
            return False
        return True

    def _paths_from_names(
        self, ctx: TaskContext, template_names: Sequence[str]
    ) -> List[Path]:
        """Resuelve nombres lógicos de template a rutas físicas en disco."""
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

    def _closest_coord(self, target: Coord, options: List[Coord]) -> Coord:
        """Devuelve la coordenada más cercana al punto objetivo usando distancia Manhattan."""
        def distance(a: Coord, b: Coord) -> int:
            return abs(a[0] - b[0]) + abs(a[1] - b[1])

        return min(options, key=lambda coord: distance(coord, target))

    @staticmethod
    def _distance(a: Coord, b: Coord) -> int:
        """Calcula distancia Manhattan entre dos puntos."""
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    @staticmethod
    def _coord_present(target: Coord, pool: List[Coord], tolerance: int = 25) -> bool:
        """Indica si una coordenada ya existe en la lista considerando un margen."""
        for coord in pool:
            if abs(coord[0] - target[0]) <= tolerance and abs(coord[1] - target[1]) <= tolerance:
                return True
        return False

    def _return_home(self, ctx: TaskContext, config: FuryLordConfig) -> bool:
        """Busca el botón de regreso a la sede y, si está oculto, limpia overlays antes."""
        attempts = 0
        max_attempts = 3
        while attempts < max_attempts:
            if self._tap_template_group(
                ctx,
                config.sede_button_templates,
                label="return-base",
                timeout=5.0,
                threshold=config.sede_button_threshold,
            ):
                ctx.device.sleep(config.event_transition_delay)
                return True
            self._dismiss_attack_overlay(
                ctx,
                config,
                reason="'return-base' oculto; cerrando overlays",
                log=(attempts == 0),
            )
            attempts += 1
        ctx.console.log(
            "[warning] No se pudo encontrar el botón 'return-base' tras cerrar overlays"
        )
        return False

    def _has_already_claimed(self, ctx: TaskContext) -> bool:
        """Indica si el tracker ya dejó registro del reclamo diario."""
        if not ctx.daily_tracker:
            return False
        return ctx.daily_tracker.is_flag_set(
            ctx.farm.name,
            self.name,
            self.CLAIMED_FLAG,
        )

    def _tracker_count(self, ctx: TaskContext) -> int:
        """Devuelve cuántos ataques lleva ejecutados esta granja según el tracker."""
        if not ctx.daily_tracker:
            return 0
        return ctx.daily_tracker.current_count(ctx.farm.name, self.name)

    def _sync_completion_count(self, ctx: TaskContext, config: FuryLordConfig) -> None:
        """Marca en el tracker los ataques restantes cuando el juego muestra completado."""
        if not ctx.daily_tracker:
            return
        current = ctx.daily_tracker.current_count(ctx.farm.name, self.name)
        missing = max(0, config.max_attacks - current)
        if missing > 0:
            ctx.daily_tracker.record_progress(
                ctx.farm.name,
                self.name,
                amount=missing,
            )

    def _claim_rewards_if_needed(
        self,
        ctx: TaskContext,
        config: FuryLordConfig,
        *,
        panel_ready: bool = False,
    ) -> bool:
        """Abre el panel de recompensas y marca el flag cuando todo está reclamado."""
        tracker = ctx.daily_tracker
        if not tracker or not config.reward_templates:
            return False
        if tracker.is_flag_set(ctx.farm.name, self.name, self.CLAIMED_FLAG):
            return False
        if tracker.current_count(ctx.farm.name, self.name) < config.max_attacks:
            return False
        if not panel_ready:
            if not self._ensure_city_view(ctx, config):
                ctx.console.log("[warning] No se pudo volver a la ciudad para reclamar recompensas")
                return False
            if not self._open_event_center(
                ctx,
                config,
                skip_scroll=self._event_center_pinned,
            ):
                return False
            if not self._open_fury_lord(ctx, config):
                return False
        else:
            ctx.console.log("[info] Panel del Fury Lord ya abierto; reclamando recompensa directa")
        if not self._tap_reward_panel(ctx, config):
            ctx.console.log("[info] Panel de recompensas del Fury Lord no disponible todavía")
            return False
        self._dismiss_reward_overlay(ctx, config)
        claimed_now = self._has_completed_attacks(ctx, config)
        self._tap_back(ctx, config)
        if not claimed_now:
            ctx.console.log(
                "[info] No apareció el banner de recompensas completadas tras reclamar; se mantendrá pendiente"
            )
            return False
        tracker.set_flag(ctx.farm.name, self.name, self.CLAIMED_FLAG, True)
        ctx.console.log("Recompensas del Fury Lord reclamadas y registradas")
        return True

    def _tap_reward_panel(self, ctx: TaskContext, config: FuryLordConfig) -> bool:
        """Toca el ícono de reclamación del Fury Lord y espera el overlay."""
        if not ctx.vision:
            return False
        paths = self._paths_from_names(ctx, config.reward_templates)
        if not paths:
            return False
        result = ctx.vision.wait_for_any_template(
            paths,
            timeout=config.reward_timeout,
            threshold=config.reward_threshold,
            poll_interval=0.5,
            raise_on_timeout=False,
        )
        if not result:
            return False
        coords, matched_path = result
        ctx.console.log(f"Template '{matched_path.name}' seleccionado (fury-reward)")
        ctx.device.tap(coords, label="fury-reward")
        ctx.device.sleep(config.reward_overlay_delay)
        return True

    def _dismiss_reward_overlay(self, ctx: TaskContext, config: FuryLordConfig) -> None:
        """Cierra el overlay de recompensa usando templates o un tap de respaldo."""
        overlay_closed = dismiss_overlay_if_present(
            ctx,
            config.reward_overlay_templates or None,
            config.reward_overlay_button,
            timeout=config.reward_overlay_timeout,
            poll_interval=config.reward_overlay_poll,
            threshold=config.reward_overlay_threshold,
            delay=config.reward_overlay_delay,
            use_brightness=config.reward_overlay_use_brightness,
            brightness_threshold=config.reward_overlay_brightness_threshold,
            fallback_tap=config.reward_overlay_tap,
        )
        if not overlay_closed:
            ctx.console.log(
                "[warning] No se detectó overlay de recompensas del Fury Lord; se usó tap directo"
            )
            ctx.device.tap(config.reward_overlay_tap, label="fury-reward-overlay")
            if config.reward_overlay_delay > 0:
                ctx.device.sleep(config.reward_overlay_delay)

    def _ensure_city_view(self, ctx: TaskContext, config: FuryLordConfig, attempts: int = 2) -> bool:
        """Vuelve a la vista de ciudad intentando con back, return-home y reintentos."""
        if self._ensure_city_scene(ctx, config):
            return True
        for _ in range(max(1, attempts)):
            self._tap_back(ctx, config)
            if self._ensure_city_scene(ctx, config):
                return True
        self._return_home(ctx, config)
        if self._ensure_city_scene(ctx, config):
            return True
        return False

    def _is_within_availability(self, config: FuryLordConfig) -> bool:
        """Evalúa si la hora actual cae dentro de alguna ventana permitida."""
        if not config.available_hours:
            return True
        now = datetime.now()
        current_minutes = now.hour * 60 + now.minute
        window_minutes = max(1, config.availability_window_hours) * 60
        day_minutes = 24 * 60
        normalized_hours = {hour % 24 for hour in config.available_hours}
        for hour in normalized_hours:
            start_minutes = hour * 60
            delta = (current_minutes - start_minutes) % day_minutes
            if delta < window_minutes:
                return True
        return False

    def _format_availability_windows(self, config: FuryLordConfig) -> str:
        """Devuelve un string amigable con las horas configuradas para logs."""
        if not config.available_hours:
            return "todo el día"
        hours = sorted({hour % 24 for hour in config.available_hours})
        return ", ".join(f"{hour:02d}:00" for hour in hours)
