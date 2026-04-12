"""Orquestador de rutinas: selecciona granjas y ejecuta tareas automatizadas."""

from __future__ import annotations

import os
import threading
import time
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Collection, Dict, Iterable, List, Mapping, MutableMapping, Sequence

import pytesseract
from rich.console import Console
from rich.table import Table

from .config import BotConfig, InstanceConfig, LayoutConfig, RoutineConfig, TaskSpec
from .debug import DebugReporter, get_debug_reporter
from .farm_console import FarmConsole
from .navigation import tap_back_button
from .daily_tracker import DailyTaskTracker, GATHER_SLOT_TASK_PREFIX
from .devices import (
    BlueStacksInstanceManager,
    DeviceController,
    DeviceCaptureError,
    DeviceRecoverableError,
)
from .tasks import build_registry
from .tasks.base import TaskContext
from .vision import VisionHelper
from .pending import collect_pending_tasks as pending_order, load_daily_tasks, parse_datetime


class PauseController:
    """Escucha una tecla y permite pausar/reanudar la rutina sin matarla."""

    def __init__(self, key: str, console: Console) -> None:
        self.key = (key or "").lower()
        self.console = console
        self._paused = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._available = False
        self._last_toggle = 0.0
        self._min_interval = 0.4  # segundos de debounce
        if not self.key:
            return
        try:
            import msvcrt  # type: ignore
        except ImportError:
            self.console.log("[warning] Hotkey de pausa no disponible en esta plataforma")
            return
        self._available = True

        def _loop() -> None:
            while not self._stop.is_set():
                try:
                    if msvcrt.kbhit():  # type: ignore[attr-defined]
                        raw = msvcrt.getch()  # type: ignore[attr-defined]
                        if not raw:
                            continue
                        try:
                            ch = raw.decode(errors="ignore").lower()
                        except Exception:
                            continue
                        if ch == self.key:
                            now = time.monotonic()
                            if now - self._last_toggle < self._min_interval:
                                continue
                            self._last_toggle = now
                            if self._paused.is_set():
                                self._paused.clear()
                                self.console.log("[info] Rutina reanudada (hotkey)")
                            else:
                                self._paused.set()
                                self.console.log("[warning] Rutina en pausa (hotkey)")
                            # Vaciar buffer para no repetir al soltar la tecla
                            while msvcrt.kbhit():  # type: ignore[attr-defined]
                                msvcrt.getch()  # type: ignore[attr-defined]
                except Exception:
                    # No romper el hilo por errores de lectura de teclado
                    pass
                time.sleep(0.1)

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()

    def wait_if_paused(self) -> None:
        """Ejecuta la logica de `wait_if_paused` dentro del flujo del bot.

        Returns:
            object: Resultado de la operacion.
        """
        while self._available and self._paused.is_set() and not self._stop.is_set():
            time.sleep(0.25)

    def stop(self) -> None:
        """Ejecuta la logica de `stop` dentro del flujo del bot.

        Returns:
            object: Resultado de la operacion.
        """
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)


class RoutineRestartError(RuntimeError):
    """Señala que la rutina debe reiniciarse desde BlueStacks."""


class LoadingRestartError(RoutineRestartError):
    """Señala que se reinició por pantalla de carga persistente."""


@dataclass
class RunnerOptions:
    """Parámetros CLI que controlan qué granjas y rutinas se corren."""

    routine_override: str | None = None
    batch_start: int = 0
    batch_size: int | None = None
    farm_names: Sequence[str] | None = None
    simulate: bool = False
    loop: bool = False
    loop_start_index: int = 1
    single_task: str | None = None
    pending_scheduler: bool = False
    state_file: Path | None = None
    pending_poll_interval: float = 60.0
    pending_max_runs: int | None = None
    pause_key: str | None = "p"
    concurrent: bool = False
    max_workers: int | None = None


class RoutineRunner:
    """Ejecuta una rutina sobre múltiples granjas controlando ADB y visión."""

    def __init__(self, config: BotConfig, options: RunnerOptions) -> None:
        self.config = config
        self.options = options
        self.console = Console()
        self.registry = build_registry()
        self._stop_requested = False
        self._stop_event = threading.Event()
        self._single_task_routine: RoutineConfig | None = None
        if options.single_task:
            try:
                self.registry.get(options.single_task)
            except KeyError as exc:
                raise SystemExit(str(exc)) from exc
            self._single_task_routine = RoutineConfig(
                tasks=[TaskSpec(task=options.single_task, params={})]
            )
        if config.tesseract_cmd:
            os.environ["TESSERACT_CMD"] = config.tesseract_cmd
            pytesseract.pytesseract.tesseract_cmd = config.tesseract_cmd
        self.instance_manager = BlueStacksInstanceManager(
            config.bluestacks, self.console, simulate=options.simulate
        )
        tracking_cfg = config.daily_tracking
        self._state_path = Path(
            options.state_file
            or (tracking_cfg.storage_path if tracking_cfg and tracking_cfg.storage_path else "state/daily_tasks.json")
        )
        # Alias legacy name para compatibilidad con logs previos
        self.state_dir = self._state_path
        self.daily_tracker: DailyTaskTracker | None = None
        self.debug_reporter: DebugReporter = get_debug_reporter()
        self._truck_trigger_state: Dict[str, float] = {}
        self._radar_trigger_state: Dict[str, float] = {}
        self._last_fallback_farm: str | None = None
        self._recent_farm_history: deque[str] = deque(maxlen=8)
        self._pair_loop_window = max(4, int(self.config.timing("pending_pair_loop_window", 6)))
        self._pending_idle_threshold_hours = max(
            0.0, float(self.config.timing("pending_idle_hours", 8.0))
        )
        self._pause_controller = (
            PauseController(options.pause_key, self.console)
            if options.pause_key
            else None
        )
        if (
            tracking_cfg
            and tracking_cfg.enabled
            and tracking_cfg.tracked_tasks
        ):
            storage_path = Path(tracking_cfg.storage_path)
            self.daily_tracker = DailyTaskTracker(
                storage_path,
                tracking_cfg.reset_hour_local,
                tracking_cfg.tracked_tasks,
                self.console,
            )

    def run(self) -> None:
        """Aplica filtros, muestra el resumen y ejecuta cada rutina seleccionada."""
        # Reset de señales por si hubo una ejecución previa con Ctrl+C
        self._stop_requested = False
        self._stop_event.clear()
        farms = self._select_farms()
        if not farms:
            self.console.print("[bold red]No hay granjas para ejecutar")
            return
        farms = self._apply_loop_rotation(farms)
        self._print_farm_summary(farms)

        if self.options.pending_scheduler:
            try:
                self._run_pending_scheduler(farms)
            finally:
                if self._pause_controller:
                    self._pause_controller.stop()
            return

        if self.options.concurrent:
            try:
                self._run_concurrent(farms)
            finally:
                if self._pause_controller:
                    self._pause_controller.stop()
            return

        iteration = 0
        loading_restarts = 0
        max_loading_restarts = 2
        try:
            while True:
                iteration += 1
                if self.options.loop:
                    self.console.rule(f"Iteración #{iteration}")

                for farm in farms:
                    self._pause_if_needed()
                    self._execute_farm(farm)

                if not self.options.loop:
                    break
        except KeyboardInterrupt:
            self._stop_requested = True
            self._stop_event.set()
            self.console.log("[info] Ejecución interrumpida por el usuario")
        finally:
            if self._pause_controller:
                self._pause_controller.stop()
            if self.options.loop and self._stop_requested:
                self.console.log("[info] Loop permanente detenido")

    def _print_farm_summary(self, farms: Sequence[InstanceConfig]) -> None:
        summary = Table(title="Granjas seleccionadas")
        summary.add_column("#")
        summary.add_column("Farm")
        summary.add_column("Instancia")
        summary.add_column("Rutina")
        for idx, farm in enumerate(farms, start=1):
            summary.add_row(
                str(idx),
                farm.name,
                farm.instance,
                self._routine_label(farm),
            )
        self.console.print(summary)

    def _run_concurrent(self, farms: Sequence[InstanceConfig]) -> None:
        workers = self.options.max_workers or len(farms)
        selected = list(farms)[:workers]
        if not selected:
            return
        self.console.log(
            f"[info] Ejecutando {len(selected)} granjas en paralelo (workers={workers})"
        )

        threads: list[threading.Thread] = []

        def _target(farm: InstanceConfig) -> None:
            try:
                self.console.log(f"[debug] Iniciando hilo para {farm.name}")
                self._execute_farm(farm)
            except KeyboardInterrupt:
                self._stop_event.set()
            except DeviceRecoverableError as exc:
                self.console.log(f"[warning] {farm.name}: ejecución interrumpida ({exc})")
                self._stop_event.set()
            except Exception as exc:  # pragma: no cover - defensa ante fallos inesperados
                self.console.log(f"[error] {farm.name}: error no controlado en hilo ({exc})")
                self._stop_event.set()
            finally:
                self.console.log(f"[debug] Hilo de {farm.name} finalizado")

        for farm in selected:
            t = threading.Thread(target=_target, args=(farm,), daemon=True)
            threads.append(t)
            t.start()

        try:
            for t in threads:
                t.join()
        except KeyboardInterrupt:
            self._stop_event.set()
            self.console.log("[info] Interrupción recibida; deteniendo hilos concurrentes")
            for t in threads:
                t.join(timeout=1.0)

    def _execute_farm(self, farm: InstanceConfig) -> None:
        if self._stop_event.is_set():
            self.console.log(f"[debug] stop_event activo; se omite {farm.name}")
            return
        routine_id = self.options.routine_override or farm.routine
        routine = self._single_task_routine or self.config.routine_for(routine_id)
        layout = self.config.layout_for(farm)
        total_tasks = len(routine.tasks)
        next_task_idx = 0
        restart_attempts = 0
        max_restarts = max(0, int(self.config.timing("routine_restart_attempts", 1)))
        loading_restarts = 0
        max_loading_restarts = max(0, int(self.config.timing("loading_restart_attempts", 2)))

        self.console.rule(f"{farm.name} :: rutina {self._routine_label(farm)}")
        while next_task_idx < total_tasks:
            try:
                next_task_idx = self._run_routine_attempt(
                    farm,
                    routine,
                    layout,
                    start_task=next_task_idx,
                )
            except DeviceRecoverableError as exc:
                self.console.log(
                    f"[error] {farm.name}: dispositivo no respondió ({exc}); se omitirá la granja"
                )
                break
            except RoutineRestartError as exc:
                if isinstance(exc, LoadingRestartError):
                    loading_restarts += 1
                    if loading_restarts > max_loading_restarts:
                        self.console.log(
                            f"[error] {farm.name}: demasiados reinicios por pantalla de carga; se detendrá la granja"
                        )
                        break
                    self.console.log(
                        f"[warning] {farm.name}: reinicio por pantalla de carga ({loading_restarts}/{max_loading_restarts})"
                    )
                    continue
                folder = self.debug_reporter.persist_failure(
                    farm.name, str(exc)
                )
                if folder:
                    self.console.log(
                        f"[warning] Artifacts guardados para {farm.name} en {folder}"
                    )
                restart_attempts += 1
                if restart_attempts > max_restarts:
                    self.console.log(
                        f"[error] {farm.name}: se excedieron los reinicios permitidos ({exc})"
                    )
                    break
                self.console.log(
                    f"[warning] {farm.name}: {exc}; reiniciando intento {restart_attempts}/{max_restarts}"
                )
                continue

            if not isinstance(next_task_idx, int):
                self.console.log(
                    f"[warning] {farm.name}: índice de tarea inválido ({next_task_idx}); se aborta la rutina"
                )
                # No matar instancia; dejar abierta para diagnóstico/reintento manual
                break

            if next_task_idx < total_tasks:
                self.console.log(
                    f"[warning] {farm.name}: rutina incompleta ({next_task_idx}/{total_tasks} tareas)"
                )

        # Registrar actividad reciente para evitar re-selección inmediata por inactividad
        self._record_farm_activity(farm.name)

    def _run_pending_scheduler(self, farms: Sequence[InstanceConfig]) -> None:
        state_path = self._state_path
        poll = max(5.0, float(self.options.pending_poll_interval or 60.0))
        max_runs = self.options.pending_max_runs
        farm_map = {farm.name: farm for farm in farms}
        if not farm_map:
            farm_map = {farm.name: farm for farm in self.config.instances}
        if not farm_map:
            self.console.print("[bold red]No hay granjas disponibles para modo pending")
            return

        processed = 0
        last_run_farm: str | None = None
        consecutive_runs = 0
        repeat_limit = 3  # salta a otra granja cuando se alcanzan 3 ejecuciones seguidas
        self.console.rule("Scheduler de pendientes activo")
        try:
            while True:
                data = load_daily_tasks(state_path)
                entries = pending_order(data)
                ordered = [entry for entry in entries if entry.farm in farm_map]
                if not ordered:
                    self.console.log(
                        f"[info] No hay tareas pendientes; esperando {poll:.0f}s antes de reintentar"
                    )
                    time.sleep(poll)
                    continue

                now = datetime.now()
                ready = [item for item in ordered if not item.next_ready or item.next_ready <= now]
                selected = ready[0] if ready else None
                fallback_last_update: datetime | None = None
                using_fallback = False
                next_due = next((entry.next_ready for entry in ordered if entry.next_ready), None)
                next_hint = ordered[0].next_ready if ordered else None

                if selected is None:
                    if next_due and next_due > now:
                        self._wait_for_next_ready(next_due, poll)
                        continue
                    farm, fallback_last_update = self._select_least_recent_farm(
                        data,
                        farm_map,
                        previous=self._last_fallback_farm,
                    )
                    if not farm:
                        self.console.log(
                            f"[info] No hay granjas disponibles para rotación; esperando {poll:.0f}s"
                        )
                        time.sleep(poll)
                        continue
                    using_fallback = True
                else:
                    farm = farm_map[selected.farm]

                if not farm:
                    time.sleep(poll)
                    continue

                idle_farm, idle_last_update = self._select_idle_farm(
                    data,
                    farm_map,
                    threshold_hours=self._pending_idle_threshold_hours,
                    skip_names={farm.name},
                )
                if idle_farm and idle_farm.name != farm.name:
                    age_text = (
                        f"{((datetime.now() - idle_last_update).total_seconds() / 3600):.1f}h"
                        if idle_last_update
                        else "sin registros previos"
                    )
                    self.console.log(
                        f"[warning] {idle_farm.name} lleva {age_text} sin actividad; priorizando esta granja"
                    )
                    selected = None
                    farm = idle_farm
                    fallback_last_update = idle_last_update
                    using_fallback = True

                repeated_candidate = (
                    last_run_farm
                    and farm.name == last_run_farm
                    and consecutive_runs >= repeat_limit - 1
                )
                if repeated_candidate:
                    alt_entry, alt_farm = self._select_ready_alternative(
                        ready,
                        farm_map,
                        skip_names={farm.name},
                    )
                    if alt_farm:
                        self.console.log(
                            f"[warning] {farm.name} se ejecutó {repeat_limit} veces seguidas; alternando a {alt_farm.name}"
                        )
                        selected = alt_entry
                        farm = alt_farm
                        fallback_last_update = None
                        using_fallback = False
                    else:
                        alt_farm, alt_last_update = self._select_least_recent_farm(
                            data,
                            farm_map,
                            previous=self._last_fallback_farm,
                            blocked={farm.name},
                        )
                        if alt_farm:
                            self.console.log(
                                f"[warning] {farm.name} se ejecutó {repeat_limit} veces seguidas; rotando a {alt_farm.name}"
                            )
                            selected = None
                            farm = alt_farm
                            fallback_last_update = alt_last_update
                            using_fallback = True
                        else:
                            self.console.log(
                                f"[warning] {farm.name} se ejecutó {repeat_limit} veces seguidas, pero no hay granjas alternas listas; se continuará"
                            )

                pair_block = self._detect_pair_loop(farm.name)
                if pair_block:
                    alt_entry, alt_farm = self._select_ready_alternative(
                        ready,
                        farm_map,
                        skip_names=pair_block,
                    )
                    pair_text = ", ".join(sorted(pair_block))
                    if alt_farm:
                        self.console.log(
                            f"[warning] Se detectó un bucle entre {pair_text}; cambiando a {alt_farm.name}"
                        )
                        selected = alt_entry
                        farm = alt_farm
                        fallback_last_update = None
                        using_fallback = False
                    else:
                        alt_farm, alt_last_update = self._select_least_recent_farm(
                            data,
                            farm_map,
                            previous=self._last_fallback_farm,
                            blocked=pair_block,
                        )
                        if alt_farm:
                            self.console.log(
                                f"[warning] Se detectó un bucle entre {pair_text}; rotando a {alt_farm.name}"
                            )
                            selected = None
                            farm = alt_farm
                            fallback_last_update = alt_last_update
                            using_fallback = True
                        else:
                            self.console.log(
                                f"[warning] Solo {pair_text} tienen pendientes activos; esperando {poll:.0f}s antes de reintentar"
                            )
                            time.sleep(poll)
                            continue

                if using_fallback:
                    self._last_fallback_farm = farm.name
                    next_due_candidate = next_due or next_hint
                    next_due_text = (
                        next_due_candidate.strftime("%H:%M:%S")
                        if next_due_candidate
                        else "—"
                    )
                    last_text = (
                        fallback_last_update.strftime("%H:%M:%S")
                        if fallback_last_update
                        else "sin registros"
                    )
                    self.console.rule(
                        f"Siguiente granja: {farm.name} (rotación por inactividad; última {last_text}, próxima tarea {next_due_text})"
                    )
                else:
                    self._last_fallback_farm = None
                    next_ready_text = selected.next_ready if selected else "—"
                    task_name = selected.name if selected else "pendiente"
                    self.console.rule(
                        f"Siguiente granja: {farm.name} (tarea {task_name}, próxima {next_ready_text or '—'})"
                    )

                if farm.name == last_run_farm:
                    consecutive_runs += 1
                else:
                    last_run_farm = farm.name
                    consecutive_runs = 1

                self._execute_farm(farm)
                self._recent_farm_history.append(farm.name)
                processed += 1
                if max_runs and processed >= max_runs:
                    self.console.log(
                        f"[info] Se alcanzó el límite de ejecuciones ({max_runs}) en modo pending"
                    )
                    break
        except KeyboardInterrupt:
            self.console.log("[info] Scheduler interrumpido por el usuario")

    def _record_farm_activity(self, farm_name: str) -> None:
        """Marca actividad reciente para evitar re-selección por inactividad."""
        try:
            tracking_cfg = self.config.daily_tracking
            reset_hour = tracking_cfg.reset_hour_local if tracking_cfg else 23
            task_limits = tracking_cfg.tracked_tasks if tracking_cfg else {}
            tracker = DailyTaskTracker(self._state_path, reset_hour, task_limits, self.console)
            tracker.touch_farm(farm_name)
            self.console.log(f"[info] {farm_name}: actividad registrada")
        except Exception as exc:  # pragma: no cover - best-effort persistence
            self.console.log(
                f"[warning] {farm_name}: no se pudo registrar la actividad ({exc})"
            )

    def _current_reset_anchor(self) -> datetime | None:
        """Devuelve el último reset diario configurado, si aplica."""

        tracking_cfg = self.config.daily_tracking
        if not tracking_cfg or not tracking_cfg.enabled:
            return None
        reset_hour = max(0, min(23, int(getattr(tracking_cfg, "reset_hour_local", 0) or 0)))
        now = datetime.now()
        anchor = now.replace(hour=reset_hour, minute=0, second=0, microsecond=0)
        if now < anchor:
            anchor -= timedelta(days=1)
        return anchor

    def _select_ready_alternative(
        self,
        ready: Sequence[Any],
        farm_map: Mapping[str, InstanceConfig],
        *,
        skip_names: Collection[str] | None = None,
    ) -> tuple[Any | None, InstanceConfig | None]:
        skip = set(skip_names or ())
        for entry in ready:
            farm = farm_map.get(getattr(entry, "farm", ""))
            if not farm or (skip and farm.name in skip):
                continue
            return entry, farm
        return None, None

    def _detect_pair_loop(self, candidate: str) -> set[str] | None:
        history = list(self._recent_farm_history)
        history.append(candidate)
        window = history[-self._pair_loop_window :]
        if len(window) < self._pair_loop_window:
            return None
        unique = set(window)
        return unique if len(unique) <= 2 else None

    def _wait_for_next_ready(self, target: datetime, poll: float) -> None:
        remaining = (target - datetime.now()).total_seconds()
        if remaining <= 0:
            return
        wait_seconds = max(5.0, min(remaining, poll))
        eta = target.strftime("%H:%M:%S")
        self.console.log(
            f"[info] Todas las tareas pendientes estarán listas alrededor de {eta}; esperando {wait_seconds:.0f}s"
        )
        time.sleep(wait_seconds)

    def _select_least_recent_farm(
        self,
        data: Mapping[str, Any],
        farm_map: Mapping[str, InstanceConfig],
        *,
        previous: str | None = None,
        blocked: Collection[str] | None = None,
    ) -> tuple[InstanceConfig | None, datetime | None]:
        last_updates = self._collect_farm_last_updates(data)
        sorted_candidates: list[tuple[datetime, InstanceConfig, datetime | None]] = []
        for name, farm in farm_map.items():
            ts = last_updates.get(name)
            key = ts or datetime.min
            sorted_candidates.append((key, farm, ts))
        sorted_candidates.sort(key=lambda item: item[0])
        for key, farm, ts in sorted_candidates:
            if blocked and farm.name in blocked:
                continue
            if previous and farm.name == previous and len(sorted_candidates) > 1:
                continue
            return farm, ts
        return None, None

    def _select_idle_farm(
        self,
        data: Mapping[str, Any],
        farm_map: Mapping[str, InstanceConfig],
        *,
        threshold_hours: float,
        skip_names: Collection[str] | None = None,
    ) -> tuple[InstanceConfig | None, datetime | None]:
        if threshold_hours <= 0:
            return None, None
        last_updates = self._collect_farm_last_updates(data)
        skip = set(skip_names or ())
        candidate: tuple[str, datetime | None] | None = None
        for name, farm in farm_map.items():
            if skip and name in skip:
                continue
            ts = last_updates.get(name)
            if ts is None:
                return farm, None
            if not candidate or (ts and ts < candidate[1]):
                candidate = (name, ts)
        if not candidate or candidate[1] is None:
            return None, None
        idle_ts = candidate[1]
        if datetime.now() - idle_ts >= timedelta(hours=threshold_hours):
            return farm_map[candidate[0]], idle_ts
        return None, None

    def _collect_farm_last_updates(self, data: Mapping[str, Any]) -> Dict[str, datetime | None]:
        result: Dict[str, datetime | None] = {}
        anchor = self._current_reset_anchor()
        for farm_name, tasks in data.items():
            if not isinstance(tasks, MutableMapping):
                continue
            last_update: datetime | None = None
            last_activity = parse_datetime(tasks.get("__last_activity__", {}).get("timestamp") if isinstance(tasks.get("__last_activity__"), MutableMapping) else None)
            if last_activity:
                last_update = last_activity
            for task_name, entry in tasks.items():
                if task_name.startswith(GATHER_SLOT_TASK_PREFIX):
                    continue
                if not isinstance(entry, MutableMapping):
                    continue
                ts = parse_datetime(entry.get("timestamp"))
                if not ts:
                    continue
                if anchor and ts < anchor:
                    continue
                if last_update is None or ts > last_update:
                    last_update = ts
            result[farm_name] = last_update
        return result

    def _pause_if_needed(self) -> None:
        if self._pause_controller:
            self._pause_controller.wait_if_paused()

    def _run_routine_attempt(
        self,
        farm: InstanceConfig,
        routine: RoutineConfig,
        layout: LayoutConfig,
        *,
        start_task: int = 0,
    ) -> int:
        """Ejecuta la rutina para una granja, manejando reinicios recuperables."""
        base_console = self.console
        farm_console = FarmConsole(
            base_console, farm.name, debug_reporter=self.debug_reporter
        )
        previous_manager_console = getattr(self.instance_manager, "console", None)
        interrupted = False
        device_fault = False
        try:
            self.console = farm_console
            if previous_manager_console is not None:
                self.instance_manager.console = farm_console
            self.console.log(
                f"[debug] Preparando rutina para {farm.name} (tareas={len(routine.tasks)})"
            )
            device = DeviceController(
                self.config.adb,
                farm.device_port,
                self.console,
                simulate=self.options.simulate,
                stop_event=self._stop_event,
            )
            with self.instance_manager.ensure_instance(
                farm,
                before_shutdown=lambda: self._close_game_before_instance_shutdown(device),
            ):
                self.console.log("[debug] Instancia abierta; esperando dispositivo ADB")
                device.wait_for_device()
                self.console.log("[debug] Dispositivo ADB listo; preparando juego")
                warmup = self.config.timings.get(
                    "instance_warmup_seconds", self.config.instance_warmup_seconds
                )
                if warmup > 0:
                    self.console.log(
                        f"Esperando {warmup:.1f}s para que cargue la instancia"
                    )
                    device.sleep(warmup)

                self._ensure_game_package_present(device)

                vision = VisionHelper(
                    device=device,
                    console=self.console,
                    farm_name=farm.name,
                    debug_reporter=self.debug_reporter,
                )
                device.set_debug_capture(vision.capture_for_debug)
                self._maybe_launch_game(layout, vision)

                task_index = start_task

                try:
                    try:
                        # Si ya estamos en pantalla lista, evitar esperar popups largos.
                        if not self._wait_for_ready(layout, vision, strict=False, timeout_override=2.0):
                            self._dismiss_initial_popups(layout, vision)
                        self._wait_for_ready(layout, vision, strict=True)

                        ctx = TaskContext(
                            device=device,
                            farm=farm,
                            layout=layout,
                            console=self.console,
                            simulate=self.options.simulate,
                            vision=vision,
                            daily_tracker=self.daily_tracker,
                            config=self.config,
                            pause_cb=self._pause_if_needed,
                            stop_event=self._stop_event,
                        )
                        if task_index > 0:
                            self.console.log(
                                f"Reanudando rutina en la tarea #{task_index + 1}/{len(routine.tasks)}"
                            )

                        self._maybe_trigger_trucks(ctx)
                        self._maybe_trigger_radar_quests(ctx)

                        while task_index < len(routine.tasks):
                            if self._stop_event.is_set():
                                break
                            self._pause_if_needed()
                            spec = routine.tasks[task_index]
                            task = self.registry.get(spec.task)
                            farm_console.set_task(spec.task)
                            allow_repeat = getattr(
                                task, "allow_repeat_after_completion", False
                            )
                            should_skip = (
                                self.daily_tracker
                                and self.daily_tracker.should_skip(farm.name, spec.task)
                                and not self.options.single_task
                            )
                            try:
                                if should_skip and not allow_repeat:
                                    self.console.log(
                                        f"[info] '{spec.task}' ya se completó desde el último reset para {farm.name}; saltando"
                                    )
                                    task_index += 1
                                    continue
                                self._ensure_world_screen(
                                    layout,
                                    vision,
                                    stage=f"antes de '{spec.task}'",
                                    strict=True,
                                )
                                self._maybe_trigger_trucks(ctx)
                                self._maybe_trigger_radar_quests(ctx)
                                self.console.log(f"Ejecutando task '{spec.task}'")
                                params = self.config.task_defaults_for(spec.task)
                                if self.options.single_task:
                                    params["skip_daily_limit_check"] = True
                                if spec.params:
                                    params.update(spec.params)
                                task.run(ctx, params)
                                if self.daily_tracker and not getattr(task, "manual_daily_logging", False):
                                    self.daily_tracker.record_progress(farm.name, spec.task)
                                self._ensure_world_screen(
                                    layout,
                                    vision,
                                    stage=f"después de '{spec.task}'",
                                    strict=True,
                                )
                                self._maybe_trigger_trucks(ctx)
                                self._maybe_trigger_radar_quests(ctx)
                                task_index += 1
                            finally:
                                farm_console.set_task(None)

                        return task_index
                    except (DeviceCaptureError, DeviceRecoverableError) as exc:
                        device_fault = True
                        raise RoutineRestartError(str(exc)) from exc
                except RoutineRestartError:
                    # Reiniciar rutina cerrando y relanzando la app dentro de la misma instancia
                    if device_fault:
                        # Si el dispositivo ya falló, no forzar más acciones ADB; abortar intento
                        raise
                    try:
                        self._restart_app_only(layout, vision, device)
                    except DeviceRecoverableError as exc:
                        device_fault = True
                        raise RoutineRestartError(str(exc)) from exc
                    raise
                except KeyboardInterrupt:
                    interrupted = True
                    self._stop_requested = True
                    raise
                finally:
                    if not (interrupted or self._stop_requested or device_fault):
                        # Si ya estamos listos, solo intenta recuperación de loading (no alteres el retorno normal)
                        try:
                            if self._wait_for_ready(layout, vision, strict=False, timeout_override=2.0):
                                return task_index
                        except Exception:
                            pass
                        if self._recover_from_loading(layout, vision, device):
                            raise RoutineRestartError(
                                "Reapertura del juego tras pantalla de carga"
                            )
        finally:
            if previous_manager_console is not None:
                self.instance_manager.console = previous_manager_console
            self.console = base_console

    def _select_farms(self) -> List[InstanceConfig]:
        """Filtra granjas según CLI (nombres, lotes o todas)."""
        if self.options.farm_names:
            name_to_farm = {farm.name: farm for farm in self.config.instances}
            ordered_farms: List[InstanceConfig] = []
            missing: List[str] = []
            for name in self.options.farm_names:
                farm = name_to_farm.get(name)
                if farm:
                    ordered_farms.append(farm)
                else:
                    missing.append(name)
            if missing:
                missing_str = ", ".join(missing)
                self.console.log(
                    f"[warning] Algunas granjas indicadas no existen en la config: {missing_str}"
                )
            return ordered_farms

        farms_list = list(self.config.instances)
        start = max(self.options.batch_start, 0)
        end = start + self.options.batch_size if self.options.batch_size else None
        return farms_list[start:end]

    def _apply_loop_rotation(self, farms: List[InstanceConfig]) -> List[InstanceConfig]:
        """Reordena la lista cuando se corre en bucle para repartir la carga."""
        if not farms or not self.options.loop:
            return farms
        length = len(farms)
        start_index = max(1, self.options.loop_start_index)
        offset = (start_index - 1) % length
        if offset == 0:
            return farms
        return farms[offset:] + farms[:offset]

    def _routine_label(self, farm: InstanceConfig) -> str:
        """Devuelve el texto mostrado para la rutina según los overrides activos."""
        if self.options.single_task:
            return f"task:{self.options.single_task}"
        return self.options.routine_override or farm.routine

    def _maybe_launch_game(
        self, layout: LayoutConfig, vision: VisionHelper
    ) -> None:
        """Intenta abrir el juego ya sea por template o coordenada de layout."""
        # Si ya estamos dentro (world/home listos), no relanzar.
        if self._wait_for_ready(layout, vision, strict=False, timeout_override=1.5):
            self.console.log("Juego ya abierto; se omite relanzar")
            return
        # Si ya está la pantalla de carga visible, solo confirmar y salir.
        if self._is_loading_screen(layout, vision):
            self.console.log("Pantalla de carga ya visible; no se relanza")
            self._confirm_game_loading(layout, vision)
            return

        if self._launch_game_via_template(layout, vision):
            self._confirm_game_loading(layout, vision)
            self._ensure_game_launched(layout, vision)
            return
        launch_coord = getattr(layout, "buttons", {}).get("launch_game")
        if not launch_coord:
            return
        self.console.log("Lanzando juego desde la coordenada del layout")
        vision.device.tap(launch_coord, label="launch-game")
        launch_wait = self.config.timing("game_launch_wait_seconds", 0)
        if launch_wait > 0:
            vision.device.sleep(launch_wait)
        self._confirm_game_loading(layout, vision)
        self._ensure_game_launched(layout, vision, fallback_tap=launch_coord)

    def _launch_game_via_template(
        self, layout: LayoutConfig, vision: VisionHelper
    ) -> bool:
        """Busca plantillas configuradas para lanzar el juego desde el escritorio."""
        template_name = self._launch_game_template_name(layout)
        if not template_name:
            return False
        try:
            template_paths = layout.template_paths(template_name)
        except KeyError:
            return False

        timeout = self.config.timing("launch_game_template_timeout", 20.0)
        poll = max(self.config.timing("launch_game_template_poll", 1.0), 0.5)
        threshold = self.config.timing("launch_game_template_threshold", 0.85)

        self.console.log("Buscando icono del juego para iniciar")
        result = vision.wait_for_any_template(
            template_paths,
            timeout=timeout,
            poll_interval=poll,
            threshold=threshold,
            raise_on_timeout=False,
        )
        if not result:
            self.console.log(
                "[warning] No se detectó el icono del juego; se usará la coordenada del layout"
            )
            return False
        coords, matched_path = result
        self.console.log(
            f"Icono '{matched_path.name}' detectado; lanzando juego"
        )
        vision.device.tap(coords, label="launch-game-template")
        launch_wait = self.config.timing("game_launch_wait_seconds", 0)
        if launch_wait > 0:
            vision.device.sleep(launch_wait)
        return True

    @staticmethod
    def _launch_game_template_name(layout: LayoutConfig) -> str | None:
        if "launch_game_icon" in layout.templates:
            return "launch_game_icon"
        if "launch_game" in layout.templates:
            return "launch_game"
        return None

    def _ensure_game_launched(
        self,
        layout: LayoutConfig,
        vision: VisionHelper,
        *,
        fallback_tap: tuple[int, int] | None = None,
    ) -> None:
        """Verifica que el icono del juego desaparezca tras lanzarlo y reintenta si no."""
        if self.options.simulate:
            return
        icon_paths = self._launch_icon_paths(layout)
        if not icon_paths:
            return
        check_delay = max(1.0, self.config.timing("game_launch_check_delay", 3.0))
        retries = max(1, int(self.config.timing("game_launch_retry_attempts", 3)))
        threshold = self.config.timing("launch_game_template_threshold", 0.85)
        for attempt in range(retries):
            vision.device.sleep(check_delay)
            visible = vision.find_any_template(
                icon_paths,
                threshold=threshold,
            )
            if not visible:
                return
            coords, matched_path = visible
            self.console.log(
                f"[warning] El icono '{matched_path.name}' sigue visible tras lanzar (intento {attempt + 1}/{retries})"
            )
            tap_point = coords or fallback_tap
            if tap_point:
                vision.device.tap(tap_point, label="launch-game-retry")
            elif fallback_tap:
                vision.device.tap(fallback_tap, label="launch-game-retry")
            else:
                break
        raise RoutineRestartError(
            "El juego no se abrió tras múltiples intentos; se reiniciará la rutina"
        )

    @staticmethod
    def _launch_icon_paths(layout: LayoutConfig) -> List[Path]:
        try:
            return layout.template_paths("launch_game_icon")
        except KeyError:
            return []

    @staticmethod
    def _level_up_overlay_templates(layout: LayoutConfig) -> List[Path]:
        try:
            return layout.template_paths("level_up_overlay")
        except KeyError:
            return []

    @staticmethod
    def _game_loading_template_name(layout: LayoutConfig) -> str | None:
        if "game_loading_screen" in layout.templates:
            return "game_loading_screen"
        if "game_loading" in layout.templates:
            return "game_loading"
        return None

    def _is_loading_screen(self, layout: LayoutConfig, vision: VisionHelper) -> bool:
        """Detecta si la pantalla de carga/actualización sigue visible."""

        template_name = self._game_loading_template_name(layout)
        if not template_name:
            return False
        try:
            loading_paths = layout.template_paths(template_name)
        except KeyError:
            return False
        if not loading_paths:
            return False
        threshold = self.config.timing("game_loading_detect_threshold", 0.75)
        result = vision.find_any_template(loading_paths, threshold=threshold)
        return bool(result)

    def _recover_from_loading(
        self,
        layout: LayoutConfig,
        vision: VisionHelper,
        device: DeviceController,
    ) -> bool:
        """Si está en pantalla de carga, cierra la app, relanza y espera listo."""
        if self._stop_requested:
            return False
        # Si ya estamos en la pantalla principal, no intentar recuperación.
        try:
            if self._wait_for_ready(layout, vision, strict=False, timeout_override=2.0):
                return False
        except Exception:
            pass
        # Si el dispositivo está en fallo (captura previa falló), no insistir.
        try:
            vision.device
        except Exception:
            return False
        if self.options.simulate:
            return False
        try:
            if not self._is_loading_screen(layout, vision):
                return False
        except DeviceCaptureError:
            return False

        self.console.log(
            "[warning] Pantalla de carga detectada; se cerrará y reabrirá el juego en lugar de matar la instancia"
        )
        device.press_home()
        package = getattr(self.config, "game_package", None)
        if package:
            device.force_stop_app(package)
        device.sleep(1.5)

        self._maybe_launch_game(layout, vision)
        self._confirm_game_loading(layout, vision)
        # Bucle de recuperación: verificar carga cada 5s y reintentar lanzamiento si desaparece
        extended_timeout = self.config.timing(
            "loading_recovery_ready_timeout",
            self.config.timing("initial_popup_first_timeout", 180),
        )
        poll = 5.0
        start = time.monotonic()

        while time.monotonic() - start <= extended_timeout:
            try:
                self._dismiss_initial_popups_quick(layout, vision, timeout=2.5)
            except Exception as exc:
                self.console.log(
                    f"[warning] No se pudieron cerrar popups tras recargar (rápido): {exc}"
                )

            try:
                ready = self._wait_for_ready(
                    layout,
                    vision,
                    strict=False,
                    timeout_override=poll,
                )
                if ready:
                    return True
            except Exception:
                pass

            if not self._is_loading_screen(layout, vision):
                self.console.log(
                    "[warning] La pantalla de carga desapareció; verificando estado del juego"
                )
                # Si ya estamos listos, no relanzar.
                if self._wait_for_ready(layout, vision, strict=False, timeout_override=2.0):
                    return True
                # Si estamos en el escritorio y vemos el icono, relanzar
                if self._is_home_screen(layout, vision):
                    self._maybe_launch_game(layout, vision)
                    self._confirm_game_loading(layout, vision)
                    continue

                # Si hay captura negra (posible minimize), intenta tocar launch si existe
                try:
                    home_icon = self._launch_icon_paths(layout)
                    if home_icon:
                        result = vision.find_any_template(
                            home_icon,
                            threshold=self.config.timing(
                                "launch_game_template_threshold", 0.85
                            ),
                        )
                        if result:
                            coords, matched = result
                            self.console.log(
                                f"[warning] Icono '{matched.name}' visible; relanzando"
                            )
                            vision.device.tap(coords, label="launch-game-template-retry")
                            self._confirm_game_loading(layout, vision)
                            continue
                except Exception:
                    pass

                # Si no hay loading ni icono, intentar relanzar igualmente como fallback
                self.console.log(
                    "[warning] No se detecta pantalla de carga ni icono; se relanzará el juego"
                )
                self._maybe_launch_game(layout, vision)
                self._confirm_game_loading(layout, vision)
                continue

            vision.device.sleep(poll)

        raise LoadingRestartError("Reapertura del juego tras pantalla de carga")

    def _restart_app_only(
        self,
        layout: LayoutConfig,
        vision: VisionHelper,
        device: DeviceController,
    ) -> None:
        """Cierra la app y la relanza sin tocar la instancia (uso general en reinicios)."""

        if self.options.simulate:
            return
        package = getattr(self.config, "game_package", None)
        device.press_home()
        if package:
            device.force_stop_app(package)
        device.sleep(1.5)
        self._maybe_launch_game(layout, vision)
        self._confirm_game_loading(layout, vision)

    def _close_game_before_instance_shutdown(self, device: DeviceController) -> None:
        """Cierra la app del juego antes de apagar la instancia."""

        if self.options.simulate:
            return
        package = getattr(self.config, "game_package", None)
        if not package:
            return
        try:
            device.press_home()
            success = device.force_stop_app(package)
            if not success:
                self.console.log(
                    f"[warning] No se pudo cerrar '{package}' antes de apagar la instancia"
                )
        except Exception as exc:
            self.console.log(
                f"[warning] Error al cerrar el juego antes de apagar la instancia: {exc}"
            )

    def _ensure_game_package_present(self, device: DeviceController) -> None:
        """Valida que el paquete del juego exista y lo reinstala opcionalmente."""

        if self.options.simulate:
            return
        package = getattr(self.config, "game_package", None)
        if not package:
            return
        apk_path_raw = getattr(self.config, "game_apk_path", None)
        auto_reinstall = bool(getattr(self.config, "auto_reinstall_game", False))
        auto_update = bool(getattr(self.config, "auto_update_game", False))

        if device.package_installed(package):
            # Si ya está instalado, se puede actualizar en sitio y/o crear respaldo si falta.
            if auto_update and apk_path_raw:
                apk_path = Path(apk_path_raw)
                base_apk = self._resolve_base_apk(apk_path)
                if base_apk:
                    installed_vname, _ = device.get_installed_version(package)
                    apk_vname, _ = device.get_apk_version(base_apk)
                    if installed_vname and apk_vname:
                        if self._is_version_older(installed_vname, apk_vname):
                            self.console.log(
                                f"[info] Versión instalada {installed_vname} < respaldo {apk_vname}; actualizando sin borrar datos"
                            )
                            if apk_path.is_dir():
                                device.install_apk_bundle(apk_path, package=package)
                            else:
                                device.install_apk(apk_path, replace=True)
                            if device.package_installed(package):
                                self.console.log("[info] Juego actualizado en sitio")
                        else:
                            self.console.log(
                                f"[info] Versión instalada {installed_vname} ya está al día (respaldo {apk_vname})"
                            )
                    else:
                        self.console.log(
                            "[info] No se pudo comparar versiones (falta versionName en instalado o respaldo); no se fuerza update"
                        )

            if auto_reinstall and apk_path_raw:
                apk_path = Path(apk_path_raw)

                backup_missing = False
                if apk_path.is_dir():
                    apk_files = [p for p in apk_path.iterdir() if p.suffix.lower() == ".apk"]
                    backup_missing = len(apk_files) == 0
                else:
                    backup_missing = not apk_path.exists() or apk_path.stat().st_size == 0

                if not backup_missing:
                    return

                if apk_path.suffix.lower() == ".apk":
                    self.console.log(
                        f"[info] APK de respaldo no encontrada; extrayendo base {package} a {apk_path}"
                    )
                    device.extract_apk(package, apk_path)
                else:
                    self.console.log(
                        f"[info] Respaldo tipo bundle no encontrado; extrayendo bundle de {package} a {apk_path}"
                    )
                    device.extract_apk_bundle(package, apk_path)
            return

        # Paquete ausente: intentar reinstalar desde respaldo
        if auto_reinstall and apk_path_raw:
            apk_path = Path(apk_path_raw)
            if apk_path.is_dir():
                apk_files = [p for p in apk_path.iterdir() if p.suffix.lower() == ".apk"]
                if not apk_files:
                    self.console.log(
                        f"[warning] Respaldo de bundle vacío en {apk_path}; extrae desde una instancia sana primero"
                    )
                else:
                    self.console.log(
                        f"[warning] Paquete '{package}' no encontrado; intentando reinstalar desde bundle {apk_path}"
                    )
                    if device.install_apk_bundle(apk_path, package=package):
                        if device.package_installed(package):
                            self.console.log("[info] Paquete del juego recuperado tras reinstalación múltiple")
                            return
                        self.console.log(
                            "[warning] El paquete sigue ausente tras reinstalar el bundle; se detendrá la rutina"
                        )
            elif apk_path.is_file():
                self.console.log(
                    f"[warning] Paquete '{package}' no encontrado; intentando reinstalar desde {apk_path}"
                )
                if device.install_apk(apk_path, replace=True):
                    if device.package_installed(package):
                        self.console.log("[info] Paquete del juego recuperado tras reinstalación")
                        return
                    self.console.log(
                        "[warning] El paquete sigue ausente tras reinstalar; se detendrá la rutina"
                    )
            else:
                self.console.log(
                    f"[warning] Respaldo no encontrado en {apk_path}; extrae el APK/bundle desde una instancia sana primero"
                )

        self.console.log(
            f"[error] Paquete '{package}' no instalado; reinstala manualmente"
        )
        # Se permite el cierre normal de la instancia para no dejar ventanas abiertas
        raise RoutineRestartError(
            f"El paquete '{package}' no está instalado; respalda o reinstala la app para continuar"
        )

    def _resolve_base_apk(self, apk_path: Path) -> Path | None:
        """Devuelve la ruta al base.apk dentro de un bundle o el archivo en sí."""

        if apk_path.is_file():
            return apk_path
        if not apk_path.exists() or not apk_path.is_dir():
            return None
        apk_files = [p for p in apk_path.iterdir() if p.suffix.lower() == ".apk"]
        if not apk_files:
            return None
        base = next((p for p in apk_files if "base" in p.name.lower()), None)
        return base or apk_files[0]

    @staticmethod
    def _is_version_older(current: str, target: str) -> bool:
        """Comparación simple de versionName usando componentes numéricos; fallback lexicográfico."""

        def _parts(v: str) -> list[int]:
            nums = re.split(r"[^0-9]+", v)
            return [int(n) for n in nums if n.isdigit()]

        cur_parts = _parts(current)
        tgt_parts = _parts(target)
        if cur_parts and tgt_parts:
            length = max(len(cur_parts), len(tgt_parts))
            cur_parts += [0] * (length - len(cur_parts))
            tgt_parts += [0] * (length - len(tgt_parts))
            return cur_parts < tgt_parts
        return current.strip() < target.strip()

    def _confirm_game_loading(self, layout: LayoutConfig, vision: VisionHelper) -> None:
        template_name = self._game_loading_template_name(layout)
        if not template_name:
            return
        try:
            loading_paths = layout.template_paths(template_name)
        except KeyError:
            return
        if not loading_paths:
            return

        timeout = max(1.0, self.config.timing("game_loading_detect_timeout", 10.0))
        threshold = self.config.timing("game_loading_detect_threshold", 0.75)
        attempts = max(1, int(self.config.timing("game_loading_retry_attempts", 2)))
        retry_delay = self.config.timing("game_launch_retry_delay", 2.0)
        icon_paths = self._launch_icon_paths(layout)
        icon_threshold = self.config.timing("launch_game_template_threshold", 0.85)

        for attempt in range(attempts):
            result = vision.wait_for_any_template(
                loading_paths,
                timeout=timeout,
                poll_interval=0.5,
                threshold=threshold,
                raise_on_timeout=False,
            )
            if result:
                _, matched_path = result
                self.console.log(
                    f"Pantalla de carga detectada ('{matched_path.name}') tras lanzar el juego"
                )
                return
            if icon_paths:
                still_home = vision.find_any_template(icon_paths, threshold=icon_threshold)
                if still_home:
                    coords, matched_path = still_home
                    self.console.log(
                        f"[warning] El icono '{matched_path.name}' sigue visible tras lanzar (verificación {attempt + 1}/{attempts}); reintentando"
                    )
                    vision.device.tap(coords, label="launch-game-template-retry")
                    if retry_delay > 0:
                        vision.device.sleep(retry_delay)
                    continue
            break
        self.console.log(
            "[warning] No se confirmó la pantalla de carga tras lanzar el juego; se continuará"
        )

    def _is_home_screen(self, layout: LayoutConfig, vision: VisionHelper) -> bool:
        """Detecta el icono del juego en el escritorio para reintentos rápidos."""

        icon_paths = self._launch_icon_paths(layout)
        if not icon_paths:
            return False
        threshold = self.config.timing("launch_game_template_threshold", 0.85)
        return bool(
            vision.find_any_template(
                icon_paths,
                threshold=threshold,
            )
        )

    def _dismiss_level_up_overlay(
        self,
        layout: LayoutConfig,
        vision: VisionHelper,
        template_paths: Sequence[Path],
    ) -> bool:
        threshold = self.config.timing("level_up_overlay_threshold", 0.9)
        result = vision.find_any_template(template_paths, threshold=threshold)
        if not result:
            return False
        tap_point = layout.buttons.get("close_popup") or (270, 440)
        matched = result[1]
        self.console.log(
            f"[info] Overlay de nivel detectado ('{matched.name}'); tocando pantalla para cerrarlo"
        )
        vision.device.tap(tap_point, label="level-up-dismiss")
        vision.device.sleep(0.8)
        return True

    def _dismiss_initial_popups(self, layout: LayoutConfig, vision: VisionHelper) -> None:
        """Cierra overlays iniciales antes de comenzar la rutina."""
        if "popup_close" not in layout.templates:
            return

        # Si ya estamos listos, no bloquear en popups.
        if self._wait_for_ready(layout, vision, strict=False, timeout_override=1.5):
            self.console.log("Pantalla lista detectada; se omite espera de popups")
            return

        level_overlay_paths = self._level_up_overlay_templates(layout)
        template_paths = layout.template_paths("popup_close")
        max_attempts = int(self.config.timing("initial_popup_max_attempts", 5))
        first_timeout = self.config.timing("initial_popup_first_timeout", 60)
        next_timeout = self.config.timing("initial_popup_next_timeout", 5)
        popup_delay = self.config.timing("initial_popup_delay", 1.0)

        def _wait_popup_with_relaunch(total_timeout: float):
            """Espera por el popup en ventanas cortas y relanza si el juego se minimizó."""

            deadline = time.monotonic() + max(total_timeout, 0.1)
            slice_timeout = min(10.0, total_timeout)
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                window = max(0.5, min(slice_timeout, remaining))
                result = vision.wait_for_any_template(
                    template_paths,
                    timeout=window,
                    poll_interval=1.0,
                    raise_on_timeout=False,
                )
                if result:
                    return result

                if self._is_home_screen(layout, vision):
                    self.console.log(
                        "[warning] El juego parece minimizado/cerrado durante los popups; relanzando"
                    )
                    self._maybe_launch_game(layout, vision)
                    self._confirm_game_loading(layout, vision)
                    deadline = time.monotonic() + max(total_timeout, 0.1)
            return None

        attempts = 0
        timeout = first_timeout
        while True:
            if max_attempts > 0 and attempts >= max_attempts:
                break

            result = _wait_popup_with_relaunch(timeout)
            if not result:
                if level_overlay_paths and self._dismiss_level_up_overlay(
                    layout, vision, level_overlay_paths
                ):
                    continue
                msg = (
                    "No se detectaron popups iniciales"
                    if attempts == 0
                    else "No apareció más popup inicial"
                )
                self.console.log(msg)
                break
            self.console.log("Popup detectado, cerrando")
            coords, matched_path = result
            self.console.log(
                f"Usando template '{matched_path.name}' para cerrar popup inicial"
            )
            vision.device.tap(coords, label="initial-popup-close")
            vision.device.sleep(popup_delay)
            attempts += 1
            timeout = next_timeout

    def _dismiss_initial_popups_quick(
        self,
        layout: LayoutConfig,
        vision: VisionHelper,
        *,
        timeout: float = 5.0,
    ) -> bool:
        """Intento breve de cerrar un popup inicial sin bloquear (una sola vez)."""

        if "popup_close" not in layout.templates:
            return False

        level_overlay_paths = self._level_up_overlay_templates(layout)
        template_paths = layout.template_paths("popup_close")
        popup_delay = self.config.timing("initial_popup_delay", 1.0)

        result = vision.wait_for_any_template(
            template_paths,
            timeout=timeout,
            poll_interval=1.0,
            raise_on_timeout=False,
        )
        if result:
            self.console.log("Popup detectado (rápido), cerrando")
            coords, matched_path = result
            self.console.log(
                f"Usando template '{matched_path.name}' para cerrar popup inicial"
            )
            vision.device.tap(coords, label="initial-popup-close")
            vision.device.sleep(popup_delay)
            return True

        if level_overlay_paths and self._dismiss_level_up_overlay(
            layout, vision, level_overlay_paths
        ):
            return True
        return False

    def _wait_for_ready(
        self,
        layout: LayoutConfig,
        vision: VisionHelper,
        *,
        strict: bool = False,
        timeout_override: float | None = None,
    ) -> bool:
        """Espera a que aparezcan templates que indican la pantalla lista."""
        template_names = self._ready_template_candidates(layout)
        if not template_names:
            return True

        template_map: Dict[str, List[Path]] = {}
        for name in template_names:
            try:
                template_map[name] = layout.template_paths(name)
            except KeyError:
                continue
        if not template_map:
            return True

        combined_paths: List[Path] = []
        path_to_name: Dict[Path, str] = {}
        for name, paths in template_map.items():
            for path in paths:
                combined_paths.append(path)
                path_to_name[path] = name

        timeout = max(
            timeout_override
            if timeout_override is not None
            else self.config.timing("ready_template_timeout", 30),
            5.0,
        )
        poll = max(self.config.timing("ready_template_poll", 1.0), 0.5)
        start = time.monotonic()

        while time.monotonic() - start <= timeout:
            result = vision.find_any_template(
                combined_paths,
                threshold=0.7,
            )
            if result:
                coords, matched_path = result
                matched_name = path_to_name.get(matched_path, matched_path.name)
                if matched_name == "sede_button":
                    self.console.log(
                        "Template 'sede_button' detectado; regresando a la ciudad antes de continuar"
                    )
                    vision.device.tap(coords, label="runner-return-base")
                    recover_delay = self.config.timing("return_base_delay", 3.0)
                    if recover_delay > 0:
                        vision.device.sleep(recover_delay)
                    continue
                self.console.log(
                    f"Template '{matched_name}' detectado ({matched_path.name}) en {time.monotonic() - start:.1f}s"
                )
                return True
            time.sleep(poll)

        labels = ", ".join(template_map)
        message = f"Templates [{labels}] no aparecieron en {timeout:.1f}s"
        if strict:
            raise RoutineRestartError(message)
        self.console.log(f"[warning] {message}; continuando de todas formas")
        return False

    def _ready_template_candidates(self, layout: LayoutConfig) -> List[str]:
        candidates: List[str] = []
        if "world_button" in layout.templates:
            candidates.append("world_button")
        if "sede_button" in layout.templates:
            candidates.append("sede_button")
        if "game_ready" in layout.templates:
            candidates.append("game_ready")
        return candidates

    def _ensure_world_screen(
        self,
        layout: LayoutConfig,
        vision: VisionHelper,
        *,
        stage: str,
        strict: bool = False,
    ) -> None:
        """Garantiza que la pantalla principal está visible antes/después de cada task."""
        template_names = self._ready_template_candidates(layout)
        if not template_names:
            return

        self.console.log(f"Verificando pantalla principal ({stage})")
        if self._wait_for_ready(layout, vision, strict=False):
            return

        self.console.log(
            "[warning] Pantalla principal no detectada; presionando 'back_button' para intentar volver"
        )
        for attempt in (1, 2):
            if self._tap_back_button(layout, vision):
                if self._wait_for_ready(layout, vision, strict=False):
                    return
                self.console.log(
                    f"[warning] 'back_button' no devolvió al mapa (intento {attempt}/2); reintentando"
                )

        message = (
            f"Templates {template_names} no aparecieron incluso tras intentar volver con 'back_button'"
        )
        if strict:
            raise RoutineRestartError(message)
        self.console.log(f"[warning] {message}; continuando de todas formas")

    def _tap_back_button(self, layout: LayoutConfig, vision: VisionHelper) -> bool:
        """Presiona el botón 'back' usando visión para salir de overlays."""
        detect_timeout = self.config.timing("back_button_detect_timeout", 4.0)
        detect_threshold = self.config.timing("back_button_detect_threshold", 0.83)
        tapped = tap_back_button(
            device=vision.device,
            layout=layout,
            console=self.console,
            vision=vision,
            label="ensure-world-back",
            timeout=detect_timeout,
            threshold=detect_threshold,
        )
        if tapped:
            back_delay = self.config.timing("back_button_recover_delay", 2.0)
            if back_delay > 0:
                vision.device.sleep(back_delay)
        return tapped

    def _maybe_trigger_trucks(self, ctx: TaskContext) -> None:
        """Ejecuta la tarea 'trucks' automáticamente si se detecta el ícono de alerta."""
        vision = ctx.vision
        if not vision:
            return
        tracker = ctx.daily_tracker
        if tracker and tracker.should_skip(ctx.farm.name, "trucks"):
            return
        if "truck_alert_icon" not in ctx.layout.templates:
            return
        try:
            template_paths = ctx.layout.template_paths("truck_alert_icon")
        except KeyError:
            return
        threshold = self.config.timing("truck_alert_threshold", 0.85)
        result = vision.find_any_template(template_paths, threshold=threshold)
        if not result:
            return
        cooldown = self.config.timing("truck_alert_cooldown_seconds", 300.0)
        last_run = self._truck_trigger_state.get(ctx.farm.name, 0.0)
        now = time.monotonic()
        if cooldown > 0 and now - last_run < cooldown:
            return
        trucks_task = self.registry.get("trucks")
        if trucks_task is None:
            return
        previous_task = self.console.current_task() if hasattr(self.console, "current_task") else None
        if hasattr(self.console, "set_task"):
            self.console.set_task("trucks(auto)")
        try:
            self.console.log("[info] Icono de camión con alerta detectado; ejecutando 'trucks'")
            params = self.config.task_defaults_for("trucks")
            trucks_task.run(ctx, params)
        finally:
            if hasattr(self.console, "set_task"):
                self.console.set_task(previous_task)
        self._truck_trigger_state[ctx.farm.name] = now
        self._ensure_world_screen(ctx.layout, vision, stage="tras trucks automáticos", strict=False)

    def _maybe_trigger_radar_quests(self, ctx: TaskContext) -> None:
        """Lanza 'radar_quests' cuando aparece el ícono y cooldown lo permite."""
        vision = ctx.vision
        if not vision:
            return
        template_name = "radar_alert_icon"
        if template_name not in ctx.layout.templates:
            return
        try:
            template_paths = ctx.layout.template_paths(template_name)
        except KeyError:
            return
        threshold = self.config.timing("radar_alert_threshold", 0.85)
        result = vision.find_any_template(template_paths, threshold=threshold)
        if not result:
            return
        cooldown = self.config.timing("radar_alert_cooldown_seconds", 300.0)
        last_run = self._radar_trigger_state.get(ctx.farm.name, 0.0)
        now = time.monotonic()
        if cooldown > 0 and now - last_run < cooldown:
            return
        radar_task = self.registry.get("radar_quests")
        if radar_task is None:
            return
        previous_task = self.console.current_task() if hasattr(self.console, "current_task") else None
        if hasattr(self.console, "set_task"):
            self.console.set_task("radar_quests(auto)")
        try:
            self.console.log("[info] Icono de radar con alerta detectado; ejecutando 'radar_quests'")
            params = dict(self.config.task_defaults_for("radar_quests"))
            params["skip_daily_limit_check"] = True
            radar_task.run(ctx, params)
        finally:
            if hasattr(self.console, "set_task"):
                self.console.set_task(previous_task)
        self._radar_trigger_state[ctx.farm.name] = now
        self._ensure_world_screen(ctx.layout, vision, stage="tras radar automático", strict=False)
