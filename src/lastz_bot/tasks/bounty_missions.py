"""Gestión de misiones de recompensa: abre menú, envía héroes y reclama loot."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from ..devices import resolve_button
from .base import TaskContext
from .utils import tap_back_button, dismiss_overlay_if_present

Coord = Tuple[int, int]


class MissionResult(Enum):
    """Resultado al intentar despachar una misión."""

    SENT = auto()
    HERO_BUSY = auto()
    RETRY = auto()
    FAILURE = auto()


class HeroBusyReason(Enum):
    """Causas comunes por las que no se pudo enviar héroes."""

    OVERLAY = auto()
    DETAIL_PERSIST = auto()


@dataclass
class BountyMissionConfig:
    """Todos los parámetros dinámicos requeridos por la tarea."""
    icon_templates: Sequence[Path]
    menu_templates: Sequence[Path]
    go_button_templates: Sequence[Path]
    forbidden_go_templates: Sequence[Path]
    quick_deploy_templates: Sequence[Path]
    send_button_templates: Sequence[Path]
    hero_busy_templates: Sequence[Path]
    no_missions_templates: Sequence[Path]
    mission_badge_templates: Sequence[Path]
    claim_button_templates: Sequence[Path]
    claim_overlay_templates: Sequence[Path]
    hero_busy_dismiss_button: str | None
    claim_overlay_dismiss_button: str | None
    claim_overlay_threshold: float
    claim_overlay_timeout: float
    claim_overlay_poll: float
    claim_overlay_use_brightness: bool
    claim_overlay_brightness_threshold: float
    claim_overlay_fallback_tap: Coord | None
    claim_overlay_delay: float
    tap_delay: float
    icon_threshold: float
    menu_threshold: float
    go_button_threshold: float
    forbidden_go_threshold: float
    quick_deploy_threshold: float
    send_button_threshold: float
    hero_busy_threshold: float
    no_missions_threshold: float
    claim_button_threshold: float
    icon_timeout: float
    menu_timeout: float
    go_button_timeout: float
    quick_deploy_timeout: float
    send_button_timeout: float
    hero_busy_timeout: float
    claim_button_timeout: float
    mission_completion_timeout: float
    mission_completion_poll: float
    max_missions_per_run: int
    max_dispatch_fail_safe: int
    go_button_scan_limit: int
    max_no_go_retries: int
    go_retry_delay: float
    quick_deploy_delay: float
    send_delay: float
    post_send_delay: float
    back_delay: float
    daily_task_name: str
    daily_limit: int
    skip_daily_limit_check: bool

    @staticmethod
    def from_params(ctx: TaskContext, params: Dict[str, Any]) -> "BountyMissionConfig":
        """Construye la configuración resolviendo templates y defaults."""
        layout = ctx.layout
        console = ctx.console

        def as_list(value: object) -> List[str]:
            """Ejecuta la logica de `as_list` dentro del flujo del bot.

            Args:
                value: Parametro usado por la rutina.
            Returns:
                object: Resultado de la operacion.
            """
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

        def ensure_list(value: object, fallback: Sequence[str]) -> List[str]:
            """Ejecuta la logica de `ensure_list` dentro del flujo del bot.

            Args:
                value: Parametro usado por la rutina.
                fallback: Parametro usado por la rutina.
            Returns:
                object: Resultado de la operacion.
            """
            entries = as_list(value)
            if entries:
                return entries
            return [str(item).strip() for item in fallback if str(item).strip()]

        def as_coord(value: object) -> Coord | None:
            """Ejecuta la logica de `as_coord` dentro del flujo del bot.

            Args:
                value: Parametro usado por la rutina.
            Returns:
                object: Resultado de la operacion.
            """
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

        def resolve(names: Sequence[str]) -> List[Path]:
            """Ejecuta la logica de `resolve` dentro del flujo del bot.

            Args:
                names: Parametro usado por la rutina.
            Returns:
                object: Resultado de la operacion.
            """
            paths: List[Path] = []
            for name in names:
                try:
                    paths.extend(layout.template_paths(name))
                except KeyError:
                    console.log(
                        f"[warning] Template '{name}' no definido para bounty_missions"
                    )
            return paths

        icon_templates = resolve(ensure_list(params.get("icon_template"), ["bounty_icon"]))
        menu_templates = resolve(ensure_list(params.get("menu_template"), ["bounty_menu_header"]))
        go_button_templates = resolve(ensure_list(params.get("go_button_templates"), ["bounty_go_button"]))
        forbidden_go_templates = resolve(as_list(params.get("forbidden_go_templates")))
        quick_deploy_templates = resolve(ensure_list(params.get("quick_deploy_template"), ["bounty_quick_deploy"]))
        send_button_templates = resolve(ensure_list(params.get("send_button_templates"), ["bounty_send_button", "march_button"]))
        hero_busy_templates = resolve(as_list(params.get("hero_busy_templates")))
        no_missions_templates = resolve(as_list(params.get("no_missions_templates")))
        mission_badge_templates = resolve(as_list(params.get("mission_badge_templates")))
        claim_button_templates = resolve(as_list(params.get("claim_button_templates")))
        claim_overlay_templates = resolve(as_list(params.get("claim_overlay_templates")))

        dismiss_raw = params.get("hero_busy_dismiss_button", "close_popup")
        dismiss_name = str(dismiss_raw).strip() if dismiss_raw is not None else ""
        claim_dismiss_raw = params.get("claim_overlay_dismiss_button")
        if claim_dismiss_raw is None:
            claim_dismiss = dismiss_name
        else:
            claim_dismiss = str(claim_dismiss_raw).strip() if claim_dismiss_raw is not None else ""

        tap_delay = float(params.get("tap_delay", 1.0))
        claim_overlay_delay = float(params.get("claim_overlay_delay", tap_delay))

        claim_overlay_threshold = float(
            params.get(
                "claim_overlay_threshold",
                params.get("claim_button_threshold", 0.84),
            )
        )

        claim_overlay_timeout = float(params.get("claim_overlay_timeout", 4.0))
        claim_overlay_poll = float(params.get("claim_overlay_poll_interval", 0.4))
        claim_overlay_use_brightness = bool(
            params.get("claim_overlay_use_brightness", True)
        )
        claim_overlay_brightness_threshold = float(
            params.get("claim_overlay_brightness_threshold", 0.35)
        )
        claim_overlay_fallback = as_coord(params.get("claim_overlay_dismiss_tap"))

        go_button_scan_limit = max(1, int(params.get("go_button_scan_limit", 12)))
        max_no_go_retries = max(1, int(params.get("max_no_go_retries", 2)))

        return BountyMissionConfig(
            icon_templates=icon_templates,
            menu_templates=menu_templates,
            go_button_templates=go_button_templates,
            forbidden_go_templates=forbidden_go_templates,
            quick_deploy_templates=quick_deploy_templates,
            send_button_templates=send_button_templates,
            hero_busy_templates=hero_busy_templates,
            no_missions_templates=no_missions_templates,
            mission_badge_templates=mission_badge_templates,
            claim_button_templates=claim_button_templates,
            claim_overlay_templates=claim_overlay_templates,
            hero_busy_dismiss_button=dismiss_name or None,
            claim_overlay_dismiss_button=claim_dismiss or None,
            claim_overlay_threshold=claim_overlay_threshold,
            claim_overlay_timeout=claim_overlay_timeout,
            claim_overlay_poll=claim_overlay_poll,
            claim_overlay_use_brightness=claim_overlay_use_brightness,
            claim_overlay_brightness_threshold=claim_overlay_brightness_threshold,
            claim_overlay_fallback_tap=claim_overlay_fallback,
            claim_overlay_delay=claim_overlay_delay,
            tap_delay=tap_delay,
            icon_threshold=float(params.get("icon_threshold", 0.82)),
            menu_threshold=float(params.get("menu_threshold", 0.82)),
            go_button_threshold=float(params.get("go_button_threshold", 0.89)),
            forbidden_go_threshold=float(
                params.get("forbidden_go_threshold")
                or params.get("go_button_threshold", 0.89)
            ),
            quick_deploy_threshold=float(params.get("quick_deploy_threshold", 0.84)),
            send_button_threshold=float(params.get("send_button_threshold", 0.84)),
            hero_busy_threshold=float(params.get("hero_busy_threshold", 0.84)),
            no_missions_threshold=float(params.get("no_missions_threshold", 0.84)),
            claim_button_threshold=float(params.get("claim_button_threshold", 0.84)),
            icon_timeout=float(params.get("icon_timeout", 6.0)),
            menu_timeout=float(params.get("menu_timeout", 5.0)),
            go_button_timeout=float(params.get("go_button_timeout", 4.0)),
            quick_deploy_timeout=float(params.get("quick_deploy_timeout", 4.0)),
            send_button_timeout=float(params.get("send_button_timeout", 5.0)),
            hero_busy_timeout=float(params.get("hero_busy_timeout", 3.0)),
            claim_button_timeout=float(params.get("claim_button_timeout", 2.0)),
            mission_completion_timeout=float(params.get("mission_completion_timeout", 4.0)),
            mission_completion_poll=float(params.get("mission_completion_poll", 0.5)),
            max_missions_per_run=max(1, int(params.get("max_missions_per_run", 4))),
            max_dispatch_fail_safe=max(0, int(params.get("max_dispatch_fail_safe", 12))),
            go_button_scan_limit=go_button_scan_limit,
            max_no_go_retries=max_no_go_retries,
            go_retry_delay=float(params.get("go_retry_delay", 2.0)),
            quick_deploy_delay=float(params.get("quick_deploy_delay", 1.5)),
            send_delay=float(params.get("send_delay", 1.0)),
            post_send_delay=float(params.get("post_send_delay", 4.0)),
            back_delay=float(params.get("back_delay", 1.0)),
            daily_task_name=str(params.get("daily_task_name") or "bounty_missions"),
            daily_limit=max(1, int(params.get("daily_limit", 4))),
            skip_daily_limit_check=bool(params.get("skip_daily_limit_check", False)),
        )


class BountyMissionsTask:
    """Controla el envío de misiones normales/rápidas y manejo de overlays."""

    name = "bounty_missions"
    manual_daily_logging = True
    _failure_radius = 48
    _forbidden_radius = 48
    _max_attempts_per_coord = 2

    def run(self, ctx: TaskContext, params: Dict[str, Any]) -> None:  # type: ignore[override]
        """Envía misiones hasta agotar límites, reclamando recompensas si aparecen."""
        if not ctx.vision:
            ctx.console.log("[warning] VisionHelper no disponible; bounty_missions requiere detecciones")
            return

        config = BountyMissionConfig.from_params(ctx, params)
        if not config.icon_templates or not config.menu_templates or not config.go_button_templates:
            ctx.console.log("[warning] Faltan templates críticos para bounty_missions")
            return
        if not config.quick_deploy_templates or not config.send_button_templates:
            ctx.console.log("[warning] Faltan templates para el flujo de despliegue de bounty_missions")
            return

        tracker_count = self._current_tracker(ctx, config.daily_task_name)
        limit_enforced = not config.skip_daily_limit_check and config.daily_limit > 0
        if limit_enforced and tracker_count >= config.daily_limit:
            ctx.console.log("[info] Las bounty missions diarias ya se registraron; se omite la tarea")
            return

        if not self._ensure_menu_visible(ctx, config):
            ctx.console.log("[warning] No se pudo abrir el menú de bounty missions")
            return

        missions_launched = 0
        reopen_needed = False
        failed_targets: List[Coord] = []
        target_failures: Dict[Coord, int] = {}
        consecutive_go_failures = 0
        max_go_failures = max(1, min(2, config.go_button_scan_limit))
        consecutive_no_go = 0
        while missions_launched < config.max_missions_per_run:
            if limit_enforced and tracker_count >= config.daily_limit:
                ctx.console.log("[info] Se alcanzó el límite diario de bounty missions")
                break

            if reopen_needed:
                if not self._ensure_menu_visible(ctx, config):
                    ctx.console.log("[warning] No se pudo reabrir el menú tras enviar una misión")
                    break
                reopen_needed = False

            target = self._next_go_target(ctx, config, failed_targets)
            if target is None:
                consecutive_no_go += 1
                if self._no_missions_remaining(ctx, config):
                    ctx.console.log("[info] No se encontraron más misiones disponibles")
                    break
                if consecutive_no_go >= config.max_no_go_retries:
                    ctx.console.log(
                        "[warning] No se detectaron botones 'Go' tras varios reintentos; se detiene bounty_missions"
                    )
                    break
                if config.go_retry_delay > 0:
                    ctx.console.log("[info] No se detectó botón 'Go'; reintentando tras una pausa corta")
                    ctx.device.sleep(config.go_retry_delay)
                    continue
                break
            consecutive_no_go = 0

            result = self._execute_mission(ctx, config, target)
            target_key: Coord = (int(target[0]), int(target[1]))
            if result is MissionResult.SENT:
                missions_launched += 1
                ctx.console.log(f"Bounty mission enviada #{missions_launched}")
                tracker_count += 1
                tracker_count = self._record_progress(ctx, config.daily_task_name, tracker_count)
                if config.max_dispatch_fail_safe > 0 and missions_launched >= config.max_dispatch_fail_safe:
                    ctx.console.log(
                        "[warning] Se alcanzó el límite de envíos consecutivos configurado; se detiene bounty_missions para evitar bucles"
                    )
                    break
                reopen_needed = True
                consecutive_go_failures = 0
                target_failures.pop(target_key, None)
                continue
            if result is MissionResult.HERO_BUSY:
                # Sin héroes disponibles en esta misión: bloquear coord y probar con otra
                target_failures[target_key] = target_failures.get(target_key, 0) + 1
                if target not in failed_targets:
                    failed_targets.append(target)
                consecutive_go_failures = 0
                ctx.console.log(
                    "[info] Misión rechazada por héroes ocupados; se bloquea ese 'Go' y se intenta con otro"
                )
                continue

            if result is MissionResult.RETRY:
                attempts = target_failures.get(target_key, 0) + 1
                target_failures[target_key] = attempts
                if attempts >= self._max_attempts_per_coord and target not in failed_targets:
                    failed_targets.append(target)
                consecutive_go_failures += 1
                if consecutive_go_failures >= max_go_failures:
                    ctx.console.log(
                        "[warning] Varias misiones no se pudieron abrir tras pulsar 'Go'; se asume que no quedan misiones válidas"
                    )
                    break
                ctx.console.log(
                    f"[info] Botón 'Go' en {target_key} falló; reintento {consecutive_go_failures}/{max_go_failures} (intentos en coord={attempts}/{self._max_attempts_per_coord})"
                )
                self._return_to_mission_list(ctx, config)
                continue

            # Otros fallos definitivos
            target_failures[target_key] = target_failures.get(target_key, 0) + 1
            if target not in failed_targets:
                failed_targets.append(target)
            ctx.console.log("[warning] La mision actual no pudo completarse; deteniendo bounty_missions")
            break

        if self._is_menu_visible(ctx, config):
            self._close_menu(ctx, config)

    def _execute_mission(
        self,
        ctx: TaskContext,
        config: BountyMissionConfig,
        target: Coord,
    ) -> MissionResult:
        ctx.device.tap(target, label="bounty-go")
        if config.tap_delay > 0:
            ctx.device.sleep(config.tap_delay)

        if not self._wait_for_quick_deploy(ctx, config):
            ctx.console.log(
                "[warning] No se detectó 'Quick Deploy' tras pulsar 'Go'; regresando al listado"
            )
            self._return_to_mission_list(ctx, config)
            return MissionResult.RETRY

        if not self._tap_first_template(
            ctx,
            config.quick_deploy_templates,
            config.quick_deploy_threshold,
            config.quick_deploy_timeout,
            label="bounty-quick-deploy",
            delay=config.quick_deploy_delay,
        ):
            return MissionResult.RETRY

        send_tapped = self._tap_first_template(
            ctx,
            config.send_button_templates,
            config.send_button_threshold,
            config.send_button_timeout,
            label="bounty-send",
            delay=config.send_delay,
        )
        if not send_tapped:
            busy_reason = self._detect_hero_busy_reason(ctx, config, wait=False)
            if busy_reason:
                ctx.console.log("[info] No hay heroes disponibles para la mision actual")
                self._handle_hero_busy(ctx, config, busy_reason)
                return MissionResult.HERO_BUSY
            return MissionResult.RETRY

        busy_reason = self._detect_hero_busy_reason(ctx, config, wait=True)
        if busy_reason:
            ctx.console.log("[info] La mision fue rechazada por falta de heroes")
            self._handle_hero_busy(ctx, config, busy_reason)
            return MissionResult.HERO_BUSY

        if config.post_send_delay > 0:
            ctx.device.sleep(config.post_send_delay)

        if not self._wait_for_icon(ctx, config):
            return MissionResult.RETRY
        return MissionResult.SENT

    def _ensure_menu_visible(self, ctx: TaskContext, config: BountyMissionConfig) -> bool:
        if self._is_menu_visible(ctx, config):
            self._handle_pending_claims(ctx, config)
            return True
        opened = self._open_menu(ctx, config)
        if opened:
            self._handle_pending_claims(ctx, config)
        return opened

    def _handle_pending_claims(self, ctx: TaskContext, config: BountyMissionConfig) -> None:
        if not ctx.vision or not config.claim_button_templates:
            return
        result = ctx.vision.find_any_template(
            config.claim_button_templates,
            threshold=config.claim_button_threshold,
        )
        if not result and config.claim_button_timeout > 0:
            result = ctx.vision.wait_for_any_template(
                config.claim_button_templates,
                timeout=config.claim_button_timeout,
                poll_interval=0.4,
                threshold=config.claim_button_threshold,
                raise_on_timeout=False,
            )
        if not result:
            return
        coords, matched = result
        ctx.console.log(
            f"[info] Botón '{matched.name}' detectado en bounty missions; reclamando recompensa antes de continuar"
        )
        ctx.device.tap(coords, label="bounty-claim")
        if config.tap_delay > 0:
            ctx.device.sleep(config.tap_delay)
        self._dismiss_claim_overlay(ctx, config)
        if not self._wait_for_menu(ctx, config):
            ctx.console.log(
                "[warning] El menú de bounty missions no reapareció tras reclamar; reintentando apertura"
            )
            self._open_menu(ctx, config)

    def _dismiss_claim_overlay(self, ctx: TaskContext, config: BountyMissionConfig) -> None:
        overlay_closed = dismiss_overlay_if_present(
            ctx,
            list(config.claim_overlay_templates) or None,
            config.claim_overlay_dismiss_button,
            timeout=config.claim_overlay_timeout,
            poll_interval=config.claim_overlay_poll,
            threshold=config.claim_overlay_threshold,
            delay=config.claim_overlay_delay,
            use_brightness=config.claim_overlay_use_brightness,
            brightness_threshold=config.claim_overlay_brightness_threshold,
            fallback_tap=config.claim_overlay_fallback_tap,
        )
        if overlay_closed:
            return

        tried: set[str] = set()
        for name in (config.claim_overlay_dismiss_button, config.hero_busy_dismiss_button):
            if not name or name in tried:
                continue
            tried.add(name)
            try:
                coord = resolve_button(ctx.layout, name)
            except KeyError:
                continue
            ctx.device.tap(coord, label="bounty-claim-close")
            if config.claim_overlay_delay > 0:
                ctx.device.sleep(config.claim_overlay_delay)
            return

        back_coord = ctx.layout.buttons.get("back_button")
        if back_coord:
            ctx.device.tap(back_coord, label="back-button")
        elif not tap_back_button(ctx, label="bounty-claim-back"):
            ctx.console.log(
                "[warning] No se detectó el botón 'back' al cerrar overlay de recompensas; se usará coordenada (539, 0)"
            )
            ctx.device.tap((539, 0), label="bounty-claim-fallback")
        if config.back_delay > 0:
            ctx.device.sleep(config.back_delay)

    def _open_menu(self, ctx: TaskContext, config: BountyMissionConfig) -> bool:
        tapped = self._tap_first_template(
            ctx,
            config.icon_templates,
            config.icon_threshold,
            config.icon_timeout,
            label="bounty-icon",
            delay=config.tap_delay,
        )
        if not tapped:
            return False
        return self._wait_for_menu(ctx, config, timeout=config.mission_completion_timeout)

    def _wait_for_menu(
        self,
        ctx: TaskContext,
        config: BountyMissionConfig,
        *,
        timeout: float | None = None,
    ) -> bool:
        if not config.menu_templates:
            return True
        assert ctx.vision is not None
        effective_timeout = config.menu_timeout if timeout is None else timeout
        result = ctx.vision.wait_for_any_template(
            config.menu_templates,
            timeout=effective_timeout,
            poll_interval=0.5,
            threshold=config.menu_threshold,
            raise_on_timeout=False,
        )
        return bool(result)

    def _is_menu_visible(self, ctx: TaskContext, config: BountyMissionConfig) -> bool:
        if not config.menu_templates or not ctx.vision:
            return True
        result = ctx.vision.find_any_template(
            config.menu_templates,
            threshold=config.menu_threshold,
        )
        return result is not None

    def _wait_for_quick_deploy(self, ctx: TaskContext, config: BountyMissionConfig) -> bool:
        assert ctx.vision is not None
        result = ctx.vision.wait_for_any_template(
            config.quick_deploy_templates,
            timeout=config.quick_deploy_timeout,
            poll_interval=0.5,
            threshold=config.quick_deploy_threshold,
            raise_on_timeout=False,
        )
        return bool(result)

    def _next_go_target(
        self,
        ctx: TaskContext,
        config: BountyMissionConfig,
        failed: Sequence[Coord],
    ) -> Coord | None:
        assert ctx.vision is not None
        forbidden = self._forbidden_targets(ctx, config)
        if forbidden:
            coords_str = ", ".join(f"({x},{y})" for x, y in forbidden[:5])
            ctx.console.log(
                f"[info] Detectados {len(forbidden)} botones 'Go' inválidos cerca de: {coords_str}"
            )
        matches = ctx.vision.find_all_templates(
            config.go_button_templates,
            threshold=config.go_button_threshold,
            max_results=config.go_button_scan_limit,
        )
        if not matches:
            fallback_threshold = max(0.7, config.go_button_threshold * 0.9)
            fallback_limit = max(config.go_button_scan_limit, 12) * 2
            ctx.console.log(
                f"[info] No se detectaron 'Go' al umbral {config.go_button_threshold:.2f}; reintentando con {fallback_threshold:.2f} y límite {fallback_limit}"
            )
            matches = ctx.vision.find_all_templates(
                config.go_button_templates,
                threshold=fallback_threshold,
                max_results=fallback_limit,
            )
        if matches:
            matches = self._dedup_matches(matches, radius=2.5)
            matches = sorted(matches, key=lambda item: (item[0][1], item[0][0]))
            sample = ", ".join(f"({c[0]},{c[1]})" for c, _ in matches[:6])
            ctx.console.log(
                f"[info] Coincidencias 'Go' encontradas (orden Y,X): {len(matches)} -> {sample}"
            )
        filtered_any = False
        for coords, _ in matches:
            if self._is_failed(coords, failed):
                continue
            if forbidden and self._is_forbidden(coords, forbidden):
                continue
            filtered_any = True
            return coords
        if matches and not filtered_any:
            ctx.console.log("[info] Todas las coincidencias 'Go' quedaron filtradas (fallidas/forbidden)")
        return None

    def _forbidden_targets(
        self,
        ctx: TaskContext,
        config: BountyMissionConfig,
    ) -> List[Coord]:
        if not config.forbidden_go_templates or not ctx.vision:
            return []
        matches = ctx.vision.find_all_templates(
            config.forbidden_go_templates,
            threshold=config.forbidden_go_threshold,
            max_results=config.go_button_scan_limit * 2,
        )
        return [coords for coords, _ in matches]

    def _dedup_matches(
        self,
        matches: Sequence[tuple[Coord, Path]],
        *,
        radius: float,
    ) -> List[tuple[Coord, Path]]:
        """Elimina coincidencias muy cercanas (p.ej., 1px) para evitar duplicados del mismo botón."""

        unique: List[tuple[Coord, Path]] = []
        for coords, match in matches:
            cx, cy = coords
            if any(math.hypot(cx - ux, cy - uy) <= radius for (ux, uy), _ in unique):
                continue
            unique.append((coords, match))
        return unique

    def _is_failed(self, coords: Coord, failed: Sequence[Coord]) -> bool:
        for fx, fy in failed:
            if math.hypot(coords[0] - fx, coords[1] - fy) <= self._failure_radius:
                return True
        return False

    def _is_forbidden(self, coords: Coord, forbidden: Sequence[Coord]) -> bool:
        for fx, fy in forbidden:
            if math.hypot(coords[0] - fx, coords[1] - fy) <= self._forbidden_radius:
                return True
        return False

    def _no_missions_remaining(self, ctx: TaskContext, config: BountyMissionConfig) -> bool:
        if config.no_missions_templates and ctx.vision:
            result = ctx.vision.find_any_template(
                config.no_missions_templates,
                threshold=config.no_missions_threshold,
            )
            if result:
                return True
        if config.mission_badge_templates and ctx.vision:
            badge = ctx.vision.find_any_template(
                config.mission_badge_templates,
                threshold=config.go_button_threshold,
            )
            return badge is None
        # Si no hay detectores específicos configurados, no asumir que no hay misiones; se reintentará escanear.
        return False

    def _tap_first_template(
        self,
        ctx: TaskContext,
        template_paths: Sequence[Path],
        threshold: float,
        timeout: float,
        *,
        label: str,
        delay: float = 0.0,
    ) -> bool:
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
        ctx.console.log(f"Template '{matched.name}' detectado para {label}")
        ctx.device.tap(coords, label=label)
        if delay > 0:
            ctx.device.sleep(delay)
        return True

    def _detect_hero_busy_reason(
        self,
        ctx: TaskContext,
        config: BountyMissionConfig,
        *,
        wait: bool,
    ) -> HeroBusyReason | None:
        if ctx.vision and config.hero_busy_templates:
            if wait:
                result = ctx.vision.wait_for_any_template(
                    config.hero_busy_templates,
                    timeout=config.hero_busy_timeout,
                    poll_interval=0.5,
                    threshold=config.hero_busy_threshold,
                    raise_on_timeout=False,
                )
                if result:
                    return HeroBusyReason.OVERLAY
            else:
                result = ctx.vision.find_any_template(
                    config.hero_busy_templates,
                    threshold=config.hero_busy_threshold,
                )
                if result:
                    return HeroBusyReason.OVERLAY

        if self._detail_panel_persists(ctx, config, wait=wait):
            return HeroBusyReason.DETAIL_PERSIST
        return None

    def _handle_hero_busy(
        self,
        ctx: TaskContext,
        config: BountyMissionConfig,
        reason: HeroBusyReason,
    ) -> None:
        if reason is HeroBusyReason.OVERLAY:
            self._dismiss_hero_busy(ctx, config)
        self._reset_after_busy(ctx, config)

    def _reset_after_busy(self, ctx: TaskContext, config: BountyMissionConfig) -> None:
        """Cierra la vista actual una sola vez y reabre el menú desde el icono."""

        if self._is_menu_visible(ctx, config):
            return

        closed = self._tap_close_button(ctx, config)
        if not closed and not tap_back_button(ctx, label="bounty-busy-back"):
            ctx.console.log("[warning] No se pudo cerrar la vista tras héroes ocupados")
        if config.back_delay > 0:
            ctx.device.sleep(config.back_delay)

        if not self._open_menu(ctx, config):
            ctx.console.log(
                "[warning] No se pudo reabrir el menú de bounty tras héroes ocupados"
            )

    def _detail_panel_persists(
        self,
        ctx: TaskContext,
        config: BountyMissionConfig,
        *,
        wait: bool,
    ) -> bool:
        if not ctx.vision or not config.quick_deploy_templates:
            return False
        if not wait:
            return (
                ctx.vision.find_any_template(
                    config.quick_deploy_templates,
                    threshold=config.quick_deploy_threshold,
                )
                is not None
            )

        timeout = max(0.5, config.hero_busy_timeout)
        poll = max(0.2, min(1.0, config.mission_completion_poll or 0.5))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if ctx.vision.find_any_template(
                config.quick_deploy_templates,
                threshold=config.quick_deploy_threshold,
            ) is None:
                return False
            ctx.device.sleep(poll)
        return True

    def _dismiss_hero_busy(self, ctx: TaskContext, config: BountyMissionConfig) -> None:
        if self._tap_close_button(ctx, config, label="bounty-hero-dismiss"):
            return
        if config.hero_busy_templates and ctx.vision:
            result = ctx.vision.find_any_template(
                config.hero_busy_templates,
                threshold=config.hero_busy_threshold,
            )
            if result:
                coords, _ = result
                ctx.device.tap(coords, label="bounty-hero-dismiss")
                if config.tap_delay > 0:
                    ctx.device.sleep(config.tap_delay)
                return
        back_coord = ctx.layout.buttons.get("back_button")
        if back_coord:
            ctx.device.tap(back_coord, label="back-button")
        elif not tap_back_button(ctx, label="bounty-hero-dismiss"):
            ctx.console.log(
                "[warning] Botón 'back' no detectado para cerrar el aviso de héroe ocupado; se usará coordenada (539, 0)"
            )
            ctx.device.tap((539, 0), label="bounty-hero-fallback")
        if config.tap_delay > 0:
            ctx.device.sleep(config.tap_delay)

    def _return_to_mission_list(self, ctx: TaskContext, config: BountyMissionConfig) -> bool:
        if self._is_menu_visible(ctx, config):
            return True

        if not self._tap_close_button(ctx, config):
            tap_back_button(ctx, label="bounty-back")
        if config.back_delay > 0:
            ctx.device.sleep(config.back_delay)

        if self._is_menu_visible(ctx, config):
            return True
        return self._open_menu(ctx, config)

    def _tap_close_button(
        self,
        ctx: TaskContext,
        config: BountyMissionConfig,
        *,
        label: str = "bounty-close",
    ) -> bool:
        button_name = config.hero_busy_dismiss_button or "close_popup"
        if not button_name:
            return False
        try:
            coord = resolve_button(ctx.layout, button_name)
        except KeyError:
            return False
        ctx.device.tap(coord, label=label)
        if config.tap_delay > 0:
            ctx.device.sleep(config.tap_delay)
        return True

    def _wait_for_icon(self, ctx: TaskContext, config: BountyMissionConfig) -> bool:
        if not ctx.vision or not config.icon_templates:
            return True
        result = ctx.vision.wait_for_any_template(
            config.icon_templates,
            timeout=config.icon_timeout,
            poll_interval=0.5,
            threshold=config.icon_threshold,
            raise_on_timeout=False,
        )
        return bool(result)

    def _close_menu(self, ctx: TaskContext, config: BountyMissionConfig) -> None:
        if not tap_back_button(ctx, label="bounty-menu-close"):
            ctx.console.log("[warning] No se detectó el botón 'back' para cerrar el menú de bounty missions")
        if config.back_delay > 0:
            ctx.device.sleep(config.back_delay)

    def _current_tracker(self, ctx: TaskContext, task_name: str) -> int:
        if not ctx.daily_tracker:
            return 0
        return ctx.daily_tracker.current_count(ctx.farm.name, task_name)

    def _record_progress(
        self,
        ctx: TaskContext,
        task_name: str,
        fallback: int,
    ) -> int:
        if not ctx.daily_tracker:
            return fallback
        ctx.daily_tracker.record_progress(ctx.farm.name, task_name)
        return ctx.daily_tracker.current_count(ctx.farm.name, task_name)
