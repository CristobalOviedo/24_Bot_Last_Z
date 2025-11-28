"""Detección de estados de tropas usando templates configurables."""

from __future__ import annotations

import time
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from .config import Coord, LayoutConfig
from .tasks.base import TaskContext

RegionPixels = Tuple[int, int, int, int]
SlotRegionDebug = Tuple[str, str, np.ndarray]
ENABLE_TROOP_SNAPSHOT_DEBUG = False


class TroopActivity(str, Enum):
    """Enumera las actividades que puede reportar cada tropa."""
    IDLE = "idle"
    MARCHING = "marching"
    STATIONED = "stationed"
    RETURNING = "returning"
    GARRISON = "garrison"
    GATHERING = "gathering"
    RALLY = "rally"
    COMBAT = "combat"
    BUSY = "busy"
    UNKNOWN = "unknown"

    @classmethod
    def from_key(cls, value: str) -> "TroopActivity":
        normalized = value.strip().lower()
        alias_map = {
            "idle": cls.IDLE,
            "sleep": cls.IDLE,
            "sleeping": cls.IDLE,
            "march": cls.MARCHING,
            "marching": cls.MARCHING,
            "moving": cls.MARCHING,
            "stationed": cls.STATIONED,
            "holding": cls.STATIONED,
            "occupying": cls.STATIONED,
            "return": cls.RETURNING,
            "returning": cls.RETURNING,
            "home": cls.RETURNING,
            "garrison": cls.GARRISON,
            "reinforce": cls.GARRISON,
            "gather": cls.GATHERING,
            "gathering": cls.GATHERING,
            "rally": cls.RALLY,
            "combat": cls.COMBAT,
            "fight": cls.COMBAT,
            "busy": cls.BUSY,
        }
        return alias_map.get(normalized, cls.UNKNOWN)


@dataclass
class TroopSlotStatus:
    """Estado detectado para un slot, listo para tomar decisiones."""

    slot_id: str
    tap: Coord
    state: TroopActivity
    state_key: str
    confidence: Optional[float] = None
    label: Optional[str] = None
    source: str = "detector"
    reference_coord: Coord | None = None

    @property
    def is_idle(self) -> bool:
        """Conveniencia para saber si la tropa está libre."""
        return self.state == TroopActivity.IDLE


_TEMPLATE_CACHE: Dict[Path, np.ndarray] = {}
_WARNED_LAYOUTS: set[int] = set()

_STATE_DISPLAY = {
    TroopActivity.IDLE: "descansando",
    TroopActivity.MARCHING: "marchando",
    TroopActivity.STATIONED: "en posición",
    TroopActivity.RETURNING: "regresando",
    TroopActivity.GARRISON: "reforzando",
    TroopActivity.GATHERING: "recolectando",
    TroopActivity.RALLY: "en rally",
    TroopActivity.COMBAT: "combatiendo",
    TroopActivity.BUSY: "ocupada",
    TroopActivity.UNKNOWN: "desconocido",
}


def describe_activity(activity: TroopActivity) -> str:
    """Devuelve una etiqueta en español para mostrar en logs."""
    return _STATE_DISPLAY.get(activity, activity.value)


def layout_supports_troop_states(layout: LayoutConfig) -> bool:
    """Confirma si el layout tiene slots y templates necesarios."""
    cfg = getattr(layout, "troop_state", None)
    return bool(cfg and cfg.slots and cfg.state_templates)


def detect_troop_states(ctx: TaskContext) -> List[TroopSlotStatus]:
    """Corre las detecciones de estado y devuelve la lista completa de slots."""
    layout = ctx.layout
    cfg = getattr(layout, "troop_state", None)
    if not cfg or not cfg.slots or not cfg.state_templates:
        _warn_once(ctx, layout)
        return []
    if not ctx.vision:
        return []

    screenshot = ctx.vision.capture()
    if screenshot is None:
        return []
    captured_at = datetime.now()
    image_h, image_w = screenshot.shape[:2]

    state_templates = _resolve_state_templates(layout, cfg.state_templates, ctx)
    threshold = cfg.detection_threshold
    debug_regions_enabled = bool(getattr(cfg, "debug_regions_enabled", False))
    slot_regions: List[SlotRegionDebug] = []

    states: List[TroopSlotStatus] = []
    for slot_name, slot_cfg in cfg.slots.items():
        region = _region_to_pixels(image_h, image_w, slot_cfg.indicator_region)
        if region is None:
            continue
        y1, y2, x1, x2 = region
        roi = screenshot[y1:y2, x1:x2]
        if roi.size == 0:
            continue
        debug_roi = roi.copy() if debug_regions_enabled else None

        best_state: TroopActivity | None = None
        best_key = ""
        best_confidence = float("-inf")

        for state_key, template_paths in state_templates.items():
            for template_path in template_paths:
                template = _load_template(template_path, ctx)
                if template is None:
                    continue
                th, tw = template.shape[:2]
                if roi.shape[0] < th or roi.shape[1] < tw:
                    continue
                result = cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, _ = cv2.minMaxLoc(result)
                if max_val >= threshold and max_val > best_confidence:
                    best_confidence = max_val
                    best_state = TroopActivity.from_key(state_key)
                    best_key = state_key
        if best_state is None:
            current_state = TroopActivity.IDLE
            state_key = "idle"
            confidence = None
        else:
            current_state = best_state
            state_key = best_key
            confidence = best_confidence

        slot_status = TroopSlotStatus(
            slot_id=slot_name,
            tap=slot_cfg.tap,
            state=current_state,
            state_key=state_key,
            confidence=confidence,
            label=slot_cfg.label,
            reference_coord=slot_cfg.tap,
        )
        states.append(slot_status)

        if debug_regions_enabled and debug_roi is not None:
            slot_label = slot_cfg.label or slot_name or f"slot-{len(slot_regions) + 1}"
            slot_regions.append(
                (
                    slot_label,
                    slot_status.state.value,
                    debug_roi,
                )
            )
    _log_state_summary(ctx, states)
    need_debug_folder = (
        (debug_regions_enabled and slot_regions)
        or ENABLE_TROOP_SNAPSHOT_DEBUG
    )
    debug_folder: Path | None = None
    if need_debug_folder:
        debug_folder = _prepare_debug_folder(ctx, captured_at)
    if debug_regions_enabled:
        debug_folder = _persist_slot_regions(
            ctx,
            slot_regions,
            captured_at=captured_at,
            folder=debug_folder,
        )
    if ENABLE_TROOP_SNAPSHOT_DEBUG:
        _persist_troop_snapshot(
            ctx,
            screenshot,
            states,
            captured_at,
            folder=debug_folder,
        )
    return states


def idle_slots(ctx: TaskContext) -> List[TroopSlotStatus]:
    """Conveniencia para filtrar solo slots libres."""
    return [slot for slot in detect_troop_states(ctx) if slot.is_idle]


def wait_for_idle_slots(
    ctx: TaskContext,
    *,
    min_idle: int = 1,
    timeout: float = 0.0,
    poll: float = 0.5,
) -> List[TroopSlotStatus]:
    """Bloquea hasta que haya suficientes tropas libres o expire el timeout."""
    if not layout_supports_troop_states(ctx.layout):
        return []
    timeout = max(0.0, timeout)
    poll = max(0.1, poll)
    if timeout == 0:
        slots = idle_slots(ctx)
        return slots[:min_idle] if min_idle else slots
    last_seen: List[TroopSlotStatus] = []
    start = time.monotonic()
    while time.monotonic() - start <= timeout:
        slots = idle_slots(ctx)
        if len(slots) >= min_idle:
            return slots
        last_seen = slots
        time.sleep(poll)
    return last_seen


def wait_for_slot_state_change(
    ctx: TaskContext,
    slot_id: str,
    *,
    from_state: TroopActivity = TroopActivity.IDLE,
    timeout: float = 5.0,
    poll: float = 0.5,
) -> bool:
    """Espera a que un slot deje un estado dado (por ejemplo, tras tocar march)."""
    if not layout_supports_troop_states(ctx.layout):
        return False
    timeout = max(0.0, timeout)
    poll = max(0.1, poll)
    start = time.monotonic()
    while time.monotonic() - start <= timeout:
        slots = detect_troop_states(ctx)
        target = next((slot for slot in slots if slot.slot_id == slot_id), None)
        if target is None:
            time.sleep(poll)
            continue
        if target.state != from_state:
            return True
        time.sleep(poll)
    return False


def resolve_slot_for_tap(
    ctx: TaskContext,
    tap_point: Coord,
    *,
    slots: Sequence[TroopSlotStatus] | None = None,
    fallback: TroopSlotStatus | None = None,
) -> TroopSlotStatus | None:
    """Elige el slot con coordenada más cercana al tap efectuado."""
    candidates: Sequence[TroopSlotStatus]
    if slots is None:
        candidates = detect_troop_states(ctx)
    else:
        candidates = slots
    if not candidates:
        return fallback

    def _anchor(slot: TroopSlotStatus) -> Coord:
        return slot.reference_coord or slot.tap

    best = min(
        candidates,
        key=lambda slot: _manhattan_distance(_anchor(slot), tap_point),
    )
    if fallback and best.slot_id == fallback.slot_id:
        return fallback
    return best


def detect_departing_slot(
    ctx: TaskContext,
    *,
    expected: TroopSlotStatus | None,
    idle_snapshot: Sequence[TroopSlotStatus] | None = None,
    context_label: str | None = None,
) -> TroopSlotStatus | None:
    """Determina qué slot salió realmente de idle luego de enviar órdenes."""
    if not layout_supports_troop_states(ctx.layout):
        return expected
    states = detect_troop_states(ctx)
    if not states:
        return expected

    def _label(slot: TroopSlotStatus | None) -> str:
        if not slot:
            return "?"
        return (slot.label or slot.slot_id or "?").upper()

    if expected and expected.slot_id:
        match = next((slot for slot in states if slot.slot_id == expected.slot_id), None)
        if match and match.state != TroopActivity.IDLE:
            return match

    baseline_ids = {
        slot.slot_id: slot
        for slot in idle_snapshot or []
        if slot.slot_id
    }
    if not baseline_ids:
        return expected

    candidates = [
        slot
        for slot in states
        if slot.slot_id in baseline_ids and slot.state != TroopActivity.IDLE
    ]
    if not candidates:
        return expected

    chosen = candidates[0]
    if expected and chosen.slot_id != expected.slot_id:
        prefix = f"{context_label} " if context_label else ""
        ctx.console.log(
            f"{prefix}[info] La tropa {_label(chosen)} inició marcha en lugar de {_label(expected)}"
        )
    return chosen


def _resolve_state_templates(
    layout: LayoutConfig,
    mapping: Dict[str, Sequence[str]],
    ctx: TaskContext,
) -> Dict[str, List[Path]]:
    resolved: Dict[str, List[Path]] = {}
    for state_key, identifiers in mapping.items():
        paths: List[Path] = []
        for identifier in identifiers:
            paths.extend(_resolve_template(layout, identifier, ctx))
        if not paths:
            ctx.console.log(
                f"[warning] No hay templates válidos para el estado de tropa '{state_key}'"
            )
            continue
        resolved[state_key] = paths
    return resolved


def _resolve_template(layout: LayoutConfig, identifier: str, ctx: TaskContext) -> List[Path]:
    try:
        return layout.template_paths(identifier)
    except KeyError:
        path = Path(identifier)
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            ctx.console.log(f"[warning] Template '{identifier}' no existe en el layout ni como ruta absoluta")
            return []
        return [path]


def _region_to_pixels(
    height: int,
    width: int,
    region: Tuple[Tuple[float, float], Tuple[float, float]],
) -> RegionPixels | None:
    (y_start, y_end), (x_start, x_end) = region
    y1 = max(int(height * y_start), 0)
    y2 = min(int(height * y_end), height)
    x1 = max(int(width * x_start), 0)
    x2 = min(int(width * x_end), width)
    if y2 <= y1 or x2 <= x1:
        return None
    return y1, y2, x1, x2


def _load_template(path: Path, ctx: TaskContext) -> np.ndarray | None:
    template = _TEMPLATE_CACHE.get(path)
    if template is not None:
        return template
    if not path.exists():
        ctx.console.log(f"[warning] No se encontró el template de estado de tropa: {path}")
        return None
    template = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if template is None:
        ctx.console.log(f"[warning] No se pudo leer la imagen {path}")
        return None
    _TEMPLATE_CACHE[path] = template
    return template


def _warn_once(ctx: TaskContext, layout: LayoutConfig) -> None:
    layout_id = id(layout)
    if layout_id in _WARNED_LAYOUTS:
        return
    ctx.console.log(
        "[warning] El layout actual no tiene configurado 'troop_state'; se usará el método antiguo de ZZZ"
    )
    _WARNED_LAYOUTS.add(layout_id)


def _log_state_summary(ctx: TaskContext, states: Sequence[TroopSlotStatus]) -> None:
    if not states:
        return
    summary = ", ".join(
        f"{(slot.label or slot.slot_id).upper()}: {describe_activity(slot.state)}"
        for slot in states
    )
    ctx.console.log(f"Estados de tropas -> {summary}")


def _persist_troop_snapshot(
    ctx: TaskContext,
    screenshot: np.ndarray,
    states: Sequence[TroopSlotStatus],
    captured_at: datetime,
    *,
    folder: Path | None = None,
) -> Path | None:
    folder = folder or _prepare_debug_folder(ctx, captured_at)
    if folder is None:
        return None
    farm_name = getattr(ctx.farm, "name", "unknown") or "unknown"

    image_path = folder / "screenshot.png"
    if not cv2.imwrite(str(image_path), screenshot):
        ctx.console.log("[warning] No se pudo guardar la captura de tropas en disco")
        return folder

    metadata = {
        "farm": farm_name,
        "captured_at": captured_at.isoformat(),
        "slot_count": len(states),
        "states": [
            {
                "slot_id": slot.slot_id,
                "label": slot.label,
                "state": slot.state.value,
                "state_key": slot.state_key,
                "confidence": slot.confidence,
                "tap": slot.tap,
                "reference_coord": slot.reference_coord,
                "source": slot.source,
            }
            for slot in states
        ],
    }

    metadata_path = folder / "metadata.json"
    try:
        with metadata_path.open("w", encoding="utf-8") as fh:
            json.dump(metadata, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        ctx.console.log(
            f"[warning] No se pudo escribir metadata del snapshot de tropas: {exc}"
        )
        return

    ctx.console.log(f"[debug] Snapshot de tropas guardado en {folder}")
    return folder


def _persist_slot_regions(
    ctx: TaskContext,
    slot_regions: Sequence[SlotRegionDebug],
    *,
    captured_at: datetime,
    folder: Path | None = None,
) -> Path | None:
    if not slot_regions:
        return folder
    folder = folder or _prepare_debug_folder(ctx, captured_at)
    if folder is None:
        return None
    for slot_label, state_label, image in slot_regions:
        state_slug = _slugify(state_label) or "state"
        slot_slug = _slugify(slot_label) or "slot"
        filename = f"{state_slug}_{slot_slug}.png"
        image_path = folder / filename
        if not cv2.imwrite(str(image_path), image):
            ctx.console.log(
                f"[warning] No se pudo guardar el recorte de tropas '{filename}'"
            )
    return folder


def _prepare_debug_folder(ctx: TaskContext, captured_at: datetime) -> Path | None:
    farm_name = getattr(ctx.farm, "name", "unknown") or "unknown"
    farm_slug = _slugify(farm_name)
    base_dir = Path("debug_reports") / "troop_states" / farm_slug
    folder = base_dir / f"{captured_at:%Y%m%d_%H%M%S_%f}"
    attempt = 0
    while attempt < 3:
        try:
            folder.mkdir(parents=True, exist_ok=False)
            return folder
        except FileExistsError:
            attempt += 1
            folder = base_dir / f"{captured_at:%Y%m%d_%H%M%S_%f}_{attempt}"
    ctx.console.log(
        "[warning] No se pudo crear carpeta para debug de tropas tras múltiples intentos"
    )
    return None


def _slugify(value: str) -> str:
    cleaned = [
        ch.lower()
        if ch.isalnum()
        else "-"
        for ch in value.strip()
    ]
    slug = "".join(cleaned).strip("-")
    return slug or "farm"


def _manhattan_distance(a: Coord, b: Coord) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])
