"""OCR utilitario para extraer temporizadores del HUD del juego."""

from __future__ import annotations

import re
import os
from datetime import timedelta
from typing import List, Optional, Tuple

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
_TIMER_OCR_CONFIG = "--psm 7 -c tessedit_char_whitelist=0123456789d:"


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
    variants = _timer_binary_variants(gray)

    white_only = _extract_white_text(crop)
    if white_only is not None:
        variants.extend(_timer_binary_variants(white_only))

    green_suppressed = _suppress_green_background(crop)
    if green_suppressed is not None:
        variants.extend(_timer_binary_variants(green_suppressed))
    for variant in variants:
        text = pytesseract.image_to_string(variant, config=_TIMER_OCR_CONFIG)
        parsed = _parse_timer_string(text)
        if parsed:
            return parsed
    return None


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


def _timer_binary_variants(gray: np.ndarray) -> List[np.ndarray]:
    scale = 3.0 if max(gray.shape) < 400 else 2.0
    resized = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(6, 6))
    enhanced = clahe.apply(resized)
    normalized = cv2.normalize(enhanced, None, 0, 255, cv2.NORM_MINMAX)
    contrast = cv2.convertScaleAbs(normalized, alpha=1.6, beta=-15)
    blurred = cv2.GaussianBlur(contrast, (3, 3), 0)

    variants: List[np.ndarray] = []

    def _append_pair(image: np.ndarray, *, include_invert: bool = True) -> None:
        variants.append(image)
        if include_invert:
            variants.append(cv2.bitwise_not(image))

    _append_pair(contrast, include_invert=True)

    _, otsu = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    _append_pair(otsu)

    adaptive_gauss = cv2.adaptiveThreshold(
        blurred,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        21,
        2,
    )
    _append_pair(adaptive_gauss)

    adaptive_mean = cv2.adaptiveThreshold(
        blurred,
        255,
        cv2.ADAPTIVE_THRESH_MEAN_C,
        cv2.THRESH_BINARY,
        25,
        3,
    )
    _append_pair(adaptive_mean)

    equalized = cv2.equalizeHist(blurred)
    _, high_thresh = cv2.threshold(equalized, 200, 255, cv2.THRESH_BINARY)
    _append_pair(high_thresh)

    for thresh in (110, 140, 170):
        _, binary = cv2.threshold(blurred, thresh, 255, cv2.THRESH_BINARY)
        _append_pair(binary)

    kernel_small = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    kernel_top_hat = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    top_hat = cv2.morphologyEx(contrast, cv2.MORPH_TOPHAT, kernel_top_hat)
    _append_pair(top_hat, include_invert=False)

    processed: List[np.ndarray] = []
    for candidate in variants:
        processed.append(candidate)
        processed.append(cv2.morphologyEx(candidate, cv2.MORPH_CLOSE, kernel_small))

    unique: List[np.ndarray] = []
    seen = set()
    for candidate in processed:
        key = (candidate.shape, int(candidate.sum()))
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _extract_white_text(image: np.ndarray) -> Optional[np.ndarray]:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (0, 0, 180), (180, 90, 255))
    if cv2.countNonZero(mask) < 10:
        return None
    isolated = cv2.bitwise_and(image, image, mask=mask)
    gray = cv2.cvtColor(isolated, cv2.COLOR_BGR2GRAY)
    return gray if gray.size > 0 else None


def _suppress_green_background(image: np.ndarray) -> Optional[np.ndarray]:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    green_mask = cv2.inRange(hsv, (35, 40, 40), (95, 255, 255))
    if cv2.countNonZero(green_mask) == 0:
        return None
    inverted_mask = cv2.bitwise_not(green_mask)
    suppressed = cv2.bitwise_and(image, image, mask=inverted_mask)
    gray = cv2.cvtColor(suppressed, cv2.COLOR_BGR2GRAY)
    return gray if gray.size > 0 else None
