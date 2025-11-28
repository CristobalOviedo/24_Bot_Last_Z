"""Funciones auxiliares para detectar y tocar el botón 'Back' via visión."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from rich.console import Console

from .config import LayoutConfig
from .devices import DeviceController
from .vision import VisionHelper

DEFAULT_BACK_BUTTON_TEMPLATES: tuple[str, ...] = (
    "back_button_icon",
    "back_button",
)


def _resolve_template_paths(
    layout: LayoutConfig,
    template_names: Sequence[str],
) -> list[Path]:
    """Convierte nombres lógicos en rutas absolutas según el layout."""
    paths: list[Path] = []
    for name in template_names:
        try:
            layout_paths = layout.template_paths(name)
        except KeyError:
            continue
        paths.extend(layout_paths)
    return paths


def tap_back_button(
    *,
    device: DeviceController,
    layout: LayoutConfig,
    console: Console,
    vision: VisionHelper | None,
    label: str = "back-button",
    timeout: float = 4.0,
    threshold: float = 0.83,
    poll_interval: float = 0.5,
    template_names: Sequence[str] | None = None,
) -> bool:
    """Busca el botón 'back' y lo toca, registrando logs de apoyo."""
    if vision is None:
        console.log("[warning] VisionHelper no disponible para detectar el botón 'back'")
        return False

    names = tuple(template_names or DEFAULT_BACK_BUTTON_TEMPLATES)
    template_paths = _resolve_template_paths(layout, names)
    if not template_paths:
        console.log(
            f"[warning] No hay templates configurados para el botón 'back' (candidatos: {', '.join(names)})"
        )
        return False

    result = vision.wait_for_any_template(
        template_paths,
        timeout=timeout,
        threshold=threshold,
        poll_interval=poll_interval,
        raise_on_timeout=False,
    )
    if not result:
        console.log(
            f"[warning] El botón 'back' no se detectó dentro de {timeout:.1f}s (label: {label})"
        )
        return False

    coords, matched = result
    console.log(f"Botón 'back' detectado con template '{matched.name}' ({label})")
    device.tap(coords, label=label)
    return True
