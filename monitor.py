"""Tablero en vivo para visualizar `state/daily_tasks.json` con Rich."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Mapping, MutableMapping, Sequence

from rich import box
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

ROOT = Path(__file__).resolve().parent
SRC_DIR = ROOT / "src"
if SRC_DIR.exists() and str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from lastz_bot.daily_tracker import GATHER_SLOT_TASK_PREFIX
from lastz_bot.config import load_config
from lastz_bot.pending import collect_pending_tasks as shared_collect_pending_tasks

DailyTasks = Dict[str, Dict[str, Dict[str, Any]]]


@dataclass
class FurySchedule:
    """Modela la responsabilidad de `FurySchedule` en la automatizacion del bot."""
    hours: list[int]
    window_hours: int
    skip_when_unavailable: bool


@dataclass
class Snapshot:
    """Estructura intermedia para representar el estado leído desde disco."""

    data: DailyTasks
    error: str | None = None
    read_at: datetime | None = None
    file_mtime: datetime | None = None


def parse_args() -> argparse.Namespace:
    """Ejecuta la logica de `parse_args` dentro del flujo del bot.

    Returns:
        object: Resultado de la operacion.
    """
    parser = argparse.ArgumentParser(
        description="Monitoriza daily_tasks.json renderizando una tabla en vivo",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--file",
        "-f",
        type=Path,
        default=Path("state/daily_tasks.json"),
        help="Ruta al archivo JSON que se desea observar",
    )
    parser.add_argument(
        "--interval",
        "-i",
        type=float,
        default=5.0,
        help="Segundos entre refrescos sucesivos",
    )
    parser.add_argument(
        "--reset-hour",
        type=int,
        default=None,
        help="Hora local (0-23) en que se reinician las tareas diarias; se detecta del YAML si no se especifica",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/farms.yaml"),
        help="Archivo de configuración YAML para leer reset_hour por defecto",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Renderiza una sola captura y termina (ideal para scrollear)",
    )
    parser.add_argument(
        "--fullscreen",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Usa la pantalla alternativa para evitar scroll; desactiva con --no-fullscreen",
    )
    return parser.parse_args()


def load_snapshot(path: Path) -> Snapshot:
    """Ejecuta la logica de `load_snapshot` dentro del flujo del bot.

    Args:
        path: Parametro usado por la rutina.
    Returns:
        object: Resultado de la operacion.
    """
    read_at = datetime.now()
    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return Snapshot(data={}, error=f"Archivo no encontrado: {path}", read_at=read_at)
    except json.JSONDecodeError as exc:
        return Snapshot(
            data={},
            error=f"JSON inválido en {path}: {exc}",
            read_at=read_at,
        )
    if not isinstance(raw, Mapping):
        return Snapshot(data={}, error="El JSON no tiene el formato esperado", read_at=read_at)
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        mtime = None
    return Snapshot(data=dict(raw), read_at=read_at, file_mtime=mtime)


def build_dashboard(
    snapshot: Snapshot,
    *,
    source_path: Path,
    reset_anchor: datetime,
    task_limits: Mapping[str, int],
    fury_schedule: FurySchedule | None,
) -> Layout:
    """Ejecuta la logica de `build_dashboard` dentro del flujo del bot.

    Args:
        snapshot: Parametro usado por la rutina.
        source_path: Parametro usado por la rutina.
        reset_anchor: Parametro usado por la rutina.
        task_limits: Parametro usado por la rutina.
        fury_schedule: Parametro usado por la rutina.
    Returns:
        object: Resultado de la operacion.
    """
    farm_summaries = collect_farm_summaries(snapshot, reset_anchor, task_limits)
    # Mostrar gathers futuros también para priorizar reenvíos apenas lleguen.
    pending_tasks = collect_pending_tasks(snapshot)
    pending_tasks.extend(
        collect_fury_pending_tasks(
            snapshot,
            reset_anchor=reset_anchor,
            task_limits=task_limits,
            schedule=fury_schedule,
        )
    )
    pending_tasks.sort(
        key=lambda item: (
            item.next_ready or datetime.max,
            item.updated or datetime.max,
            item.farm,
            item.name,
        )
    )
    next_reset = reset_anchor + timedelta(days=1)

    layout = Layout()
    layout.split_column(
        Layout(build_summary_panel(snapshot, farm_summaries, next_reset), size=6, minimum_size=6),
        Layout(name="body", ratio=1),
        Layout(build_footer(snapshot, source_path, next_reset), size=4, minimum_size=3),
    )
    layout["body"].split_row(
        Layout(build_farm_overview(farm_summaries), ratio=2),
        Layout(build_pending_table(pending_tasks), ratio=3),
    )
    return layout


def build_summary_panel(
    snapshot: Snapshot,
    farm_summaries: Sequence["FarmSummary"],
    next_reset: datetime,
) -> Panel:
    """Ejecuta la logica de `build_summary_panel` dentro del flujo del bot.

    Args:
        snapshot: Parametro usado por la rutina.
        farm_summaries: Parametro usado por la rutina.
        next_reset: Parametro usado por la rutina.
    Returns:
        object: Resultado de la operacion.
    """
    farms = len(farm_summaries)
    fury_done = sum(item.fury_done for item in farm_summaries)
    fury_goal = sum(item.fury_limit for item in farm_summaries if item.fury_limit)
    rally_done = sum(item.rally_done for item in farm_summaries)
    rally_goal = sum(item.rally_limit for item in farm_summaries if item.rally_limit)
    construction_active = sum(
        1 for item in farm_summaries if item.construction and item.construction.active
    )
    investigation_active = sum(
        1 for item in farm_summaries if item.investigation and item.investigation.active
    )
    gather_active = sum(item.gather_active for item in farm_summaries)
    gather_total = sum(item.gather_total for item in farm_summaries)
    grid = Table.grid(expand=True)
    grid.add_column(justify="left", no_wrap=True, ratio=1)
    grid.add_column(justify="center", no_wrap=True, ratio=1)
    grid.add_column(justify="right", no_wrap=True, ratio=1)
    grid.add_row(
        f"🌾 Granjas [bold]{farms}",
        f"⚔️ Fury {format_ratio(fury_done, fury_goal)}",
        f"📣 Rally {format_ratio(rally_done, rally_goal)}",
    )
    grid.add_row(
        f"🏗️ Construcción {format_ratio(construction_active, farms)}",
        f"🔬 Investigación {format_ratio(investigation_active, farms)}",
        f"⛏️ Gather {format_ratio(gather_active, gather_total)}",
    )
    return Panel(grid, title="Resumen", border_style="cyan")


@dataclass
class ContinuousTaskStatus:
    """Modela la responsabilidad de `ContinuousTaskStatus` en la automatizacion del bot."""
    active: bool
    next_ready: datetime | None


@dataclass
class FarmSummary:
    """Modela la responsabilidad de `FarmSummary` en la automatizacion del bot."""
    name: str
    fury_done: int
    fury_limit: int
    rally_done: int
    rally_limit: int
    construction: ContinuousTaskStatus | None
    investigation: ContinuousTaskStatus | None
    gather_active: int
    gather_total: int
    gather_next_ready: datetime | None
    last_update: datetime | None


def collect_farm_summaries(
    snapshot: Snapshot,
    reset_anchor: datetime,
    task_limits: Mapping[str, int],
) -> list[FarmSummary]:
    """Ejecuta la logica de `collect_farm_summaries` dentro del flujo del bot.

    Args:
        snapshot: Parametro usado por la rutina.
        reset_anchor: Parametro usado por la rutina.
        task_limits: Parametro usado por la rutina.
    Returns:
        object: Resultado de la operacion.
    """
    summaries: list[FarmSummary] = []
    fury_limit = int(task_limits.get("attack_furylord", 0) or 0)
    rally_limit = int(task_limits.get("rally_boomer", 0) or 0)
    for farm_name, tasks in snapshot.data.items():
        if not isinstance(tasks, MutableMapping):
            continue
        fury_done = read_counter_entry(tasks.get("attack_furylord"), reset_anchor, fury_limit)
        rally_done = read_counter_entry(tasks.get("rally_boomer"), reset_anchor, rally_limit)
        construction = read_continuous_status(tasks.get("construction"), reset_anchor)
        investigation = read_continuous_status(tasks.get("investigation"), reset_anchor)
        gather_active = 0
        gather_total = 0
        gather_next_ready: datetime | None = None
        last_update: datetime | None = None
        for task_name, entry in tasks.items():
            if not isinstance(entry, MutableMapping):
                continue
            timestamp = parse_datetime(entry.get("timestamp"))
            if timestamp and timestamp >= reset_anchor:
                if last_update is None or timestamp > last_update:
                    last_update = timestamp
            if not task_name.startswith(GATHER_SLOT_TASK_PREFIX):
                continue
            if not entry_is_active(entry, reset_anchor):
                continue
            gather_total += 1
            if entry.get("completed") is False:
                gather_active += 1
            metadata = entry.get("metadata") if isinstance(entry.get("metadata"), MutableMapping) else None
            next_ready = parse_datetime(metadata.get("next_ready_at")) if isinstance(metadata, Mapping) else None
            if next_ready and (gather_next_ready is None or next_ready < gather_next_ready):
                gather_next_ready = next_ready
        summaries.append(
            FarmSummary(
                name=farm_name,
                fury_done=min(fury_done, fury_limit) if fury_limit else fury_done,
                fury_limit=fury_limit,
                rally_done=min(rally_done, rally_limit) if rally_limit else rally_done,
                rally_limit=rally_limit,
                construction=construction,
                investigation=investigation,
                gather_active=gather_active,
                gather_total=gather_total,
                gather_next_ready=gather_next_ready,
                last_update=last_update,
            )
        )
    return sorted(summaries, key=lambda item: item.name)


def read_counter_entry(entry: Any, reset_anchor: datetime, limit: int) -> int:
    """Ejecuta la logica de `read_counter_entry` dentro del flujo del bot.

    Args:
        entry: Parametro usado por la rutina.
        reset_anchor: Parametro usado por la rutina.
        limit: Parametro usado por la rutina.
    Returns:
        object: Resultado de la operacion.
    """
    if not isinstance(entry, MutableMapping):
        return 0
    if not entry_is_active(entry, reset_anchor):
        return 0
    try:
        value = int(entry.get("count", 0))
    except (TypeError, ValueError):
        value = 0
    if value <= 0 and entry.get("completed") is True and limit:
        return limit
    return max(0, value)


def read_continuous_status(entry: Any, reset_anchor: datetime) -> ContinuousTaskStatus | None:
    """Ejecuta la logica de `read_continuous_status` dentro del flujo del bot.

    Args:
        entry: Parametro usado por la rutina.
        reset_anchor: Parametro usado por la rutina.
    Returns:
        object: Resultado de la operacion.
    """
    if not isinstance(entry, MutableMapping):
        return None
    if not entry_is_active(entry, reset_anchor):
        return None
    metadata = entry.get("metadata") if isinstance(entry.get("metadata"), MutableMapping) else None
    next_ready = parse_datetime(metadata.get("next_ready_at")) if isinstance(metadata, Mapping) else None
    active = entry.get("completed") is not True
    return ContinuousTaskStatus(active=active, next_ready=next_ready)


def format_ratio(value: int, total: int | None) -> str:
    """Ejecuta la logica de `format_ratio` dentro del flujo del bot.

    Args:
        value: Parametro usado por la rutina.
        total: Parametro usado por la rutina.
    Returns:
        object: Resultado de la operacion.
    """
    if not total:
        if value <= 0:
            return "[dim]—"
        return f"[bold]{value}"
    clipped = min(value, total)
    if clipped >= total:
        color = "green"
    elif clipped > 0:
        color = "yellow"
    else:
        color = "red"
    return f"[{color}]{clipped}/{total}"


def format_progress(value: int, limit: int) -> str:
    """Ejecuta la logica de `format_progress` dentro del flujo del bot.

    Args:
        value: Parametro usado por la rutina.
        limit: Parametro usado por la rutina.
    Returns:
        object: Resultado de la operacion.
    """
    if limit <= 0:
        if value <= 0:
            return "[dim]—"
        return f"[bold]{value}"
    clipped = min(value, limit)
    if clipped >= limit:
        color = "green"
    elif clipped > 0:
        color = "yellow"
    else:
        color = "red"
    return f"[{color}]{clipped}/{limit}"


def format_continuous_status(status: ContinuousTaskStatus | None) -> str:
    """Ejecuta la logica de `format_continuous_status` dentro del flujo del bot.

    Args:
        status: Parametro usado por la rutina.
    Returns:
        object: Resultado de la operacion.
    """
    if not status:
        return "[dim]—"
    if status.active:
        if status.next_ready:
            return f"[yellow]{format_time(status.next_ready)}"
        return "[yellow]En curso"
    return "[green]Listo"


def format_gather_status(summary: FarmSummary) -> str:
    """Ejecuta la logica de `format_gather_status` dentro del flujo del bot.

    Args:
        summary: Parametro usado por la rutina.
    Returns:
        object: Resultado de la operacion.
    """
    if summary.gather_total <= 0:
        return "[dim]—"
    if summary.gather_active == 0:
        color = "red"
    elif summary.gather_active < summary.gather_total:
        color = "yellow"
    else:
        color = "green"
    text = f"{summary.gather_active}/{summary.gather_total}"
    if summary.gather_next_ready:
        text += f" → {format_time(summary.gather_next_ready)}"
    return f"[{color}]{text}"


def build_farm_overview(farm_summaries: Sequence[FarmSummary]) -> Table:
    """Ejecuta la logica de `build_farm_overview` dentro del flujo del bot.

    Args:
        farm_summaries: Parametro usado por la rutina.
    Returns:
        object: Resultado de la operacion.
    """
    table = Table(
        title="Actividad por granja",
        expand=True,
        box=box.SIMPLE_HEAD,
    )
    table.add_column("Granja", style="bold magenta")
    table.add_column("Fury", justify="center", style="red")
    table.add_column("Rally", justify="center", style="cyan")
    table.add_column("Construcción", justify="center", style="yellow")
    table.add_column("Investig.", justify="center", style="bright_magenta")
    table.add_column("Gather", justify="center", style="bright_green")
    table.add_column("Última", justify="right", style="cyan")
    if not farm_summaries:
        table.add_row("—", "—", "—", "—", "—", "—", "—")
        return table
    for summary in farm_summaries:
        table.add_row(
            summary.name,
            format_progress(summary.fury_done, summary.fury_limit),
            format_progress(summary.rally_done, summary.rally_limit),
            format_continuous_status(summary.construction),
            format_continuous_status(summary.investigation),
            format_gather_status(summary),
            format_time(summary.last_update),
        )
    return table


@dataclass
class PendingTask:
    """Modela la responsabilidad de `PendingTask` en la automatizacion del bot."""
    farm: str
    name: str
    entry: Mapping[str, Any]
    next_ready: datetime | None
    updated: datetime | None
    label: str | None = None
    status_override: str | None = None
    detail_override: str | None = None


def collect_pending_tasks(snapshot: Snapshot) -> list[PendingTask]:
    """Ejecuta la logica de `collect_pending_tasks` dentro del flujo del bot.

    Args:
        snapshot: Parametro usado por la rutina.
    Returns:
        object: Resultado de la operacion.
    """
    entries = shared_collect_pending_tasks(snapshot.data, include_future_gather=True)
    converted: list[PendingTask] = []
    for entry in entries:
        converted.append(
            PendingTask(
                farm=entry.farm,
                name=entry.name,
                entry=entry.entry,
                next_ready=entry.next_ready,
                updated=entry.updated,
            )
        )
    return converted


def collect_fury_pending_tasks(
    snapshot: Snapshot,
    *,
    reset_anchor: datetime,
    task_limits: Mapping[str, int],
    schedule: FurySchedule | None,
) -> list[PendingTask]:
    """Ejecuta la logica de `collect_fury_pending_tasks` dentro del flujo del bot.

    Args:
        snapshot: Parametro usado por la rutina.
        reset_anchor: Parametro usado por la rutina.
        task_limits: Parametro usado por la rutina.
        schedule: Parametro usado por la rutina.
    Returns:
        object: Resultado de la operacion.
    """
    if not schedule or not schedule.hours:
        return []
    limit = int(task_limits.get("attack_furylord", 0) or 0)
    if limit <= 0:
        return []
    now = snapshot.read_at or datetime.now()
    due_time, within_window = compute_fury_due_time(now, schedule)
    if not due_time:
        return []
    reminders: list[PendingTask] = []
    hours_label = format_due_label(due_time, now)
    detail = "—"
    for farm_name, tasks in snapshot.data.items():
        if not isinstance(tasks, MutableMapping):
            continue
        entry = tasks.get("attack_furylord")
        count = read_counter_entry(entry, reset_anchor, limit)
        if count >= limit:
            continue
        remaining = max(0, limit - min(count, limit))
        updated = parse_datetime(entry.get("timestamp")) if isinstance(entry, Mapping) else None
        status = "[yellow]Fury"
        reminders.append(
            PendingTask(
                farm=farm_name,
                name="attack_furylord",
                entry=entry or {},
                next_ready=due_time,
                updated=updated,
                label="Fury",
                status_override=status,
                detail_override=detail,
            )
        )
    return reminders


def compute_fury_due_time(
    reference: datetime,
    schedule: FurySchedule,
) -> tuple[datetime | None, bool]:
    """Ejecuta la logica de `compute_fury_due_time` dentro del flujo del bot.

    Args:
        reference: Parametro usado por la rutina.
        schedule: Parametro usado por la rutina.
    Returns:
        object: Resultado de la operacion.
    """
    hours = sorted({hour % 24 for hour in schedule.hours})
    if not hours:
        return (None, False)
    window_delta = timedelta(hours=max(1, schedule.window_hours))
    for hour in hours:
        start_today = reference.replace(hour=hour, minute=0, second=0, microsecond=0)
        start_time = start_today if reference >= start_today else start_today - timedelta(days=1)
        if start_time <= reference < start_time + window_delta:
            return (start_time, True)
    next_start: datetime | None = None
    for hour in hours:
        candidate = reference.replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate <= reference:
            candidate += timedelta(days=1)
        if next_start is None or candidate < next_start:
            next_start = candidate
    return (next_start, False)


def format_due_label(due_time: datetime, reference: datetime) -> str:
    """Ejecuta la logica de `format_due_label` dentro del flujo del bot.

    Args:
        due_time: Parametro usado por la rutina.
        reference: Parametro usado por la rutina.
    Returns:
        object: Resultado de la operacion.
    """
    if due_time.date() == reference.date():
        return due_time.strftime("%H:%M")
    if due_time.date() == (reference + timedelta(days=1)).date():
        return f"mañ {due_time.strftime('%H:%M')}"
    return due_time.strftime("%d %b %H:%M")


def build_pending_table(pending_tasks: Sequence[PendingTask]) -> Table:
    """Ejecuta la logica de `build_pending_table` dentro del flujo del bot.

    Args:
        pending_tasks: Parametro usado por la rutina.
    Returns:
        object: Resultado de la operacion.
    """
    table = Table(
        title="Tareas pendientes",
        expand=True,
        box=box.SIMPLE,
        show_lines=False,
        padding=(0, 0),
    )
    table.add_column("Granja", style="bold magenta", no_wrap=True, max_width=8, overflow="ellipsis")
    table.add_column("Tarea", style="bold yellow", no_wrap=True, max_width=8, overflow="ellipsis")
    table.add_column("Estado", style="bold", no_wrap=True, max_width=8, overflow="ellipsis")
    table.add_column("Próximo", justify="center", style="bright_cyan", no_wrap=True, max_width=8, overflow="ellipsis")
    table.add_column("Detalle", style="dim", no_wrap=True, max_width=8, overflow="ellipsis")
    table.add_column("Actualizado", justify="right", style="cyan", no_wrap=True, max_width=10, overflow="ellipsis")

    if not pending_tasks:
        table.add_row("—", "—", "[green]Sin pendientes", "—", "", "")
        return table

    for task in pending_tasks:
        friendly_task = task.label or format_task_label(task.name, task.entry)
        if task.status_override is not None or task.detail_override is not None:
            status = task.status_override or "[yellow]⌛ Pendiente"
            detail = task.detail_override or "—"
        else:
            status, detail, _ = render_task_entry(task.name, task.entry)
        table.add_row(
            task.farm,
            friendly_task,
            status,
            format_time(task.next_ready),
            detail,
            format_time(task.updated),
        )
    return table


def format_task_label(task_name: str, entry: Mapping[str, Any]) -> str:
    """Ejecuta la logica de `format_task_label` dentro del flujo del bot.

    Args:
        task_name: Parametro usado por la rutina.
        entry: Parametro usado por la rutina.
    Returns:
        object: Resultado de la operacion.
    """
    if task_name.startswith(GATHER_SLOT_TASK_PREFIX):
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), MutableMapping) else {}
        label = metadata.get("troop_label") if isinstance(metadata, Mapping) else None
        if label:
            return f"Tropa {str(label).upper()} (gather)"
        suffix = task_name[len(GATHER_SLOT_TASK_PREFIX) :].replace("_", " ") or task_name
        return f"Tropa {suffix.upper()} (gather)"
    return task_name.replace("_", " ")


def format_duration(value: Any) -> str:
    """Ejecuta la logica de `format_duration` dentro del flujo del bot.

    Args:
        value: Parametro usado por la rutina.
    Returns:
        object: Resultado de la operacion.
    """
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return str(value)
    if seconds < 0:
        seconds = 0
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours:02}:{minutes:02}:{sec:02}"
    return f"{hours:02}:{minutes:02}:{sec:02}"


def render_task_entry(task_name: str, entry: Any) -> tuple[str, str, str]:
    """Ejecuta la logica de `render_task_entry` dentro del flujo del bot.

    Args:
        task_name: Parametro usado por la rutina.
        entry: Parametro usado por la rutina.
    Returns:
        object: Resultado de la operacion.
    """
    if not isinstance(entry, MutableMapping):
        return ("[red]✖ inválido[/]", "—", "—")
    completed = entry.get("completed")
    claimed = entry.get("claimed")
    count = entry.get("count")
    timestamp = entry.get("timestamp")
    metadata = entry.get("metadata") if isinstance(entry.get("metadata"), MutableMapping) else {}

    if completed is True:
        status = "[green]✅ Completo"
    elif completed is False:
        status = "[yellow]⌛ Pendiente"
    else:
        status = "[cyan]ℹ Seguimiento"

    if claimed:
        status += " 🎁"

    details: list[str] = []
    if task_name.startswith(GATHER_SLOT_TASK_PREFIX):
        label = metadata.get("troop_label")
        if label:
            details.append(f"tropa={str(label).upper()}")
        gather_seconds = metadata.get("gather_duration_seconds")
        if gather_seconds is not None:
            details.append(f"recolección={format_duration(gather_seconds)}")
        travel_seconds = metadata.get("travel_duration_seconds")
        if travel_seconds is not None:
            details.append(f"viaje={format_duration(travel_seconds)}")
    elif count is not None:
        details.append(f"conteo={count}")
    detail_text = ", ".join(details) if details else "—"
    updated_text = format_time(timestamp)
    return status, detail_text, updated_text


def build_footer(snapshot: Snapshot, source_path: Path, next_reset: datetime) -> Panel:
    """Ejecuta la logica de `build_footer` dentro del flujo del bot.

    Args:
        snapshot: Parametro usado por la rutina.
        source_path: Parametro usado por la rutina.
        next_reset: Parametro usado por la rutina.
    Returns:
        object: Resultado de la operacion.
    """
    text = Text()
    text.append(f"Fuente: {source_path}", style="bold")
    if snapshot.file_mtime:
        text.append(f" | Última modificación: {format_dt(snapshot.file_mtime)}", style="dim")
    text.append(
        f"\n🔁 Reset diario: {next_reset.strftime('%d %b %H:%M')} | 🕒 Lectura: {format_dt(snapshot.read_at)}",
        style="dim",
    )
    if snapshot.error:
        text.append(f"\n⚠ {snapshot.error}", style="bold red")
    return Panel(text, border_style="dim")


def format_time(value: Any) -> str:
    """Ejecuta la logica de `format_time` dentro del flujo del bot.

    Args:
        value: Parametro usado por la rutina.
    Returns:
        object: Resultado de la operacion.
    """
    if not value:
        return "—"
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return str(value)
    try:
        return dt.strftime("%H:%M:%S")
    except ValueError:
        return str(value)


def parse_datetime(value: Any) -> datetime | None:
    """Ejecuta la logica de `parse_datetime` dentro del flujo del bot.

    Args:
        value: Parametro usado por la rutina.
    Returns:
        object: Resultado de la operacion.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def format_dt(value: datetime | None) -> str:
    """Ejecuta la logica de `format_dt` dentro del flujo del bot.

    Args:
        value: Parametro usado por la rutina.
    Returns:
        object: Resultado de la operacion.
    """
    if not value:
        return "—"
    return value.strftime("%Y-%m-%d %H:%M:%S")


def compute_reset_anchor(reference: datetime | None, reset_hour: int) -> datetime:
    """Ejecuta la logica de `compute_reset_anchor` dentro del flujo del bot.

    Args:
        reference: Parametro usado por la rutina.
        reset_hour: Parametro usado por la rutina.
    Returns:
        object: Resultado de la operacion.
    """
    reset_hour = max(0, min(23, reset_hour))
    now = reference or datetime.now()
    anchor = now.replace(hour=reset_hour, minute=0, second=0, microsecond=0)
    if now < anchor:
        anchor -= timedelta(days=1)
    return anchor


def entry_is_active(entry: Mapping[str, Any], reset_anchor: datetime) -> bool:
    """Ejecuta la logica de `entry_is_active` dentro del flujo del bot.

    Args:
        entry: Parametro usado por la rutina.
        reset_anchor: Parametro usado por la rutina.
    Returns:
        object: Resultado de la operacion.
    """
    timestamp = parse_datetime(entry.get("timestamp"))
    if not timestamp:
        return False
    return timestamp >= reset_anchor


def load_tracking_settings(
    cli_reset_hour: int | None,
    config_path: Path | None,
) -> tuple[int, Dict[str, int], FurySchedule | None]:
    """Ejecuta la logica de `load_tracking_settings` dentro del flujo del bot.

    Args:
        cli_reset_hour: Parametro usado por la rutina.
        config_path: Parametro usado por la rutina.
    Returns:
        object: Resultado de la operacion.
    """
    reset_hour = 23
    task_limits: Dict[str, int] = {}
    fury_schedule: FurySchedule | None = None
    cfg = None
    if config_path:
        try:
            cfg = load_config(config_path)
            if cfg.daily_tracking:
                reset_hour = max(0, min(23, cfg.daily_tracking.reset_hour_local))
                task_limits = dict(cfg.daily_tracking.tracked_tasks)
        except Exception:
            cfg = None
    if cli_reset_hour is not None:
        reset_hour = max(0, min(23, cli_reset_hour))
    if cfg:
        fury_schedule = extract_fury_schedule(cfg)
    return reset_hour, task_limits, fury_schedule


def extract_fury_schedule(config: Any) -> FurySchedule | None:
    """Ejecuta la logica de `extract_fury_schedule` dentro del flujo del bot.

    Args:
        config: Parametro usado por la rutina.
    Returns:
        object: Resultado de la operacion.
    """
    try:
        defaults = config.task_defaults_for("attack_furylord")
    except Exception:
        return None
    hours_raw = (
        defaults.get("availability_hours")
        or defaults.get("available_hours")
        or []
    )
    if isinstance(hours_raw, (int, float)):
        hours_iter = [hours_raw]
    elif isinstance(hours_raw, (list, tuple, set)):
        hours_iter = list(hours_raw)
    else:
        hours_iter = []
    hours: list[int] = []
    for value in hours_iter:
        try:
            hours.append(int(value) % 24)
        except (TypeError, ValueError):
            continue
    hours = sorted({hour for hour in hours})
    if not hours:
        return None
    window_value = (
        defaults.get("availability_window_hours")
        or defaults.get("available_window_hours")
        or 3
    )
    try:
        window_hours = max(1, int(window_value))
    except (TypeError, ValueError):
        window_hours = 3
    skip = bool(defaults.get("skip_when_unavailable", False))
    return FurySchedule(hours=hours, window_hours=window_hours, skip_when_unavailable=skip)


def main() -> None:
    """Ejecuta la logica de `main` dentro del flujo del bot.

    Returns:
        object: Resultado de la operacion.
    """
    args = parse_args()
    console = Console()
    path = args.file.resolve()
    interval = max(0.5, args.interval)
    reset_hour, task_limits, fury_schedule = load_tracking_settings(args.reset_hour, args.config)
    console.log(f"Usando reset_hour={reset_hour}")
    console.print(
        f"[bold cyan]Monitor de tareas activado[/] (archivo: {path}, intervalo: {interval:.1f}s)"
    )

    snapshot = load_snapshot(path)
    reset_anchor = compute_reset_anchor(snapshot.read_at, reset_hour)
    initial_render = build_dashboard(
        snapshot,
        source_path=path,
        reset_anchor=reset_anchor,
        task_limits=task_limits,
        fury_schedule=fury_schedule,
    )
    if args.once:
        console.print(initial_render)
        return

    last_signature = (snapshot.file_mtime, snapshot.error)
    try:
        with Live(
            console=console,
            screen=args.fullscreen,
            auto_refresh=False,
        ) as live:
            live.update(initial_render, refresh=True)
            while True:
                time.sleep(interval)
                snapshot = load_snapshot(path)
                signature = (snapshot.file_mtime, snapshot.error)
                if signature == last_signature:
                    continue
                reset_anchor = compute_reset_anchor(snapshot.read_at, reset_hour)
                live.update(
                    build_dashboard(
                        snapshot,
                        source_path=path,
                        reset_anchor=reset_anchor,
                        task_limits=task_limits,
                        fury_schedule=fury_schedule,
                    ),
                    refresh=True,
                )
                last_signature = signature
    except KeyboardInterrupt:
        console.print("\n[yellow]Monitor detenido por el usuario[/]")


if __name__ == "__main__":
    main()
