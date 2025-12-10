"""Automatiza la mejora del Cuartel General y registra el temporizador resultante."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import cv2
import numpy as np
import pytesseract

from ..ocr import read_timer_from_region
from .base import TaskContext
from .utils import tap_back_button, dismiss_overlay_if_present

Coord = Tuple[int, int]
Region = Tuple[Coord, Coord]
NormalizedRegion = Tuple[Tuple[float, float], Tuple[float, float]]


def _as_list(value: object) -> List[str]:
    """Normaliza el parámetro recibido a una lista de strings limpios."""

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


def _coord_from_value(value: object, fallback: Coord) -> Coord:
    """Convierte coordenadas expresadas como lista/tupla/string a tupla entera."""

    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return fallback
    if isinstance(value, str):
        parts = value.split(",")
        if len(parts) == 2:
            try:
                return int(parts[0].strip()), int(parts[1].strip())
            except (TypeError, ValueError):
                return fallback
    return fallback


def _region_from_value(value: object, fallback: Region) -> Region:
    """Convierte una región en formato flexible a dos coordenadas absolutas."""

    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return fallback
    start, end = value
    start_coord = _coord_from_value(start, fallback[0])
    end_coord = _coord_from_value(end, fallback[1])
    return start_coord, end_coord


def _normalized_region_from_value(
    value: object, fallback: NormalizedRegion | None
) -> NormalizedRegion | None:
    """Convierte regiones normalizadas expresadas como listas en tuplas."""

    if value is None:
        return fallback
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return fallback
    y_pair, x_pair = value
    if (
        isinstance(y_pair, (list, tuple))
        and len(y_pair) == 2
        and isinstance(x_pair, (list, tuple))
        and len(x_pair) == 2
    ):
        try:
            y_region = (float(y_pair[0]), float(y_pair[1]))
            x_region = (float(x_pair[0]), float(x_pair[1]))
            return y_region, x_region
        except (TypeError, ValueError):
            return fallback
    return fallback


def _resolve_template_paths(layout, names: object) -> List[Path]:  # type: ignore[no-untyped-def]
    """Resuelve nombres de template a rutas absolutas usando el layout activo."""

    resolved: List[Path] = []
    for template_name in _as_list(names):
        try:
            resolved.extend(layout.template_paths(template_name))
        except KeyError:
            continue
    return resolved


@dataclass
class ConstructionConfig:
    """Configura plantillas, coordenadas y tiempos usados por la tarea de construcción."""

    icon_templates: Sequence[Path]
    hq_panel_templates: Sequence[Path]
    upgrade_button_templates: Sequence[Path]
    upgrade_disabled_templates: Sequence[Path]
    go_button_templates: Sequence[Path]
    requirement_upgrade_templates: Sequence[Path]
    level_up_templates: Sequence[Path]
    resource_shortage_templates: Sequence[Path]
    resource_autofill_templates: Sequence[Path]
    resource_confirm_templates: Sequence[Path]
    help_button_templates: Sequence[Path]
    william_button_templates: Sequence[Path]
    timer_panel_templates: Sequence[Path]
    timer_region: Region
    timer_secondary_region: Region | None
    overlay_dismiss_tap: Coord | None
    overlay_use_brightness: bool
    overlay_brightness_threshold: float
    overlay_brightness_region: NormalizedRegion | None
    hq_tap: Coord
    icon_threshold: float
    panel_threshold: float
    button_threshold: float
    icon_timeout: float
    panel_timeout: float
    button_timeout: float
    tap_delay: float
    panel_delay: float
    travel_delay: float
    resource_delay: float
    help_delay: float
    timer_settle_delay: float
    metadata_key: str

    @staticmethod
    def from_params(ctx: TaskContext, params: Dict[str, object]) -> "ConstructionConfig":
        layout = ctx.layout
        overlay_tap = None
        if params.get("overlay_dismiss_tap") is not None:
            overlay_tap = _coord_from_value(params.get("overlay_dismiss_tap"), (539, 0))
        return ConstructionConfig(
            icon_templates=_resolve_template_paths(layout, params.get("icon_templates")),
            hq_panel_templates=_resolve_template_paths(layout, params.get("hq_panel_templates")),
            upgrade_button_templates=_resolve_template_paths(
                layout, params.get("upgrade_button_templates")
            ),
            upgrade_disabled_templates=_resolve_template_paths(
                layout, params.get("upgrade_disabled_templates")
            ),
            go_button_templates=_resolve_template_paths(layout, params.get("go_button_templates")),
            requirement_upgrade_templates=_resolve_template_paths(
                layout, params.get("requirement_upgrade_templates")
            ),
            level_up_templates=_resolve_template_paths(
                layout, params.get("level_up_templates")
            ),
            resource_shortage_templates=_resolve_template_paths(
                layout, params.get("resource_shortage_templates")
            ),
            resource_autofill_templates=_resolve_template_paths(
                layout, params.get("resource_autofill_templates")
            ),
            resource_confirm_templates=_resolve_template_paths(
                layout, params.get("resource_confirm_templates")
            ),
            help_button_templates=_resolve_template_paths(layout, params.get("help_button_templates")),
            william_button_templates=_resolve_template_paths(
                layout, params.get("william_button_templates")
            ),
            timer_panel_templates=_resolve_template_paths(layout, params.get("timer_panel_templates")),
            timer_region=_region_from_value(
                params.get("timer_region"), ((185, 463), (338, 481))
            ),
            timer_secondary_region=(
                None
                if "timer_secondary_region" in params and params.get("timer_secondary_region") is None
                else _region_from_value(
                    params.get("timer_secondary_region"), ((184, 364), (339, 382))
                )
            ),
            overlay_dismiss_tap=overlay_tap,
            overlay_use_brightness=bool(params.get("overlay_use_brightness", True)),
            overlay_brightness_threshold=float(params.get("overlay_dark_threshold", 0.35)),
            overlay_brightness_region=_normalized_region_from_value(
                params.get("overlay_dark_region"), ((0.1, 0.9), (0.1, 0.9))
            ),
            hq_tap=_coord_from_value(params.get("hq_tap"), (270, 480)),
            icon_threshold=float(params.get("icon_threshold", 0.82)),
            panel_threshold=float(params.get("panel_threshold", 0.82)),
            button_threshold=float(params.get("button_threshold", 0.85)),
            icon_timeout=float(params.get("icon_timeout", 6.0)),
            panel_timeout=float(params.get("panel_timeout", 5.0)),
            button_timeout=float(params.get("button_timeout", 5.0)),
            tap_delay=float(params.get("tap_delay", 1.5)),
            panel_delay=float(params.get("panel_delay", 2.0)),
            travel_delay=float(params.get("go_travel_delay", 3.0)),
            resource_delay=float(params.get("resource_panel_delay", 1.5)),
            help_delay=float(params.get("help_delay", 1.0)),
            timer_settle_delay=float(params.get("timer_settle_delay", 1.5)),
            metadata_key=str(params.get("metadata_key") or "next_ready_at"),
        )


class ConstructionTask:
    """Gestiona la mejora del HQ y registra el temporizador de construcción."""

    name = "construction"
    manual_daily_logging = True
    allow_repeat_after_completion = True

    def run(self, ctx: TaskContext, params: Dict[str, object]) -> None:  # type: ignore[override]
        """Ejecuta la rutina de construcción enfocada en el Cuartel General."""

        if not ctx.vision:
            ctx.console.log("[warning] VisionHelper no disponible; construction requiere detecciones")
            return
        if not getattr(ctx.farm, "construction_enabled", False):
            ctx.console.log(
                f"[info] Construcción deshabilitada para {ctx.farm.name}; omitiendo la tarea"
            )
            return
        config = ConstructionConfig.from_params(ctx, params)
        if not config.icon_templates:
            ctx.console.log("[warning] No hay templates configurados para construcción")
            return

        icon_available = self._is_construction_available(ctx, config)
        next_ready = self._get_next_ready_at(ctx, config)
        now = datetime.now()

        if not icon_available:
            if next_ready and next_ready > now:
                remaining = next_ready - now
                self._log_wait_message(ctx, remaining)
            else:
                ctx.console.log("[info] No se detectó el icono de construcción disponible")
            return

        if not self._should_start_construction(ctx, config):
            return
        if not self._focus_headquarter(ctx, config):
            ctx.console.log("[warning] No se pudo abrir el Cuartel General")
            return
        started_upgrade = self._start_headquarter_upgrade(ctx, config)
        if not started_upgrade:
            self._close_headquarter(ctx, config)
            remaining = self._refresh_timer_only(ctx, config)
            if remaining is not None and remaining.total_seconds() > 0:
                self._log_wait_message(ctx, remaining)
            return
        self._press_help_button(ctx, config)
        if not self._open_construction_menu(ctx, config):
            ctx.console.log(
                "[warning] No se pudo abrir el menú de construcción tras la mejora; se omite la lectura"
            )
            return
        if config.timer_settle_delay > 0:
            ctx.device.sleep(config.timer_settle_delay)
        if self._capture_timer_and_store(
            ctx,
            config,
            apply_help_reduction=started_upgrade,
        ):
            ctx.console.log("[info] Temporizador de construcción registrado")
        else:
            ctx.console.log("[warning] No se pudo registrar el temporizador de construcción")
        self._close_timer_overlay(ctx, config)

    def _refresh_timer_only(self, ctx: TaskContext, config: ConstructionConfig) -> timedelta | None:
        """Abre el menú radial para actualizar el temporizador y retorna el remanente."""

        if not self._open_construction_menu(ctx, config):
            return None
        if config.timer_settle_delay > 0:
            ctx.device.sleep(config.timer_settle_delay)
        remaining = self._read_timer_remaining(ctx, config)
        adjusted_remaining = None
        if remaining is not None:
            adjusted_remaining = self._store_timer_from_remaining(
                ctx,
                config,
                remaining,
                apply_help_reduction=False,
            )
        else:
            self._handle_timer_unreadable(ctx, config, reason="ocr-refresh-failed")
        self._close_timer_overlay(ctx, config)
        return adjusted_remaining

    def _is_construction_available(self, ctx: TaskContext, config: ConstructionConfig) -> bool:
        """Verifica si el icono de construcción está visible en el HUD."""

        if not ctx.vision:
            return False
        result = ctx.vision.find_any_template(
            config.icon_templates,
            threshold=config.icon_threshold,
        )
        return bool(result)

    def _has_william_ready_button(self, ctx: TaskContext, config: ConstructionConfig) -> bool:
        """Detecta el botón especial de William que indica que la construcción terminó."""

        if not ctx.vision or not config.william_button_templates:
            return False
        result = ctx.vision.find_any_template(
            config.william_button_templates,
            threshold=config.button_threshold,
        )
        return bool(result)

    def _focus_headquarter(self, ctx: TaskContext, config: ConstructionConfig) -> bool:
        """Cierra overlays y enfoca el Cuartel General tocando el centro de la ciudad."""

        ctx.device.tap(config.hq_tap, label="construction-hq")
        if config.panel_delay > 0:
            ctx.device.sleep(config.panel_delay)
        if self._dismiss_level_up_overlay(ctx, config):
            self._refocus_headquarter(ctx, config, "cerrar overlay inicial del HQ")
        if not self._tap_first_template(
            ctx,
            config.requirement_upgrade_templates or config.upgrade_button_templates,
            config.button_threshold,
            label="construction-hq-entry",
            delay=config.tap_delay,
            timeout=config.button_timeout,
        ):
            if self._dismiss_level_up_overlay(ctx, config):
                self._refocus_headquarter(ctx, config, "reintentar botón de mejora")
            if not self._tap_first_template(
                ctx,
                config.requirement_upgrade_templates or config.upgrade_button_templates,
                config.button_threshold,
                label="construction-hq-entry",
                delay=config.tap_delay,
                timeout=config.button_timeout,
            ):
                ctx.console.log(
                    "[warning] No se encontró el botón 'Mejorar' del HQ tras seleccionarlo"
                )
                return False
        if self._dismiss_level_up_overlay(ctx, config):
            self._refocus_headquarter(ctx, config, "confirmar panel del HQ")
        if config.panel_delay > 0:
            ctx.device.sleep(config.panel_delay)
        if not config.hq_panel_templates or not ctx.vision:
            return True
        result = ctx.vision.find_any_template(
            config.hq_panel_templates,
            threshold=config.panel_threshold,
        )
        if result:
            return True
        ctx.console.log(
            "[warning] No se confirmó el panel del HQ tras abrirlo; se continuará igualmente"
        )
        return True

    def _start_headquarter_upgrade(self, ctx: TaskContext, config: ConstructionConfig) -> bool:
        """Intenta mejorar el HQ o resuelve requisitos cuando el botón está bloqueado."""

        if self._tap_upgrade_button(ctx, config, label="construction-upgrade"):
            return True
        if not config.go_button_templates:
            ctx.console.log("[warning] El botón 'Ir' no está configurado")
            return False
        if not self._tap_first_template(
            ctx,
            config.go_button_templates,
            config.button_threshold,
            label="construction-go",
            delay=config.tap_delay,
            timeout=config.button_timeout,
        ):
            ctx.console.log("[warning] No se pudo abrir el requisito para mejorar el HQ")
            return False
        if config.travel_delay > 0:
            ctx.device.sleep(config.travel_delay)
        # Al desplazarse hasta el edificio requisito puede aparecer un overlay de nivel; cerrarlo antes de continuar
        self._dismiss_level_up_overlay(ctx, config)
        self._dismiss_level_up_overlay(ctx, config)
        return self._tap_requirement_upgrade(ctx, config)

    def _tap_upgrade_button(
        self,
        ctx: TaskContext,
        config: ConstructionConfig,
        *,
        label: str,
    ) -> bool:
        """Pulsa el botón azul 'Mejorar' cuando está disponible en el panel actual."""

        if self._tap_first_template(
            ctx,
            config.upgrade_button_templates,
            config.button_threshold,
            label=label,
            delay=config.tap_delay,
            timeout=config.button_timeout,
        ):
            if config.panel_delay > 0:
                ctx.device.sleep(config.panel_delay)
            self._handle_resource_shortage(ctx, config)
            self._dismiss_level_up_overlay(ctx, config)
            return True
        if config.upgrade_disabled_templates and ctx.vision:
            disabled = ctx.vision.find_any_template(
                config.upgrade_disabled_templates,
                threshold=config.button_threshold,
            )
            if disabled:
                ctx.console.log("[info] El botón 'Mejorar' está bloqueado por requisitos previos")
        return False

    def _tap_requirement_upgrade(self, ctx: TaskContext, config: ConstructionConfig) -> bool:
        """Mejora el edificio requerido tras pulsar 'Ir'."""
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            if self._tap_first_template(
                ctx,
                config.requirement_upgrade_templates or config.upgrade_button_templates,
                config.button_threshold,
                label="construction-requirement-upgrade",
                delay=config.tap_delay,
                timeout=config.button_timeout,
            ):
                if config.panel_delay > 0:
                    ctx.device.sleep(config.panel_delay)
                return self._tap_upgrade_button(
                    ctx, config, label="construction-upgrade-requirement"
                )

            ctx.console.log(
                "[info] Botón del edificio requisito no visible; verificando overlay 'Subir nivel'"
            )
            overlay_closed = self._dismiss_level_up_overlay(ctx, config)
            if overlay_closed:
                ctx.console.log(
                    "[info] Overlay 'Subir nivel' cerrado; reintentando buscar el botón"
                )
            if config.panel_delay > 0:
                ctx.device.sleep(min(2.0, config.panel_delay))

        ctx.console.log(
            "[warning] No se encontró el botón 'Mejorar' del edificio requisito tras varios intentos"
        )
        return False

    def _handle_resource_shortage(self, ctx: TaskContext, config: ConstructionConfig) -> None:
        """Resuelve el popup de recursos insuficientes usando rellenado automático."""

        if not ctx.vision or not config.resource_shortage_templates:
            return
        shortage = ctx.vision.find_any_template(
            config.resource_shortage_templates,
            threshold=config.panel_threshold,
        )
        if not shortage:
            return
        ctx.console.log("[info] Recursos insuficientes detectados; rellenando automáticamente")
        if config.resource_delay > 0:
            ctx.device.sleep(config.resource_delay)
        self._tap_first_template(
            ctx,
            config.resource_autofill_templates,
            config.button_threshold,
            label="construction-autofill",
            delay=config.tap_delay,
            timeout=config.button_timeout,
        )
        if config.resource_delay > 0:
            ctx.device.sleep(config.resource_delay)
        self._tap_first_template(
            ctx,
            config.resource_confirm_templates,
            config.button_threshold,
            label="construction-refill-confirm",
            delay=config.tap_delay,
            timeout=config.button_timeout,
        )
        if config.panel_delay > 0:
            ctx.device.sleep(config.panel_delay)

    def _dismiss_level_up_overlay(self, ctx: TaskContext, config: ConstructionConfig) -> bool:
        """Cierra el overlay de recompensa por subir de nivel si aparece tras una mejora."""

        if not ctx.vision:
            return False
        overlay_closed = dismiss_overlay_if_present(
            ctx,
            config.level_up_templates,
            None,
            timeout=config.panel_timeout,
            poll_interval=0.5,
            threshold=config.panel_threshold,
            delay=config.tap_delay,
            use_brightness=config.overlay_use_brightness,
            brightness_threshold=config.overlay_brightness_threshold,
            brightness_region=config.overlay_brightness_region,
            fallback_tap=config.overlay_dismiss_tap or (539, 0),
        )
        if overlay_closed:
            ctx.console.log(
                "[info] Overlay de recompensa cerrado; reanudando construcción"
            )
        return overlay_closed

    def _refocus_headquarter(self, ctx: TaskContext, config: ConstructionConfig, reason: str) -> None:
        """Toca nuevamente el centro de la base tras cerrar overlays que lo cubren."""

        ctx.console.log(f"[info] Reenfocando el HQ tras {reason}")
        ctx.device.tap(config.hq_tap, label="construction-hq-refocus")
        if config.panel_delay > 0:
            ctx.device.sleep(config.panel_delay)

    def _press_help_button(self, ctx: TaskContext, config: ConstructionConfig) -> None:
        """Pulsa el botón de ayuda de alianza tras iniciar la construcción."""

        if not config.help_button_templates:
            return
        if self._tap_first_template(
            ctx,
            config.help_button_templates,
            config.button_threshold,
            label="construction-help",
            delay=config.tap_delay,
            timeout=config.button_timeout,
        ):
            if config.help_delay > 0:
                ctx.device.sleep(config.help_delay)

    def _close_headquarter(self, ctx: TaskContext, config: ConstructionConfig) -> None:
        """Cierra el panel del HQ usando el botón back o un fallback fijo."""

        if tap_back_button(ctx, label="construction-exit"):
            if config.tap_delay > 0:
                ctx.device.sleep(config.tap_delay)
        elif config.overlay_dismiss_tap:
            ctx.device.tap(config.overlay_dismiss_tap, label="construction-exit-fallback")
            if config.tap_delay > 0:
                ctx.device.sleep(config.tap_delay)

    def _open_construction_menu(self, ctx: TaskContext, config: ConstructionConfig) -> bool:
        """Abre el menú radial de construcción para leer el temporizador."""

        opened = self._tap_first_template(
            ctx,
            config.icon_templates,
            config.icon_threshold,
            label="construction-menu",
            delay=config.tap_delay,
            timeout=config.icon_timeout,
        )
        if opened and ctx.vision and config.timer_panel_templates:
            ctx.vision.wait_for_any_template(
                config.timer_panel_templates,
                timeout=config.panel_timeout,
                poll_interval=0.5,
                threshold=config.panel_threshold,
                raise_on_timeout=False,
            )
        return opened

    def _close_timer_overlay(self, ctx: TaskContext, config: ConstructionConfig) -> None:
        """Cierra el overlay del temporizador usando el tap configurado."""

        if config.overlay_dismiss_tap:
            ctx.device.tap(config.overlay_dismiss_tap, label="construction-timer-close")

    def _should_start_construction(self, ctx: TaskContext, config: ConstructionConfig) -> bool:
        """Solo autoriza la construcción cuando aparezca el botón 'William'; en otro caso guarda el timer."""

        if not self._open_construction_menu(ctx, config):
            ctx.console.log("[warning] No se pudo abrir el menú de construcción para validar el estado")
            return False

        should_build = False
        if self._has_william_ready_button(ctx, config):
            ctx.console.log(
                "[info] Botón 'William construcción' detectado; se iniciará la mejora"
            )
            should_build = True
        else:
            if config.timer_settle_delay > 0:
                ctx.device.sleep(config.timer_settle_delay)

            remaining = self._read_timer_remaining(ctx, config)
            if remaining is None:
                ctx.console.log(
                    "[warning] No se pudo leer el temporizador y no hay botón 'William'; se omite la construcción"
                )
                self._handle_timer_unreadable(ctx, config, reason="ocr-menu-failed")
                should_build = False
            else:
                adjusted = self._store_timer_from_remaining(
                    ctx,
                    config,
                    remaining,
                    apply_help_reduction=False,
                )
                if adjusted is None:
                    ctx.console.log(
                        "[warning] No se pudo almacenar el temporizador de construcción"
                    )
                elif adjusted.total_seconds() <= 0:
                    ctx.console.log(
                        "[info] El temporizador llegó a cero pero no apareció 'William'; se esperará al botón"
                    )
                else:
                    self._log_wait_message(ctx, adjusted)
        self._close_timer_overlay(ctx, config)
        return should_build

    def _capture_timer_and_store(
        self,
        ctx: TaskContext,
        config: ConstructionConfig,
        *,
        apply_help_reduction: bool,
    ) -> bool:
        """Captura el temporizador del HQ y lo guarda en el tracker diario."""

        remaining = self._read_timer_remaining(ctx, config)
        if remaining is None:
            return False
        adjusted = self._store_timer_from_remaining(
            ctx,
            config,
            remaining,
            apply_help_reduction=apply_help_reduction,
        )
        return adjusted is not None

    def _read_timer_remaining(self, ctx: TaskContext, config: ConstructionConfig) -> timedelta | None:
        """Lee el temporizador del HQ y retorna el tiempo restante bruto."""

        if not ctx.vision:
            return None
        screenshot = ctx.vision.capture()
        if screenshot is None:
            return None
        regions: list[Region] = [config.timer_region]
        if config.timer_secondary_region and config.timer_secondary_region != config.timer_region:
            regions.append(config.timer_secondary_region)

        for idx, region in enumerate(regions):
            try:
                remaining = read_timer_from_region(screenshot, region)
            except pytesseract.TesseractNotFoundError:
                failure_path = self._record_timer_failure_debug(
                    ctx,
                    screenshot,
                    config,
                    reason="tesseract-missing",
                )
                if failure_path:
                    ctx.console.log(
                        f"[debug] Captura de construcción guardada en {failure_path}"
                    )
                ctx.console.log(
                    "[error] pytesseract no encontró 'tesseract.exe'; configura TESSERACT_CMD para leer timers"
                )
                return None

            if remaining:
                if idx > 0:
                    ctx.console.log(
                        "[info] Temporizador leído usando la región alternativa de construcción"
                    )
                return remaining

        failure_path = self._record_timer_failure_debug(
            ctx,
            screenshot,
            config,
            reason="empty-ocr",
        )
        if failure_path:
            ctx.console.log(f"[debug] Captura de construcción guardada en {failure_path}")
        return None

    def _store_timer_from_remaining(
        self,
        ctx: TaskContext,
        config: ConstructionConfig,
        remaining: timedelta,
        *,
        apply_help_reduction: bool,
    ) -> timedelta | None:
        """Aplica reducciones opcionales y guarda la hora estimada."""

        adjusted_remaining, reduction_minutes = self._apply_construction_reductions(
            ctx,
            remaining,
            include_help=apply_help_reduction,
        )
        ready_at = datetime.now() + adjusted_remaining
        self._store_ready_at(
            ctx,
            config,
            ready_at,
            reduction_minutes=reduction_minutes,
            raw_duration=remaining,
        )
        return adjusted_remaining

    def _handle_timer_unreadable(
        self,
        ctx: TaskContext,
        config: ConstructionConfig,
        *,
        reason: str,
    ) -> None:
        """Marca la tarea como bloqueada cuando el temporizador no puede leerse."""

        if not ctx.daily_tracker:
            return
        ctx.console.log(
            "[warning] Temporizador inválido; se limpiará el registro para reintentar en la siguiente pasada"
        )
        ctx.daily_tracker.set_metadata(
            ctx.farm.name,
            self.name,
            config.metadata_key,
            None,
        )
        ctx.daily_tracker.set_metadata(
            ctx.farm.name,
            self.name,
            f"{config.metadata_key}_blocked_reason",
            reason,
        )
        ctx.daily_tracker.set_flag(
            ctx.farm.name,
            self.name,
            f"{config.metadata_key}_blocked",
            True,
        )

    def _log_wait_message(self, ctx: TaskContext, remaining: timedelta) -> None:
        if remaining.total_seconds() <= 0:
            return
        ctx.console.log(
            f"[info] Construcción lista en {remaining}; se volverá a intentar después"
        )

    def _apply_construction_reductions(
        self,
        ctx: TaskContext,
        remaining: timedelta,
        *,
        include_help: bool,
    ) -> Tuple[timedelta, float]:
        """Aplica minutos gratis y ayuda de alianza al temporizador crudo."""

        if remaining.total_seconds() <= 0:
            return remaining, 0.0
        help_limit = getattr(ctx.farm, "alliance_help_limit", 0)
        help_minutes = getattr(ctx.farm, "alliance_help_minutes", 0.0)
        free_minutes = getattr(ctx.farm, "free_construction_minutes", 0.0)
        total_minutes = max(0.0, float(free_minutes))
        if include_help and help_limit and help_minutes:
            total_minutes += max(0.0, float(help_limit * help_minutes))
        if total_minutes <= 0:
            return remaining, 0.0
        applied_minutes = min(total_minutes, remaining.total_seconds() / 60.0)
        reduction = timedelta(minutes=applied_minutes)
        return remaining - reduction, applied_minutes

    def _store_ready_at(
        self,
        ctx: TaskContext,
        config: ConstructionConfig,
        ready_at: datetime,
        *,
        reduction_minutes: float = 0.0,
        raw_duration: timedelta | None = None,
    ) -> None:
        """Guarda la hora estimada del HQ listo en el tracker diario."""

        if not ctx.daily_tracker:
            return
        ctx.daily_tracker.set_count(ctx.farm.name, self.name, 0)
        ctx.daily_tracker.set_metadata(
            ctx.farm.name,
            self.name,
            config.metadata_key,
            ready_at.isoformat(),
        )
        ctx.daily_tracker.set_metadata(
            ctx.farm.name,
            self.name,
            f"{config.metadata_key}_reduction_minutes",
            round(reduction_minutes, 2) if reduction_minutes > 0 else None,
        )
        if raw_duration is not None:
            ctx.daily_tracker.set_metadata(
                ctx.farm.name,
                self.name,
                f"{config.metadata_key}_raw_seconds",
                int(raw_duration.total_seconds()),
            )

    def _get_next_ready_at(
        self,
        ctx: TaskContext,
        config: ConstructionConfig,
    ) -> datetime | None:
        if not ctx.daily_tracker:
            return None
        value = ctx.daily_tracker.get_metadata(
            ctx.farm.name,
            self.name,
            config.metadata_key,
        )
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

    def _record_timer_failure_debug(
        self,
        ctx: TaskContext,
        screenshot: np.ndarray,
        config: ConstructionConfig,
        *,
        reason: str,
    ) -> Path | None:
        """Guarda la pantalla completa y el recorte del temporizador cuando el OCR falla."""

        try:
            farm_name = ctx.farm.name if ctx.farm else "unknown"
            live_dir = Path("debug_reports") / "live" / farm_name
            live_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%H%M%S_%f")
            reason_slug = reason.replace(" ", "-")
            base_name = f"{timestamp}_{reason_slug}"
            full_path = live_dir / f"{base_name}.png"
            cv2.imwrite(str(full_path), screenshot)
            if ctx.vision:
                ctx.vision._record_debug_frame(screenshot.copy(), f"construction-ocr-{reason_slug}")
            (x1, y1), (x2, y2) = config.timer_region
            height, width = screenshot.shape[:2]
            x_start, x_end = sorted((max(0, min(x1, width)), max(0, min(x2, width))))
            y_start, y_end = sorted((max(0, min(y1, height)), max(0, min(y2, height))))
            if x_end > x_start and y_end > y_start:
                crop = screenshot[y_start:y_end, x_start:x_end]
                crop_path = live_dir / f"{base_name}_crop.png"
                cv2.imwrite(str(crop_path), crop)
                if ctx.vision:
                    ctx.vision._record_debug_frame(crop.copy(), f"construction-ocr-crop-{reason_slug}")
            return full_path
        except Exception:
            return None

    def _tap_first_template(
        self,
        ctx: TaskContext,
        template_paths: Sequence[Path],
        threshold: float,
        *,
        label: str,
        delay: float,
        timeout: float,
    ) -> bool:
        """Pulsa el primer template disponible respetando threshold y timeout configurados."""

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