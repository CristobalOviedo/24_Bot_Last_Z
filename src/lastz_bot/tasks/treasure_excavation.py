"""Dispara la secuencia del tesoro cuando el icono aparece en pantalla."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from ..devices import resolve_button
from .base import TaskContext
from .utils import dismiss_overlay_if_present

Coord = Tuple[int, int]


def _as_list(value: object) -> List[str]:
    """Normaliza valores a una lista de nombres."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        entries: List[str] = []
        for item in value:
            text = str(item).strip()
            if text:
                entries.append(text)
        return entries
    text = str(value).strip()
    return [text] if text else []


def _paths_from_names(ctx: TaskContext, names: Sequence[str], missing: set[str]) -> List[Path]:
    """Resuelve nombres de template a rutas físicas, registrando faltantes."""
    paths: List[Path] = []
    for name in names:
        if not name:
            continue
        try:
            paths.extend(ctx.layout.template_paths(name))
        except KeyError:
            if name not in missing:
                ctx.console.log(f"[warning] Template '{name}' no está definido en el layout")
                missing.add(name)
    return paths


@dataclass
class TreasureExcavationConfig:
    """Modela la responsabilidad de `TreasureExcavationConfig` en la automatizacion del bot."""
    icon_paths: List[Path]
    dug_button_paths: List[Path]
    chest_paths: List[Path]
    overlay_paths: List[Path]
    home_paths: List[Path]
    icon_threshold: float
    dug_button_threshold: float
    chest_threshold: float
    overlay_threshold: float
    home_threshold: float
    dug_timeout: float
    chest_timeout: float
    overlay_timeout: float
    home_timeout: float
    poll_interval: float
    icon_tap_delay: float
    dug_button_delay: float
    chest_delay: float
    overlay_delay: float
    dismiss_tap: Coord
    back_delay: float
    return_home: bool
    overlay_use_brightness: bool
    overlay_brightness_threshold: float

    @staticmethod
    def from_params(ctx: TaskContext, params: Dict[str, Any], missing: set[str]) -> "TreasureExcavationConfig":
        """Ejecuta la logica de `from_params` dentro del flujo del bot.

        Args:
            ctx: Parametro usado por la rutina.
            params: Parametro usado por la rutina.
            missing: Parametro usado por la rutina.
        Returns:
            object: Resultado de la operacion.
        """
        icon_paths = _paths_from_names(
            ctx,
            _as_list(params.get("icon_templates") or ["treasure_icon"]),
            missing,
        )
        dug_button_paths = _paths_from_names(
            ctx,
            _as_list(params.get("dug_button_templates") or ["treasure_dug_button"]),
            missing,
        )
        chest_paths = _paths_from_names(
            ctx,
            _as_list(params.get("chest_templates") or ["treasure_chest"]),
            missing,
        )
        overlay_paths = _paths_from_names(
            ctx,
            _as_list(params.get("overlay_templates") or ["treasure_reward_overlay"]),
            missing,
        )
        home_paths = _paths_from_names(
            ctx,
            _as_list(params.get("home_button_templates") or ["sede_button"]),
            missing,
        )

        def as_coord(value: object, fallback: Coord) -> Coord:
            """Ejecuta la logica de `as_coord` dentro del flujo del bot.

            Args:
                value: Parametro usado por la rutina.
                fallback: Parametro usado por la rutina.
            Returns:
                object: Resultado de la operacion.
            """
            if isinstance(value, (list, tuple)) and len(value) == 2:
                try:
                    return int(value[0]), int(value[1])
                except (TypeError, ValueError):
                    return fallback
            return fallback

        return TreasureExcavationConfig(
            icon_paths=icon_paths,
            dug_button_paths=dug_button_paths,
            chest_paths=chest_paths,
            overlay_paths=overlay_paths,
            home_paths=home_paths,
            icon_threshold=float(params.get("icon_threshold", 0.82)),
            dug_button_threshold=float(params.get("dug_button_threshold", 0.82)),
            chest_threshold=float(params.get("chest_threshold", 0.82)),
            overlay_threshold=float(params.get("overlay_threshold", 0.84)),
            home_threshold=float(params.get("home_button_threshold", 0.7)),
            dug_timeout=float(params.get("dug_button_timeout", 6.0)),
            chest_timeout=float(params.get("chest_timeout", 6.0)),
            overlay_timeout=float(params.get("overlay_timeout", 4.0)),
            home_timeout=float(params.get("home_button_timeout", 4.0)),
            poll_interval=float(params.get("poll_interval", 0.5)),
            icon_tap_delay=float(params.get("icon_tap_delay", 2.0)),
            dug_button_delay=float(params.get("dug_button_delay", 3.0)),
            chest_delay=float(params.get("chest_delay", 0.5)),
            overlay_delay=float(params.get("overlay_delay", 0.5)),
            dismiss_tap=as_coord(params.get("overlay_dismiss_tap"), (539, 0)),
            back_delay=float(params.get("back_delay", 1.0)),
            return_home=bool(params.get("return_home", True)),
            overlay_use_brightness=bool(params.get("overlay_use_brightness", True)),
            overlay_brightness_threshold=float(params.get("overlay_brightness_threshold", 0.35)),
        )


class TreasureExcavationTask:
    """Detecta el icono de tesoro, lo abre y reclama el cofre."""

    name = "treasure_excavation"

    def __init__(self) -> None:
        self._missing_templates: set[str] = set()

    def run(self, ctx: TaskContext, params: Dict[str, Any]) -> None:  # type: ignore[override]
        """Ejecuta la logica de `run` dentro del flujo del bot.

        Args:
            ctx: Parametro usado por la rutina.
            params: Parametro usado por la rutina.
        Returns:
            object: Resultado de la operacion.
        """
        if not ctx.vision:
            ctx.console.log(
                "[warning] VisionHelper no disponible; treasure_excavation requiere detecciones"
            )
            return

        config = TreasureExcavationConfig.from_params(ctx, params, self._missing_templates)
        if not config.icon_paths:
            ctx.console.log("[warning] Falta template para el icono de tesoro")
            return
        if not config.dug_button_paths or not config.chest_paths:
            ctx.console.log("[warning] Faltan templates para 'tesoro excavado' o cofre")
            return

        icon_match = ctx.vision.find_any_template(
            config.icon_paths, threshold=config.icon_threshold
        )
        if not icon_match:
            fallback_threshold = max(0.68, config.icon_threshold - 0.1)
            icon_match = ctx.vision.wait_for_any_template(
                config.icon_paths,
                timeout=2.0,
                poll_interval=0.3,
                threshold=fallback_threshold,
                raise_on_timeout=False,
            )
        if not icon_match:
            ctx.console.log(
                f"[info] No se detectó icono de tesoro (umbral={config.icon_threshold:.2f}, fallback={fallback_threshold:.2f}); se omite treasure_excavation"
            )
            return

        ctx.console.log("Icono de tesoro detectado; abriendo evento")
        ctx.device.tap(icon_match[0], label="treasure-icon")
        if config.icon_tap_delay > 0:
            ctx.device.sleep(config.icon_tap_delay)

        dug_match = ctx.vision.wait_for_any_template(
            config.dug_button_paths,
            timeout=config.dug_timeout,
            poll_interval=config.poll_interval,
            threshold=config.dug_button_threshold,
            raise_on_timeout=False,
        )
        if not dug_match:
            ctx.console.log("[warning] No se encontró botón 'Tesoro excavado'")
            return

        ctx.console.log("Botón 'Tesoro excavado' detectado; ingresando")
        ctx.device.tap(dug_match[0], label="treasure-dug")
        if config.dug_button_delay > 0:
            ctx.device.sleep(config.dug_button_delay)

        chest_match = ctx.vision.wait_for_any_template(
            config.chest_paths,
            timeout=config.chest_timeout,
            poll_interval=config.poll_interval,
            threshold=config.chest_threshold,
            raise_on_timeout=False,
        )
        if not chest_match:
            ctx.console.log("[warning] No se detectó el cofre del tesoro")
            return

        ctx.console.log("Cofre detectado; reclamando recompensa")
        ctx.device.tap(chest_match[0], label="treasure-chest")
        if config.chest_delay > 0:
            ctx.device.sleep(config.chest_delay)

        dismissed = dismiss_overlay_if_present(
            ctx,
            config.overlay_paths,
            close_button=None,
            timeout=config.overlay_timeout,
            poll_interval=config.poll_interval,
            threshold=config.overlay_threshold,
            delay=config.overlay_delay,
            use_brightness=config.overlay_use_brightness,
            brightness_threshold=config.overlay_brightness_threshold,
            fallback_tap=config.dismiss_tap,
        )
        if not dismissed:
            ctx.device.tap(config.dismiss_tap, label="treasure-dismiss")
            if config.overlay_delay > 0:
                ctx.device.sleep(config.overlay_delay)

        self._return_home(ctx, config)

    def _return_home(self, ctx: TaskContext, config: TreasureExcavationConfig) -> None:
        if not config.return_home:
            return

        home_match = None
        if ctx.vision and config.home_paths:
            home_match = ctx.vision.wait_for_any_template(
                config.home_paths,
                timeout=config.home_timeout,
                poll_interval=config.poll_interval,
                threshold=config.home_threshold,
                raise_on_timeout=False,
            )
        if home_match:
            ctx.device.tap(home_match[0], label="treasure-home")
            if config.back_delay > 0:
                ctx.device.sleep(config.back_delay)
            return

        fallback = None
        try:
            fallback = resolve_button(ctx.layout, "back_button")
        except KeyError:
            fallback = None
        if fallback:
            ctx.device.tap(fallback, label="treasure-back")
            if config.back_delay > 0:
                ctx.device.sleep(config.back_delay)
