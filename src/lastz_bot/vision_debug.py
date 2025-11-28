"""Utilidades para depurar coincidencias de templates grabando imagenes anotadas."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2


@dataclass
class TemplateMatchResult:
    """Resumen del puntaje encontrado y ruta de la captura marcada."""

    max_val: float
    match_image_path: str


def debug_match(
    screenshot_path: str,
    template_path: str,
    output_path: str,
) -> TemplateMatchResult:
    """Dibuja el rectangulo del template encontrado dentro de una captura.

    Args:
        screenshot_path (str): Ruta al PNG/JPG con la captura sin marcar.
        template_path (str): Ruta al template que se desea localizar.
        output_path (str): Ruta destino donde guardar la imagen anotada.

    Returns:
        TemplateMatchResult: Puntaje maximo y ubicacion del archivo generado.
    """
    screenshot = cv2.imread(screenshot_path, cv2.IMREAD_COLOR)
    template = cv2.imread(template_path, cv2.IMREAD_COLOR)
    result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    h, w = template.shape[:2]
    cv2.rectangle(
        screenshot,
        max_loc,
        (max_loc[0] + w, max_loc[1] + h),
        (0, 255, 0),
        2,
    )
    cv2.imwrite(output_path, screenshot)
    return TemplateMatchResult(max_val=max_val, match_image_path=output_path)


def save_screenshot(buffer: bytes, output_path: str) -> Path:
    """Guarda un buffer de bytes como imagen para posteriores inspecciones."""
    output = Path(output_path)
    output.write_bytes(buffer)
    return output