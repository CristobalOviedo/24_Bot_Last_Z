"""Ejecuta una donación de alianza manteniendo presionado el botón de donate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from .base import TaskContext
from .utils import tap_back_button


Coord = Tuple[int, int]


def _as_list(value: object) -> List[str]:
    """Normaliza valores a una lista de strings limpios."""

    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    items: List[str] = []
    for entry in value:  # type: ignore[arg-type]
        text = str(entry).strip()
        if text:
            items.append(text)
    return items


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


def _resolve_templates(ctx: TaskContext, names: Sequence[str]) -> List[Path]:
    """Resuelve nombres de template a rutas absolutas usando el layout activo."""

    resolved: List[Path] = []
    for name in names:
        if not name:
            continue
        try:
            resolved.extend(ctx.layout.template_paths(name))
        except KeyError:
            ctx.console.log(
                f"[warning] Template '{name}' no está definido para alliance_donation"
            )
    return resolved


@dataclass
class AllianceDonationConfig:
    """Parámetros y plantillas usadas para donar a la alianza."""

    icon_templates: Sequence[Path]
    tech_templates: Sequence[Path]
    recommended_templates: Sequence[Path]
    donate_templates: Sequence[Path]
    dismiss_tap: Coord
    back_presses: int
    tap_delay: float
    poll_interval: float
    icon_threshold: float
    tech_threshold: float
    recommended_threshold: float
    donate_threshold: float
    icon_timeout: float
    tech_timeout: float
    recommended_timeout: float
    donate_timeout: float
    hold_duration: float
    cooldown_hours: float
    daily_task_name: str

    @staticmethod
    def from_params(ctx: TaskContext, params: Dict[str, Any]) -> "AllianceDonationConfig":
        """Ejecuta la logica de `from_params` dentro del flujo del bot.

        Args:
            ctx: Parametro usado por la rutina.
            params: Parametro usado por la rutina.
        Returns:
            object: Resultado de la operacion.
        """
        icon_templates = _resolve_templates(
            ctx, _as_list(params.get("icon_templates")) or ["alliance_icon"]
        )
        tech_templates = _resolve_templates(
            ctx, _as_list(params.get("tech_templates")) or ["alliance_tech_button"]
        )
        recommended_templates = _resolve_templates(
            ctx,
            _as_list(params.get("recommended_templates"))
            or ["alliance_recommended_button"],
        )
        donate_templates = _resolve_templates(
            ctx, _as_list(params.get("donate_templates")) or ["alliance_donate_button"]
        )

        dismiss_tap = _coord_from_value(params.get("dismiss_tap"), (539, 0))

        return AllianceDonationConfig(
            icon_templates=icon_templates,
            tech_templates=tech_templates,
            recommended_templates=recommended_templates,
            donate_templates=donate_templates,
            dismiss_tap=dismiss_tap,
            back_presses=max(1, int(params.get("back_presses", 2))),
            tap_delay=float(params.get("tap_delay", 1.5)),
            poll_interval=float(params.get("poll_interval", 0.5)),
            icon_threshold=float(params.get("icon_threshold", 0.82)),
            tech_threshold=float(params.get("tech_threshold", 0.82)),
            recommended_threshold=float(params.get("recommended_threshold", 0.82)),
            donate_threshold=float(params.get("donate_threshold", 0.84)),
            icon_timeout=float(params.get("icon_timeout", 6.0)),
            tech_timeout=float(params.get("tech_timeout", 5.0)),
            recommended_timeout=float(params.get("recommended_timeout", 5.0)),
            donate_timeout=float(params.get("donate_timeout", 5.0)),
            hold_duration=float(params.get("hold_duration", 10.0)),
            cooldown_hours=float(params.get("cooldown_hours", 8.0)),
            daily_task_name=str(params.get("daily_task_name") or "alliance_donation"),
        )


class AllianceDonationTask:
    """Abre el panel de tecnología de alianza y realiza una donación prolongada."""

    name = "alliance_donation"
    manual_daily_logging = True

    def run(self, ctx: TaskContext, params: Dict[str, Any]) -> None:
        """Ejecuta la logica de `run` dentro del flujo del bot.

        Args:
            ctx: Parametro usado por la rutina.
            params: Parametro usado por la rutina.
        Returns:
            object: Resultado de la operacion.
        """
        config = AllianceDonationConfig.from_params(ctx, params)

        if not ctx.vision:
            ctx.console.log("[warning] Visión no disponible; no se puede detectar UI para donar")
            return

        tracker = ctx.daily_tracker
        farm_name = ctx.farm.name
        task_name = config.daily_task_name
        now = datetime.now()

        if tracker:
            next_ready_raw = tracker.get_metadata(farm_name, task_name, "next_ready_at")
            if isinstance(next_ready_raw, str):
                try:
                    next_ready = datetime.fromisoformat(next_ready_raw)
                except ValueError:
                    next_ready = None
                if next_ready and next_ready > now:
                    remaining = next_ready - now
                    hours_left = remaining.total_seconds() / 3600
                    ctx.console.log(
                        f"[info] Donación de alianza en cooldown; faltan {hours_left:.1f}h"
                    )
                    return

        icon_coord = self._locate_template(
            ctx,
            templates=config.icon_templates,
            threshold=config.icon_threshold,
            timeout=config.icon_timeout,
            poll_interval=config.poll_interval,
            label="icono de alianza",
        )
        if not icon_coord:
            return
        ctx.device.tap(icon_coord, label="alliance-icon")
        ctx.device.sleep(config.tap_delay)

        tech_coord = self._locate_template(
            ctx,
            templates=config.tech_templates,
            threshold=config.tech_threshold,
            timeout=config.tech_timeout,
            poll_interval=config.poll_interval,
            label="panel de tecnología",
        )
        if not tech_coord:
            return
        ctx.device.tap(tech_coord, label="alliance-tech")
        ctx.device.sleep(config.tap_delay)

        recommended_coord = self._locate_template(
            ctx,
            templates=config.recommended_templates,
            threshold=config.recommended_threshold,
            timeout=config.recommended_timeout,
            poll_interval=config.poll_interval,
            label="recomendado",
        )
        if not recommended_coord:
            return
        ctx.device.tap(recommended_coord, label="alliance-recommended")
        ctx.device.sleep(config.tap_delay)

        donate_coord = self._locate_template(
            ctx,
            templates=config.donate_templates,
            threshold=config.donate_threshold,
            timeout=config.donate_timeout,
            poll_interval=config.poll_interval,
            label="donar",
        )
        if not donate_coord:
            return

        hold_ms = max(1, int(config.hold_duration * 1000))
        ctx.console.log(f"Manteniendo donar por {config.hold_duration:.1f}s")
        ctx.device.swipe(donate_coord, donate_coord, duration_ms=hold_ms, label="donate-hold")
        ctx.device.sleep(config.tap_delay)

        if config.dismiss_tap:
            ctx.device.tap(config.dismiss_tap, label="donate-dismiss")
            ctx.device.sleep(config.tap_delay)

        for attempt in range(config.back_presses):
            if not tap_back_button(
                ctx,
                label=f"donation-back-{attempt + 1}",
                timeout=config.tech_timeout,
                threshold=config.tech_threshold,
                poll_interval=config.poll_interval,
            ):
                ctx.console.log("[warning] No se pudo navegar atrás tras donar")
                break
            ctx.device.sleep(config.tap_delay)

        if tracker:
            completed_at = datetime.now()
            tracker.record_progress(farm_name, task_name, now=completed_at)
            next_ready = completed_at + timedelta(hours=config.cooldown_hours)
            tracker.set_metadata(
                farm_name, task_name, "last_donation_at", completed_at.isoformat(), now=completed_at
            )
            tracker.set_metadata(
                farm_name, task_name, "next_ready_at", next_ready.isoformat(), now=completed_at
            )

    @staticmethod
    def _locate_template(
        ctx: TaskContext,
        *,
        templates: Sequence[Path],
        threshold: float,
        timeout: float,
        poll_interval: float,
        label: str,
    ) -> Coord | None:
        """Busca un template y devuelve su coordenada si aparece."""

        if not templates:
            ctx.console.log(f"[warning] No hay templates configurados para {label}")
            return None
        if not ctx.vision:
            return None
        result = ctx.vision.wait_for_any_template(
            templates,
            timeout=timeout,
            poll_interval=poll_interval,
            threshold=threshold,
            raise_on_timeout=False,
        )
        if not result:
            ctx.console.log(f"[warning] No se encontró {label} (timeout {timeout:.1f}s)")
            return None
        coords, matched_path = result
        ctx.console.log(f"Template '{matched_path.name}' detectado para {label}")
        return coords
