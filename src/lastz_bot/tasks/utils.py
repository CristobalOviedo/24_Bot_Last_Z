"""Utilidades comunes para tareas: overlays, templates y botón back."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence, Tuple, Union, List

from ..devices import resolve_button
from ..navigation import tap_back_button as tap_back_button_device
from .base import TaskContext


OverlayRegion = Tuple[Tuple[float, float], Tuple[float, float]]
TemplateArg = Optional[Union[str, Path, Sequence[Union[str, Path]]]]


def _normalize_template_sources(template_arg: TemplateArg) -> List[Union[str, Path]]:
    """Convierte strings, Paths o secuencias en una lista plana de fuentes."""
    if template_arg is None:
        return []
    if isinstance(template_arg, (str, Path)):
        return [template_arg]
    sources: List[Union[str, Path]] = []
    for item in template_arg:
        if not item:
            continue
        if isinstance(item, (str, Path)):
            sources.append(item)
        else:
            text = str(item).strip()
            if text:
                sources.append(text)
    return sources


def _collect_template_paths(
    ctx: TaskContext, sources: Sequence[Union[str, Path]]
) -> List[Path]:
    """Resuelve rutas físicas para cada fuente, registrando faltantes."""
    paths: List[Path] = []
    for source in sources:
        if isinstance(source, Path):
            if source.exists():
                paths.append(source)
            else:
                ctx.console.log(f"[warning] Template no encontrado: {source}")
            continue
        name = str(source).strip()
        if not name:
            continue
        try:
            paths.extend(ctx.layout.template_paths(name))
        except KeyError:
            ctx.console.log(
                f"[warning] Template '{name}' no está definido en el layout"
            )
    return paths


def wait_for_overlay(
    ctx: TaskContext,
    template_sources: TemplateArg = None,
    *,
    timeout: float = 5.0,
    poll_interval: float = 0.5,
    threshold: float = 0.85,
    use_brightness: bool = False,
    brightness_threshold: float = 0.35,
    brightness_region: OverlayRegion | None = ((0.1, 0.9), (0.1, 0.9)),
) -> Tuple[bool, Optional[Tuple[int, int]], Optional[str]]:
    """Espera a que aparezca un overlay detectándolo por template o brillo."""

    if not ctx.vision:
        return False, None, None

    sources = _normalize_template_sources(template_sources)
    template_paths = _collect_template_paths(ctx, sources)

    if template_paths:
        result = ctx.vision.wait_for_any_template(
            template_paths,
            timeout=timeout,
            poll_interval=poll_interval,
            threshold=threshold,
            raise_on_timeout=False,
        )
        if result:
            coords, matched_path = result
            ctx.console.log(
                f"Overlay detectado con template '{matched_path.name}'"
            )
            return True, coords, matched_path.name

    if use_brightness:
        brightness_ok = ctx.vision.wait_for_dim_screen(
            threshold=brightness_threshold,
            timeout=timeout,
            poll_interval=poll_interval,
            region=brightness_region,
        )
        if brightness_ok:
            ctx.console.log("Overlay detectado por brillo reducido")
            return True, None, None

    return False, None, None


def dismiss_overlay_if_present(
    ctx: TaskContext,
    template_sources: TemplateArg,
    close_button: Optional[str],
    *,
    timeout: float = 5.0,
    poll_interval: float = 0.5,
    threshold: float = 0.85,
    delay: float = 0.3,
    use_brightness: bool = False,
    brightness_threshold: float = 0.35,
    brightness_region: OverlayRegion | None = ((0.1, 0.9), (0.1, 0.9)),
    fallback_tap: Optional[Tuple[int, int]] = None,
) -> bool:
    """Espera y cierra un overlay usando templates o detección por brillo."""

    detected, coords_from_template, matched_name = wait_for_overlay(
        ctx,
        template_sources,
        timeout=timeout,
        poll_interval=poll_interval,
        threshold=threshold,
        use_brightness=use_brightness,
        brightness_threshold=brightness_threshold,
        brightness_region=brightness_region,
    )
    if not detected:
        return False

    target_coords = None
    if close_button:
        try:
            target_coords = resolve_button(ctx.layout, close_button)
        except KeyError:
            ctx.console.log(
                f"[warning] Botón '{close_button}' no definido; se usará el fallback"
            )
    if target_coords is None and fallback_tap is not None:
        target_coords = fallback_tap
    if target_coords is None:
        target_coords = coords_from_template
    if target_coords is None:
        target_coords = ctx.layout.buttons.get("back_button")
    if target_coords is None:
        ctx.console.log(
            "[warning] No hay coordenadas para cerrar overlay; se omite"
        )
        return False

    label = matched_name or close_button or "overlay"
    ctx.device.tap(target_coords, label=f"dismiss-{label}")
    if delay > 0:
        ctx.device.sleep(delay)
    return True


def tap_back_button(
    ctx: TaskContext,
    *,
    label: str = "back-button",
    timeout: float = 4.0,
    threshold: float = 0.83,
    poll_interval: float = 0.5,
    template_names: Sequence[str] | None = None,
) -> bool:
    """Delegado que toca el botón back detectándolo por template personalizado."""

    return tap_back_button_device(
        device=ctx.device,
        layout=ctx.layout,
        console=ctx.console,
        vision=ctx.vision,
        label=label,
        timeout=timeout,
        threshold=threshold,
        poll_interval=poll_interval,
        template_names=template_names,
    )
