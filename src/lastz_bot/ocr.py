"""OCR utilitario para extraer temporizadores del HUD del juego."""

from __future__ import annotations

import re
import os
from datetime import timedelta
from typing import Optional, Tuple

import cv2
import numpy as np
import pytesseract

Coord = Tuple[int, int]
Region = Tuple[Coord, Coord]

_TESSERACT_CMD = os.environ.get("TESSERACT_CMD")
if _TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = _TESSERACT_CMD

_TIMER_PATTERN = re.compile(
    r"(?:(?P<days>\d+)\s*d\s*)?(?P<hours>\d{1,2}):(?P<minutes>\d{2}):(?P<seconds>\d{2})"
)


def read_timer_from_region(image: np.ndarray, region: Region) -> Optional[timedelta]:
    """Lee un temporizador ``HH:MM:SS`` (con días opcionales) desde una región."""
    if image is None:
        return None
    (x1, y1), (x2, y2) = region
    x_start, x_end = sorted((max(0, x1), max(0, x2)))
    y_start, y_end = sorted((max(0, y1), max(0, y2)))
    if x_end <= x_start or y_end <= y_start:
        return None
    crop = image[y_start:y_end, x_start:x_end]
    if crop.size == 0:
        return None
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    config = "--psm 7 -c tessedit_char_whitelist=0123456789d:"
    text = pytesseract.image_to_string(bw, config=config)
    return _parse_timer_string(text)


def _parse_timer_string(text: str) -> Optional[timedelta]:
    """Convierte cadenas detectadas por OCR en un ``timedelta`` normalizado."""
    if not text:
        return None
    normalized = text.strip().lower().replace("\n", " ")
    match = _TIMER_PATTERN.search(normalized)
    if not match:
        return None
    days = int(match.group("days") or 0)
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)
    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
