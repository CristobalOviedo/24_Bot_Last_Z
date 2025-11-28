"""Orquestador de rutinas: selecciona granjas y ejecuta tareas automatizadas."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence
import os
import time

from rich.console import Console
from rich.table import Table

from .config import BotConfig, InstanceConfig, LayoutConfig, RoutineConfig, TaskSpec
from .farm_console import FarmConsole
from .debug import DebugReporter, get_debug_reporter
from .navigation import tap_back_button
import pytesseract


class RoutineRestartError(RuntimeError):
    """Señala que la rutina debe reiniciarse desde BlueStacks."""

from .daily_tracker import DailyTaskTracker
from .devices import (
    BlueStacksInstanceManager,
    DeviceController,
    DeviceCaptureError,
    DeviceRecoverableError,
)
from .tasks import build_registry
from .tasks.base import TaskContext
from .vision import VisionHelper


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


class RoutineRunner:
    """Ejecuta una rutina sobre múltiples granjas controlando ADB y visión."""

    def __init__(self, config: BotConfig, options: RunnerOptions) -> None:
        self.config = config
        self.options = options
        self.console = Console()
        self.registry = build_registry()
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
        self.daily_tracker: DailyTaskTracker | None = None
        self.debug_reporter: DebugReporter = get_debug_reporter()
        self._truck_trigger_state: Dict[str, float] = {}
        self._radar_trigger_state: Dict[str, float] = {}
        tracking_cfg = config.daily_tracking
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
        farms = self._select_farms()
        if not farms:
            self.console.print("[bold red]No hay granjas para ejecutar")
            return
        farms = self._apply_loop_rotation(farms)

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

        iteration = 0
        try:
            while True:
                iteration += 1
                if self.options.loop:
                    self.console.rule(f"Iteración #{iteration}")

                for farm in farms:
                    routine_id = self.options.routine_override or farm.routine
                    routine = self._single_task_routine or self.config.routine_for(routine_id)
                    layout = self.config.layout_for(farm)
                    total_tasks = len(routine.tasks)
                    next_task_idx = 0
                    restart_attempts = 0
                    max_restarts = max(0, int(self.config.timing("routine_restart_attempts", 1)))

                    self.console.rule(f"{farm.name} :: rutina {self._routine_label(farm)}")
                    while next_task_idx < total_tasks:
                        try:
                            next_task_idx = self._run_routine_attempt(
                                farm,
                                routine,
                                layout,
                                start_task=next_task_idx,
                            )
                        except RoutineRestartError as exc:
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

                        if next_task_idx < total_tasks:
                            self.console.log(
                                f"[warning] {farm.name}: rutina incompleta ({next_task_idx}/{total_tasks} tareas)"
                            )

                if not self.options.loop:
                    break
        except KeyboardInterrupt:
            self.console.log("[info] Ejecución interrumpida por el usuario")

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
        try:
            self.console = farm_console
            if previous_manager_console is not None:
                self.instance_manager.console = farm_console
            with self.instance_manager.ensure_instance(farm):
                device = DeviceController(
                    self.config.adb,
                    farm.device_port,
                    self.console,
                    simulate=self.options.simulate,
                )
                device.wait_for_device()
                warmup = self.config.timings.get(
                    "instance_warmup_seconds", self.config.instance_warmup_seconds
                )
                if warmup > 0:
                    self.console.log(
                        f"Esperando {warmup:.1f}s para que cargue la instancia"
                    )
                    device.sleep(warmup)

                vision = VisionHelper(
                    device=device,
                    console=self.console,
                    farm_name=farm.name,
                    debug_reporter=self.debug_reporter,
                )
                device.set_debug_capture(vision.capture_for_debug)
                self._maybe_launch_game(layout, vision)

                try:
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
                    )

                    task_index = start_task
                    if task_index > 0:
                        self.console.log(
                            f"Reanudando rutina en la tarea #{task_index + 1}/{len(routine.tasks)}"
                        )

                    self._maybe_trigger_trucks(ctx)
                    self._maybe_trigger_radar_quests(ctx)

                    while task_index < len(routine.tasks):
                        spec = routine.tasks[task_index]
                        task = self.registry.get(spec.task)
                        allow_repeat = getattr(
                            task, "allow_repeat_after_completion", False
                        )
                        should_skip = (
                            self.daily_tracker
                            and self.daily_tracker.should_skip(farm.name, spec.task)
                        )
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

                    return task_index
                except (DeviceCaptureError, DeviceRecoverableError) as exc:
                    raise RoutineRestartError(str(exc)) from exc
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
        if self._launch_game_via_template(layout, vision):
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

        level_overlay_paths = self._level_up_overlay_templates(layout)
        template_paths = layout.template_paths("popup_close")
        max_attempts = int(self.config.timing("initial_popup_max_attempts", 5))
        first_timeout = self.config.timing("initial_popup_first_timeout", 60)
        next_timeout = self.config.timing("initial_popup_next_timeout", 5)
        popup_delay = self.config.timing("initial_popup_delay", 1.0)

        attempts = 0
        timeout = first_timeout
        while True:
            if max_attempts > 0 and attempts >= max_attempts:
                break

            result = vision.wait_for_any_template(
                template_paths,
                timeout=timeout,
                poll_interval=1.0,
                raise_on_timeout=False,
            )
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

    def _wait_for_ready(
        self, layout: LayoutConfig, vision: VisionHelper, *, strict: bool = False
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

        timeout = max(self.config.timing("ready_template_timeout", 30), 5.0)
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
        if self._tap_back_button(layout, vision):
            if self._wait_for_ready(layout, vision, strict=False):
                return

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
        self.console.log("[info] Icono de camión con alerta detectado; ejecutando 'trucks'")
        params = self.config.task_defaults_for("trucks")
        trucks_task.run(ctx, params)
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
        self.console.log("[info] Icono de radar con alerta detectado; ejecutando 'radar_quests'")
        params = dict(self.config.task_defaults_for("radar_quests"))
        params["skip_daily_limit_check"] = True
        radar_task.run(ctx, params)
        self._radar_trigger_state[ctx.farm.name] = now
        self._ensure_world_screen(ctx.layout, vision, stage="tras radar automático", strict=False)
