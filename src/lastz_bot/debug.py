"""Soporte de depuración: bufferiza logs/capturas y persiste fallas."""

from __future__ import annotations

import re
import threading
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Deque, Dict, List, Optional, Tuple

import cv2
import numpy as np

TimestampedMessage = Tuple[datetime, str]
TimestampedImage = Tuple[datetime, np.ndarray, str]


class DebugReporter:
    """Agrupa logs y capturas recientes por granja para guardarlas ante errores."""

    def __init__(
        self,
        base_dir: str | Path = "debug_reports",
        *,
        max_logs: int = 400,
        max_screens: int = 6,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._max_logs = max_logs
        self._max_screens = max_screens
        self._log_buffers: Dict[str, Deque[TimestampedMessage]] = defaultdict(
            lambda: deque(maxlen=self._max_logs)
        )
        self._screen_buffers: Dict[str, Deque[TimestampedImage]] = defaultdict(
            lambda: deque(maxlen=self._max_screens)
        )
        self._lock = threading.Lock()

    def record_log(self, farm_name: str, message: str) -> None:
        """Almacena temporalmente un mensaje asociado a la granja."""
        if not farm_name or not message:
            return
        entry = (datetime.now(), message)
        with self._lock:
            self._log_buffers[farm_name].append(entry)

    def record_screenshot(
        self,
        farm_name: str,
        image: np.ndarray,
        label: str | None = None,
    ) -> None:
        """Guarda una captura en memoria con etiqueta y timestamp."""
        if not farm_name or image is None or self._max_screens <= 0:
            return
        clean_label = _slugify(label or "frame")
        with self._lock:
            self._screen_buffers[farm_name].append((datetime.now(), image.copy(), clean_label))

    def persist_failure(self, farm_name: str, reason: str) -> Optional[Path]:
        """Escribe en disco los últimos logs/capturas cuando ocurre un fallo."""
        timestamp = datetime.now()
        slug = _slugify(reason or "failure")
        folder_name = f"{timestamp:%Y%m%d_%H%M%S}_{farm_name}_{slug}"
        folder = self.base_dir / folder_name
        try:
            folder.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            # In the unlikely event of collision, append milliseconds
            folder = self.base_dir / f"{folder_name}_{int(timestamp.microsecond/1000)}"
            folder.mkdir(parents=True, exist_ok=True)

        with self._lock:
            logs = list(self._log_buffers.get(farm_name, []))
            screens = list(self._screen_buffers.get(farm_name, []))

        log_path = folder / "log.txt"
        with log_path.open("w", encoding="utf-8") as fh:
            fh.write(f"Farm: {farm_name}\n")
            fh.write(f"Timestamp: {timestamp.isoformat()}\n")
            fh.write(f"Reason: {reason}\n")
            fh.write("\n--- Recent log entries ---\n")
            for entry_time, message in logs:
                fh.write(f"[{entry_time:%H:%M:%S}] {message}\n")

        for index, (frame_time, image, label) in enumerate(screens, start=1):
            filename = f"{index:02d}_{frame_time:%H%M%S}_{label}.png"
            cv2.imwrite(str(folder / filename), image)

        return folder


def _slugify(text: str) -> str:
    """Normaliza etiquetas para que sirvan como nombres de carpeta/archivo."""
    lowered = text.lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return cleaned or "report"


_global_reporter: Optional[DebugReporter] = None


def get_debug_reporter() -> DebugReporter:
    """Entrega un singleton compartido para registrar eventos de depuración."""
    global _global_reporter
    if _global_reporter is None:
        _global_reporter = DebugReporter()
    return _global_reporter
