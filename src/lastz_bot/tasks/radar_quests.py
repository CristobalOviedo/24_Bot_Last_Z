"""Gestiona el panel de Radar Quests: reclamos, Laura, misiones especiales y ayuda."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from ..troop_state import (
    describe_activity,
    detect_troop_states,
    idle_slots as detect_idle_slots,
    layout_supports_troop_states,
)
from .base import TaskContext
from .utils import tap_back_button, dismiss_overlay_if_present

Coord = Tuple[int, int]
HandledHelpMission = Tuple[Coord, float]


class HelpMissionStatus(Enum):
    EXECUTED = "executed"
    NONE_AVAILABLE = "none_available"
    FAILED = "failed"


def _as_list(value: object) -> List[str]:
    """Normaliza cualquier entrada a lista de strings limpiados."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple, set)):
        items: List[str] = []
        for entry in value:
            text = str(entry).strip()
            if text:
                items.append(text)
        return items
    text = str(value).strip()
    return [text] if text else []


def _ensure_list(value: object, fallback: Sequence[str]) -> List[str]:
    """Devuelve lista del valor o, si está vacío, una copia del fallback."""
    items = _as_list(value)
    if items:
        return items
    return list(fallback)


def _coord_from_value(value: object) -> Coord | None:
    """Convierte ``(x, y)`` expresado como lista/tupla/string a tupla de enteros."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return None
    if isinstance(value, str):
        parts = value.split(",")
        if len(parts) == 2:
            try:
                return int(parts[0].strip()), int(parts[1].strip())
            except (TypeError, ValueError):
                return None
    return None


@dataclass
class RadarQuestConfig:
    """Agrupa templates, thresholds y tiempos usados por la tarea del radar."""
    icon_templates: Sequence[Path]
    world_icon_templates: Sequence[Path]
    menu_templates: Sequence[Path]
    claim_button_templates: Sequence[Path]
    laura_button_templates: Sequence[Path]
    overlay_templates: Sequence[Path]
    special_mission_templates: Sequence[Path]
    special_go_button_templates: Sequence[Path]
    help_mission_templates: Sequence[Path]
    help_go_button_templates: Sequence[Path]
    help_button_templates: Sequence[Path]
    attack_button_templates: Sequence[Path]
    idle_troop_templates: Sequence[Path]
    march_button_templates: Sequence[Path]
    overlay_dismiss_button: str | None
    overlay_free_tap: Coord | None
    overlay_poll_interval: float
    overlay_use_brightness: bool
    overlay_brightness_threshold: float
    tap_delay: float
    icon_threshold: float
    world_icon_threshold: float
    menu_threshold: float
    claim_button_threshold: float
    laura_button_threshold: float
    overlay_threshold: float
    special_mission_threshold: float
    special_go_button_threshold: float
    help_mission_threshold: float
    help_go_button_threshold: float
    help_button_threshold: float
    help_mission_timeout: float
    attack_button_threshold: float
    idle_troop_threshold: float
    march_button_threshold: float
    icon_timeout: float
    world_icon_timeout: float
    menu_timeout: float
    claim_button_timeout: float
    laura_button_timeout: float
    overlay_timeout: float
    special_go_button_timeout: float
    help_go_button_timeout: float
    help_button_timeout: float
    attack_button_timeout: float
    idle_troop_timeout: float
    march_button_timeout: float
    post_claim_delay: float
    overlay_dismiss_delay: float
    laura_delay: float
    cycle_delay: float
    special_travel_delay: float
    help_travel_delay: float
    help_button_delay: float
    attack_panel_delay: float
    troop_select_delay: float
    post_attack_delay: float
    post_march_delay: float
    max_cycles: int
    daily_task_name: str
    daily_limit: int
    skip_daily_limit_check: bool

    @staticmethod
    def from_params(ctx: TaskContext, params: Dict[str, Any]) -> "RadarQuestConfig":
        """Resuelve rutas de template y parámetros numéricos desde un diccionario crudo."""
        layout = ctx.layout
        console = ctx.console

        def resolve(names: Sequence[str]) -> List[Path]:
            paths: List[Path] = []
            for name in names:
                if not name:
                    continue
                try:
                    paths.extend(layout.template_paths(name))
                except KeyError:
                    console.log(f"[warning] Template '{name}' no definido para radar_quests")
            return paths

        icon_templates = resolve(_ensure_list(params.get("icon_templates"), ["radar_icon"]))
        world_icon_templates = resolve(_as_list(params.get("world_icon_templates")))
        menu_templates = resolve(_ensure_list(params.get("menu_templates"), ["radar_menu_header"]))
        claim_button_templates = resolve(_ensure_list(params.get("claim_button_templates"), ["radar_claim_button"]))
        laura_button_templates = resolve(_as_list(params.get("laura_button_templates")))
        overlay_templates = resolve(_as_list(params.get("overlay_templates")))
        special_mission_templates = resolve(_as_list(params.get("special_mission_templates")))
        special_go_button_templates = resolve(_as_list(params.get("special_go_button_templates")))
        help_mission_templates = resolve(_as_list(params.get("help_mission_templates")))
        help_go_button_templates = resolve(_as_list(params.get("help_go_button_templates")))
        if not help_go_button_templates:
            help_go_button_templates = list(special_go_button_templates)
        help_button_templates = resolve(_as_list(params.get("help_button_templates")))
        attack_button_templates = resolve(_ensure_list(params.get("attack_button_templates"), ["radar_attack_button"]))
        idle_troop_templates = resolve(_ensure_list(params.get("idle_troop_templates"), ["idle_troop_sleep"]))
        march_button_templates = resolve(_ensure_list(params.get("march_button_templates"), ["march_button"]))

        dismiss_raw = params.get("overlay_dismiss_button", "close_popup")
        dismiss_name = str(dismiss_raw).strip() if dismiss_raw else ""

        if "overlay_free_tap" in params:
            free_tap = _coord_from_value(params.get("overlay_free_tap"))
        else:
            free_tap = (270, 440)

        return RadarQuestConfig(
            icon_templates=icon_templates,
            world_icon_templates=world_icon_templates,
            menu_templates=menu_templates,
            claim_button_templates=claim_button_templates,
            laura_button_templates=laura_button_templates,
            overlay_templates=overlay_templates,
            special_mission_templates=special_mission_templates,
            special_go_button_templates=special_go_button_templates,
            help_mission_templates=help_mission_templates,
            help_go_button_templates=help_go_button_templates,
            help_button_templates=help_button_templates,
            attack_button_templates=attack_button_templates,
            idle_troop_templates=idle_troop_templates,
            march_button_templates=march_button_templates,
            overlay_dismiss_button=dismiss_name or None,
            overlay_free_tap=free_tap,
            overlay_poll_interval=float(params.get("overlay_poll_interval", 0.4)),
            overlay_use_brightness=bool(params.get("overlay_use_brightness", True)),
            overlay_brightness_threshold=float(
                params.get("overlay_brightness_threshold", 0.33)
            ),
            tap_delay=float(params.get("tap_delay", 1.0)),
            icon_threshold=float(params.get("icon_threshold", 0.82)),
            world_icon_threshold=float(params.get("world_icon_threshold", 0.82)),
            menu_threshold=float(params.get("menu_threshold", 0.82)),
            claim_button_threshold=float(params.get("claim_button_threshold", 0.85)),
            laura_button_threshold=float(params.get("laura_button_threshold", 0.85)),
            overlay_threshold=float(params.get("overlay_threshold", 0.8)),
            special_mission_threshold=float(params.get("special_mission_threshold", 0.88)),
            special_go_button_threshold=float(params.get("special_go_button_threshold", 0.85)),
            help_mission_threshold=float(params.get("help_mission_threshold", params.get("special_mission_threshold", 0.88))),
            help_go_button_threshold=float(params.get("help_go_button_threshold", params.get("special_go_button_threshold", 0.85))),
            help_button_threshold=float(params.get("help_button_threshold", 0.85)),
            help_mission_timeout=float(params.get("help_mission_timeout", params.get("menu_timeout", 5.0))),
            attack_button_threshold=float(params.get("attack_button_threshold", 0.82)),
            idle_troop_threshold=float(params.get("idle_troop_threshold", 0.82)),
            march_button_threshold=float(params.get("march_button_threshold", 0.82)),
            icon_timeout=float(params.get("icon_timeout", 6.0)),
            world_icon_timeout=float(params.get("world_icon_timeout", 6.0)),
            menu_timeout=float(params.get("menu_timeout", 5.0)),
            claim_button_timeout=float(params.get("claim_button_timeout", 4.0)),
            laura_button_timeout=float(params.get("laura_button_timeout", 3.0)),
            overlay_timeout=float(params.get("overlay_timeout", 3.0)),
            special_go_button_timeout=float(params.get("special_go_button_timeout", 4.0)),
            help_go_button_timeout=float(params.get("help_go_button_timeout", params.get("special_go_button_timeout", 4.0))),
            help_button_timeout=float(params.get("help_button_timeout", 4.0)),
            attack_button_timeout=float(params.get("attack_button_timeout", 5.0)),
            idle_troop_timeout=float(params.get("idle_troop_timeout", 4.0)),
            march_button_timeout=float(params.get("march_button_timeout", 4.0)),
            post_claim_delay=float(params.get("post_claim_delay", 2.0)),
            overlay_dismiss_delay=float(params.get("overlay_dismiss_delay", 0.5)),
            laura_delay=float(params.get("laura_delay", 1.5)),
            cycle_delay=float(params.get("cycle_delay", 1.5)),
            special_travel_delay=float(params.get("special_travel_delay", 3.0)),
            help_travel_delay=float(params.get("help_travel_delay", params.get("special_travel_delay", 3.0))),
            help_button_delay=float(params.get("help_button_delay", 1.0)),
            attack_panel_delay=float(params.get("attack_panel_delay", 2.0)),
            troop_select_delay=float(params.get("troop_select_delay", 1.0)),
            post_attack_delay=float(params.get("post_attack_delay", 1.5)),
            post_march_delay=float(params.get("post_march_delay", 3.0)),
            max_cycles=max(1, int(params.get("max_cycles", 10))),
            daily_task_name=str(params.get("daily_task_name") or "radar_quests"),
            daily_limit=max(1, int(params.get("daily_limit", 1))),
            skip_daily_limit_check=bool(params.get("skip_daily_limit_check", False)),
        )


class RadarQuestsTask:
    """Orquesta reclamos y misiones del radar respetando el límite diario."""
    name = "radar_quests"
    manual_daily_logging = True

    def run(self, ctx: TaskContext, params: Dict[str, Any]) -> None:  # type: ignore[override]
        """Reclama recompensas, pulsa Laura y ejecuta misiones especiales/ayuda."""
        if not ctx.vision:
            ctx.console.log("[warning] VisionHelper no disponible; radar_quests requiere detecciones")
            return

        config = RadarQuestConfig.from_params(ctx, params)
        if not config.icon_templates and not config.world_icon_templates:
            ctx.console.log("[warning] No hay iconos configurados para radar_quests")
            return
        if not config.menu_templates:
            ctx.console.log("[warning] No hay templates para validar el panel de radar")
            return
        if not config.claim_button_templates:
            ctx.console.log("[warning] No se configuró el botón de reclamo automático del radar")
            return

        tracker_count = self._current_tracker(ctx, config.daily_task_name)
        limit_enforced = not config.skip_daily_limit_check and config.daily_limit > 0
        if limit_enforced and tracker_count >= config.daily_limit:
            ctx.console.log("[info] Radar quests ya registrado en el tracker diario; se omite")
            return

        if not self._ensure_menu_visible(ctx, config):
            ctx.console.log("[warning] No se pudo abrir el panel de radar quests")
            return

        total_claims = 0
        laura_clicked = False
        special_completed = False
        cycles = 0
        handled_help_missions: List[HandledHelpMission] = []
        while cycles < config.max_cycles:
            cycles += 1
            actions = False
            if self._claim_once(ctx, config):
                actions = True
                total_claims += 1
            if not laura_clicked and self._tap_laura_button(ctx, config):
                laura_clicked = True
                actions = True
            if self._has_special_mission(ctx, config):
                if self._execute_special_mission(ctx, config):
                    actions = True
                    special_completed = True
                    # tras regresar al mundo volveremos a abrir el panel automáticamente
                    if not self._ensure_menu_visible(ctx, config):
                        ctx.console.log("[warning] No se pudo volver al panel del radar tras la misión especial")
                        break
                    continue
                else:
                    # si la misión falló tras abrir el mapa, intenta recuperar el panel
                    if not self._ensure_menu_visible(ctx, config):
                        ctx.console.log("[warning] No se pudo recuperar el panel del radar tras fallar la misión especial")
                        break
            help_status = self._execute_help_missions(ctx, config, handled_help_missions)
            if help_status is HelpMissionStatus.EXECUTED:
                actions = True
                continue
            if help_status is HelpMissionStatus.NONE_AVAILABLE:
                ctx.console.log("[info] Sin misiones de ayuda nuevas; regresando al menú principal del radar")
                self._close_menu(ctx, config)
                break
            if not actions:
                break
            if config.cycle_delay > 0:
                ctx.device.sleep(config.cycle_delay)

        self._close_menu(ctx, config)

        if total_claims > 0 or laura_clicked or special_completed:
            tracker_count = self._record_progress(ctx, config.daily_task_name, tracker_count)

    def _claim_once(self, ctx: TaskContext, config: RadarQuestConfig) -> bool:
        """Busca el botón de reclamo, lo pulsa y maneja overlays posteriores."""
        if not ctx.vision:
            return False
        result = ctx.vision.wait_for_any_template(
            config.claim_button_templates,
            timeout=config.claim_button_timeout,
            poll_interval=0.4,
            threshold=config.claim_button_threshold,
            raise_on_timeout=False,
        )
        if not result:
            return False
        coords, matched = result
        ctx.console.log(f"Botón de reclamo detectado ('{matched.name}')")
        ctx.device.tap(coords, label="radar-claim")
        if config.tap_delay > 0:
            ctx.device.sleep(config.tap_delay)
        self._handle_claim_overlay(ctx, config)
        if config.post_claim_delay > 0:
            ctx.device.sleep(config.post_claim_delay)
        return True

    def _handle_claim_overlay(self, ctx: TaskContext, config: RadarQuestConfig) -> None:
        """Intenta cerrar los overlays tras reclamar usando templates o taps libres."""
        if not ctx.vision:
            return
        overlay_closed = dismiss_overlay_if_present(
            ctx,
            list(config.overlay_templates) or None,
            config.overlay_dismiss_button,
            timeout=config.overlay_timeout,
            poll_interval=config.overlay_poll_interval,
            threshold=config.overlay_threshold,
            delay=config.overlay_dismiss_delay,
            use_brightness=config.overlay_use_brightness,
            brightness_threshold=config.overlay_brightness_threshold,
            fallback_tap=config.overlay_free_tap,
        )
        if not overlay_closed:
            back_coord = ctx.layout.buttons.get("back_button")
            if back_coord:
                ctx.device.tap(back_coord, label="radar-overlay-back")
            elif not tap_back_button(ctx, label="radar-overlay-back"):
                ctx.console.log(
                    "[warning] Botón 'back' no detectado tras reclamar en radar; se usará coordenada (539, 0)"
                )
                ctx.device.tap((539, 0), label="radar-overlay-fallback")
            if config.overlay_dismiss_delay > 0:
                ctx.device.sleep(config.overlay_dismiss_delay)

    def _tap_laura_button(self, ctx: TaskContext, config: RadarQuestConfig) -> bool:
        """Pulsa el botón de Laura si está disponible para reclamar claves/bonos."""
        if not ctx.vision or not config.laura_button_templates:
            return False
        result = ctx.vision.find_any_template(
            config.laura_button_templates,
            threshold=config.laura_button_threshold,
        )
        if not result:
            return False
        coords, matched = result
        ctx.console.log(f"Botón de Laura detectado ('{matched.name}'); tocando")
        ctx.device.tap(coords, label="radar-laura")
        if config.laura_delay > 0:
            ctx.device.sleep(config.laura_delay)
        return True

    def _has_special_mission(self, ctx: TaskContext, config: RadarQuestConfig) -> bool:
        """Detecta si hay una misión especial lista dentro del panel."""
        if not ctx.vision or not config.special_mission_templates:
            return False
        result = ctx.vision.find_any_template(
            config.special_mission_templates,
            threshold=config.special_mission_threshold,
        )
        return bool(result)

    def _execute_special_mission(self, ctx: TaskContext, config: RadarQuestConfig) -> bool:
        """Abre la misión especial, viaja, ataca con una tropa libre y espera el march."""
        if not ctx.vision or not config.special_mission_templates:
            return False
        result = ctx.vision.find_any_template(
            config.special_mission_templates,
            threshold=config.special_mission_threshold,
        )
        if not result:
            return False
        mission_coords, matched = result
        ctx.console.log(f"Misión especial del radar detectada ('{matched.name}')")
        ctx.device.tap(mission_coords, label="radar-mission")
        if config.tap_delay > 0:
            ctx.device.sleep(config.tap_delay)
        if not self._tap_first_template(
            ctx,
            config.special_go_button_templates,
            config.special_go_button_threshold,
            config.special_go_button_timeout,
            label="radar-mission-go",
            delay=config.tap_delay,
        ):
            ctx.console.log("[warning] No se encontró el botón 'Ir' para la misión especial")
            return False
        if config.special_travel_delay > 0:
            ctx.device.sleep(config.special_travel_delay)
        if not self._wait_for_attack_option(ctx, config):
            ctx.console.log("[warning] No se detectó el botón de ataque tras abrir la misión especial")
            return False
        if not self._attack_with_idle_troop(ctx, config):
            ctx.console.log("[warning] No se pudo despachar la misión especial")
            self._recover_from_mission_screen(ctx, config)
            return False
        if config.post_march_delay > 0:
            ctx.device.sleep(config.post_march_delay)
        return True

    def _execute_help_missions(
        self,
        ctx: TaskContext,
        config: RadarQuestConfig,
        handled_help_missions: List[HandledHelpMission],
    ) -> HelpMissionStatus:
        """Itera misiones de ayuda, evitando repetir coordenadas ya atendidas."""
        if (
            not ctx.vision
            or not config.help_mission_templates
            or not config.help_go_button_templates
            or not config.help_button_templates
        ):
            return HelpMissionStatus.NONE_AVAILABLE
        executed_any = False
        exhausted = False
        while True:
            self._prune_handled_help_missions(handled_help_missions)
            matches = ctx.vision.find_all_templates(
                config.help_mission_templates,
                threshold=config.help_mission_threshold,
                max_results=5,
            )
            mission = self._next_unhandled_help_mission(matches, handled_help_missions)
            if not mission:
                exhausted = True
                if matches:
                    ctx.console.log(
                        "[info] Las misiones de ayuda detectadas ya fueron atendidas en este ciclo"
                    )
                elif not executed_any:
                    ctx.console.log("[info] No se detectaron misiones de ayuda disponibles")
                break
            mission_coords, matched = mission
            ctx.console.log(f"Misión de ayuda detectada ('{matched.name}')")
            if not self._run_help_mission(ctx, config, mission_coords):
                self._recover_from_mission_screen(ctx, config)
                if not self._ensure_menu_visible(ctx, config):
                    ctx.console.log(
                        "[warning] No se pudo recuperar el panel del radar tras fallar la misión de ayuda"
                    )
                    return HelpMissionStatus.FAILED
                return HelpMissionStatus.EXECUTED if executed_any else HelpMissionStatus.FAILED
            executed_any = True
            handled_help_missions.append((mission_coords, time.monotonic()))
        if executed_any:
            return HelpMissionStatus.EXECUTED
        return HelpMissionStatus.NONE_AVAILABLE if exhausted else HelpMissionStatus.FAILED

    def _run_help_mission(
        self,
        ctx: TaskContext,
        config: RadarQuestConfig,
        mission_coords: Coord,
    ) -> bool:
        """Viaja a la misión de ayuda, pulsa el botón de acción y retorna al menú."""
        ctx.device.tap(mission_coords, label="radar-help-mission")
        if config.tap_delay > 0:
            ctx.device.sleep(config.tap_delay)
        if not self._tap_first_template(
            ctx,
            config.help_go_button_templates,
            config.help_go_button_threshold,
            config.help_go_button_timeout,
            label="radar-help-go",
            delay=config.tap_delay,
        ):
            ctx.console.log("[warning] No se encontró el botón 'Ir' para la misión de ayuda")
            return False
        if config.help_travel_delay > 0:
            ctx.device.sleep(config.help_travel_delay)
        if not self._tap_first_template(
            ctx,
            config.help_button_templates,
            config.help_button_threshold,
            config.help_button_timeout,
            label="radar-help-action",
            delay=config.help_button_delay,
        ):
            ctx.console.log("[warning] No se detectó el botón de 'ayuda' en el mapa")
            return False
        if not self._return_to_radar_menu(ctx, config):
            ctx.console.log(
                "[warning] No se pudo volver al menú del radar tras completar la misión de ayuda"
            )
            return False
        return True

    def _next_unhandled_help_mission(
        self,
        matches: Sequence[tuple[Coord, Path]],
        handled: Sequence[HandledHelpMission],
        tolerance: int = 52,
    ) -> tuple[Coord, Path] | None:
        """Devuelve la primera misión cuya coordenada no exista en el historial local."""
        for coords, matched in matches:
            if self._mission_already_handled(coords, handled, tolerance):
                continue
            return coords, matched
        return None

    def _mission_already_handled(
        self,
        coords: Coord,
        handled: Sequence[HandledHelpMission],
        tolerance: int,
    ) -> bool:
        """Determina si una misión ya se atendió comparando coordenadas con tolerancia."""
        for previous, _ in handled:
            if abs(previous[0] - coords[0]) <= tolerance and abs(previous[1] - coords[1]) <= tolerance:
                return True
        return False

    def _prune_handled_help_missions(
        self,
        handled: List[HandledHelpMission],
        ttl_seconds: float = 600.0,
    ) -> None:
        """Depura el historial de misiones ayudadas para que expiren tras cierto tiempo."""
        if not handled:
            return
        now = time.monotonic()
        handled[:] = [entry for entry in handled if now - entry[1] < ttl_seconds]

    def _wait_for_attack_option(self, ctx: TaskContext, config: RadarQuestConfig) -> bool:
        """Espera a que aparezca el botón Attack tras viajar a la misión."""
        if not ctx.vision:
            return False
        result = ctx.vision.wait_for_any_template(
            config.attack_button_templates,
            timeout=config.attack_button_timeout,
            poll_interval=0.5,
            threshold=config.attack_button_threshold,
            raise_on_timeout=False,
        )
        return bool(result)

    def _attack_with_idle_troop(self, ctx: TaskContext, config: RadarQuestConfig) -> bool:
        """Abre el panel de ataque, selecciona tropa libre y confirma march."""
        if not self._idle_troops_available(ctx, config):
            ctx.console.log("[warning] No se encontraron tropas descansando para la misión del radar")
            return False
        if not ctx.vision:
            return False
        if not self._tap_first_template(
            ctx,
            config.attack_button_templates,
            config.attack_button_threshold,
            config.attack_button_timeout,
            label="radar-attack",
            delay=config.tap_delay,
        ):
            return False
        if config.attack_panel_delay > 0:
            ctx.device.sleep(config.attack_panel_delay)
        idle_result = self._locate_idle_panel_slot(ctx, config)
        if not idle_result:
            ctx.console.log("[warning] Al abrir el panel no se encontró la tropa disponible tras reintentos")
            return False
        idle_coords, matched = idle_result
        ctx.console.log(f"Tropa libre detectada ('{matched.name}')")
        ctx.device.tap(idle_coords, label="radar-idle")
        if config.troop_select_delay > 0:
            ctx.device.sleep(config.troop_select_delay)
        if not self._tap_march_with_retry(ctx, config):
            ctx.console.log("[warning] No se pudo confirmar el march de la misión del radar")
            return False
        if config.post_attack_delay > 0:
            ctx.device.sleep(config.post_attack_delay)
        return True

    def _idle_troops_available(self, ctx: TaskContext, config: RadarQuestConfig) -> bool:
        """Valida disponibilidad de tropas ya sea por HUD o template legacy."""
        if layout_supports_troop_states(ctx.layout):
            slots = detect_idle_slots(ctx)
            if slots:
                slot = slots[0]
                label = (slot.label or slot.slot_id or "?").upper()
                ctx.console.log(
                    f"[info] Tropas en descanso detectadas vía estados -> {label} ({describe_activity(slot.state)})"
                )
                return True
        if not ctx.vision or not config.idle_troop_templates:
            return False
        result = ctx.vision.find_any_template(
            config.idle_troop_templates,
            threshold=config.idle_troop_threshold,
        )
        if result:
            _, matched = result
            ctx.console.log(
                f"[info] Tropa libre detectada mediante template '{matched.name}'"
            )
            return True
        return False

    def _locate_idle_panel_slot(
        self, ctx: TaskContext, config: RadarQuestConfig, attempts: int = 2
    ) -> tuple[Coord, Path] | None:
        """Busca el template de tropa libre dentro del panel de ataque con reintentos."""
        if not ctx.vision:
            return None
        attempts = max(1, attempts)
        for attempt in range(attempts):
            result = ctx.vision.wait_for_any_template(
                config.idle_troop_templates,
                timeout=config.idle_troop_timeout,
                poll_interval=0.5,
                threshold=config.idle_troop_threshold,
                raise_on_timeout=False,
            )
            if result:
                return result
            self._log_troop_state_snapshot(ctx)
            if attempt < attempts - 1:
                ctx.console.log(
                    "[info] No se detectó tropa libre en el panel del radar; reintentando"
                )
                ctx.device.sleep(1.0)
        return None

    def _tap_march_with_retry(
        self, ctx: TaskContext, config: RadarQuestConfig, attempts: int = 2
    ) -> bool:
        """Pulsa el botón March con reintentos, registrando estados si falla."""
        attempts = max(1, attempts)
        for attempt in range(attempts):
            if self._tap_first_template(
                ctx,
                config.march_button_templates,
                config.march_button_threshold,
                config.march_button_timeout,
                label="radar-march",
                delay=config.tap_delay,
            ):
                return True
            self._log_troop_state_snapshot(ctx)
            if attempt < attempts - 1:
                ctx.console.log(
                    "[info] Botón 'March' no detectado; se intentará nuevamente"
                )
                ctx.device.sleep(1.0)
        return False

    def _log_troop_state_snapshot(self, ctx: TaskContext) -> None:
        """Imprime el estado actual de las tropas si el layout soporta el HUD."""
        if not layout_supports_troop_states(ctx.layout):
            return
        states = detect_troop_states(ctx)
        if not states:
            return
        summary = ", ".join(
            f"{(slot.label or slot.slot_id).upper()}: {describe_activity(slot.state)}"
            for slot in states
        )
        ctx.console.log(f"[info] Estados actuales de tropas -> {summary}")

    def _ensure_menu_visible(self, ctx: TaskContext, config: RadarQuestConfig) -> bool:
        """Comprueba si el panel del radar está abierto y, de no ser así, lo abre."""
        if self._is_menu_visible(ctx, config):
            return True
        return self._open_menu(ctx, config)

    def _is_menu_visible(self, ctx: TaskContext, config: RadarQuestConfig) -> bool:
        """Usa templates del header para saber si seguimos dentro del panel."""
        if not ctx.vision or not config.menu_templates:
            return False
        result = ctx.vision.find_any_template(
            config.menu_templates,
            threshold=config.menu_threshold,
        )
        return bool(result)

    def _open_menu(self, ctx: TaskContext, config: RadarQuestConfig) -> bool:
        """Intenta primero el icono local y luego el de mapa para mostrar el radar."""
        if self._tap_icon_and_wait_for_menu(
            ctx,
            config,
            config.icon_templates,
            config.icon_threshold,
            config.icon_timeout,
            label="radar-icon",
        ):
            return True
        if self._tap_icon_and_wait_for_menu(
            ctx,
            config,
            config.world_icon_templates,
            config.world_icon_threshold,
            config.world_icon_timeout,
            label="radar-world-icon",
        ):
            return True
        return False

    def _tap_icon_and_wait_for_menu(
        self,
        ctx: TaskContext,
        config: RadarQuestConfig,
        template_paths: Sequence[Path],
        threshold: float,
        timeout: float,
        *,
        label: str,
    ) -> bool:
        if not template_paths or not ctx.vision:
            return False
        if not self._tap_first_template(
            ctx,
            template_paths,
            threshold,
            timeout,
            label=label,
            delay=config.tap_delay,
        ):
            return False
        if self._wait_for_menu(ctx, config):
            return True
        ctx.console.log(
            f"[warning] Icono del radar pulsado pero el panel no apareció; se intentará volver con 'back'"
        )
        self._press_back_with_fallback(ctx, config, label="radar-open-recovery")
        return False

    def _wait_for_menu(self, ctx: TaskContext, config: RadarQuestConfig) -> bool:
        """Espera a que aparezca el header del menú tras tocar los iconos."""
        if not ctx.vision:
            return False
        result = ctx.vision.wait_for_any_template(
            config.menu_templates,
            timeout=config.menu_timeout,
            poll_interval=0.5,
            threshold=config.menu_threshold,
            raise_on_timeout=False,
        )
        return bool(result)

    def _tap_first_template(
        self,
        ctx: TaskContext,
        template_paths: Sequence[Path],
        threshold: float,
        timeout: float,
        *,
        label: str,
        delay: float,
    ) -> bool:
        """Pulsa el primer template disponible de una lista y aplica delay opcional."""
        if not template_paths or not ctx.vision:
            return False
        result = ctx.vision.wait_for_any_template(
            template_paths,
            timeout=timeout,
            poll_interval=0.5,
            threshold=threshold,
            raise_on_timeout=False,
        )
        if not result:
            return False
        coords, matched = result
        ctx.console.log(f"Template '{matched.name}' detectado ({label})")
        ctx.device.tap(coords, label=label)
        if delay > 0:
            ctx.device.sleep(delay)
        return True

    def _close_menu(self, ctx: TaskContext, config: RadarQuestConfig, *, force: bool = False) -> None:
        """Cierra el panel de radar usando el botón back; puede forzarse aunque no haya header."""
        if not force and not self._is_menu_visible(ctx, config):
            return
        self._press_back_with_fallback(ctx, config, label="radar-exit")

    def _press_back_with_fallback(
        self,
        ctx: TaskContext,
        config: RadarQuestConfig,
        *,
        label: str,
    ) -> None:
        if tap_back_button(ctx, label=label):
            if config.tap_delay > 0:
                ctx.device.sleep(config.tap_delay)
            return
        back_coord = ctx.layout.buttons.get("back_button")
        if back_coord:
            ctx.console.log(
                f"[warning] Botón 'back' no detectado por template ({label}); usando coordenada del layout"
            )
            ctx.device.tap(back_coord, label=f"{label}-layout")
        else:
            ctx.console.log(
                f"[warning] Botón 'back' no detectado y sin layout; tocando (539, 0) ({label})"
            )
            ctx.device.tap((539, 0), label=f"{label}-fallback")
        if config.tap_delay > 0:
            ctx.device.sleep(config.tap_delay)

    def _current_tracker(self, ctx: TaskContext, task_name: str) -> int:
        """Lee el progreso registrado en el tracker diario para la tarea indicada."""
        if not ctx.daily_tracker:
            return 0
        return ctx.daily_tracker.current_count(ctx.farm.name, task_name)

    def _record_progress(self, ctx: TaskContext, task_name: str, fallback: int) -> int:
        """Actualiza el tracker y devuelve el nuevo total (o fallback si no existe)."""
        if not ctx.daily_tracker:
            return fallback
        ctx.daily_tracker.record_progress(ctx.farm.name, task_name)
        return ctx.daily_tracker.current_count(ctx.farm.name, task_name)

    def _recover_from_mission_screen(self, ctx: TaskContext, config: RadarQuestConfig) -> None:
        """Intenta cerrar el panel del mapa tras un intento de misión fallido."""
        if not tap_back_button(ctx, label="radar-mission-back"):
            ctx.console.log(
                "[warning] Botón 'back' no detectado al salir del mapa del radar; se usará coordenada (539, 0)"
            )
            ctx.device.tap((539, 0), label="radar-mission-fallback")
        if config.tap_delay > 0:
            ctx.device.sleep(config.tap_delay)

    def _return_to_radar_menu(self, ctx: TaskContext, config: RadarQuestConfig, attempts: int = 2) -> bool:
        """Garantiza que el panel del radar vuelva a mostrarse tras usar el mapa."""
        attempts = max(1, attempts)
        for attempt in range(attempts):
            if self._ensure_menu_visible(ctx, config):
                return True
            ctx.console.log(
                f"[info] Radar no visible tras misión; intentando recuperación ({attempt + 1}/{attempts})"
            )
            self._recover_from_mission_screen(ctx, config)
        return self._ensure_menu_visible(ctx, config)
