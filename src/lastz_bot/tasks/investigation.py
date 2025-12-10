from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import cv2
import numpy as np
import pytesseract

from ..ocr import read_timer_from_region
from .base import TaskContext
from .utils import tap_back_button

Coord = Tuple[int, int]
Region = Tuple[Coord, Coord]

_TEMPLATE_CACHE: Dict[Path, np.ndarray] = {}


def _as_list(value: object) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("[") and text.endswith("]"):
            raw_items = text[1:-1].split(",")
            return [item.strip().strip("\"'") for item in raw_items if item.strip()]
        return [text] if text else []
    items: List[str] = []
    for entry in value:  # type: ignore[arg-type]
        text = str(entry).strip()
        if text:
            items.append(text)
    return items


def _coord_from_value(value: object, fallback: Coord | None = None) -> Coord | None:
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


def _region_from_value(value: object) -> Region | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    start = _coord_from_value(value[0])
    end = _coord_from_value(value[1])
    if not start or not end:
        return None
    return start, end


def _load_template(path: Path) -> np.ndarray | None:
    cached = _TEMPLATE_CACHE.get(path)
    if cached is not None:
        return cached
    if not path.exists():
        return None
    template = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if template is None:
        return None
    _TEMPLATE_CACHE[path] = template
    return template


@dataclass
class InvestigationNode:
    tap: Coord
    max_region: Region


@dataclass
class InvestigationNodeTemplate:
    template: Path
    max_region_offset: Region | None


@dataclass
class InvestigationConfig:
    icon_templates: Sequence[Path]
    panel_templates: Sequence[Path]
    free_button_templates: Sequence[Path]
    complete_button_templates: Sequence[Path]
    start_button_templates: Sequence[Path]
    alliance_button_templates: Sequence[Path]
    recommended_panel_templates: Sequence[Path]
    recommended_back_templates: Sequence[Path]
    node_invest_templates: Sequence[Path]
    help_button_templates: Sequence[Path]
    max_label_templates: Sequence[Path]
    resource_panel_templates: Sequence[Path]
    resource_target_templates: Sequence[Path]
    resource_use_button_templates: Sequence[Path]
    resource_batch_button_templates: Sequence[Path]
    badge_store_templates: Sequence[Path]
    overlay_templates: Sequence[Path]
    overlay_dismiss_button: str | None
    overlay_dismiss_tap: Coord | None
    overlay_use_brightness: bool
    overlay_dark_threshold: float
    timer_region: Region
    nodes: Sequence[InvestigationNode]
    node_templates: Sequence[InvestigationNodeTemplate]
    default_max_region_offset: Region | None
    icon_threshold: float
    panel_threshold: float
    button_threshold: float
    recommended_panel_threshold: float
    recommended_back_threshold: float
    max_label_threshold: float
    badge_store_threshold: float
    node_template_threshold: float
    icon_timeout: float
    panel_timeout: float
    button_timeout: float
    node_template_max_results: int
    tap_delay: float
    panel_delay: float
    back_delay: float
    resource_wait_timeout: float
    help_button_delay: float
    overlay_delay: float
    badge_shortage_cooldown_minutes: float
    metadata_key: str

    @staticmethod
    def from_params(ctx: TaskContext, params: Dict[str, Any]) -> "InvestigationConfig":
        layout = ctx.layout

        def resolve(names: Sequence[str]) -> List[Path]:
            paths: List[Path] = []
            for name in names:
                if not name:
                    continue
                try:
                    paths.extend(layout.template_paths(name))
                except KeyError:
                    ctx.console.log(f"[warning] Template '{name}' no está definido para investigation")
            return paths

        icon_templates = resolve(_as_list(params.get("icon_templates")) or ["research_icon"])
        panel_templates = resolve(_as_list(params.get("panel_templates")) or ["research_panel_header"])
        free_button_templates = resolve(_as_list(params.get("free_button_templates")))
        complete_button_templates = resolve(_as_list(params.get("complete_button_templates")) or ["research_complete_button"])
        start_button_templates = resolve(_as_list(params.get("start_button_templates")) or ["research_start_button"])
        alliance_button_templates = resolve(_as_list(params.get("alliance_button_templates")) or ["research_alliance_button"])
        recommended_panel_templates = resolve(_as_list(params.get("recommended_panel_templates")))
        recommended_back_templates = resolve(_as_list(params.get("recommended_back_templates")))
        node_invest_templates = resolve(_as_list(params.get("node_invest_templates")) or ["research_node_invest_button"])
        help_button_templates = resolve(_as_list(params.get("help_button_templates")) or ["research_help_button"])
        max_label_templates = resolve(_as_list(params.get("max_label_templates")) or ["research_max_label"])
        resource_panel_templates = resolve(_as_list(params.get("resource_panel_templates")) or ["research_resource_panel"])
        resource_target_templates = resolve(_as_list(params.get("resource_target_templates")) or ["research_power_crate"])
        resource_use_button_templates = resolve(_as_list(params.get("resource_use_button_templates")) or ["research_use_button"])
        resource_batch_button_templates = resolve(_as_list(params.get("resource_batch_button_templates")) or ["research_batch_button"])
        badge_store_templates = resolve(_as_list(params.get("badge_store_templates")) or ["research_badge_store"])
        overlay_templates = resolve(_as_list(params.get("overlay_templates")))
        overlay_dismiss_button_raw = params.get("overlay_dismiss_button", "close_popup")
        overlay_dismiss_button = str(overlay_dismiss_button_raw).strip() if overlay_dismiss_button_raw else None
        overlay_dismiss_tap = _coord_from_value(params.get("overlay_dismiss_tap"), (539, 0))
        timer_region = _region_from_value(params.get("timer_region")) or ((183, 363), (341, 380))
        default_max_region_offset = _region_from_value(params.get("default_max_region_offset"))

        nodes: List[InvestigationNode] = []
        for entry in params.get("nodes", []):
            tap = _coord_from_value(entry.get("tap")) if isinstance(entry, dict) else None
            max_region = _region_from_value(entry.get("max_region")) if isinstance(entry, dict) else None
            if tap and max_region:
                nodes.append(InvestigationNode(tap=tap, max_region=max_region))
        raw_node_templates = params.get("node_templates", [])
        if isinstance(raw_node_templates, str):
            node_entries: Sequence[Any] = _as_list(raw_node_templates)
        elif raw_node_templates is None:
            node_entries = []
        else:
            try:
                node_entries = list(raw_node_templates)
            except TypeError:
                node_entries = []
        node_templates: List[InvestigationNodeTemplate] = []
        for entry in node_entries:
            templates: Sequence[str] = []
            max_offset = default_max_region_offset
            if isinstance(entry, str):
                templates = _as_list(entry)
            elif isinstance(entry, dict):
                templates = _as_list(entry.get("template")) or _as_list(entry.get("templates"))
                offset = _region_from_value(entry.get("max_region_offset"))
                if offset:
                    max_offset = offset
            else:
                continue
            for name in templates:
                if not name:
                    continue
                for path in resolve([name]):
                    node_templates.append(
                        InvestigationNodeTemplate(
                            template=path,
                            max_region_offset=max_offset,
                        )
                    )
        if not nodes and not node_templates:
            ctx.console.log(
                "[warning] No se configuraron nodos ni plantillas para investigation; agrega al menos una opción"
            )

        return InvestigationConfig(
            icon_templates=icon_templates,
            panel_templates=panel_templates,
            free_button_templates=free_button_templates,
            complete_button_templates=complete_button_templates,
            start_button_templates=start_button_templates,
            alliance_button_templates=alliance_button_templates,
            recommended_panel_templates=recommended_panel_templates,
            recommended_back_templates=recommended_back_templates,
            node_invest_templates=node_invest_templates,
            help_button_templates=help_button_templates,
            max_label_templates=max_label_templates,
            resource_panel_templates=resource_panel_templates,
            resource_target_templates=resource_target_templates,
            resource_use_button_templates=resource_use_button_templates,
            resource_batch_button_templates=resource_batch_button_templates,
            badge_store_templates=badge_store_templates,
            overlay_templates=overlay_templates,
            overlay_dismiss_button=overlay_dismiss_button or None,
            overlay_dismiss_tap=overlay_dismiss_tap,
            overlay_use_brightness=bool(params.get("overlay_use_brightness", False)),
            overlay_dark_threshold=float(params.get("overlay_dark_threshold", 0.35)),
            timer_region=timer_region,
            nodes=nodes,
            node_templates=node_templates,
            default_max_region_offset=default_max_region_offset,
            icon_threshold=float(params.get("icon_threshold", 0.82)),
            panel_threshold=float(params.get("panel_threshold", 0.82)),
            button_threshold=float(params.get("button_threshold", 0.85)),
            recommended_panel_threshold=float(params.get("recommended_panel_threshold", 0.82)),
            recommended_back_threshold=float(params.get("recommended_back_threshold", 0.85)),
            max_label_threshold=float(params.get("max_label_threshold", 0.9)),
            badge_store_threshold=float(params.get("badge_store_threshold", 0.9)),
            node_template_threshold=float(params.get("node_template_threshold", 0.88)),
            icon_timeout=float(params.get("icon_timeout", 6.0)),
            panel_timeout=float(params.get("panel_timeout", 5.0)),
            button_timeout=float(params.get("button_timeout", 5.0)),
            node_template_max_results=int(params.get("node_template_max_results", 8)),
            tap_delay=float(params.get("tap_delay", 1.5)),
            panel_delay=float(params.get("panel_delay", 2.0)),
            back_delay=float(params.get("back_delay", 1.0)),
            resource_wait_timeout=float(params.get("resource_wait_timeout", 6.0)),
            help_button_delay=float(params.get("help_button_delay", 1.0)),
            overlay_delay=float(params.get("overlay_delay", 0.5)),
            badge_shortage_cooldown_minutes=float(
                params.get("badge_shortage_cooldown_minutes", 1440.0)
            ),
            metadata_key=str(params.get("metadata_key") or "next_ready_at"),
        )


class InvestigationTask:
    name = "investigation"
    manual_daily_logging = True

    def run(self, ctx: TaskContext, params: Dict[str, Any]) -> None:  # type: ignore[override]
        if not ctx.vision:
            ctx.console.log("[warning] VisionHelper no disponible; investigation requiere detecciones")
            return

        config = InvestigationConfig.from_params(ctx, params)
        if not config.icon_templates:
            ctx.console.log("[warning] No se configuró el ícono de investigación")
            return
        next_ready = self._get_next_ready_at(ctx, config)
        now = datetime.now()
        if next_ready and next_ready > now:
            remaining = next_ready - now
            ctx.console.log(
                f"[info] Investigación lista en {remaining}; se volverá a intentar después"
            )
            return

        panel_ready, saw_recommended, auto_recommended = self._ensure_panel_ready(ctx, config)
        if not panel_ready:
            ctx.console.log("[warning] No se pudo abrir el panel de investigación")
            return

        if auto_recommended:
            self._mark_investigation_completed(ctx, config, reason="panel recomendado se abrió automáticamente")
            self._close_panel(ctx, config)
            return

        handled = False
        started_new_research = False
        if self._has_template(ctx, config.complete_button_templates, config.button_threshold):
            handled = self._complete_and_restart(ctx, config)
            if not handled:
                self._close_panel(ctx, config)
                return
            self._close_panel(ctx, config)
            panel_ready, _, _ = self._ensure_panel_ready(ctx, config)
            if not panel_ready:
                ctx.console.log("[warning] No se pudo reabrir el panel tras iniciar la investigación")
                return
            started_new_research = True
        elif saw_recommended:
            handled = self._start_new_research(ctx, config)
            if not handled:
                self._close_panel(ctx, config)
                return
            self._close_panel(ctx, config)
            panel_ready, _, _ = self._ensure_panel_ready(ctx, config)
            if not panel_ready:
                ctx.console.log("[warning] No se pudo leer el panel tras iniciar la investigación")
                return
            started_new_research = True

        if self._capture_timer_and_store(
            ctx,
            config,
            apply_help_reduction=started_new_research,
        ):
            ctx.console.log("[info] Temporizador de investigación registrado")
        else:
            ctx.console.log("[warning] No se pudo leer el temporizador de investigación")
        self._close_panel(ctx, config)

    # --- panel helpers -------------------------------------------------
    def _ensure_panel_ready(
        self, ctx: TaskContext, config: InvestigationConfig
    ) -> Tuple[bool, bool, bool]:
        if not ctx.vision:
            return False, False, False

        attempts = 0
        tried_open = False
        max_attempts = 2
        saw_recommended = False
        auto_recommended = False

        while attempts <= max_attempts:
            if self._is_main_panel_visible(ctx, config):
                return True, saw_recommended, auto_recommended
            if self._is_recommended_panel_visible(ctx, config):
                saw_recommended = True
                if tried_open:
                    auto_recommended = True
                ctx.console.log("[info] Rama recomendada detectada; regresando antes de continuar")
                if not self._exit_recommended_panel(ctx, config):
                    return False, saw_recommended, auto_recommended
                tried_open = False
                attempts += 1
                continue
            if not tried_open:
                if not self._open_panel(ctx, config):
                    return False, saw_recommended, auto_recommended
                tried_open = True
                continue
            if not self._handle_unknown_panel_state(ctx, config):
                break
            tried_open = False
            attempts += 1
        return self._is_main_panel_visible(ctx, config), saw_recommended, auto_recommended

    def _is_main_panel_visible(self, ctx: TaskContext, config: InvestigationConfig) -> bool:
        if not ctx.vision or not config.panel_templates:
            return False
        result = ctx.vision.find_any_template(
            config.panel_templates,
            threshold=config.panel_threshold,
        )
        if result:
            return True
        if ctx.vision and config.alliance_button_templates:
            secondary = ctx.vision.find_any_template(
                config.alliance_button_templates,
                threshold=config.button_threshold,
            )
            return bool(secondary)
        return False

    def _is_recommended_panel_visible(self, ctx: TaskContext, config: InvestigationConfig) -> bool:
        if not ctx.vision or not config.recommended_panel_templates:
            return False
        result = ctx.vision.find_any_template(
            config.recommended_panel_templates,
            threshold=config.recommended_panel_threshold,
        )
        return bool(result)

    def _handle_unknown_panel_state(self, ctx: TaskContext, config: InvestigationConfig) -> bool:
        ctx.console.log(
            "[info] No se detectó el panel de investigación; se intentará volver con 'Back'"
        )
        return self._exit_recommended_panel(ctx, config)

    def _start_new_research(self, ctx: TaskContext, config: InvestigationConfig) -> bool:
        ctx.console.log("[info] No hay investigación en curso; se abrirá Alliance Recognition")
        if not self._tap_template_group(
            ctx,
            config.alliance_button_templates,
            threshold=config.button_threshold,
            timeout=config.button_timeout,
            label="research-alliance",
        ):
            ctx.console.log("[warning] No se pudo abrir Alliance Recognition para iniciar una nueva investigación")
            return False
        if config.panel_delay > 0:
            ctx.device.sleep(config.panel_delay)
        if not self._select_node_and_start(ctx, config):
            return False
        self._press_help_and_exit(ctx, config)
        return True

    def _open_panel(self, ctx: TaskContext, config: InvestigationConfig) -> bool:
        if not self._tap_template_group(
            ctx,
            config.icon_templates,
            threshold=config.icon_threshold,
            timeout=config.icon_timeout,
            label="research-icon",
        ):
            return False
        if config.panel_delay > 0:
            ctx.device.sleep(config.panel_delay)
        self._claim_free_completion(ctx, config)
        return True

    def _exit_recommended_panel(self, ctx: TaskContext, config: InvestigationConfig) -> bool:
        if not ctx.vision:
            return False
        if config.recommended_back_templates:
            if self._tap_template_group(
                ctx,
                config.recommended_back_templates,
                threshold=config.recommended_back_threshold,
                timeout=config.button_timeout,
                label="research-recommended-back",
            ):
                if config.back_delay > 0:
                    ctx.device.sleep(config.back_delay)
                return True
        self._press_back_button(
            ctx,
            config,
            label="research-recommended-back",
            fallback_message="[info] Botón 'Back' no detectado en panel recomendado; usando fallback",
        )
        return True


    def _close_panel(self, ctx: TaskContext, config: InvestigationConfig) -> None:
        if ctx.vision and not self._is_main_panel_visible(ctx, config):
            ctx.console.log("[debug] Panel de investigación ya está cerrado; se omite 'back'")
            return
        self._press_back_button(
            ctx,
            config,
            label="research-exit",
            fallback_message="[info] Botón 'Back' no disponible; usando coordenada fallback para salir",
        )

    def _press_back_button(
        self,
        ctx: TaskContext,
        config: InvestigationConfig,
        *,
        label: str,
        fallback_message: str | None = None,
        fallback_tap: Coord | None = None,
    ) -> None:
        if tap_back_button(ctx, label=label):
            if config.back_delay > 0:
                ctx.device.sleep(config.back_delay)
            return
        target = fallback_tap or config.overlay_dismiss_tap or (539, 0)
        message = fallback_message or (
            "[warning] Botón 'Back' no detectado; se usa coordenada fallback"
        )
        ctx.console.log(message)
        ctx.device.tap(target, label=f"{label}-fallback")
        if config.back_delay > 0:
            ctx.device.sleep(config.back_delay)

    # --- timer handling ------------------------------------------------
    def _capture_timer_and_store(
        self,
        ctx: TaskContext,
        config: InvestigationConfig,
        *,
        apply_help_reduction: bool,
    ) -> bool:
        screenshot = ctx.vision.capture()
        if screenshot is None:
            return False
        try:
            remaining = read_timer_from_region(screenshot, config.timer_region)
        except pytesseract.TesseractNotFoundError:
            failure_path = self._record_timer_failure_debug(
                ctx,
                screenshot,
                config,
                reason="tesseract-missing",
            )
            if failure_path:
                ctx.console.log(f"[debug] Captura de temporizador guardada en {failure_path}")
            ctx.console.log(
                "[error] pytesseract no encontró 'tesseract.exe'; instala Tesseract OCR o configura la variable TESSERACT_CMD con la ruta al ejecutable"
            )
            return False
        if not remaining:
            failure_path = self._record_timer_failure_debug(
                ctx,
                screenshot,
                config,
                reason="ocr-empty",
            )
            if failure_path:
                ctx.console.log(f"[debug] Capturas de temporizador guardadas en {failure_path.parent}")
            return False
        adjusted_remaining, reduction_minutes = self._apply_research_reductions(
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
        if reduction_minutes > 0:
            ctx.console.log(
                f"[info] Investigación lista a las {ready_at:%H:%M:%S} (reducción {reduction_minutes:.1f} min)"
            )
        else:
            ctx.console.log(f"[info] Investigación lista a las {ready_at:%H:%M:%S}")
        return True

    def _store_ready_at(
        self,
        ctx: TaskContext,
        config: InvestigationConfig,
        ready_at: datetime,
        *,
        reduction_minutes: float = 0.0,
        raw_duration: timedelta | None = None,
    ) -> None:
        if not ctx.daily_tracker:
            return
        ctx.daily_tracker.set_metadata(
            ctx.farm.name,
            self.name,
            config.metadata_key,
            ready_at.isoformat(),
        )
        if reduction_minutes > 0:
            ctx.daily_tracker.set_metadata(
                ctx.farm.name,
                self.name,
                f"{config.metadata_key}_reduction_minutes",
                round(reduction_minutes, 2),
            )
        else:
            ctx.daily_tracker.set_metadata(
                ctx.farm.name,
                self.name,
                f"{config.metadata_key}_reduction_minutes",
                None,
            )
        if raw_duration is not None:
            ctx.daily_tracker.set_metadata(
                ctx.farm.name,
                self.name,
                f"{config.metadata_key}_raw_seconds",
                int(raw_duration.total_seconds()),
            )

    def _clear_ready_metadata(self, ctx: TaskContext, config: InvestigationConfig) -> None:
        if not ctx.daily_tracker:
            return
        ctx.daily_tracker.set_metadata(
            ctx.farm.name,
            self.name,
            config.metadata_key,
            None,
        )
        ctx.daily_tracker.set_metadata(
            ctx.farm.name,
            self.name,
            f"{config.metadata_key}_reduction_minutes",
            None,
        )
        ctx.daily_tracker.set_metadata(
            ctx.farm.name,
            self.name,
            f"{config.metadata_key}_raw_seconds",
            None,
        )

    def _mark_investigation_completed(
        self,
        ctx: TaskContext,
        config: InvestigationConfig,
        *,
        reason: str,
    ) -> None:
        ctx.console.log(
            f"[info] Investigación marcada como completada para {ctx.farm.name}: {reason}"
        )
        if ctx.daily_tracker:
            ctx.daily_tracker.record_progress(ctx.farm.name, self.name)
        self._clear_ready_metadata(ctx, config)

    def _get_next_ready_at(
        self,
        ctx: TaskContext,
        config: InvestigationConfig,
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

    def _apply_research_reductions(
        self,
        ctx: TaskContext,
        remaining: timedelta,
        *,
        include_help: bool,
    ) -> tuple[timedelta, float]:
        if remaining.total_seconds() <= 0:
            return remaining, 0.0
        help_limit = getattr(ctx.farm, "alliance_help_limit", 0)
        help_minutes = getattr(ctx.farm, "alliance_help_minutes", 0.0)
        free_minutes = getattr(ctx.farm, "free_research_minutes", 0.0)
        total_minutes = max(0.0, float(free_minutes))
        if include_help and help_limit and help_minutes:
            total_minutes += max(0.0, float(help_limit * help_minutes))
        if total_minutes <= 0:
            return remaining, 0.0
        max_minutes = remaining.total_seconds() / 60.0
        applied_minutes = min(total_minutes, max_minutes)
        reduction = timedelta(minutes=applied_minutes)
        return remaining - reduction, applied_minutes

    # --- flow ----------------------------------------------------------
    def _complete_and_restart(
        self,
        ctx: TaskContext,
        config: InvestigationConfig,
    ) -> bool:
        if not self._tap_template_group(
            ctx,
            config.complete_button_templates,
            threshold=config.button_threshold,
            timeout=config.button_timeout,
            label="research-complete",
        ):
            ctx.console.log("[warning] Botón 'Completar' no disponible")
            return False
        if config.tap_delay > 0:
            ctx.device.sleep(config.tap_delay)
        if not self._tap_template_group(
            ctx,
            config.start_button_templates,
            threshold=config.button_threshold,
            timeout=config.button_timeout,
            label="research-start",
        ):
            ctx.console.log("[warning] No se encontró el botón 'Investigar' recomendado")
            return False
        if config.panel_delay > 0:
            ctx.device.sleep(config.panel_delay)
        self._press_back_button(
            ctx,
            config,
            label="research-panel-back",
            fallback_message="[info] Botón 'Back' no detectado tras iniciar investigación; usando fallback",
        )
        if not self._tap_template_group(
            ctx,
            config.alliance_button_templates,
            threshold=config.button_threshold,
            timeout=config.button_timeout,
            label="research-alliance",
        ):
            ctx.console.log("[warning] No se pudo abrir Alliance Recognition")
            return False
        if config.panel_delay > 0:
            ctx.device.sleep(config.panel_delay)
        if not self._select_node_and_start(ctx, config):
            return False
        self._press_help_and_exit(ctx, config)
        return True

    def _select_node_and_start(self, ctx: TaskContext, config: InvestigationConfig) -> bool:
        if not ctx.vision:
            return False
        screenshot = ctx.vision.capture()
        if screenshot is None:
            ctx.console.log("[warning] No se pudo capturar la pantalla de nodos")
            return False
        if config.node_templates:
            return self._select_node_with_templates(ctx, config, screenshot)
        if config.nodes:
            return self._select_node_with_coords(ctx, config, screenshot)
        ctx.console.log("[warning] No hay nodos configurados para investigación")
        return False

    def _select_node_with_coords(
        self,
        ctx: TaskContext,
        config: InvestigationConfig,
        screenshot: np.ndarray,
    ) -> bool:
        for node in config.nodes:
            if self._region_has_max(ctx, screenshot, config, node.max_region, "coords"):
                continue
            self._record_node_candidate_debug(ctx, screenshot, node.tap, "coords")
            ctx.device.tap(node.tap, label="research-node")
            if config.tap_delay > 0:
                ctx.device.sleep(config.tap_delay)
            if not self._tap_template_group(
                ctx,
                config.node_invest_templates,
                threshold=config.button_threshold,
                timeout=config.button_timeout,
                label="research-node-invest",
            ):
                continue
            if config.tap_delay > 0:
                ctx.device.sleep(config.tap_delay)
            if self._handle_resource_requirements(ctx, config):
                return True
            return False
        ctx.console.log("[warning] No se encontró un nodo disponible (todos MAX)")
        return False

    def _select_node_with_templates(
        self,
        ctx: TaskContext,
        config: InvestigationConfig,
        screenshot: np.ndarray,
    ) -> bool:
        matches = self._detect_node_templates(config, screenshot)
        if not matches:
            ctx.console.log("[warning] No se detectaron nodos de investigación en pantalla")
            return False
        for coords, template_cfg in matches:
            region = self._apply_region_offset(
                coords,
                template_cfg.max_region_offset or config.default_max_region_offset,
            )
            label = template_cfg.template.stem
            if self._region_has_max(ctx, screenshot, config, region, label):
                continue
            self._record_node_candidate_debug(ctx, screenshot, coords, label)
            ctx.device.tap(coords, label="research-node")
            if config.tap_delay > 0:
                ctx.device.sleep(config.tap_delay)
            if not self._tap_template_group(
                ctx,
                config.node_invest_templates,
                threshold=config.button_threshold,
                timeout=config.button_timeout,
                label="research-node-invest",
            ):
                continue
            if config.tap_delay > 0:
                ctx.device.sleep(config.tap_delay)
            if self._handle_resource_requirements(ctx, config):
                return True
            return False
        ctx.console.log("[warning] Todos los nodos detectados están en MAX")
        return False

    def _detect_node_templates(
        self,
        config: InvestigationConfig,
        screenshot: np.ndarray,
    ) -> List[Tuple[Coord, InvestigationNodeTemplate]]:
        matches: List[Tuple[Coord, InvestigationNodeTemplate]] = []
        if not config.node_templates or config.node_template_max_results <= 0:
            return matches
        for template_cfg in config.node_templates:
            if len(matches) >= config.node_template_max_results:
                break
            template = _load_template(template_cfg.template)
            if template is None:
                continue
            h, w = template.shape[:2]
            result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
            remaining = config.node_template_max_results - len(matches)
            centers = self._consume_template_matches(
                result,
                w,
                h,
                config.node_template_threshold,
                remaining,
            )
            if not centers:
                continue
            for center in centers:
                matches.append((center, template_cfg))
                if len(matches) >= config.node_template_max_results:
                    break
        matches.sort(key=lambda item: (item[0][1], item[0][0]))
        return matches

    @staticmethod
    def _apply_region_offset(center: Coord, offset: Region | None) -> Region | None:
        if not offset:
            return None
        (dx1, dy1), (dx2, dy2) = offset
        return (
            (center[0] + dx1, center[1] + dy1),
            (center[0] + dx2, center[1] + dy2),
        )

    @staticmethod
    def _consume_template_matches(
        result_map: np.ndarray,
        width: int,
        height: int,
        threshold: float,
        max_results: int,
    ) -> List[Coord]:
        if max_results <= 0:
            return []
        matches: List[Coord] = []
        working = result_map.copy()
        while len(matches) < max_results:
            _, max_val, _, max_loc = cv2.minMaxLoc(working)
            if max_val < threshold:
                break
            center = (int(max_loc[0] + width / 2), int(max_loc[1] + height / 2))
            matches.append(center)
            cv2.rectangle(
                working,
                max_loc,
                (max_loc[0] + width, max_loc[1] + height),
                -1,
                thickness=-1,
            )
        return matches

    def _record_node_candidate_debug(
        self,
        ctx: TaskContext,
        screenshot: np.ndarray,
        coords: Coord,
        label: str,
    ) -> None:
        if not ctx.vision or screenshot is None:
            return
        try:
            preview = screenshot.copy()
            cv2.circle(preview, coords, 40, (0, 255, 0), 3)
            ctx.vision._record_debug_frame(preview, f"research-node-{label}")
        except Exception:
            pass

    def _record_timer_failure_debug(
        self,
        ctx: TaskContext,
        screenshot: np.ndarray,
        config: InvestigationConfig,
        reason: str,
    ) -> Path | None:
        if screenshot is None:
            return None
        try:
            reason_slug = "".join(ch if ch.isalnum() else "-" for ch in reason.lower()).strip("-") or "unknown"
            if ctx.vision:
                ctx.vision._record_debug_frame(
                    screenshot.copy(),
                    f"research-timer-failure-{reason_slug}",
                )
            farm_name = ctx.farm.name if ctx.farm else "unknown"
            live_dir = Path("debug_reports") / "live" / farm_name
            live_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%H%M%S_%f")
            base_name = f"{timestamp}_timer_failure_{reason_slug}"
            full_path = live_dir / f"{base_name}.png"
            cv2.imwrite(str(full_path), screenshot)

            (x1, y1), (x2, y2) = config.timer_region
            height, width = screenshot.shape[:2]
            x_start, x_end = sorted((x1, x2))
            y_start, y_end = sorted((y1, y2))
            x_start = max(0, min(x_start, width))
            x_end = max(0, min(x_end, width))
            y_start = max(0, min(y_start, height))
            y_end = max(0, min(y_end, height))
            if x_end > x_start and y_end > y_start:
                crop = screenshot[y_start:y_end, x_start:x_end]
                if crop.size:
                    cv2.imwrite(str(live_dir / f"{base_name}_crop.png"), crop)
                    if ctx.vision:
                        ctx.vision._record_debug_frame(
                            crop.copy(),
                            f"research-timer-failure-crop-{reason_slug}",
                        )
            return full_path
        except Exception:
            return None

    def _record_max_region_debug(
        self,
        ctx: TaskContext,
        screenshot: np.ndarray,
        region: Region,
        label: str,
        best_score: float,
        best_template: str | None,
        matched: bool,
    ) -> None:
        if screenshot is None:
            return
        try:
            (x1, y1), (x2, y2) = region
            x_start, x_end = sorted((x1, x2))
            y_start, y_end = sorted((y1, y2))
            preview = screenshot.copy()
            color = (0, 200, 0) if matched else (0, 0, 255)
            cv2.rectangle(preview, (x_start, y_start), (x_end, y_end), color, 2)
            text = f"{label} {'hit' if matched else 'miss'} {best_score:.2f}"
            if best_template:
                text = f"{text} {best_template}"
            cv2.putText(
                preview,
                text,
                (max(0, x_start - 5), max(20, y_start - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                color,
                1,
                cv2.LINE_AA,
            )
            suffix = "hit" if matched else "miss"
            if ctx.vision:
                ctx.vision._record_debug_frame(preview, f"research-max-{suffix}-{label}")
                crop = screenshot[y_start:y_end, x_start:x_end]
                if crop.size:
                    ctx.vision._record_debug_frame(crop.copy(), f"research-max-{suffix}-crop-{label}")
            farm_name = ctx.farm.name if ctx.farm else "unknown"
            live_dir = Path("debug_reports") / "live" / farm_name
            live_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%H%M%S_%f")
            cv2.imwrite(
                str(live_dir / f"{timestamp}_{suffix}_{label}.png"),
                preview,
            )
            crop = screenshot[y_start:y_end, x_start:x_end]
            if crop.size:
                cv2.imwrite(
                    str(live_dir / f"{timestamp}_{suffix}_{label}_crop.png"),
                    crop,
                )
        except Exception:
            pass

    def _region_has_max(
        self,
        ctx: TaskContext,
        screenshot: np.ndarray,
        config: InvestigationConfig,
        region: Region | None,
        label: str,
    ) -> bool:
        if not config.max_label_templates or not region:
            return False
        (x1, y1), (x2, y2) = region
        x_start, x_end = sorted((x1, x2))
        y_start, y_end = sorted((y1, y2))
        height, width = screenshot.shape[:2]
        x_start = max(0, min(x_start, width))
        x_end = max(0, min(x_end, width))
        y_start = max(0, min(y_start, height))
        y_end = max(0, min(y_end, height))
        if x_end <= x_start or y_end <= y_start:
            return False
        crop = screenshot[y_start:y_end, x_start:x_end]
        if crop.size == 0:
            self._record_max_region_debug(
                ctx,
                screenshot,
                ((x_start, y_start), (x_end, y_end)),
                label,
                0.0,
                None,
                matched=False,
            )
            return False
        crop_gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        best_score = 0.0
        best_template: str | None = None
        for template_path in config.max_label_templates:
            template = _load_template(template_path)
            if template is None or crop.shape[0] < template.shape[0] or crop.shape[1] < template.shape[1]:
                continue
            template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
            result = cv2.matchTemplate(crop_gray, template_gray, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)
            if max_val > best_score:
                best_score = max_val
                best_template = template_path.stem
            if max_val >= config.max_label_threshold:
                self._record_max_region_debug(
                    ctx,
                    screenshot,
                    ((x_start, y_start), (x_end, y_end)),
                    label,
                    best_score,
                    best_template,
                    matched=True,
                )
                return True
        self._record_max_region_debug(
            ctx,
            screenshot,
            ((x_start, y_start), (x_end, y_end)),
            label,
            best_score,
            best_template,
            matched=False,
        )
        return False

    def _handle_resource_requirements(self, ctx: TaskContext, config: InvestigationConfig) -> bool:
        if not ctx.vision or not config.resource_panel_templates:
            return True
        attempt = 0
        while attempt < 2:
            attempt += 1
            if self._detect_badge_store(ctx, config):
                self._handle_badge_shortage(ctx, config)
                return False
            panel = ctx.vision.wait_for_any_template(
                config.resource_panel_templates,
                timeout=config.resource_wait_timeout,
                poll_interval=0.5,
                threshold=config.button_threshold,
                raise_on_timeout=False,
            )
            if not panel:
                if self._detect_badge_store(ctx, config):
                    self._handle_badge_shortage(ctx, config)
                    return False
                return True
            ctx.console.log("[info] Recursos insuficientes; se usarán cofres de electricidad")
            if not self._use_power_crates(ctx, config):
                return False
            if not self._tap_template_group(
                ctx,
                config.node_invest_templates,
                threshold=config.button_threshold,
                timeout=config.button_timeout,
                label="research-node-invest",
            ):
                return False
            if config.tap_delay > 0:
                ctx.device.sleep(config.tap_delay)
        ctx.console.log("[warning] No se pudieron completar los recursos tras múltiples intentos")
        return False

    def _use_power_crates(self, ctx: TaskContext, config: InvestigationConfig) -> bool:
        if not ctx.vision:
            return False
        crate = ctx.vision.find_any_template(
            config.resource_target_templates,
            threshold=config.button_threshold,
        )
        if not crate:
            ctx.console.log("[warning] No se encontró el cofre de electricidad azul")
            return False
        use_buttons = ctx.vision.find_all_templates(
            config.resource_use_button_templates,
            threshold=config.button_threshold,
            max_results=5,
        )
        button_coord = self._match_button_to_row(crate[0], use_buttons)
        if not button_coord:
            ctx.console.log("[warning] No se encontró el botón 'Usar' para el cofre")
            return False
        ctx.device.tap(button_coord, label="resource-use")
        if config.tap_delay > 0:
            ctx.device.sleep(config.tap_delay)
        self._dismiss_overlay_if_dim(ctx, config, force=True)
        batch = ctx.vision.wait_for_any_template(
            config.resource_batch_button_templates,
            timeout=config.button_timeout,
            poll_interval=0.5,
            threshold=config.button_threshold,
            raise_on_timeout=False,
        )
        if batch:
            ctx.device.tap(batch[0], label="resource-batch")
            if config.tap_delay > 0:
                ctx.device.sleep(config.tap_delay)
            self._dismiss_overlay_if_dim(ctx, config, force=True)
            return True
        ctx.console.log("[info] Botón 'Batch' no disponible; se usará un segundo cofre")
        use_buttons = ctx.vision.find_all_templates(
            config.resource_use_button_templates,
            threshold=config.button_threshold,
            max_results=5,
        )
        button_coord = self._match_button_to_row(crate[0], use_buttons)
        if not button_coord:
            ctx.console.log("[warning] No se pudo encontrar el botón 'Usar' para la segunda carga")
            return False
        ctx.device.tap(button_coord, label="resource-use-second")
        if config.tap_delay > 0:
            ctx.device.sleep(config.tap_delay)
        self._dismiss_overlay_if_dim(ctx, config, force=True)
        return True

    def _match_button_to_row(
        self,
        anchor: Coord,
        buttons: Sequence[Tuple[Coord, Path]],
    ) -> Coord | None:
        for coords, _ in buttons:
            if abs(coords[1] - anchor[1]) <= 60:
                return coords
        return buttons[0][0] if buttons else None

    def _dismiss_overlay(self, ctx: TaskContext, config: InvestigationConfig) -> None:
        if config.overlay_dismiss_button:
            try:
                target = ctx.layout.get(config.overlay_dismiss_button)
                ctx.device.tap(target, label="overlay-close")
                if config.overlay_delay > 0:
                    ctx.device.sleep(config.overlay_delay)
                return
            except KeyError:
                pass
        if config.overlay_dismiss_tap:
            ctx.device.tap(config.overlay_dismiss_tap, label="overlay-close")
            if config.overlay_delay > 0:
                ctx.device.sleep(config.overlay_delay)

    def _dismiss_overlay_if_dim(
        self,
        ctx: TaskContext,
        config: InvestigationConfig,
        *,
        force: bool = False,
    ) -> None:
        if not config.overlay_use_brightness or not ctx.vision:
            self._dismiss_overlay(ctx, config)
            return
        brightness = ctx.vision.average_brightness()
        if brightness is None:
            self._dismiss_overlay(ctx, config)
            return
        if brightness <= config.overlay_dark_threshold:
            ctx.console.log(
                f"[debug] Overlay detectado (brillo {brightness:.2f} <= {config.overlay_dark_threshold:.2f})"
            )
            self._dismiss_overlay(ctx, config)
            return
        if force:
            ctx.console.log(
                f"[debug] Overlay esperado pero brillo {brightness:.2f} > {config.overlay_dark_threshold:.2f}; se descarta igualmente"
            )
            self._dismiss_overlay(ctx, config)

    def _detect_badge_store(self, ctx: TaskContext, config: InvestigationConfig) -> bool:
        if not ctx.vision or not config.badge_store_templates:
            return False
        return bool(
            ctx.vision.find_any_template(
                config.badge_store_templates,
                threshold=config.badge_store_threshold,
            )
        )

    def _handle_badge_shortage(self, ctx: TaskContext, config: InvestigationConfig) -> None:
        ctx.console.log(
            "[warning] No hay badges disponibles para investigar; se cancela la acción"
        )
        self._dismiss_overlay(ctx, config)
        self._press_back_button(
            ctx,
            config,
            label="badge-shortage-exit-1",
            fallback_message="[info] Primer intento de salir tras falta de badges; usando fallback",
        )
        self._press_back_button(
            ctx,
            config,
            label="badge-shortage-exit-2",
            fallback_message="[info] Segundo intento de salir tras falta de badges; usando fallback",
        )
        self._schedule_badge_cooldown(ctx, config)

    def _schedule_badge_cooldown(self, ctx: TaskContext, config: InvestigationConfig) -> None:
        cooldown = max(0.0, float(config.badge_shortage_cooldown_minutes))
        if cooldown <= 0:
            return
        ready_at = datetime.now() + timedelta(minutes=cooldown)
        self._store_ready_at(ctx, config, ready_at)
        ctx.console.log(
            f"[info] Se reintentará la investigación cuando haya badges (aprox. {ready_at:%Y-%m-%d %H:%M})"
        )

    def _press_help_and_exit(self, ctx: TaskContext, config: InvestigationConfig) -> None:
        if self._tap_template_group(
            ctx,
            config.help_button_templates,
            threshold=config.button_threshold,
            timeout=config.button_timeout,
            label="research-help",
        ):
            if config.help_button_delay > 0:
                ctx.device.sleep(config.help_button_delay)
        self._press_back_button(
            ctx,
            config,
            label="research-help-back-1",
            fallback_message="[info] Botón 'Back' no detectado tras ayudar; usando fallback",
        )
        self._press_back_button(
            ctx,
            config,
            label="research-help-back-2",
            fallback_message="[info] Segundo 'Back' tras ayuda no detectado; usando fallback",
        )

    # --- template helpers ----------------------------------------------
    def _has_template(
        self,
        ctx: TaskContext,
        templates: Sequence[Path],
        threshold: float,
    ) -> bool:
        if not ctx.vision or not templates:
            return False
        return bool(
            ctx.vision.find_any_template(
                templates,
                threshold=threshold,
            )
        )

    def _tap_template_group(
        self,
        ctx: TaskContext,
        templates: Sequence[Path],
        *,
        threshold: float,
        timeout: float,
        label: str,
    ) -> bool:
        if not ctx.vision or not templates:
            return False
        result = ctx.vision.wait_for_any_template(
            templates,
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
        return True

    def _claim_free_completion(self, ctx: TaskContext, config: InvestigationConfig) -> None:
        """Reclama la finalización gratuita si aparece tras pulsar el ícono.

        Usa los templates configurados para detectar el botón gratis durante el mismo
        ciclo de apertura y lo pulsa antes de continuar para que luego aparezca el
        botón «Completar» normal.
        """
        if not ctx.vision or not config.free_button_templates:
            return
        result = ctx.vision.wait_for_any_template(
            config.free_button_templates,
            timeout=config.button_timeout,
            poll_interval=0.5,
            threshold=config.button_threshold,
            raise_on_timeout=False,
        )
        if not result:
            return
        coords, matched = result
        ctx.console.log(f"[info] Botón gratis detectado ('{matched.name}'); se reclamará antes de continuar")
        ctx.device.tap(coords, label="research-free")
        if config.tap_delay > 0:
            ctx.device.sleep(config.tap_delay)
